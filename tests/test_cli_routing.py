"""CLI 到底走 daemon 还是自己直连——这条分支决定了会不会和 daemon 抢连接。

打印机同一时刻只接受一个 MQTT 连接。v0.1 里 CLI 总是自己建连，daemon 常连之后
两边会互相踢线：CLI 一连把 daemon 踢下线，daemon 重连又把 CLI 踢掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bpq.cli import main
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

FAKE_SNAPSHOT = {
    "job": {"gcode_state": "IDLE", "subtask_name": "", "hms": []},
    "temps": {"nozzle": 24.5, "nozzle_target": 0, "bed": 23.0, "bed_target": 0},
    "ams": {
        "units": [{
            "unit_id": 0, "humidity": 4,
            "trays": [{
                "global_id": 0, "unit_id": 0, "slot": 0, "is_external": False,
                "label": "A1 PETG", "tray_type": "PETG", "color": "F98C36FF",
                "rgb": "F98C36", "info_idx": "GFG00", "remain": 100, "k": 0.04,
            }],
        }],
        "external": None, "tray_now": None, "tray_tar": None,
    },
    "link": {"connected": True, "yielded": False, "stale": False},
    "stale": False,
}


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        '[printer]\nip = "10.0.0.9"\nserial = "ABC"\naccess_code = "12345678"\n'
        f'[daemon]\ndb_path = "{tmp_path.as_posix()}/bpq.sqlite3"\n'
        f'journal_path = "{tmp_path.as_posix()}/bpq.jsonl"\n'
        '[web]\nenabled = true\nport = 8710\n',
        encoding="utf-8",
    )
    return path


def make_cfg(tmp_path: Path, **web_kw: object) -> Config:
    return Config(
        printer=PrinterConfig(ip="10.0.0.9", serial="ABC", access_code="123"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(
            db_path=str(tmp_path / "bpq.sqlite3"),
            journal_path=str(tmp_path / "bpq.jsonl"),
        ),
        link=LinkConfig(),
        web=WebConfig(**web_kw),  # type: ignore[arg-type]
        path=tmp_path / "config.toml",
    )


class FakeClient:
    """假的 daemon 客户端。alive=False 表示 daemon 没在跑。"""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.calls: list[str] = []

    def probe(self) -> dict | None:
        return {"ok": True, "fake_printer": False} if self.alive else None

    def get(self, path: str) -> dict:
        self.calls.append(f"GET {path}")
        return FAKE_SNAPSHOT

    def delete(self, path: str) -> dict:
        self.calls.append(f"DELETE {path}")
        return {"ok": True}


def test_daemon_在跑时_status_走_api_不建连接(
    monkeypatch: pytest.MonkeyPatch, cfg_file: Path
) -> None:
    """这是本模块最重要的一条：CLI 不该在 daemon 跑着的时候另开 MQTT 连接。"""
    client = FakeClient(alive=True)
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: client)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("CLI 又去自己建 MQTT 连接了——这正是要消灭的行为")

    monkeypatch.setattr("bpq.transport.build", boom)

    result = CliRunner().invoke(main, ["--config", str(cfg_file), "status"])
    assert result.exit_code == 0, result.output
    assert "GET /api/printer" in client.calls
    assert "空闲" in result.output
    assert "A1 PETG" in result.output       # AMS 也一并渲染出来了
    assert "湿度 4 档" in result.output


def test_no_daemon_强制直连(monkeypatch: pytest.MonkeyPatch, cfg_file: Path) -> None:
    """排障时要能绕开 daemon。"""
    client = FakeClient(alive=True)
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: client)

    tried: list[str] = []

    def fake_build(cfg: object):  # noqa: ANN202
        tried.append("direct")
        raise RuntimeError("停在这里就够了")

    monkeypatch.setattr("bpq.transport.build", fake_build)

    CliRunner().invoke(main, ["--config", str(cfg_file), "--no-daemon", "status"])
    assert tried == ["direct"], "--no-daemon 应该直接走直连路径"
    assert not client.calls, "--no-daemon 时不该去问 daemon"


def test_daemon_不在时降级直连(monkeypatch: pytest.MonkeyPatch, cfg_file: Path) -> None:
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: FakeClient(alive=False))

    tried: list[str] = []

    def fake_build(cfg: object):  # noqa: ANN202
        tried.append("direct")
        raise RuntimeError("停在这里就够了")

    monkeypatch.setattr("bpq.transport.build", fake_build)

    result = CliRunner().invoke(main, ["--config", str(cfg_file), "status"])
    assert tried == ["direct"]
    assert "daemon 没在跑" in result.output


def test_拿不到锁又连不上_web_时拒绝直连(
    monkeypatch: pytest.MonkeyPatch, cfg_file: Path, tmp_path: Path
) -> None:
    """要命的中间态：daemon 在跑，但它的 web 没起来（端口被占、崩了）。

    这时探活失败，若 CLI 就此认定「没有 daemon」而去直连，两边立刻开始抢连接——
    恰恰是这次改动要消灭的那件事。文件锁比 HTTP 探活可靠，它就是 daemon 用来
    保证单实例的那把锁。
    """
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: FakeClient(alive=False))
    monkeypatch.setattr("bpq.transport.build", lambda cfg: pytest.fail("不该走到直连"))

    from bpq.daemon import AlreadyRunning

    def locked(path: object):  # noqa: ANN202
        raise AlreadyRunning("已经有一个 bpq daemon 在运行了")

    monkeypatch.setattr("bpq.daemon.single_instance", locked)

    result = CliRunner().invoke(main, ["--config", str(cfg_file), "status"])
    assert result.exit_code != 0
    assert "互相抢线" in result.output


def test_cancel_走_api(monkeypatch: pytest.MonkeyPatch, cfg_file: Path) -> None:
    """走 daemon 的额外好处：它能立刻从内存里的 scheduler 摘掉 job，
    不必等下一次心跳。"""
    client = FakeClient(alive=True)
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: client)

    result = CliRunner().invoke(main, ["--config", str(cfg_file), "cancel", "abc123"])
    assert result.exit_code == 0
    assert "DELETE /api/tasks/abc123" in client.calls


def test_web_命令显示地址(monkeypatch: pytest.MonkeyPatch, cfg_file: Path) -> None:
    monkeypatch.setattr("bpq.client.DaemonClient", lambda cfg, **kw: FakeClient(alive=True))
    result = CliRunner().invoke(main, ["--config", str(cfg_file), "web"])
    assert result.exit_code == 0
    assert "http://127.0.0.1:8710" in result.output


def test_client_把_0_0_0_0_换成回环(tmp_path: Path) -> None:
    """host 是 0.0.0.0 时那是「监听哪些网卡」，连接不能真去连 0.0.0.0。"""
    from bpq.client import DaemonClient

    assert DaemonClient(make_cfg(tmp_path, host="0.0.0.0", port=9000)).base == \
        "http://127.0.0.1:9000"
    assert DaemonClient(make_cfg(tmp_path, host="192.168.1.5", port=9000)).base == \
        "http://192.168.1.5:9000"


def test_web_关闭时_probe_直接返回_none(tmp_path: Path) -> None:
    """[web] enabled = false 时不必去连一个根本不存在的端口。"""
    from bpq.client import DaemonClient

    assert DaemonClient(make_cfg(tmp_path, enabled=False)).probe() is None
