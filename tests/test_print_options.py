"""每单打印参数（PrintOptions）与 project_file payload 的测试。

payload 里任何一个字段填错，症状都是「打印机接受了指令但行为不对」，
而不是一句清楚的参数校验失败——v0.1 就在 param 上栽过一次。
所以这里逐字段钉死，全程不碰网络。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from bpq.config import (
    Config,
    DaemonConfig,
    PrintConfig,
    PrinterConfig,
    SchedulerConfig,
    TransportConfig,
)
from bpq.models import PrintOptions, Task
from bpq.snapshot import EXTERNAL_TRAY_ID
from bpq.transport.lan import LanTransport


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """全局默认里把 timelapse 和 flow_cali 打开，好分辨任务值有没有真的盖住全局。"""
    return Config(
        printer=PrinterConfig(ip="10.0.0.9", serial="ABC123", access_code="12345678"),
        transport=TransportConfig(),
        print=PrintConfig(timelapse=True, flow_cali=True),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(),
        path=tmp_path / "config.toml",
    )


def make_task(**kw: object) -> Task:
    base = {
        "source_path": "/tmp/model.gcode.3mf",
        "scheduled_at": datetime(2026, 8, 25, 23, 30),
        "remote_name": "model.gcode.3mf",
        "plate": "Metadata/plate_3.gcode",
        "md5": "DEADBEEF",
        "bed_type": "textured_plate",
    }
    base.update(kw)
    return Task(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------- resolve


def test_未指定的开关跟随全局默认(cfg: Config) -> None:
    resolved = PrintOptions().resolve(cfg.print)
    assert resolved.timelapse is True       # 全局开着
    assert resolved.flow_cali is True
    assert resolved.bed_leveling is False   # 全局关着


def test_任务值盖住全局默认(cfg: Config) -> None:
    resolved = PrintOptions(timelapse=False, bed_leveling=True).resolve(cfg.print)
    assert resolved.timelapse is False      # 全局开着，任务显式关
    assert resolved.bed_leveling is True    # 全局关着，任务显式开
    assert resolved.flow_cali is True       # 没指定，仍跟随全局


def test_显式_false_不等于未指定(cfg: Config) -> None:
    """这是 None 语义的要害：False 是「这单就是不要」，None 是「听全局的」。

    如果哪天有人图省事把 None 折叠成 False，这个测试会红。
    """
    assert PrintOptions(timelapse=False).resolve(cfg.print).timelapse is False
    assert PrintOptions(timelapse=None).resolve(cfg.print).timelapse is True


# --------------------------------------------------- _build_project_file


def test_payload_的开关取自任务而非全局(cfg: Config) -> None:
    tp = LanTransport(cfg)
    task = make_task(options=PrintOptions(timelapse=False, bed_leveling=True))
    p = tp._build_project_file(task)["print"]

    assert p["timelapse"] is False     # 全局 True，任务 False → 必须听任务的
    assert p["bed_leveling"] is True
    assert p["flow_cali"] is True      # 任务没指定 → 跟随全局 True


def test_payload_两种_bed_leveling_拼写都发(cfg: Config) -> None:
    """OpenBambuAPI 文档写 bed_levelling，实抓包是 bed_leveling。

    两个都发是 v0.1 的实测折中，别有人「顺手清理重复字段」把它删了。
    """
    tp = LanTransport(cfg)
    p = tp._build_project_file(make_task(options=PrintOptions(bed_leveling=True)))["print"]
    assert p["bed_leveling"] is True
    assert p["bed_levelling"] is True


def test_payload_关键字段来自任务(cfg: Config) -> None:
    tp = LanTransport(cfg)
    task = make_task()
    p = tp._build_project_file(task)["print"]

    assert p["command"] == "project_file"
    assert p["sequence_id"] == task.id
    # param 必须是 3mf 里真实存在的那个盘——写死 plate_1 是 v0.1 的一次真实故障
    assert p["param"] == "Metadata/plate_3.gcode"
    assert p["url"] == "file:///sdcard/model.gcode.3mf"
    assert p["subtask_name"] == "model.gcode.3mf"
    assert p["md5"] == "DEADBEEF"
    assert p["bed_type"] == "textured_plate"
    # 本地打印这四个 id 一律填 "0"
    assert p["project_id"] == p["profile_id"] == p["task_id"] == p["subtask_id"] == "0"


def test_payload_可被序列化(cfg: Config) -> None:
    """start() 会把它 json.dumps 后存进 task.sent_payload，不能有不可序列化的东西。"""
    tp = LanTransport(cfg)
    text = json.dumps(tp._build_project_file(make_task()), ensure_ascii=False)
    assert json.loads(text)["print"]["command"] == "project_file"


def test_remote_name_为空时回退到源文件名(cfg: Config) -> None:
    tp = LanTransport(cfg)
    p = tp._build_project_file(make_task(remote_name=""))["print"]
    assert p["url"].endswith("/model.gcode.3mf")


# ------------------------------------------------------------ AMS 合并

# 下面只验证 LanTransport 这一层的转换（快照 → AmsTray）。
# 增量合并本身的正确性在 tests/test_report_merge.py 里，不在这里重复。


def feed(tp: LanTransport, report: dict) -> None:
    """走 _on_message 这个真实入口喂一条报文，不去戳内部状态。"""
    class _Msg:
        payload = json.dumps({"print": report}).encode()

    tp._on_message(None, None, _Msg())


def test_get_ams_trays_返回全局编号(cfg: Config) -> None:
    """tray["id"] 是单元内 0-3，多单元时会互相覆盖，必须换算成 unit*4+slot。"""
    tp = LanTransport(cfg)
    feed(tp, {"ams": {"ams": [
        {"id": "0", "tray": [
            {"id": "0", "tray_type": "PETG", "tray_color": "F98C36FF",
             "tray_info_idx": "GFG00", "remain": 100, "k": 0.04},
            {"id": "3", "tray_type": "PETG", "tray_color": "000000FF", "remain": 80},
        ]},
        {"id": "1", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FFFFFFFF"}]},
    ]}})
    trays = tp.get_ams_trays()

    assert set(trays) == {0, 3, 4}          # unit1 的 tray0 是全局 4，不是 0
    assert trays[0].type == "PETG" and trays[0].info_idx == "GFG00"
    assert trays[4].type == "PLA"           # 没有被 unit0 的 tray0 覆盖掉
    assert trays[4].unit_id == 1 and trays[4].slot == 0
    assert trays[3].remain == 80


def test_get_ams_trays_含外置料(cfg: Config) -> None:
    """vt_tray 不在 ams.ams[] 里，v0.1 完全没解析它。"""
    tp = LanTransport(cfg)
    feed(tp, {"vt_tray": {"id": "254", "tray_type": "PLA", "tray_color": "00AE42FF"}})
    trays = tp.get_ams_trays()

    assert EXTERNAL_TRAY_ID in trays
    assert trays[EXTERNAL_TRAY_ID].is_external is True
    assert trays[EXTERNAL_TRAY_ID].rgb == "00AE42"


def test_读_ams_不建连接(cfg: Config) -> None:
    """cfg 里是个不可达的 IP。WebUI 每刷新一次就读一遍 AMS，
    而打印机只接受一个 MQTT 连接——读缓存绝不能有建连的副作用。
    这个测试若开始超时，就说明有人把 _ensure_mqtt() 加回去了。"""
    tp = LanTransport(cfg)
    assert tp.get_ams_trays() == {}
    assert tp.get_snapshot().ams.units == ()


def test_不含_gcode_state_的增量不解除等待(cfg: Config) -> None:
    """v0.1 最严重那个 bug 的回归测试。

    A1 的大量增量报文不含 gcode_state。若这些报文也置位 _state_event，
    get_state() 的等待会被第一条无关的增量包提前结束，定时任务到点读到 UNKNOWN
    而放弃——打印机明明空闲着。
    """
    tp = LanTransport(cfg)
    feed(tp, {"nozzle_temper": 215.3})
    assert not tp._state_event.is_set()

    feed(tp, {"gcode_state": "IDLE"})
    assert tp._state_event.is_set()


def test_回执字典不会无限增长(cfg: Config) -> None:
    """长连接下它只增不减会缓慢泄漏。"""
    from bpq.transport.lan import MAX_REPLIES

    tp = LanTransport(cfg)
    for i in range(MAX_REPLIES + 20):
        feed(tp, {"sequence_id": str(i), "result": "success"})
    assert len(tp._replies) <= MAX_REPLIES
