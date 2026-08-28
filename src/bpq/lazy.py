"""懒人版启动路径：双击 exe 就把 daemon 和 WebUI 跑起来。

只服务一种场景——**打包成单文件 exe 之后，不带任何参数双击运行**。
从终端敲 `bpq daemon`、`bpq submit ...` 走的还是原来的 CLI，不经过这里。

做三件事，都是为了让「不看文档的人」也能用起来：

1. 没有 config.toml 就在 exe 旁边生成一份（内容是打包进去的 config.example.toml）。
   打印机 IP / SERIAL / Access Code 填不了——那是每个人自己的——但没关系：
   daemon 连不上打印机也照常启动，剩下的在网页的「设置」页里填。
2. 起 daemon，WebUI 就绪后自动把浏览器打开到那个地址。
3. 出错或退出时停下来等一次回车。双击起的窗口一关就没，不停下来的话
   报错信息跟着窗口一起消失，人只会看到「闪退」——v0.3.0 踩过的正是这个坑。
"""

from __future__ import annotations

import logging
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

from bpq import __version__
from bpq.config import ConfigError, bundled_example, find_config_path
from bpq.config import load as load_config

log = logging.getLogger(__name__)


def _pause(message: str = "按回车键关闭…") -> None:
    """停下来等回车。stdin 不可用时（被重定向 / 没有控制台）直接跳过，不要卡死。"""
    try:
        input(f"\n{message}")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def ensure_config() -> Path:
    """保证 exe 旁边有一份 config.toml，返回它的路径。

    已经有就原样用——绝不覆盖，那里面是用户填过的打印机凭据。
    """
    path = find_config_path()
    if path.exists():
        return path

    example = bundled_example()
    if example is None:
        raise ConfigError(
            f"需要一份配置文件 {path}，但程序里没带模板，没法自动生成。\n"
            "从项目仓库拷一份 config.example.toml 过来，改名成 config.toml。"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, path)
    print(f"首次运行，已经生成配置文件：{path}", flush=True)
    print("打印机的 IP / SERIAL / Access Code 待会儿在网页的「设置」页里填。\n", flush=True)
    return path


def _open_browser_later(url: str) -> None:
    """稍等一下再开浏览器。

    WebUI 的端口这时已经在听了（wait_until_started 保证过），但页面本身还要
    再等一下静态资源就位；直接开偶尔会撞上一个空白页，让人以为没起来。
    """
    def _go() -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - 开不了浏览器不该让 daemon 挂掉
            log.warning("没能自动打开浏览器（%s），手动访问 %s", exc, url)

    threading.Timer(1.0, _go).start()


def run() -> int:
    """懒人版主流程。返回进程退出码。"""
    # flush=True：daemon 的日志走 stderr，这些提示走 stdout，两条流各自缓冲。
    # 不主动 flush 的话，横幅可能排在一堆日志后面才冒出来，甚至被当成没反应。
    print(f"bpq {__version__} — 拓竹 A1 定时静默打印调度器", flush=True)
    print("=" * 46, flush=True)
    print("正在启动…（这个窗口要一直开着，关掉它定时任务就不会触发了）\n", flush=True)

    from bpq.daemon import AlreadyRunning, serve

    try:
        cfg = load_config(ensure_config())
    except ConfigError as exc:
        print(f"\n配置有问题，起不来：\n{exc}")
        _pause()
        return 1

    if not cfg.web.enabled:
        print("提醒：config.toml 里 [web] enabled = false，网页界面不会启动。")

    try:
        serve(cfg, on_web_ready=_open_browser_later)
    except AlreadyRunning as exc:
        # 双击两次是最容易犯的错，说清楚就行，不要甩栈回溯
        print(f"\n{exc}")
        _pause()
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - 双击场景下必须把错误留在屏幕上
        print(f"\n启动失败：{type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        _pause()
        return 1

    print("\nbpq 已退出。")
    _pause()
    return 0


def is_double_click() -> bool:
    """是不是「打包出来的 exe + 一个参数都没带」这种双击场景。"""
    return getattr(sys, "frozen", False) and len(sys.argv) == 1
