"""PrinterLink 与 TaskRunner 注入的测试。

全程用假打印机，零网络。这里守的是 v0.2 最容易出事故的一条线：
打印机只接受一个 MQTT 连接，所以「谁在什么时候建连」必须是确定的。
"""

from __future__ import annotations

from datetime import datetime, timedelta
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
)
from bpq.journal import Journal
from bpq.link import PrinterLink
from bpq.models import PrinterState, Task
from bpq.scheduler import TaskRunner
from bpq.store import TaskStore
from tests.fakeprinter import FakePrinterTransport


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        printer=PrinterConfig(ip="10.0.0.9", serial="ABC123", access_code="12345678"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(
            db_path=str(tmp_path / "bpq.sqlite3"),
            journal_path=str(tmp_path / "bpq.jsonl"),
        ),
        link=LinkConfig(stale_after=3600, pushall_interval=3600),
        path=tmp_path / "config.toml",
    )


class CountingFactory:
    """数一数到底建了几次连接——本模块几乎每个测试都在盯这个数。"""

    def __init__(self, **kw: object) -> None:
        self.count = 0
        self.made: list[FakePrinterTransport] = []
        self._kw = kw

    def __call__(self, cfg: Config) -> FakePrinterTransport:
        self.count += 1
        tp = FakePrinterTransport(cfg, upload_seconds=0, speed=3000, **self._kw)  # type: ignore[arg-type]
        self.made.append(tp)
        return tp


@pytest.fixture
def factory() -> CountingFactory:
    return CountingFactory()


@pytest.fixture
def link(cfg: Config, factory: CountingFactory) -> PrinterLink:
    lk = PrinterLink(cfg, factory=factory)
    lk.open()
    yield lk
    lk.close()


def make_task(tmp_path: Path, **kw: object) -> Task:
    f = tmp_path / "model.gcode.3mf"
    f.write_bytes(b"not really a 3mf")
    base = {
        "source_path": str(f),
        "scheduled_at": datetime.now() + timedelta(minutes=5),
        "remote_name": "model.gcode.3mf",
    }
    base.update(kw)
    return Task(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 生命周期


def test_open_只建一次连接(link: PrinterLink, factory: CountingFactory) -> None:
    link.open()
    link.open()
    assert factory.count == 1, "open() 必须幂等——打印机只接受一个连接"
    assert link.connected


def test_open_之后快照立刻可用(link: PrinterLink) -> None:
    """open() 内部等过首个全量报文，返回后 WebUI 应该马上有东西可显示。"""
    s = link.snapshot()
    assert s.connected
    assert s.job.gcode_state is PrinterState.IDLE
    assert len(s.ams.all_trays()) == 4
    assert s.versions


def test_session_借出后不关连接(link: PrinterLink, factory: CountingFactory) -> None:
    """这正是它与 build_transport() 的区别，也是 TaskRunner 能换一行就复用的原因。"""
    with link.session() as tp:
        assert tp is not None
    assert link.connected
    assert factory.count == 1

    with link.session():
        pass
    assert factory.count == 1, "第二次借用不该再建连接"


def test_snapshot_不建连接也不阻塞(cfg: Config, factory: CountingFactory) -> None:
    """WebUI 每秒都会问它。读状态绝不能有建连的副作用。"""
    lk = PrinterLink(cfg, factory=factory)
    s = lk.snapshot()          # 还没 open
    assert factory.count == 0
    assert not s.connected
    assert s.job.gcode_state is PrinterState.UNKNOWN


# ---------------------------------------------------------------- 让出/抢回


def test_让出连接后断开(link: PrinterLink) -> None:
    link.yield_connection()
    assert not link.connected
    assert link.yielded
    # 快照还在，但要标成「连接已断」，界面上才不会显示一份看似新鲜的旧数据
    assert not link.snapshot().connected


def test_让出是幂等的(link: PrinterLink, factory: CountingFactory) -> None:
    link.yield_connection()
    link.yield_connection()
    assert link.yielded and factory.count == 1


def test_抢回连接(link: PrinterLink, factory: CountingFactory) -> None:
    link.yield_connection()
    assert link.resume_connection(reason="测试") is True
    assert link.connected and not link.yielded
    assert factory.count == 2, "让出之后要重新建连"


def test_没让出时抢回是空操作(link: PrinterLink, factory: CountingFactory) -> None:
    assert link.resume_connection() is False
    assert factory.count == 1


def test_让出状态下借用会自动抢回(link: PrinterLink, factory: CountingFactory) -> None:
    """session() 是下发指令的入口，它绝不能因为「连接被让出去了」就失败。"""
    link.yield_connection()
    with link.session() as tp:
        assert tp is not None
    assert link.connected
    assert factory.count == 2


def test_health_区分连接状态与让出状态(link: PrinterLink) -> None:
    h = link.health()
    assert h.connected and not h.yielded
    link.yield_connection()
    h = link.health()
    assert not h.connected and h.yielded


# ---------------------------------------------------------------- 订阅


def test_订阅收到状态变化(link: PrinterLink, tmp_path: Path) -> None:
    got: list[dict] = []
    link.add_listener(lambda s, p: got.append(p))
    with link.session() as tp:
        tp.start(make_task(tmp_path))
    assert any("job" in p for p in got)


def test_取消订阅(link: PrinterLink, tmp_path: Path) -> None:
    got: list[dict] = []
    cancel = link.add_listener(lambda s, p: got.append(p))
    cancel()
    with link.session() as tp:
        tp.start(make_task(tmp_path))
    assert got == []


def test_订阅者抛异常不影响其他订阅者(link: PrinterLink, tmp_path: Path) -> None:
    """一个 SSE 客户端出问题，不该把整条打印机状态流带走。"""
    good: list[dict] = []

    def boom(s: object, p: dict) -> None:
        raise RuntimeError("这个订阅者坏了")

    link.add_listener(boom)
    link.add_listener(lambda s, p: good.append(p))
    with link.session() as tp:
        tp.start(make_task(tmp_path))
    assert good, "坏订阅者不该阻断好订阅者"


# ------------------------------------------------- TaskRunner 复用长连接


def test_taskrunner_默认行为不变(cfg: Config) -> None:
    """不注入 transport 时保持 v0.1 的语义（每次临时建连），
    现有测试和「daemon 没跑时的 CLI」都靠这条退路。"""
    runner = TaskRunner(cfg, TaskStore(cfg.daemon.db_path), Journal(cfg.daemon.journal_path))
    assert runner._transport is not None


def test_taskrunner_注入后复用同一条连接(
    cfg: Config, link: PrinterLink, factory: CountingFactory, tmp_path: Path
) -> None:
    """v0.2 连接架构的要害：上传和触发都不该新建连接。

    v0.1 的 run_task 每次触发都 build_transport()，在有长连接的前提下
    那等于 daemon 每次触发都把自己踢下线。
    """
    store = TaskStore(cfg.daemon.db_path)
    runner = TaskRunner(cfg, store, Journal(cfg.daemon.journal_path),
                        transport=link.session)
    task = make_task(tmp_path)

    runner.submit(task)              # 走 upload
    assert factory.count == 1

    runner.fire(task.id)             # 走 get_state + start
    assert factory.count == 1, "触发时又建了一次连接——TaskRunner 没在复用长连接"

    saved = store.get(task.id)
    assert saved is not None
    assert saved.state.value == "started"
    assert saved.uploaded_at is not None
    assert saved.sent_payload, "实际下发的 payload 应该被存下来供排障"
    store.close()


def test_打印机忙时到点放弃不重试(
    cfg: Config, link: PrinterLink, tmp_path: Path
) -> None:
    """硬约束：到点不空闲就放弃写日志，不排队不重试。"""
    store = TaskStore(cfg.daemon.db_path)
    journal = Journal(cfg.daemon.journal_path)
    runner = TaskRunner(cfg, store, journal, transport=link.session)

    first = make_task(tmp_path)
    runner.submit(first)
    runner.fire(first.id)            # 让打印机忙起来

    second = make_task(tmp_path)
    runner.submit(second)
    runner.fire(second.id)

    saved = store.get(second.id)
    assert saved is not None
    assert saved.state.value == "aborted"
    assert "RUNNING" in (saved.error or "")
    assert any(r["event"] == "aborted" for r in journal.read(limit=50))
    store.close()
