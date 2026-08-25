"""把 A1 的增量 report 合并成一份完整快照。

A1 连上后先推一次 pushall 全量，之后**只推变化的字段**。所以不能拿每条报文单独去
构造快照——那样温度一变，AMS 就整片消失了。做法是自己维护一份累积的原始 dict：
先把新报文深合并进去，再从累积结果整体重建 snapshot。

最容易写错的一处：`ams.ams` 是**列表**，而增量更新可能只带发生变化的那一个单元。
按下标合并会在单元顺序变化时错位，整段替换会把没变的单元丢掉——只能按元素里的
`id` 字段配对合并。`ams.ams[*].tray` 和 `info.module` 同理。

顺带回答一个反复被问到的问题：**Studio 发送对话框里那几个开关，能不能读回来？**

    延时摄影      部分能。ipcam.timelapse 是设备侧的持久设置，语义相近但不等同于
                  「本次任务是否录像」。下发 timelapse=true 会不会翻转它，待真机确认。
    自动热床调平  不能。report 里没有对应字段，只能从 stg_cur 是否经过调平阶段
                  做事后推断。
    动态流量校准  不能直接读。间接信号：校准之后 tray.k 会变。
    振动补偿      不能。间接信号同上（stg_cur 会经过 XY 振动扫频）。
    首层检查      部分能，对应 xcam.first_layer_inspector；但 A1 没有腔内摄像头，
                  这个字段很可能缺失或恒为 false。

五个里三个根本读不回来——这就是为什么「我们发了什么」（Task.options）和
「机器说了什么」（PrinterSnapshot）在数据结构上必须分开，界面上也不该画成对照表。
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from bpq.models import PrinterState
from bpq.snapshot import (
    EXTERNAL_TRAY_ID,
    AmsSnapshot,
    AmsUnitSnapshot,
    FanSnapshot,
    HmsEntry,
    IpcamSnapshot,
    JobSnapshot,
    PrinterSnapshot,
    TempSnapshot,
    TraySnapshot,
)

# 这些路径下的列表要按元素的 "id" 配对合并，而不是整段替换。
# 路径里的 "*" 匹配任意一层列表元素。
MERGE_BY_ID_PATHS: tuple[tuple[str, ...], ...] = (
    ("print", "ams", "ams"),
    ("print", "ams", "ams", "*", "tray"),
    ("info", "module"),
)

# print 段里已经建模的键。不在这里的会被记进 raw_keys_seen，
# 好让「漏了什么字段」一眼可见，而不是静默消失。
KNOWN_PRINT_KEYS = frozenset({
    "gcode_state", "print_type", "subtask_name", "gcode_file", "mc_percent",
    "mc_remaining_time", "layer_num", "total_layer_num", "stg_cur", "print_error",
    "gcode_file_prepare_percent", "hms", "nozzle_temper", "nozzle_target_temper",
    "bed_temper", "bed_target_temper", "chamber_temper", "ams", "vt_tray",
    "cooling_fan_speed", "big_fan1_speed", "big_fan2_speed", "heatbreak_fan_speed",
    "lights_report", "spd_lvl", "spd_mag", "nozzle_diameter", "nozzle_type",
    "wifi_signal", "sdcard", "home_flag", "ipcam", "xcam", "command", "sequence_id",
    "msg", "upgrade_state", "stg", "result",
})


class ReportAccumulator:
    """跨报文累积状态。线程不安全——调用方（PrinterLink）负责加锁。"""

    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        self._seen: set[str] = set()
        self._snapshot = PrinterSnapshot()

    def snapshot(self) -> PrinterSnapshot:
        return self._snapshot

    def reset(self) -> None:
        """断线重连后调用：旧快照可能已经完全过时，等新的 pushall 重建。"""
        self._raw = {}
        self._seen = set()
        self._snapshot = PrinterSnapshot()

    def apply(self, payload: dict) -> tuple[PrinterSnapshot, dict] | None:
        """合并一条报文。

        返回 (新快照, 相对上一次的 merge-patch)；这条报文没带来任何实质变化时返回
        None——A1 会重复推送相同内容，没必要为此唤醒所有 SSE 订阅者。
        """
        if not isinstance(payload, dict):
            return None

        before = self._snapshot.to_dict()
        _deep_merge(self._raw, payload, ())

        report = self._raw.get("print", {})
        if isinstance(report, dict):
            self._seen.update(set(report) - KNOWN_PRINT_KEYS)

        self._snapshot = self._build()
        after = self._snapshot.to_dict()

        patch = _merge_patch(before, after)
        # updated_at 每条报文都在变，它自己不构成「实质变化」。
        if set(patch) <= {"updated_at"}:
            return None
        return self._snapshot, patch

    # ------------------------------------------------------------------ 构造

    def _build(self) -> PrinterSnapshot:
        r = self._raw.get("print", {})
        if not isinstance(r, dict):
            r = {}

        return PrinterSnapshot(
            connected=self._snapshot.connected,
            updated_at=datetime.now(),
            stale=False,
            job=self._job(r),
            temps=TempSnapshot(
                nozzle=_f(r.get("nozzle_temper")),
                nozzle_target=_f(r.get("nozzle_target_temper")),
                bed=_f(r.get("bed_temper")),
                bed_target=_f(r.get("bed_target_temper")),
                chamber=_f(r.get("chamber_temper")),
            ),
            ams=self._ams(r),
            fans=FanSnapshot(
                cooling=_i(r.get("cooling_fan_speed")),
                big_fan1=_i(r.get("big_fan1_speed")),
                big_fan2=_i(r.get("big_fan2_speed")),
                heatbreak=_i(r.get("heatbreak_fan_speed")),
            ),
            lights={
                str(x.get("node", "")): str(x.get("mode", ""))
                for x in r.get("lights_report") or []
                if isinstance(x, dict)
            },
            speed_level=_i(r.get("spd_lvl")),
            speed_mag=_i(r.get("spd_mag")),
            nozzle_diameter=str(r.get("nozzle_diameter") or ""),
            nozzle_type=str(r.get("nozzle_type") or ""),
            wifi_signal=str(r.get("wifi_signal") or ""),
            sdcard=r.get("sdcard") if isinstance(r.get("sdcard"), bool) else None,
            home_flag=_i(r.get("home_flag")),
            ipcam=self._ipcam(r),
            xcam=dict(r.get("xcam") or {}) if isinstance(r.get("xcam"), dict) else {},
            versions=self._versions(),
            raw_keys_seen=tuple(sorted(self._seen)),
        )

    def _job(self, r: dict) -> JobSnapshot:
        raw_state = r.get("gcode_state")
        try:
            state = PrinterState(raw_state) if raw_state else PrinterState.UNKNOWN
        except ValueError:
            # 固件报了一个我们不认识的状态。当作 UNKNOWN 处理，让调度层按
            # 「不确定就别开打」走——绝不能猜成 IDLE。
            state = PrinterState.UNKNOWN

        hms = tuple(
            HmsEntry(attr=_i(h.get("attr"), 0) or 0, code=_i(h.get("code"), 0) or 0)
            for h in r.get("hms") or []
            if isinstance(h, dict)
        )
        return JobSnapshot(
            gcode_state=state,
            print_type=str(r.get("print_type") or ""),
            subtask_name=str(r.get("subtask_name") or ""),
            gcode_file=str(r.get("gcode_file") or ""),
            percent=_i(r.get("mc_percent")),
            remaining_min=_i(r.get("mc_remaining_time")),
            layer_num=_i(r.get("layer_num")),
            total_layers=_i(r.get("total_layer_num")),
            stage_code=_i(r.get("stg_cur")),
            print_error=_i(r.get("print_error")),
            prepare_percent=_i(r.get("gcode_file_prepare_percent")),
            hms=hms,
        )

    def _ams(self, r: dict) -> AmsSnapshot:
        block = r.get("ams")
        block = block if isinstance(block, dict) else {}

        units: list[AmsUnitSnapshot] = []
        for unit in block.get("ams") or []:
            if not isinstance(unit, dict):
                continue
            uid = _i(unit.get("id"))
            if uid is None or uid < 0:
                continue
            trays = tuple(
                _tray(t, unit_id=uid, slot=slot)
                for t in unit.get("tray") or []
                if isinstance(t, dict) and (slot := _i(t.get("id"))) is not None and slot >= 0
            )
            units.append(AmsUnitSnapshot(
                unit_id=uid,
                humidity=_i(unit.get("humidity")),
                temp=_f(unit.get("temp")),
                trays=tuple(sorted(trays, key=lambda t: t.slot)),
            ))

        vt = r.get("vt_tray")
        external = None
        if isinstance(vt, dict) and vt.get("tray_type"):
            external = _tray(vt, unit_id=-1, slot=-1, external=True)

        return AmsSnapshot(
            units=tuple(sorted(units, key=lambda u: u.unit_id)),
            external=external,
            tray_now=_i(block.get("tray_now")),
            tray_pre=_i(block.get("tray_pre")),
            tray_tar=_i(block.get("tray_tar")),
            exist_bits=str(block.get("ams_exist_bits") or ""),
            version=_i(block.get("version")),
        )

    def _ipcam(self, r: dict) -> IpcamSnapshot:
        cam = r.get("ipcam")
        if not isinstance(cam, dict):
            return IpcamSnapshot()
        return IpcamSnapshot(
            record=_enabled(cam.get("ipcam_record")),
            timelapse=_enabled(cam.get("timelapse")),
            resolution=str(cam.get("resolution") or ""),
            mode_bits=_i(cam.get("mode_bits")),
        )

    def _versions(self) -> dict[str, str]:
        info = self._raw.get("info")
        if not isinstance(info, dict):
            return {}
        return {
            str(m.get("name")): str(m.get("sw_ver", ""))
            for m in info.get("module") or []
            if isinstance(m, dict) and m.get("name")
        }


# ---------------------------------------------------------------- 合并原语


def _deep_merge(base: dict, incoming: dict, path: tuple[str, ...]) -> None:
    """把 incoming 深合并进 base（原地修改）。"""
    for key, value in incoming.items():
        here = (*path, key)
        current = base.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _deep_merge(current, value, here)
        elif isinstance(value, list) and isinstance(current, list) and _by_id(here):
            base[key] = _merge_list_by_id(current, value, here)
        else:
            base[key] = copy.deepcopy(value)


def _by_id(path: tuple[str, ...]) -> bool:
    for pattern in MERGE_BY_ID_PATHS:
        if len(pattern) != len(path):
            continue
        if all(p == "*" or p == q for p, q in zip(pattern, path, strict=True)):
            return True
    return False


def _merge_list_by_id(current: list, incoming: list, path: tuple[str, ...]) -> list:
    """按元素的 "id" 字段配对合并两个列表。

    增量报文可能只带变化的那一个 AMS 单元。整段替换会把没变的单元丢掉，
    按下标合并会在顺序变化时错位——只有按 id 配对是对的。
    """
    out = {str(x["id"]): copy.deepcopy(x)
           for x in current if isinstance(x, dict) and "id" in x}
    extras = [copy.deepcopy(x) for x in current if not (isinstance(x, dict) and "id" in x)]

    for item in incoming:
        if not (isinstance(item, dict) and "id" in item):
            extras.append(copy.deepcopy(item))
            continue
        key = str(item["id"])
        if key in out:
            _deep_merge(out[key], item, (*path, "*"))
        else:
            out[key] = copy.deepcopy(item)

    return [*out.values(), *extras]


def _merge_patch(before: dict, after: dict) -> dict:
    """算出 RFC 7386 风格的 merge patch：只含变化的字段，None 表示删除。

    列表整段替换，不做数组 diff——省下来的那点字节不值得换来的复杂度，
    而 AMS 单元列表本来就短。
    """
    patch: dict[str, Any] = {}
    for key in set(before) | set(after):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            sub = _merge_patch(old, new)
            if sub:
                patch[key] = sub
        else:
            patch[key] = new
    return patch


# ---------------------------------------------------------------- 取值容错


def _i(value: object, default: int | None = None) -> int | None:
    """容错取整。固件版本之间字段类型会飘（同一个字段见过字符串也见过整数），
    一处解析失败不该把整份状态拖垮。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


def _f(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _enabled(value: object) -> bool | None:
    """拓竹用 "enable" / "disable" 字符串表示开关，不是布尔。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("enable", "enabled", "on", "true", "1")
    return None


def _tray(t: dict, *, unit_id: int, slot: int, external: bool = False) -> TraySnapshot:
    return TraySnapshot(
        global_id=EXTERNAL_TRAY_ID if external else unit_id * 4 + slot,
        unit_id=unit_id,
        slot=slot,
        is_external=external,
        tray_type=str(t.get("tray_type") or ""),
        tray_sub_brands=str(t.get("tray_sub_brands") or ""),
        color=str(t.get("tray_color") or ""),
        info_idx=str(t.get("tray_info_idx") or ""),
        remain=_i(t.get("remain"), -1) or -1,
        k=_f(t.get("k")) or 0.0,
        n=_f(t.get("n")) or 0.0,
        nozzle_temp_min=_i(t.get("nozzle_temp_min")),
        nozzle_temp_max=_i(t.get("nozzle_temp_max")),
        cali_idx=_i(t.get("cali_idx")),
    )
