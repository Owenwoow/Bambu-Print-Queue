"""增量报文合并的测试。

A1 只推变化的字段。这里的每个用例都对应一种「合并写错了就会丢数据」的具体情形，
全程不碰网络、不需要真机。
"""

from __future__ import annotations

import json

import pytest

from bpq.models import PrinterState
from bpq.report import ReportAccumulator
from bpq.snapshot import EXTERNAL_TRAY_ID


def tray(slot: int, ttype: str = "PETG", **kw: object) -> dict:
    base = {
        "id": str(slot), "tray_type": ttype, "tray_color": "F98C36FF",
        "tray_info_idx": "GFG00", "remain": 100, "k": 0.04,
    }
    base.update(kw)
    return base


def pushall(**over: object) -> dict:
    """一条形状贴近真实 A1 全量报文的样本。"""
    body = {
        "command": "push_status",
        "gcode_state": "IDLE",
        "print_type": "idle",
        "subtask_name": "",
        "mc_percent": 0,
        "mc_remaining_time": 0,
        "layer_num": 0,
        "total_layer_num": 0,
        "stg_cur": -1,
        "print_error": 0,
        "nozzle_temper": 24.5,
        "nozzle_target_temper": 0,
        "bed_temper": 23.0,
        "bed_target_temper": 0,
        "nozzle_diameter": "0.4",
        "nozzle_type": "hardened_steel",
        "cooling_fan_speed": "0",
        "big_fan1_speed": "0",
        "spd_lvl": 2,
        "spd_mag": 100,
        "wifi_signal": "-52dBm",
        "lights_report": [{"node": "chamber_light", "mode": "on"}],
        "hms": [],
        "ipcam": {"ipcam_record": "enable", "timelapse": "disable", "resolution": "1080p"},
        "xcam": {"first_layer_inspector": False},
        "ams": {
            "ams_exist_bits": "1",
            "tray_now": "0",
            "tray_tar": "255",
            "ams": [{"id": "0", "humidity": "4", "temp": "28.5", "tray": [
                tray(0), tray(1, tray_color="FFFFFFFF"),
                tray(2, "PLA", tray_info_idx="GFA18"), tray(3, tray_color="000000FF"),
            ]}],
        },
    }
    body.update(over)
    return {"print": body}


@pytest.fixture
def acc() -> ReportAccumulator:
    a = ReportAccumulator()
    a.apply(pushall())
    return a


# ------------------------------------------------------------ 全量解析


def test_全量报文解析出各字段(acc: ReportAccumulator) -> None:
    s = acc.snapshot()
    assert s.job.gcode_state is PrinterState.IDLE
    assert s.temps.nozzle == pytest.approx(24.5)
    assert s.temps.bed == pytest.approx(23.0)
    assert s.speed_level == 2
    assert s.speed_name == "标准"
    assert s.lights == {"chamber_light": "on"}
    assert s.nozzle_diameter == "0.4"
    assert len(s.ams.units) == 1
    assert len(s.ams.units[0].trays) == 4
    assert s.ams.units[0].humidity == 4


def test_enable_disable_字符串转成布尔(acc: ReportAccumulator) -> None:
    """拓竹用 "enable"/"disable" 表示开关，不是布尔——直接当真值用会全部变成 True。"""
    assert acc.snapshot().ipcam.record is True
    assert acc.snapshot().ipcam.timelapse is False


def test_未知的_gcode_state_落到_unknown() -> None:
    """固件报了个不认识的状态时必须是 UNKNOWN，绝不能猜成 IDLE——
    调度层看到 UNKNOWN 会放弃触发，看到 IDLE 会开打。"""
    a = ReportAccumulator()
    a.apply(pushall(gcode_state="SLICING"))
    assert a.snapshot().job.gcode_state is PrinterState.UNKNOWN


# ------------------------------------------------ 增量合并（核心风险区）


def test_增量只带一个单元时另一个单元不丢() -> None:
    a = ReportAccumulator()
    a.apply({"print": {"ams": {"ams": [
        {"id": "0", "tray": [tray(0)]},
        {"id": "1", "tray": [tray(0, "PLA")]},
    ]}}})
    # 只更新 unit 1
    a.apply({"print": {"ams": {"ams": [{"id": "1", "humidity": "2"}]}}})

    units = {u.unit_id: u for u in a.snapshot().ams.units}
    assert set(units) == {0, 1}, "整段替换会把 unit0 丢掉"
    assert units[0].trays[0].tray_type == "PETG"
    assert units[1].humidity == 2
    assert units[1].trays[0].tray_type == "PLA", "合并 unit1 时不该把它自己的托盘冲掉"


def test_增量只带一个托盘时同单元其他托盘不丢(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"ams": {"ams": [
        {"id": "0", "tray": [{"id": "2", "remain": 33}]},
    ]}}})
    trays = {t.slot: t for t in acc.snapshot().ams.units[0].trays}
    assert set(trays) == {0, 1, 2, 3}
    assert trays[2].remain == 33
    assert trays[2].tray_type == "PLA", "只更新 remain 不该抹掉这个托盘的其他字段"
    assert trays[0].remain == 100


def test_托盘顺序变化时按_id_配对而不是按下标(acc: ReportAccumulator) -> None:
    """按下标合并的话，倒序上报会让所有托盘的数据张冠李戴。"""
    acc.apply({"print": {"ams": {"ams": [{"id": "0", "tray": [
        {"id": "3", "remain": 11}, {"id": "0", "remain": 99},
    ]}]}}})
    trays = {t.slot: t for t in acc.snapshot().ams.units[0].trays}
    assert trays[0].remain == 99
    assert trays[3].remain == 11


def test_温度增量不影响_ams(acc: ReportAccumulator) -> None:
    """最常见的增量报文就是温度。它绝不能把 AMS 整片带走。"""
    acc.apply({"print": {"nozzle_temper": 215.3}})
    s = acc.snapshot()
    assert s.temps.nozzle == pytest.approx(215.3)
    assert len(s.ams.units[0].trays) == 4
    assert s.job.gcode_state is PrinterState.IDLE


def test_全局托盘编号是_unit_乘4_加_slot() -> None:
    a = ReportAccumulator()
    a.apply({"print": {"ams": {"ams": [
        {"id": "0", "tray": [tray(0), tray(3)]},
        {"id": "1", "tray": [tray(0, "PLA")]},
    ]}}})
    ids = [t.global_id for t in a.snapshot().ams.all_trays()]
    assert ids == [0, 3, 4], "unit1 的 tray0 必须是 4，否则会覆盖 unit0 的 tray0"


def test_外置料被识别(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"vt_tray": {
        "id": "254", "tray_type": "PLA", "tray_color": "00AE42FF", "remain": -1,
    }}})
    ext = acc.snapshot().ams.external
    assert ext is not None
    assert ext.is_external and ext.global_id == EXTERNAL_TRAY_ID
    assert ext.rgb == "00AE42"
    assert ext in acc.snapshot().ams.all_trays()


def test_空的外置料槽不算(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"vt_tray": {"id": "254", "tray_type": ""}}})
    assert acc.snapshot().ams.external is None


# ---------------------------------------------------------------- patch


def test_patch_只含变化的字段(acc: ReportAccumulator) -> None:
    result = acc.apply({"print": {"nozzle_temper": 215.3, "mc_percent": 37}})
    assert result is not None
    _, patch = result
    assert patch["temps"] == {"nozzle": pytest.approx(215.3)}
    assert patch["job"] == {"percent": 37}
    assert "ams" not in patch, "没变的整块不该进 patch"


def test_无实质变化时返回_none(acc: ReportAccumulator) -> None:
    """A1 会重复推送相同内容；没必要为此唤醒所有 SSE 订阅者。"""
    assert acc.apply(pushall()) is None


def test_非字典报文被忽略(acc: ReportAccumulator) -> None:
    assert acc.apply([]) is None  # type: ignore[arg-type]


# ------------------------------------------------------------ 容错与兜底


def test_未建模的字段被记下来而不是静默丢弃(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"某个新固件字段": 1, "another_new_one": "x"}})
    seen = acc.snapshot().raw_keys_seen
    assert "某个新固件字段" in seen
    assert "another_new_one" in seen
    assert "gcode_state" not in seen, "已建模的字段不该混进来"


def test_字段类型飘了也不炸(acc: ReportAccumulator) -> None:
    """同一个字段在不同固件版本里见过字符串也见过数字。"""
    acc.apply({"print": {
        "mc_percent": "58", "nozzle_temper": "215.5", "spd_lvl": "3",
        "layer_num": None, "ams": None, "lights_report": None, "hms": "坏数据",
    }})
    s = acc.snapshot()
    assert s.job.percent == 58
    assert s.temps.nozzle == pytest.approx(215.5)
    assert s.speed_level == 3
    assert s.job.layer_num is None
    assert s.job.hms == ()


def test_畸形的_ams_结构不炸() -> None:
    a = ReportAccumulator()
    a.apply({"print": {"ams": {"ams": [
        "不是字典",
        {"id": "bad", "tray": []},
        {"id": "0", "tray": ["也不是字典", {"id": "0", "tray_type": "PLA"}]},
    ]}}})
    units = a.snapshot().ams.units
    assert len(units) == 1 and units[0].unit_id == 0
    assert len(units[0].trays) == 1


def test_hms_错误码格式(acc: ReportAccumulator) -> None:
    """只保留原始码 + 查询链接，不硬编码猜来的中文描述。"""
    acc.apply({"print": {"hms": [{"attr": 0x0C000200, "code": 0x00030003}]}})
    hms = acc.snapshot().job.hms
    assert len(hms) == 1
    assert hms[0].key == "0C00_0200_0003_0003"
    assert hms[0].url.endswith("0C00_0200_0003_0003")
    assert acc.snapshot().job.has_error


def test_固件版本从_info_段读出() -> None:
    a = ReportAccumulator()
    a.apply({"info": {"module": [
        {"name": "ota", "sw_ver": "01.08.01.00"},
        {"name": "esp32", "sw_ver": "01.16.33.15"},
    ]}})
    assert a.snapshot().versions == {"ota": "01.08.01.00", "esp32": "01.16.33.15"}


def test_版本增量按_name_配对合并() -> None:
    a = ReportAccumulator()
    a.apply({"info": {"module": [{"name": "ota", "sw_ver": "01.08.01.00"}]}})
    a.apply({"info": {"module": [{"name": "mc", "sw_ver": "00.01.30.58"}]}})
    assert set(a.snapshot().versions) == {"ota", "mc"}


def test_reset_清空累积状态(acc: ReportAccumulator) -> None:
    """断线重连后旧快照可能完全过时，等新的 pushall 重建。"""
    acc.reset()
    s = acc.snapshot()
    assert s.job.gcode_state is PrinterState.UNKNOWN
    assert s.ams.units == ()


# --------------------------------------------------------------- 序列化


def test_to_dict_可被_json_序列化(acc: ReportAccumulator) -> None:
    """快照要走 SSE 推给浏览器，不能有 datetime 之类的东西漏出去。"""
    text = json.dumps(acc.snapshot().to_dict(), ensure_ascii=False)
    data = json.loads(text)
    assert data["job"]["gcode_state"] == "IDLE"
    assert isinstance(data["updated_at"], str)
    assert data["ams"]["units"][0]["trays"][0]["label"] == "A1 PETG"
    # 风扇档位（0-15）要转成百分比给前端，别让界面显示「转速 7」
    assert data["fans"]["cooling"] == 0


def test_风扇档位转百分比(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"cooling_fan_speed": "15", "big_fan1_speed": "8"}})
    d = acc.snapshot().to_dict()
    assert d["fans"]["cooling"] == 100
    assert d["fans"]["big_fan1"] == 53


def test_阶段名查不到时不炸(acc: ReportAccumulator) -> None:
    acc.apply({"print": {"stg_cur": 999}})
    assert acc.snapshot().job.stage == "未知阶段 999"
