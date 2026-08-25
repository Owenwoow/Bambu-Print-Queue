"""把配置写回 config.toml，保留注释。

为什么不能用 `tomllib` + 重新序列化：`config.toml` 里的注释几乎全是实测结论——
「明文数据通道直接 EOF，必须 PROT P」「这一行尚未实测，社区里 -1 和 255 都见过」——
重写一次就全没了，而那些结论比配置值本身更难重新获得。

`tomlkit` 就是为「改 TOML 保留格式与注释」而生的，所以用它。

写入走「临时文件 + 原子替换」：这个文件里有打印机的 access_code，
写到一半断电留下半个配置文件，下次启动就连不上打印机了。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import tomlkit

log = logging.getLogger(__name__)

CRLF = "\r\n"
LF = "\n"


class ConfigWriteError(RuntimeError):
    """配置写不进去。调用方应该把它翻成给人看的话。"""


def update(path: str | Path, section: str, values: dict[str, Any]) -> None:
    """更新 config.toml 里某个段的若干个键。

    段不存在就新建。传进来的值为 None 的键会被跳过（表示「这一项不改」），
    因为 TOML 没有 null，写 None 进去只会得到一个语法错误。
    """
    path = Path(path)
    if not path.exists():
        raise ConfigWriteError(f"配置文件不在：{path}")

    raw = path.read_bytes()
    try:
        doc = tomlkit.parse(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - tomlkit 的异常类型不稳定
        raise ConfigWriteError(f"{path.name} 解析失败，没有改动任何内容：{exc}") from exc

    table = doc.get(section)
    if table is None:
        table = tomlkit.table()
        doc[section] = table

    changed = []
    for key, value in values.items():
        if value is None:
            continue
        if table.get(key) != value:
            changed.append(key)
        table[key] = value

    if not changed:
        return

    _atomic_write(path, tomlkit.dumps(doc), newline=_detect_newline(raw))
    log.info("已更新 %s 的 [%s] 段：%s", path.name, section, "、".join(changed))


def _detect_newline(raw: bytes) -> str:
    """看原文件用的是哪种行尾，写回时保持一致。

    不保留的话，在网页上点一下开关，整个文件的行尾就从 CRLF 变成 LF——
    功能上没影响，但拿 diff 工具一看会以为全文都改了，很容易吓人一跳。
    """
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    return CRLF if crlf > bare_lf else LF


def _atomic_write(path: Path, text: str, *, newline: str = LF) -> None:
    """先写同目录的临时文件再替换。

    同目录是必须的：os.replace 只在同一个文件系统上才是原子的。
    打开时 newline="" 关掉 Python 的自动转换，行尾由下面这几行自己控制。
    """
    # 先归一再转换。tomlkit **保留**原文件的行尾，所以它的输出里可能已经是 CRLF；
    # 直接做 LF→CRLF 替换会把那些变成 CRCRLF，而 tomlkit 下次读这个文件时，
    # 会把多出来的 CR 当成注释里的非法控制字符直接拒绝解析。
    # 症状是「在网页上改了两次配置，第二次开始报解析失败」。
    text = text.replace(CRLF, LF)
    if newline != LF:
        text = text.replace(LF, newline)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
