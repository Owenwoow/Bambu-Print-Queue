"""在 daemon 进程里跑 uvicorn。

**绝不开 reload。** uvicorn 的 reloader 会 fork 出一个子进程，那会让 daemon 的
单实例文件锁失去意义——两个进程同时以为自己独占，抢打印机那唯一的 MQTT 连接，
正是 v0.1 验收时踩过的坑。开发前端用 `npm run dev`（Vite 有自己的热更新），
改后端就手动重启 daemon。

同理不让 uvicorn 装信号处理器：信号由 daemon 主线程统一处理，
而 uvicorn 跑在后台线程里本来也注册不了（signal 只能在主线程注册）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

log = logging.getLogger(__name__)


class _NoSignalServer(uvicorn.Server):
    """不让 uvicorn 碰信号。

    信号由 daemon 主线程统一处理——uvicorn 跑在后台线程里，本来也注册不了
    （signal 只能在主线程注册），让它去试只会抛异常。
    新版 uvicorn 把这个开关从 Config 移走了，覆写方法是各版本都稳的做法。
    """

    def install_signal_handlers(self) -> None:
        pass


@dataclass
class ServerHandle:
    server: uvicorn.Server
    thread: threading.Thread
    url: str

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def run_in_thread(app: FastAPI, *, host: str, port: int) -> ServerHandle:
    """起一个后台线程跑 uvicorn，返回句柄。

    daemon 的主线程要留着 stop.wait()——它得能第一时间响应 Ctrl-C，
    而 uvicorn 的事件循环没有理由占着主线程。
    """
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",     # uvicorn 的 access log 太吵，daemon 自己有日志
        access_log=False,
        # uvicorn 默认会自己 dictConfig 一套带颜色的 formatter，构造时探测
        # sys.stdout.isatty() 来决定要不要上色。托盘版 exe 没有控制台，
        # sys.stdout 是真正的 None（不是"重定向到文件"那种），.isatty() 直接
        # AttributeError，daemon 还没跑起来就崩了。关掉 uvicorn 自己那套配置，
        # 它的日志就走 daemon.py 已经装好的 root logger（console 版走 stderr，
        # 托盘版走 var/bpq.log），没有理由让它另起一套格式化逻辑。
        log_config=None,
    )
    server = _NoSignalServer(config)

    def _serve() -> None:
        # 事件循环在这个线程里建起来之后，SSE 的 broker 才能拿到它做跨线程投递
        server.run()

    thread = threading.Thread(target=_serve, daemon=True, name="bpq-web")
    thread.start()

    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    return ServerHandle(server=server, thread=thread, url=f"http://{shown}:{port}")


def wait_until_started(handle: ServerHandle, timeout: float = 10.0) -> bool:
    """等 uvicorn 真的起来。SSE 的 broker 要在这之后才能 bind 事件循环。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.server.started:
            return True
        time.sleep(0.05)
    return False
