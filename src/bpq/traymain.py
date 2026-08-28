"""托盘版 exe 的入口：打包成**无控制台窗口**（PyInstaller `--windowed`）跑。

`cli.py` 保持不变，继续给控制台版 exe（`bpq daemon` / `bpq submit` ...）用；
这里是给托盘常驻版本单独开的入口，两条打包产物并存，互不影响。

没有控制台窗口意味着 `print()` 没人能看见——排障只能靠日志文件和一个原生弹窗，
所以本模块的大半内容都是在为「出了问题时人能看到什么」兜底。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bpq import __version__
from bpq.config import ConfigError, project_root
from bpq.config import load as load_config
from bpq.lazy import ensure_config


def _setup_logging() -> Path:
    """无控制台窗口，唯一的排障途径是日志文件。

    必须在调用 daemon.serve() 之前跑：serve() 内部会调
    `logging.basicConfig(level=logging.INFO, format=...)`，而标准库
    `basicConfig()` 的既有语义是——root logger 已经有 handler 时这个调用什么都不做。
    这里先把文件 handler 装好，那一行就自动变成 no-op，不需要改 daemon.py。
    """
    log_dir = project_root() / "var"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "bpq.log"
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def _show_error(title: str, message: str) -> None:
    """无控制台窗口时唯一能让用户看到错误的办法：原生弹窗。

    这个模块只会在 Windows 打包出的托盘 exe 里跑，但 `ctypes.windll` 只在
    typeshed 的 `sys.platform == "win32"` 分支下才有定义——这层判断是为了让
    mypy 在非 Windows 平台（CI 的 ubuntu-latest）上检查时能剪掉这条分支，
    不是运行时真的会走到别处（tray.py 的 `_open_path`/`_confirm_yesno` 同理）。
    """
    if sys.platform != "win32":
        return
    mb_iconerror = 0x00000010
    ctypes.windll.user32.MessageBoxW(0, message, title, mb_iconerror)


def _wait_until(predicate, timeout: float, interval: float = 0.1) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def main() -> int:
    log_path = _setup_logging()
    log = logging.getLogger(__name__)
    log.info("bpq %s 托盘版启动，日志见 %s", __version__, log_path)

    # CI 的 Windows runner 未必有交互式桌面 session，直接跑 pystray.Icon.run()
    # 可能因为拿不到 window station 而挂起。这个环境变量把 tray.run() 那一步
    # 跳过，只验证「配置能读、daemon 能起、WebUI 能绑定端口、能优雅退出」这条链路，
    # 平时（用户双击）不会被设置，是完全休眠的代码路径。
    selftest = os.environ.get("BPQ_TRAY_SELFTEST") == "1"

    try:
        cfg = load_config(ensure_config())
    except ConfigError as exc:
        log.error("配置有问题：%s", exc)
        if not selftest:
            _show_error("bpq 启动失败", str(exc))
        return 1

    from bpq import runtime
    from bpq.daemon import AlreadyRunning, serve

    stop_event = threading.Event()
    web_ready = threading.Event()
    state: dict = {"url": None, "error": None, "already_running": False}

    def _on_web_ready(url: str) -> None:
        state["url"] = url
        web_ready.set()

    def _run_daemon() -> None:
        try:
            serve(cfg, on_web_ready=_on_web_ready, stop_event=stop_event)
        except AlreadyRunning:
            # 双击两次是最容易犯的错：已经有一个实例在跑了，直接把浏览器
            # 指过去就好，不当成错误处理。
            state["already_running"] = True
            web_ready.set()
        except Exception as exc:  # noqa: BLE001 - 必须让主线程能看到，不能吞掉
            log.exception("daemon 启动失败")
            state["error"] = exc
            web_ready.set()

    daemon_thread = threading.Thread(target=_run_daemon, name="bpq-daemon", daemon=False)
    daemon_thread.start()

    if cfg.web.enabled:
        started = web_ready.wait(timeout=30)
    else:
        # WebUI 关着就没有 on_web_ready 回调可等，退而求其次等 runtime 登记完成。
        started = _wait_until(
            lambda: runtime.current() is not None or state["error"] or state["already_running"],
            timeout=30,
        )

    if state["already_running"]:
        from bpq.client import DaemonClient

        url = DaemonClient(cfg).base
        log.info("已有实例在跑，把浏览器指过去（%s）然后退出这次启动", url)
        if not selftest:
            webbrowser.open(url)
        return 0

    if state["error"] is not None:
        if not selftest:
            _show_error("bpq 启动失败", f"{type(state['error']).__name__}: {state['error']}")
        return 1

    if not started:
        log.error("daemon 启动超时")
        if not selftest:
            _show_error("bpq 启动失败", "daemon 启动超时，详见日志")
        stop_event.set()
        daemon_thread.join(timeout=10)
        return 1

    web_url = state["url"] if cfg.web.enabled else None
    if web_url and not selftest:
        webbrowser.open(web_url)

    if selftest:
        out_path = Path(os.environ.get("BPQ_TRAY_SELFTEST_OUT", "selftest_result.txt"))
        out_path.write_text("OK\n", encoding="utf-8")
        log.info("selftest 通过，准备退出")
        stop_event.set()
        daemon_thread.join(timeout=10)
        return 0

    from bpq import tray

    try:
        tray.run(cfg, stop_event=stop_event, web_url=web_url)
    finally:
        stop_event.set()
        daemon_thread.join(timeout=10)
        if daemon_thread.is_alive():
            log.warning("daemon 线程 10 秒内没退出")

    log.info("bpq 托盘版已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
