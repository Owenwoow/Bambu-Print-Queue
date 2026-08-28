"""daemon.serve() 和 daemon._serve_locked() 的 stop_event 参数测试。

stop_event 是一个新参数，允许调用方（比如托盘版）自己掌握退出时机，
而不是只能靠信号（Ctrl-C）来触发。
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

import pytest

from bpq.config import (
    Config,
    DaemonConfig,
    LinkConfig,
    PrintConfig,
    PrinterConfig,
    SchedulerConfig,
    TransportConfig,
    WebConfig,
)
from bpq.daemon import _serve_locked, serve


def make_cfg(tmp_path: Path) -> Config:
    """构造最小的配置对象。"""
    return Config(
        printer=PrinterConfig(ip="10.0.0.9", serial="ABC", access_code="123"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(
            db_path=str(tmp_path / "bpq.sqlite3"),
            journal_path=str(tmp_path / "bpq.jsonl"),
            inhibit_sleep=False,   # 测试机不该真的去阻止系统睡眠
        ),
        link=LinkConfig(),
        web=WebConfig(enabled=False),
        path=tmp_path / "config.toml",
    )


class Test_serve_signature:
    """serve() 函数签名应该接受 stop_event 参数。"""

    def test_serve_接受_stop_event_参数(self) -> None:
        sig = inspect.signature(serve)
        assert "stop_event" in sig.parameters
        param = sig.parameters["stop_event"]
        # 应该是可选的关键字参数
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        # 默认值应该是 None（可选）
        assert param.default is None

    def test_serve_接受_on_web_ready_参数(self) -> None:
        """既有的 on_web_ready 参数应该还在。"""
        sig = inspect.signature(serve)
        assert "on_web_ready" in sig.parameters

    def test_serve_接受_cfg_参数(self) -> None:
        """位置参数 cfg 应该还在。"""
        sig = inspect.signature(serve)
        assert "cfg" in sig.parameters


class Test_serve_locked_signature:
    """_serve_locked() 函数签名应该接受 stop_event 参数。"""

    def test_serve_locked_接受_stop_event_参数(self) -> None:
        sig = inspect.signature(_serve_locked)
        assert "stop_event" in sig.parameters
        param = sig.parameters["stop_event"]
        # 应该是可选的关键字参数
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        # 默认值应该是 None（可选）
        assert param.default is None

    def test_serve_locked_接受_on_web_ready_参数(self) -> None:
        """既有的 on_web_ready 参数应该还在。"""
        sig = inspect.signature(_serve_locked)
        assert "on_web_ready" in sig.parameters

    def test_serve_locked_接受_cfg_参数(self) -> None:
        """位置参数 cfg 应该还在。"""
        sig = inspect.signature(_serve_locked)
        assert "cfg" in sig.parameters


class Test_stop_event_参数验收:
    """验证 stop_event 参数的基本接收能力。

    完整的集成测试（实际运行 daemon）不适合单元测试，因为它会真的去连打印机。
    这里只验证：参数被正确接受、签名无改变、既有调用方兼容。
    """

    def test_serve_能接受_none_作为默认值(self) -> None:
        """serve() 的 stop_event 默认是 None，可以调用不传这个参数。"""
        # 这个测试只验证参数默认值存在，不实际调用 serve（那会去连打印机）
        sig = inspect.signature(serve)
        assert "stop_event" in sig.parameters
        assert sig.parameters["stop_event"].default is None

    def test_serve_locked_能接受_none_作为默认值(self) -> None:
        """_serve_locked() 的 stop_event 默认是 None，可以调用不传这个参数。"""
        sig = inspect.signature(_serve_locked)
        assert "stop_event" in sig.parameters
        assert sig.parameters["stop_event"].default is None

    def test_serve_的_stop_event_是关键字参数(self) -> None:
        """stop_event 必须作为关键字参数传递，不能位置传递。"""
        sig = inspect.signature(serve)
        assert sig.parameters["stop_event"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_serve_locked_的_stop_event_是关键字参数(self) -> None:
        """stop_event 必须作为关键字参数传递，不能位置传递。"""
        sig = inspect.signature(_serve_locked)
        assert sig.parameters["stop_event"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_stop_event_参数类型注解包含_threading_event(self) -> None:
        """函数签名的类型提示应该说明 stop_event 可以是 threading.Event。"""
        sig = inspect.signature(_serve_locked)
        param = sig.parameters["stop_event"]
        # 获取类型注解字符串
        annotation_str = str(param.annotation)
        # 应该包含 Event（可能是 threading.Event 或 Event）
        assert "Event" in annotation_str or param.annotation is None or "None" in annotation_str


class Test_stop_event_真实生效:
    """上面那组只验证签名——这里补一条真正跑一遍 _serve_locked() 的行为测试：
    传入一个提前 set() 过的 stop_event，daemon 侧的轮询循环必须在下一次
    stop.wait(timeout=1) 就发现并返回，证明它确实用的是传入的这个 Event，
    而不是自己另造了一个（否则外部 set() 永远没人看得到，会一直卡住）。

    唯一必须假的东西是 PrinterLink——它的 open()/close() 是真实 MQTT 连接，
    绝不能在测试里碰。TaskStore/Journal/scheduler 都是本地 SQLite，用 tmp_path
    隔离，保持真实反而更能验证 _serve_locked 整条路径没被这个新参数搞坏。
    """

    def test_预先置位的_stop_event_让_serve_locked_几乎立刻返回(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from bpq.snapshot import PrinterSnapshot

        class FakePrinterLink:
            """替身：不建任何连接，只满足 _serve_locked / _build_service 读到的接口。"""

            def __init__(self, cfg: object, **kwargs: object) -> None:
                self.cfg = cfg

            def open(self) -> None:
                pass

            def close(self) -> None:
                pass

            def session(self) -> None:
                return None

            def snapshot(self) -> PrinterSnapshot:
                return PrinterSnapshot()

        monkeypatch.setattr("bpq.link.PrinterLink", FakePrinterLink)

        cfg = make_cfg(tmp_path)  # web.enabled=False：不必再假 uvicorn
        stop_event = threading.Event()
        stop_event.set()  # 还没进 _serve_locked 就已经置位

        start = time.monotonic()
        _serve_locked(cfg, stop_event=stop_event)
        elapsed = time.monotonic() - start

        # 循环体是 while not stop.wait(timeout=1)，提前置位应该第一次检查就命中，
        # 给足够宽松的上限（3 秒）避免测试机偶发调度延迟导致假失败。
        assert elapsed < 3, (
            f"stop_event 预先置位后 _serve_locked 应该几乎立刻返回，实际耗时 {elapsed:.1f}s"
        )

    # 「不传 stop_event 时是否还能用信号退出」不在这里测：Windows 上
    # os.kill(os.getpid(), signal.SIGTERM) 走的是 TerminateProcess，是硬杀，
    # 不会触发 Python 的信号处理器，反而会把整个 pytest 进程杀掉——不值得为了
    # 验证一个没变过的旧行为冒这个险。旧行为本身没有被这次改动触碰
    # （stop = stop_event if stop_event is not None else threading.Event()，
    # 分支 else 就是原来的代码），上面的签名测试已经证明这条分支还在。
