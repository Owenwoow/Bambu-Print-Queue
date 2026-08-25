"""配置加载。真实凭据放 config.toml（已 gitignore），示例见 config.example.toml。"""

from __future__ import annotations

import dataclasses
import logging
import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TypeVar

log = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "config.toml"

_T = TypeVar("_T")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrinterConfig:
    ip: str
    serial: str
    access_code: str
    model: str = "A1"


@dataclass(frozen=True)
class TransportConfig:
    channel: str = "lan"
    ftps_port: int = 990
    # 实测本机 A1 必须加密数据通道，明文会被直接断开；与部分社区文档的说法相反。
    ftps_encrypt_data: bool = True
    ftps_remote_dir: str = "/"
    mqtt_port: int = 8883


@dataclass(frozen=True)
class PrintConfig:
    """启动指令里的减噪开关，这些是**默认值**——每个任务可以用 Task.options 单独覆盖。

    `use_ams` 字段（v0.1 遗留）已删除：project_file 的 use_ams 现在由
    task.ams_mapping 是否非空推出来，代码里早就不读这个配置项了。
    """

    bed_leveling: bool = False
    vibration_cali: bool = False
    flow_cali: bool = False
    timelapse: bool = False
    layer_inspect: bool = False
    # ams_mapping 里填这个值表示「这个耗材位走外部料，不走 AMS」。
    # 这是一个**尚未实测的猜测值**（抓包里没见过外部料出现在 mapping 里的样本）——
    # 做成配置项而不是写死在代码里，就是为了真机验证清楚之后只改一行配置，不用动代码。
    external_spool_id: int = -1


@dataclass(frozen=True)
class SchedulerConfig:
    on_printer_busy: str = "abort"
    # 上一单以 FAILED 收场时是否照常触发。默认否——板子上可能还有残骸，
    # 半夜往废墟上再打一层不如放弃。
    start_after_failure: bool = False
    misfire_grace_time: int = 300
    upload_timing: str = "early"


@dataclass(frozen=True)
class DaemonConfig:
    inhibit_sleep: bool = True
    db_path: str = "var/bpq.sqlite3"
    journal_path: str = "var/bpq.jsonl"
    spool_dir: str = "var/tasks"


@dataclass(frozen=True)
class WebConfig:
    """v0.2 起的本地 WebUI（提交任务、编辑 AMS 映射、看日志）。"""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8710
    # 明文口令——WebUI 只打算在局域网内用，没有做 HTTPS。跨网段暴露前自己加反代。
    password: str = ""
    # 从本机（127.0.0.1）访问时跳过口令校验，图个本地操作方便；
    # 副作用：同一台电脑上的任何本地进程/用户都能不经口令操作打印任务，
    # 多用户共享的电脑上应该关掉。
    allow_local_no_auth: bool = True
    session_days: int = 30


@dataclass(frozen=True)
class LinkConfig:
    """WebUI 与打印机之间常驻连接的行为参数。"""

    pushall_interval: int = 300    # 定期主动拉一次全量，防止漏掉增量报文导致状态漂移
    stale_after: int = 120         # 超过这么久没收到任何报文，判定连接已经不新鲜
    reconnect_max_delay: int = 30  # 断线重连的退避上限（秒）


@dataclass(frozen=True)
class Config:
    printer: PrinterConfig
    transport: TransportConfig
    print: PrintConfig
    scheduler: SchedulerConfig
    daemon: DaemonConfig
    path: Path
    web: WebConfig = field(default_factory=WebConfig)
    link: LinkConfig = field(default_factory=LinkConfig)


def _abs(value: str, base: Path) -> Path:
    """相对路径按 base 解析；已经是绝对路径就原样返回。"""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _section(cls: type[_T], raw: dict, name: str) -> _T:
    """按段名构造配置 dataclass，忽略它不认识的键并给出警告。

    为什么不直接 `cls(**raw[name])`：那样一来，配置项一旦被删除（比如 v0.2 去掉了
    `[print] use_ams`），所有还留着旧键的 config.toml 就会以一句
    `TypeError: got an unexpected keyword argument` 直接启动失败——
    用户没做错任何事，只是没跟着改配置文件。

    顺带把「键名拼错」这件事从静默失效变成一条明确的警告：多打一个键会被这里点名，
    而不是安安静静地不生效，让人对着一个"改了没反应"的配置项发呆。
    """
    data = raw.get(name, {})
    if not isinstance(data, dict):
        raise ConfigError(f"config.toml 的 [{name}] 段应该是一个表，实际是 {type(data).__name__}")
    known = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - known)
    if unknown:
        log.warning(
            "config.toml 的 [%s] 段有 %d 个无法识别的键，已忽略：%s",
            name, len(unknown), "、".join(unknown),
        )
    return cls(**{k: v for k, v in data.items() if k in known})


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """按 显式参数 → BPQ_CONFIG 环境变量 → 项目根 config.toml 的顺序定位。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("BPQ_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / DEFAULT_CONFIG_NAME


def project_root() -> Path:
    """src/bpq/config.py → 项目根。"""
    return Path(__file__).resolve().parents[2]


def load(explicit: str | os.PathLike[str] | None = None) -> Config:
    path = find_config_path(explicit)
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件 {path}；先复制 config.example.toml 为 config.toml 并填写。"
        )
    with path.open("rb") as f:
        raw = tomllib.load(f)

    try:
        printer = PrinterConfig(**raw["printer"])
    except KeyError as exc:
        raise ConfigError(f"config.toml 缺少 [printer] 段: {exc}") from exc

    # [notify] 段目前仍然静默忽略（v0.1 起就是占位，未实现）；
    # 新增的 [web] / [link] 都已经接进下面，不会重蹈同样的静默丢弃。
    daemon = _section(DaemonConfig, raw, "daemon")
    # 把 var/ 下那几个相对路径按 **config.toml 所在的目录** 解析成绝对路径。
    # 不这么做的话，daemon 从别的工作目录启动（systemd、开机自启、双击快捷方式）
    # 就会在那个目录下另建一套 var/：任务库、日志、上传缓存全都换了地方，
    # 表现是「提交过的任务不见了」——而且不会有任何报错。
    base = path.parent
    daemon = replace(
        daemon,
        db_path=str(_abs(daemon.db_path, base)),
        journal_path=str(_abs(daemon.journal_path, base)),
        spool_dir=str(_abs(daemon.spool_dir, base)),
    )

    return Config(
        printer=printer,
        transport=_section(TransportConfig, raw, "transport"),
        print=_section(PrintConfig, raw, "print"),
        scheduler=_section(SchedulerConfig, raw, "scheduler"),
        daemon=daemon,
        path=path,
        web=_section(WebConfig, raw, "web"),
        link=_section(LinkConfig, raw, "link"),
    )
