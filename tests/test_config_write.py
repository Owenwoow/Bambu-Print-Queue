"""配置写回 + 配置 API。

守的是两件事：
1. `config.toml` 里的注释几乎全是实测结论（「明文数据通道直接 EOF，必须 PROT P」
   之类），写回时丢了就再也拿不回来了。
2. 打印机连接参数**必须先试连再落盘**——填错 IP 却存进去、半夜任务到点才发现
   连不上，是这条链路上最难查的一类问题。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bpq.configwrite import ConfigWriteError, update
from bpq.models import PrinterState
from tests.test_web_api import build_client, make_cfg

SAMPLE = '''# bpq 配置示例

[printer]
ip          = "192.168.1.100"
serial      = "AC12309BH109"   # 从 FTPS 日志文件名读到
access_code = "12345678"
model       = "A1"

[transport]
# 实测：本机 A1 明文数据通道直接 EOF，必须 PROT P
ftps_encrypt_data = true
mqtt_port         = 8883

[print]
bed_leveling   = false
timelapse      = false
# 尚未实测：社区里 -1 和 255 都见过
external_spool_id = -1
'''


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


# ------------------------------------------------------------ 写回


def test_改值不丢注释(toml_file: Path) -> None:
    """这些注释是踩坑换来的，比配置值本身更难重新获得。"""
    update(toml_file, "print", {"timelapse": True})
    text = toml_file.read_text(encoding="utf-8")

    assert "必须 PROT P" in text
    assert "尚未实测：社区里 -1 和 255 都见过" in text
    assert "从 FTPS 日志文件名读到" in text
    assert tomllib.loads(text)["print"]["timelapse"] is True


def test_只动指定的键(toml_file: Path) -> None:
    update(toml_file, "printer", {"ip": "10.0.0.5"})
    d = tomllib.loads(toml_file.read_text(encoding="utf-8"))
    assert d["printer"]["ip"] == "10.0.0.5"
    assert d["printer"]["serial"] == "AC12309BH109"      # 没动
    assert d["printer"]["access_code"] == "12345678"     # 没动


def test_none_表示不改(toml_file: Path) -> None:
    """TOML 没有 null，写 None 进去只会得到语法错误。"""
    update(toml_file, "printer", {"ip": None, "model": "A1 mini"})
    d = tomllib.loads(toml_file.read_text(encoding="utf-8"))
    assert d["printer"]["ip"] == "192.168.1.100"
    assert d["printer"]["model"] == "A1 mini"


def test_段不存在就新建(toml_file: Path) -> None:
    update(toml_file, "web", {"port": 9999})
    d = tomllib.loads(toml_file.read_text(encoding="utf-8"))
    assert d["web"]["port"] == 9999


def test_值没变时不重写(toml_file: Path) -> None:
    before = toml_file.stat().st_mtime_ns
    update(toml_file, "print", {"timelapse": False})     # 本来就是 false
    assert toml_file.stat().st_mtime_ns == before


def test_文件不存在时报错而不是新建(tmp_path: Path) -> None:
    with pytest.raises(ConfigWriteError, match="不在"):
        update(tmp_path / "nope.toml", "print", {"timelapse": True})


def test_语法坏掉时不动原文件(tmp_path: Path) -> None:
    """解析失败要保持原样——半个配置文件比一个旧配置文件糟糕得多。"""
    p = tmp_path / "bad.toml"
    p.write_text("[printer\nip = ", encoding="utf-8")
    with pytest.raises(ConfigWriteError):
        update(p, "printer", {"ip": "1.2.3.4"})
    assert p.read_text(encoding="utf-8") == "[printer\nip = "


def test_写入不留临时文件(toml_file: Path) -> None:
    update(toml_file, "print", {"timelapse": True})
    assert list(toml_file.parent.glob("*.tmp")) == []


# ------------------------------------------------------------ API


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = make_cfg(tmp_path, allow_local_no_auth=True)
    # API 会写这个文件，得让它真的存在
    cfg.path.write_text(SAMPLE, encoding="utf-8")
    return build_client(cfg, tmp_path)


def test_读打印机配置时_access_code_打码(client: TestClient) -> None:
    d = client.get("/api/config/printer").json()
    assert d["access_code_set"] is True
    assert "123" not in d["access_code_masked"] or "*" in d["access_code_masked"]
    assert "access_code" not in d, "完整的 access_code 不该出现在响应里"


def test_试连不保存(client: TestClient, tmp_path: Path) -> None:
    """不管连得上连不上，这个端点都不该写文件——它的全部意义就是「先看看」。

    这里故意用一个不可达的地址，走的是真实的失败路径。
    """
    before = cfg_text(tmp_path)
    r = client.post("/api/config/printer/test", json={"ip": "10.255.255.1"})
    assert r.status_code == 200          # 试连失败本身不是 HTTP 错误
    assert r.json()["ok"] is False
    assert "连接失败" in r.json()["detail"] or "未收到状态上报" in r.json()["detail"]
    assert cfg_text(tmp_path) == before, "test 端点不该写文件"


def test_保存打印机配置后热生效(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    # 试连走的是真的 LanTransport（生产上必须如此），测试里换成一个成功的桩
    from bpq.web import app as app_mod

    monkeypatch.setattr(
        app_mod, "_probe_printer",
        lambda cfg, body, *, link=None: {"ok": True, "detail": "连上了", "state": "IDLE"},
    )
    r = client.patch("/api/config/printer", json={"ip": "10.9.9.9", "model": "A1 mini"})
    assert r.status_code == 200, r.text

    d = tomllib.loads(cfg_text(tmp_path))
    assert d["printer"]["ip"] == "10.9.9.9"
    assert d["printer"]["model"] == "A1 mini"
    # 不用重启就能看到新值
    assert client.get("/api/config").json()["printer"]["ip"] == "10.9.9.9"


def test_连不上时拒绝保存(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """填错 IP 却存进去、半夜到点才发现连不上，是最难查的一类问题。"""
    from bpq.web import app as app_mod

    monkeypatch.setattr(
        app_mod, "_probe_printer",
        lambda cfg, body, *, link=None: {"ok": False, "detail": "连不上：超时"},
    )
    before = cfg_text(tmp_path)
    r = client.patch("/api/config/printer", json={"ip": "10.9.9.9"})
    assert r.status_code == 400
    assert "连不上" in r.json()["detail"]
    assert cfg_text(tmp_path) == before, "试连失败还写了文件"


def test_force_可以强行保存(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """打印机关着机也得能先把参数填好。"""
    from bpq.web import app as app_mod

    monkeypatch.setattr(
        app_mod, "_probe_printer",
        lambda cfg, body, *, link=None: {"ok": False, "detail": "连不上：超时"},
    )
    r = client.patch("/api/config/printer", json={"ip": "10.9.9.9", "force": True})
    assert r.status_code == 200
    assert tomllib.loads(cfg_text(tmp_path))["printer"]["ip"] == "10.9.9.9"


def test_改全局打印默认值(client: TestClient, tmp_path: Path) -> None:
    r = client.patch("/api/config", json={"print_defaults": {"timelapse": True}})
    assert r.status_code == 200
    assert r.json()["print_defaults"]["timelapse"] is True
    assert tomllib.loads(cfg_text(tmp_path))["print"]["timelapse"] is True


def test_改调度设置(client: TestClient, tmp_path: Path) -> None:
    r = client.patch("/api/config", json={
        "scheduler": {"start_after_failure": True, "upload_timing": "late"}
    })
    assert r.status_code == 200
    d = tomllib.loads(cfg_text(tmp_path))["scheduler"]
    assert d["start_after_failure"] is True
    assert d["upload_timing"] == "late"


def test_upload_timing_只认两个值(client: TestClient) -> None:
    r = client.patch("/api/config", json={"scheduler": {"upload_timing": "whenever"}})
    assert r.status_code == 400


def test_空请求体被拒(client: TestClient) -> None:
    assert client.patch("/api/config", json={}).status_code == 400


def test_配置接口要鉴权(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, allow_local_no_auth=False)
    cfg.path.write_text(SAMPLE, encoding="utf-8")
    c = build_client(cfg, tmp_path)
    assert c.get("/api/config/printer").status_code == 401
    assert c.patch("/api/config", json={"print_defaults": {}}).status_code == 401


def test_健康检查不再有_fake_printer_字段(client: TestClient) -> None:
    """v0.3 删掉了假打印机模式。"""
    assert "fake_printer" not in client.get("/api/health").json()


class DummyLink:
    """`_probe_printer` 慢路径需要的最小 link 接口：yielded / (yield|resume)_connection。

    刻意不用真的 PrinterLink——这里只关心「探测流程有没有正确地让出/恢复连接」，
    不需要一整套假打印机。
    """

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected
        self.yielded = False
        self.calls: list[str] = []

    def yield_connection(self) -> None:
        self.calls.append("yield")
        self.yielded = True

    def resume_connection(self, *, reason: str = "") -> bool:
        self.calls.append("resume")
        self.yielded = False
        return True

    def state(self, timeout: float = 10.0) -> PrinterState:
        self.calls.append("state")
        return PrinterState.IDLE


def test_探测函数只读不下发指令(tmp_path: Path) -> None:
    """_probe_printer 只调 get_state，绝不能碰 start()。"""
    from bpq.web.app import _probe_printer

    cfg = make_cfg(tmp_path)
    calls: list[str] = []

    class Spy:
        def __init__(self, c: object) -> None: ...
        def get_state(self, timeout: float = 10.0) -> PrinterState:
            calls.append("get_state")
            return PrinterState.IDLE
        def start(self, task: object) -> str:
            calls.append("start")
            raise AssertionError("试连绝不能下发启动指令")
        def close(self) -> None:
            calls.append("close")

    import bpq.transport.lan as lan_mod
    original = lan_mod.LanTransport
    lan_mod.LanTransport = Spy  # type: ignore[misc]
    try:
        # ip 和 cfg 里的不一样 → 走慢路径，真的会调 Spy
        result = _probe_printer(cfg, {"ip": "10.0.0.1"}, link=DummyLink())
    finally:
        lan_mod.LanTransport = original  # type: ignore[misc]

    assert result["ok"] is True
    assert calls == ["get_state", "close"]


def test_探测快路径参数没变且已连上时不建连接(tmp_path: Path) -> None:
    """参数和当前配置完全一致、link 已经连着——必须走缓存，一次连接都不建。

    用一个只要被调用就抛异常的假 LanTransport 来断言：慢路径的代码根本没跑到。
    """
    from bpq.web.app import _probe_printer

    cfg = make_cfg(tmp_path)

    class ExplodingTransport:
        def __init__(self, c: object) -> None:
            raise AssertionError("快路径不该建任何新连接")

    import bpq.transport.lan as lan_mod
    original = lan_mod.LanTransport
    lan_mod.LanTransport = ExplodingTransport  # type: ignore[misc]
    link = DummyLink(connected=True)
    try:
        # 不传任何字段（或传的和现有配置一样）→ 参数视为「没变」
        result = _probe_printer(cfg, {}, link=link)
    finally:
        lan_mod.LanTransport = original  # type: ignore[misc]

    assert result["ok"] is True
    assert link.calls == ["state"], "快路径不该调 yield/resume，也不该建连接"


def test_探测慢路径会先让出连接再恢复(tmp_path: Path) -> None:
    """参数变了（或没连上）时必须先 yield_connection，探测完再 resume_connection。"""
    from bpq.web.app import _probe_printer

    cfg = make_cfg(tmp_path)

    class Spy:
        def __init__(self, c: object) -> None: ...
        def get_state(self, timeout: float = 10.0) -> PrinterState:
            return PrinterState.IDLE
        def close(self) -> None: ...

    import bpq.transport.lan as lan_mod
    original = lan_mod.LanTransport
    lan_mod.LanTransport = Spy  # type: ignore[misc]
    link = DummyLink(connected=False)
    try:
        result = _probe_printer(cfg, {"ip": "10.0.0.1"}, link=link)
    finally:
        lan_mod.LanTransport = original  # type: ignore[misc]

    assert result["ok"] is True
    assert link.calls == ["yield", "resume"], "必须先让出、探测完再恢复，且顺序不能错"
    assert link.yielded is False, "原先没让出过，探测完必须恢复成没让出的状态"


def test_探测慢路径原先就让出时探测完仍保持让出(tmp_path: Path) -> None:
    """原先就是让出状态（人手动让给了 Studio），试连一次不该把它悄悄抢回来。"""
    from bpq.web.app import _probe_printer

    cfg = make_cfg(tmp_path)

    class Spy:
        def __init__(self, c: object) -> None: ...
        def get_state(self, timeout: float = 10.0) -> PrinterState:
            return PrinterState.IDLE
        def close(self) -> None: ...

    import bpq.transport.lan as lan_mod
    original = lan_mod.LanTransport
    lan_mod.LanTransport = Spy  # type: ignore[misc]
    link = DummyLink(connected=False)
    link.yielded = True  # 模拟「原先就已经让出去了」
    try:
        result = _probe_printer(cfg, {"ip": "10.0.0.1"}, link=link)
    finally:
        lan_mod.LanTransport = original  # type: ignore[misc]

    assert result["ok"] is True
    # yield_connection 内部一般对「已经让出」是幂等的，这里只关心不会调 resume
    assert "resume" not in link.calls, "原先就是让出状态，探测完不该把它抢回来"


def cfg_text(tmp_path: Path) -> str:
    return (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_保留_crlf_行尾(tmp_path: Path) -> None:
    """Windows 上手工编辑过的配置多半是 CRLF。

    不保留的话，在网页上点一下开关整个文件的行尾就变了，
    拿 diff 工具一看会以为全文都改了。
    """
    p = tmp_path / "crlf.toml"
    p.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))

    update(p, "print", {"timelapse": True})

    raw = p.read_bytes()
    assert raw.count(b"\r\n") > 0
    assert raw.count(b"\n") == raw.count(b"\r\n"), "混进了裸 LF"


def test_反复写入不产生_crcrlf(tmp_path: Path) -> None:
    """tomlkit **保留**原文件的行尾，所以它的输出里可能已经是 CRLF。

    对它的输出再做一次 LF→CRLF 替换会得到 CRCRLF，而 tomlkit 下次读这个文件时
    会把多出来的 CR 当成注释里的非法控制字符直接拒绝解析——
    症状是「在网页上改了两次配置，第二次开始报解析失败」。
    """
    p = tmp_path / "crlf.toml"
    p.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))

    for i in range(4):
        update(p, "print", {"timelapse": i % 2 == 0})
        raw = p.read_bytes()
        assert b"\r\r\n" not in raw, f"第 {i + 1} 次写入产生了 CRCRLF"
        # 还得能被自己读回来
        assert tomllib.loads(raw.decode("utf-8"))["print"]["timelapse"] == (i % 2 == 0)
        assert "必须 PROT P" in raw.decode("utf-8"), "注释被写丢了"


def test_lf_文件保持_lf(tmp_path: Path) -> None:
    p = tmp_path / "lf.toml"
    p.write_bytes(SAMPLE.encode("utf-8"))
    update(p, "print", {"timelapse": True})
    assert b"\r\n" not in p.read_bytes()
