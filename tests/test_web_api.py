"""WebUI 后端的测试。全程假打印机 + 临时库，零网络零真机。"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

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
from bpq.journal import Journal
from bpq.link import PrinterLink
from bpq.scheduler import TaskRunner
from bpq.service import TaskService
from bpq.store import TaskStore
from bpq.web.app import create_app
from bpq.web.auth import AuthError
from tests.fakeprinter import FakePrinterTransport

PASSWORD = "hunter2"

ASSETS = Path(__file__).resolve().parents[1] / "assets"
REAL_3MF = ASSETS / "studio_reference.gcode.3mf"


def make_cfg(tmp_path: Path, **web_kw: object) -> Config:
    return Config(
        printer=PrinterConfig(ip="10.0.0.9", serial="ABC", access_code="123"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(
            db_path=str(tmp_path / "bpq.sqlite3"),
            journal_path=str(tmp_path / "bpq.jsonl"),
            spool_dir=str(tmp_path / "spool"),
        ),
        link=LinkConfig(stale_after=3600, pushall_interval=3600),
        web=WebConfig(**{"password": PASSWORD, "allow_local_no_auth": False, **web_kw}),  # type: ignore[arg-type]
        path=tmp_path / "config.toml",
    )


def build_client(cfg: Config, tmp_path: Path) -> TestClient:
    link = PrinterLink(cfg, factory=lambda c: FakePrinterTransport(
        c, upload_seconds=0, speed=3000))
    link.open()
    store = TaskStore(cfg.daemon.db_path)
    journal = Journal(cfg.daemon.journal_path)
    runner = TaskRunner(cfg, store, journal, transport=link.session)
    scheduled: list[str] = []
    service = TaskService(
        cfg, store, journal, runner,
        ams_source=link.snapshot,
        schedule=lambda t: scheduled.append(t.id),
        unschedule=lambda tid: None,
    )
    app = create_app(cfg, link=link, service=service, journal=journal,
                     var_dir=str(tmp_path))
    # TestClient 默认把来源 IP 报成 "testclient"，那样回环免鉴权那条路就测不到了。
    client = TestClient(app, client=("127.0.0.1", 51234))
    client.scheduled = scheduled  # type: ignore[attr-defined]
    client.link = link            # type: ignore[attr-defined]
    return client


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return build_client(make_cfg(tmp_path), tmp_path)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def fake_3mf(path: Path) -> Path:
    """现造一个最小可用的 3mf，免得测试依赖 .gitignore 掉的 assets/。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "G28\nG1 X1\n")
        z.writestr("Metadata/plate_1.gcode.md5", "ABC123")
        z.writestr("Metadata/plate_1.json",
                   json.dumps({"bed_type": "textured_plate", "nozzle_diameter": 0.4}))
        z.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config><plate>'
            '<metadata key="index" value="1"/>'
            '<metadata key="prediction" value="1551"/>'
            '<metadata key="weight" value="3.40"/>'
            '<filament id="1" type="PETG" color="#FF671F" tray_info_idx="GFG00" used_g="3.4"/>'
            "</plate></config>",
        )
    return path


# ---------------------------------------------------------------- 鉴权


def test_health_免鉴权(client: TestClient) -> None:
    """CLI 靠它探活，不能要求先登录。"""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["app"] == "bpq"
    # 故意不返回任何敏感信息
    assert "access_code" not in json.dumps(body)
    assert "password" not in json.dumps(body)


@pytest.mark.parametrize("path", ["/api/printer", "/api/tasks", "/api/journal",
                                  "/api/config", "/api/events"])
def test_未登录返回_401_而不是_422(client: TestClient, path: str) -> None:
    """必须是 401：前端靠这个状态码决定跳登录页。

    用「函数参数 + Depends」的写法时 FastAPI 会返回 422（参数校验失败），
    那样前端就没法区分「没登录」和「请求写错了」。
    """
    assert client.get(path).status_code == 401


def test_口令不对(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_登录后可访问(auth_client: TestClient) -> None:
    assert auth_client.get("/api/printer").status_code == 200


def test_登出后失效(auth_client: TestClient) -> None:
    auth_client.post("/api/auth/logout")
    assert auth_client.get("/api/printer").status_code == 401


def test_改口令让已发出的_token_失效(tmp_path: Path) -> None:
    """口令指纹进签名，于是改口令 = 所有设备立即登出。这个副作用是想要的。"""
    from bpq.web.auth import issue_token, verify_token

    secret = b"x" * 32
    token = issue_token(secret, "old-password", days=30)
    assert verify_token(secret, token, "old-password")
    assert not verify_token(secret, token, "new-password")


def test_登录限流(client: TestClient) -> None:
    """局域网里没有这层，一个八位口令是纸糊的。"""
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "wrong"})
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 429
    assert "秒后再试" in r.json()["detail"]


def test_本机免鉴权可开(tmp_path: Path) -> None:
    """CLI 零配置走 HTTP 靠的就是这个。"""
    cfg = make_cfg(tmp_path, allow_local_no_auth=True)
    c = build_client(cfg, tmp_path)
    assert c.get("/api/printer").status_code == 200


def test_暴露到局域网却没设口令则拒绝启动(tmp_path: Path) -> None:
    """防「随手把打印机控制权暴露到内网」的最后一道闸。"""
    cfg = make_cfg(tmp_path, host="0.0.0.0", password="")
    with pytest.raises(AuthError, match="password"):
        build_client(cfg, tmp_path)


def test_只绑回环时允许空口令(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, host="127.0.0.1", password="")
    assert build_client(cfg, tmp_path).get("/api/printer").status_code == 200


# ---------------------------------------------------------------- 打印机


def test_打印机快照(auth_client: TestClient) -> None:
    d = auth_client.get("/api/printer").json()
    assert d["job"]["gcode_state"] == "IDLE"
    assert d["temps"]["nozzle"] is not None
    assert len(d["ams"]["units"][0]["trays"]) == 4
    assert d["link"]["connected"] is True


def test_让出与抢回连接(auth_client: TestClient) -> None:
    d = auth_client.post("/api/printer/yield").json()
    assert d["link"]["yielded"] is True and d["link"]["connected"] is False

    d = auth_client.post("/api/printer/resume").json()
    assert d["link"]["yielded"] is False and d["link"]["connected"] is True


def test_yield_广播_link_状态变化(auth_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    """调用 /api/printer/yield 后，应通过 broker 广播 link 状态变化给 SSE 订阅者。

    - patch 事件中 printer 是空对象（表示快照没变）
    - link.yielded 为 true，link.connected 为 false
    """
    broker = auth_client.app.state.broker
    published: list[tuple[str, dict]] = []

    original_publish = broker.publish_threadsafe

    def track_publish(event: str, data: dict) -> None:
        published.append((event, data))
        return original_publish(event, data)

    monkeypatch.setattr(broker, "publish_threadsafe", track_publish)

    # 调用 yield
    response = auth_client.post("/api/printer/yield").json()
    assert response["link"]["yielded"] is True
    assert response["link"]["connected"] is False

    # 检查是否广播了 patch 事件
    assert len(published) >= 1
    event, data = published[-1]
    assert event == "patch"
    assert data["printer"] == {}, "patch 事件中 printer 应该是空对象（merge-patch）"
    assert data["link"]["yielded"] is True
    assert data["link"]["connected"] is False


def test_resume_广播_link_状态变化(auth_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    """调用 /api/printer/resume 后，应通过 broker 广播 link 状态变化给 SSE 订阅者。

    - patch 事件中 printer 是空对象（表示快照没变）
    - link.yielded 为 false（恢复为未让出）
    """
    broker = auth_client.app.state.broker
    published: list[tuple[str, dict]] = []

    original_publish = broker.publish_threadsafe

    def track_publish(event: str, data: dict) -> None:
        published.append((event, data))
        return original_publish(event, data)

    monkeypatch.setattr(broker, "publish_threadsafe", track_publish)

    # 先 yield 确保状态已让出，然后清空追踪列表
    auth_client.post("/api/printer/yield")
    published.clear()

    # 调用 resume
    response = auth_client.post("/api/printer/resume").json()
    assert response["link"]["yielded"] is False
    assert response["link"]["connected"] is True

    # 检查是否广播了 patch 事件
    assert len(published) >= 1
    event, data = published[-1]
    assert event == "patch"
    assert data["printer"] == {}, "patch 事件中 printer 应该是空对象（merge-patch）"
    assert data["link"]["yielded"] is False
    # resume 后应该会重新连接
    assert data["link"]["connected"] is True


def test_config_不泄露凭据(auth_client: TestClient) -> None:
    body = json.dumps(auth_client.get("/api/config").json())
    assert "123" not in body        # access_code
    assert PASSWORD not in body


# ------------------------------------------------------- 自动发现 SERIAL


def test_自动发现_serial_成功(auth_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    """成功路径：monkeypatch 掉 discover_serial，不真的去建 FTPS 连接。"""
    import bpq.transport.lan as lan

    seen: dict = {}

    def fake_discover(ip: str, access_code: str, **kwargs: object) -> str:
        seen["ip"] = ip
        seen["access_code"] = access_code
        return "AB12CD34EF5678G"

    monkeypatch.setattr(lan, "discover_serial", fake_discover)

    r = auth_client.post(
        "/api/config/printer/discover-serial",
        json={"ip": "10.0.0.9", "access_code": "999999"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "serial": "AB12CD34EF5678G"}
    assert seen == {"ip": "10.0.0.9", "access_code": "999999"}


def test_自动发现_serial_失败时返回_200_而不是_500(
    auth_client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """TransportError 要翻成 {ok: False, detail}，不能让异常直接冒成 500——
    前端得拿到写给人看的失败原因。"""
    import bpq.transport.lan as lan
    from bpq.transport.base import TransportError

    def fake_discover(ip: str, access_code: str, **kwargs: object) -> str:
        raise TransportError("连接成功，但 logger 目录里没有找到能识别出 SERIAL 的日志文件名")

    monkeypatch.setattr(lan, "discover_serial", fake_discover)

    r = auth_client.post(
        "/api/config/printer/discover-serial",
        json={"ip": "10.0.0.9", "access_code": "999999"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "SERIAL" in body["detail"]


def test_自动发现_serial_access_code_留空时退回已保存值(
    auth_client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """和 _probe_printer 一样的语义：已经保存过 access_code 就不用重填。"""
    import bpq.transport.lan as lan

    seen: dict = {}

    def fake_discover(ip: str, access_code: str, **kwargs: object) -> str:
        seen["access_code"] = access_code
        return "AB12CD34EF5678G"

    monkeypatch.setattr(lan, "discover_serial", fake_discover)

    r = auth_client.post("/api/config/printer/discover-serial", json={"ip": "10.0.0.9"})
    assert r.status_code == 200
    assert seen["access_code"] == "123"  # make_cfg() 里配的已保存值


def test_自动发现_serial_没给_ip_返回_400(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/config/printer/discover-serial", json={"access_code": "999999"}
    )
    assert r.status_code == 400


def test_自动发现_serial_没有_access_code_也没存过则返回_400(
    tmp_path: Path,
) -> None:
    """构造一台从没设过 access_code 的打印机：没传、也没得退，该拒绝。"""
    cfg = make_cfg(tmp_path)
    import dataclasses

    cfg = dataclasses.replace(cfg, printer=dataclasses.replace(cfg.printer, access_code=""))
    c = build_client(cfg, tmp_path)
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200

    r = c.post("/api/config/printer/discover-serial", json={"ip": "10.0.0.9"})
    assert r.status_code == 400


# ---------------------------------------------------------------- 文件


def test_上传并解析(auth_client: TestClient, tmp_path: Path) -> None:
    f = fake_3mf(tmp_path / "m.gcode.3mf")
    r = auth_client.post("/api/files", files={"file": ("m.gcode.3mf", f.read_bytes())})
    assert r.status_code == 200
    d = r.json()
    assert d["plates"][0]["bed_type"] == "textured_plate"
    assert d["plates"][0]["prediction_sec"] == pytest.approx(1551)
    assert d["plates"][0]["weight_g"] == pytest.approx(3.40)
    assert d["plates"][0]["filaments"][0]["rgb"] == "FF671F"
    # FTPS 只有 46KB/s，界面上要能提前告诉人这一趟要传多久
    assert d["upload_seconds"] >= 0


def test_上传垃圾文件给出人话(auth_client: TestClient) -> None:
    r = auth_client.post("/api/files", files={"file": ("x.3mf", b"not a zip")})
    assert r.status_code == 400
    assert "3mf" in r.json()["detail"]


def test_上传没切过片的文件(auth_client: TestClient, tmp_path: Path) -> None:
    p = tmp_path / "empty.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
    r = auth_client.post("/api/files", files={"file": ("empty.3mf", p.read_bytes())})
    assert r.status_code == 400
    assert "没切过片" in r.json()["detail"]


@pytest.mark.skipif(not REAL_3MF.exists(), reason="assets/ 里的真实 3mf 不在")
def test_上传真实_3mf_并取缩略图(auth_client: TestClient) -> None:
    d = auth_client.post(
        "/api/files", files={"file": (REAL_3MF.name, REAL_3MF.read_bytes())}
    ).json()
    idx = d["plates"][0]["index"]
    r = auth_client.get(f"/api/files/{d['file_id']}/thumbnail?plate={idx}")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_ams_匹配给出建议与提示(auth_client: TestClient, tmp_path: Path) -> None:
    f = fake_3mf(tmp_path / "m.gcode.3mf")
    fid = auth_client.post(
        "/api/files", files={"file": ("m.gcode.3mf", f.read_bytes())}
    ).json()["file_id"]

    d = auth_client.get(f"/api/files/{fid}/mapping?plate=1").json()
    # 假打印机的 AMS 里有三卷 GFG00 PETG，橙色那卷离 #FF671F 最近
    assert d["mapping"] == [0]
    assert any("同型号" in n for n in d["notes"])


# ---------------------------------------------------------------- 任务


def upload_and_submit(client: TestClient, tmp_path: Path, **body: object) -> dict:
    f = fake_3mf(tmp_path / "m.gcode.3mf")
    fid = client.post(
        "/api/files", files={"file": ("m.gcode.3mf", f.read_bytes())}
    ).json()["file_id"]
    payload = {
        "file_id": fid,
        "scheduled_at": (datetime.now() + timedelta(hours=3)).isoformat(),
        **body,
    }
    r = client.post("/api/tasks", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_建任务(auth_client: TestClient, tmp_path: Path) -> None:
    d = upload_and_submit(auth_client, tmp_path, title="睡前那个件")
    t = d["task"]
    assert t["title"] == "睡前那个件"
    assert t["origin"] == "web"
    assert t["plate"] == "Metadata/plate_1.gcode"
    assert t["bed_type"] == "textured_plate"
    assert t["filaments"][0]["info_idx"] == "GFG00"
    # 建的任务要真的排进调度
    assert t["id"] in auth_client.scheduled  # type: ignore[attr-defined]


def test_提交后立刻能看到已上传(auth_client: TestClient, tmp_path: Path) -> None:
    """upload_timing=early 下，提交那一刻文件就已经静默躺在打印机上了。

    「文件已经在打印机上，触发前它不会有任何动作」是这个项目存在的理由，
    接口不能因为内存对象没和数据库同步就把它显示成 pending。
    """
    t = upload_and_submit(auth_client, tmp_path)["task"]
    assert t["state"] == "uploaded"
    assert t["uploaded_at"] is not None


def test_options_保留三态(auth_client: TestClient, tmp_path: Path) -> None:
    """None（跟随全局）和 False（这单就是不要）是两回事，接口不能把它折叠掉——
    前端要照实显示成「跟随全局（当前：关）」。"""
    t = upload_and_submit(auth_client, tmp_path,
                          options={"timelapse": True, "flow_cali": False})["task"]
    assert t["options"]["timelapse"] is True
    assert t["options"]["flow_cali"] is False
    assert t["options"]["bed_leveling"] is None


def test_人工覆盖_ams_映射(auth_client: TestClient, tmp_path: Path) -> None:
    t = upload_and_submit(auth_client, tmp_path, ams_mapping=[3], use_ams=True)["task"]
    assert t["ams_mapping"] == [3]
    assert t["mapping_source"] == "manual"


def test_改时间与参数(auth_client: TestClient, tmp_path: Path) -> None:
    t = upload_and_submit(auth_client, tmp_path)["task"]
    when = (datetime.now() + timedelta(hours=9)).isoformat()
    r = auth_client.patch(f"/api/tasks/{t['id']}",
                          json={"scheduled_at": when, "options": {"timelapse": True}})
    assert r.status_code == 200
    assert r.json()["scheduled_at"] == when
    assert r.json()["options"]["timelapse"] is True


def test_取消任务(auth_client: TestClient, tmp_path: Path) -> None:
    t = upload_and_submit(auth_client, tmp_path)["task"]
    assert auth_client.delete(f"/api/tasks/{t['id']}").status_code == 200
    assert auth_client.get(f"/api/tasks/{t['id']}").json()["state"] == "cancelled"
    # 已经取消过的再取消要给明确回应，不能装作成功
    assert auth_client.delete(f"/api/tasks/{t['id']}").status_code == 409


def test_不存在的任务(auth_client: TestClient) -> None:
    assert auth_client.get("/api/tasks/nope").status_code == 404


def test_purge_已结束的任务可以硬删(auth_client: TestClient, tmp_path: Path) -> None:
    """软取消先把任务变成终态，purge=true 再真删——两步都做到才算硬删链路完整。"""
    t = upload_and_submit(auth_client, tmp_path)["task"]
    assert auth_client.delete(f"/api/tasks/{t['id']}").status_code == 200  # 软取消

    r = auth_client.delete(f"/api/tasks/{t['id']}?purge=true")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert auth_client.get(f"/api/tasks/{t['id']}").status_code == 404


def test_purge_还没结束的任务返回_409(auth_client: TestClient, tmp_path: Path) -> None:
    """不允许绕过取消直接硬删——否则 jobstore 里那个 job 会变成孤儿。"""
    t = upload_and_submit(auth_client, tmp_path)["task"]
    r = auth_client.delete(f"/api/tasks/{t['id']}?purge=true")
    assert r.status_code == 409
    assert "先取消" in r.json()["detail"]
    # 409 之后任务应该还在，没有被真的删掉
    assert auth_client.get(f"/api/tasks/{t['id']}").status_code == 200


def test_purge_不存在的任务返回_404(auth_client: TestClient) -> None:
    assert auth_client.delete("/api/tasks/nope?purge=true").status_code == 404


def test_不带_purge_行为不变仍是软取消(auth_client: TestClient, tmp_path: Path) -> None:
    t = upload_and_submit(auth_client, tmp_path)["task"]
    assert auth_client.delete(f"/api/tasks/{t['id']}").status_code == 200
    assert auth_client.get(f"/api/tasks/{t['id']}").json()["state"] == "cancelled"


def test_相对时刻写法也能用(auth_client: TestClient, tmp_path: Path) -> None:
    """和 CLI 共用 parse_when：两个入口对「今晚 23:30」的理解必须一致。"""
    d = upload_and_submit(auth_client, tmp_path, scheduled_at="+2h")
    assert d["task"]["scheduled_at"]


def test_看不懂的时刻(auth_client: TestClient, tmp_path: Path) -> None:
    f = fake_3mf(tmp_path / "m.gcode.3mf")
    fid = auth_client.post(
        "/api/files", files={"file": ("m.gcode.3mf", f.read_bytes())}
    ).json()["file_id"]
    r = auth_client.post("/api/tasks", json={"file_id": fid, "scheduled_at": "明天早上"})
    assert r.status_code == 400


# ---------------------------------------------------------------- 日志


def test_journal接口返回带分页信息的对象(auth_client: TestClient, tmp_path: Path) -> None:
    """v0.3 前是裸数组，现在改成对象——这是预期内的破坏性变更，前端另有任务卡跟上。"""
    upload_and_submit(auth_client, tmp_path)  # 至少产生 submitted（+ uploaded）两条

    r = auth_client.get("/api/journal")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"items", "total", "offset", "limit", "events"}
    assert body["total"] >= 2
    assert len(body["items"]) == body["total"]  # 默认 limit=50 够装下这几条
    assert "submitted" in body["events"]


def test_journal接口按事件筛选(auth_client: TestClient, tmp_path: Path) -> None:
    upload_and_submit(auth_client, tmp_path)

    r = auth_client.get("/api/journal", params={"event": "submitted"})
    body = r.json()
    assert body["total"] == 1
    assert all(item["event"] == "submitted" for item in body["items"])


def test_journal接口_limit_封顶(auth_client: TestClient) -> None:
    r = auth_client.get("/api/journal", params={"limit": 10_000})
    assert r.status_code == 200
    assert r.json()["limit"] == 500


def test_删除日志_不给参数清空全部(auth_client: TestClient, tmp_path: Path) -> None:
    upload_and_submit(auth_client, tmp_path)
    before_total = auth_client.get("/api/journal").json()["total"]
    assert before_total > 0

    r = auth_client.delete("/api/journal")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": before_total}
    assert auth_client.get("/api/journal").json()["total"] == 0


# ---------------------------------------------------------------- SSE


def test_sse_首帧是完整快照() -> None:
    """直接测 broker，不走 TestClient。

    TestClient 退出 with 块时会把响应体读完，而 SSE 是一条**永不结束**的流——
    用它测这个端点会直接把测试挂死。端点本身的鉴权与响应头在别的用例里覆盖。
    """
    import asyncio

    from bpq.web.events import EventBroker

    async def first_frame() -> str:
        broker = EventBroker()
        agen = broker.stream({"printer": {"job": {"gcode_state": "IDLE"}},
                              "link": {}, "tasks": []})
        frame = await agen.__anext__()
        await agen.aclose()
        return frame.decode()

    out = asyncio.run(first_frame())
    assert out.startswith("event: snapshot")
    payload = json.loads(out.split("data: ", 1)[1])
    assert "printer" in payload and "link" in payload and "tasks" in payload
    assert payload["printer"]["job"]["gcode_state"] == "IDLE"


def test_sse_端点要鉴权(client: TestClient) -> None:
    """能验的就验响应码——不去读那条永不结束的流。"""
    assert client.get("/api/events").status_code == 401


def test_sse_编码格式() -> None:
    from bpq.web.events import encode

    frame = encode("patch", {"a": 1}, event_id=7).decode()
    # SSE 的帧格式是逐行的字段 + 一个空行收尾，格式错了浏览器会静默不触发事件
    assert frame.splitlines()[:3] == ["id: 7", "event: patch", 'data: {"a": 1}']
    assert frame.endswith("\n\n"), "缺了收尾空行，浏览器不会认为这一帧结束了"


def test_sse_推送要能被订阅者收到() -> None:
    import asyncio

    from bpq.web.events import EventBroker

    async def scenario() -> str:
        broker = EventBroker()
        agen = broker.stream({"first": True})
        await agen.__anext__()
        broker._publish("patch", {"temps": {"nozzle": 215.3}})
        frame = await agen.__anext__()
        await agen.aclose()
        return frame.decode()

    out = asyncio.run(scenario())
    assert "event: patch" in out
    assert "215.3" in out


def test_慢消费者不会阻塞生产端() -> None:
    """一个卡住的浏览器绝不能把打印机的状态流一起拖停。"""
    import asyncio

    from bpq.web.events import EventBroker

    async def scenario() -> str:
        broker = EventBroker(queue_size=4)
        broker.bind()
        agen = broker.stream({"first": True})
        await agen.__anext__()                      # 消费首帧，注册订阅
        for i in range(50):                         # 远超队列容量，且没人来取
            broker._publish("patch", {"i": i})
        # 生产端没有卡住（走到这里就说明了），下一帧应该是 resync
        frames = []
        for _ in range(2):
            frames.append(await agen.__anext__())
        await agen.aclose()
        return b"".join(frames).decode()

    out = asyncio.run(scenario())
    assert "resync" in out
    assert "queue_overflow" in out


# ---------------------------------------------------------------- 静态页


def test_前端没构建时给构建说明而不是_404(auth_client: TestClient) -> None:
    """web/dist 不入库，所以 clone 下来直接跑是没有页面的。
    那时必须给一条能照做的提示。"""
    from bpq.web.static import frontend_dir

    r = auth_client.get("/")
    assert r.status_code == 200
    if not (frontend_dir() / "index.html").exists():
        assert "npm run build" in r.text
