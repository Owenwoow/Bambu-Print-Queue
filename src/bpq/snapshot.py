"""打印机状态快照：把 MQTT report 里的东西，变成界面能直接用的形状。

v0.1 只从 report 里取四样东西（gcode_state、指令回执、AMS 托盘、固件版本），其余
全部丢弃。那时够用——CLI 只需要知道「机器闲不闲」。v0.2 的 WebUI 要显示温度、进度、
层数、耗材、错误码，就必须把整份报文接住。

三条贯穿本模块的约定：

1. **只装「机器说了什么」。** 我们下发的参数（bed_leveling / flow_cali 那几个开关）
   存在 Task.options 和 Task.sent_payload 里，和这里在数据结构上完全不相交。
   原因很实际：那几个开关里有三个**根本没有对应的上报字段**（见 report.py 的说明），
   把「发出去的」和「读回来的」混在一个对象里，界面上就会出现「显示调平已关」
   而实际这台机器从没告诉过你这件事——一种编造出来的确定性。

2. **不认识的字段不丢。** 认不出来的顶层键名收进 `raw_keys_seen`，将来发现漏建模了
   一眼就能看见，而不是静默消失。

3. **不硬编码猜来的中文。** HMS 错误码的社区码表覆盖不全，这里只保留原始
   attr/code 并给一个官网查询链接；阶段名（stg_cur）查不到就显示「未知阶段 N」。
   宁可让人看见一个原始码，也不能把一个可能是错的中文摆在故障排查的路上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bpq.models import PrinterState

# 外置料槽（vt_tray）的全局托盘编号。AMS 单元占住 unit*4+slot 的低位段。
EXTERNAL_TRAY_ID = 254

# 拓竹的风扇转速是 0–15 的档位而不是百分比。
FAN_MAX = 15

# spd_lvl 的四档。这个对应关系在 Studio 界面上能直接对上，比较可靠。
SPEED_NAMES = {1: "静音", 2: "标准", 3: "运动", 4: "狂暴"}

# stg_cur（当前阶段）。
# 这张表在不同机型/固件间并不一致，下面多数是社区常见取值。
# 标「实测」的是本机 A1（固件 01.08.01.00）真机采到的；其余仍待采样。
# 查不到一律走「未知阶段 N」兜底，绝不能因为查表失败就崩。
STAGE_NAMES = {
    -1: "空闲",
    0: "打印中",          # 实测 2026-08-25：打印进行中确实是 0
    1: "自动热床调平",
    2: "热床预热",
    3: "扫描挤出机寿命",
    4: "更换耗材",
    5: "M400 暂停",
    6: "暂停：耗材用尽",
    7: "喷嘴预热",
    8: "扫描床面",
    9: "检查第一层",
    10: "识别打印板类型",
    11: "校准微型激光雷达",
    12: "挤出流量校准",
    13: "校准微型激光雷达",
    14: "XY 振动扫频",
    15: "扫描床面",
    16: "第一层扫描",
    255: "已结束",        # 实测 2026-08-25：一单打完后 stg_cur 停在 255
}


def stage_name(code: int | None) -> str:
    if code is None:
        return ""
    return STAGE_NAMES.get(code, f"未知阶段 {code}")


@dataclass(frozen=True, slots=True)
class HmsEntry:
    """一条 HMS（健康管理系统）告警。

    只保留原始的 attr / code。社区的 HMS 码表覆盖不全，硬编码一个猜来的中文描述，
    等于在最需要准确信息的时刻给人一条可能是错的线索。给查询链接更诚实。
    """

    attr: int = 0
    code: int = 0

    @property
    def key(self) -> str:
        """拓竹官网/屏幕上显示的那种四段十六进制码。"""
        return (f"{self.attr >> 16:04X}_{self.attr & 0xFFFF:04X}"
                f"_{self.code >> 16:04X}_{self.code & 0xFFFF:04X}")

    @property
    def url(self) -> str:
        return f"https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/{self.key}"

    @property
    def severity(self) -> str:
        """严重程度编码在 code 的高 4 位里。"""
        return {1: "致命", 2: "严重", 3: "一般", 4: "提示"}.get(
            (self.code >> 16) >> 12, "未知"
        )


@dataclass(frozen=True, slots=True)
class TraySnapshot:
    """AMS 里一个托盘，或者挂在外面的那一卷。

    注意 AMS lite 没有 RFID（tag_uid / tray_uuid 全 0，tray_is_bbl_bits = f）：
    type / color / info_idx 全都是用户在 Studio 里手填的，**不是机器读出来的**。
    界面上给这些值配一个「手填」的标记，比让人以为它们权威要好。
    """

    global_id: int
    unit_id: int = 0
    slot: int = 0
    is_external: bool = False
    tray_type: str = ""          # PLA / PETG / ...
    tray_sub_brands: str = ""    # "PLA Basic"
    color: str = ""              # 8 位 RRGGBBAA
    info_idx: str = ""           # tray_info_idx，耗材型号，如 GFG00
    remain: int = -1             # 剩余百分比，-1 表示未知
    k: float = 0.0               # 流量校准系数
    n: float = 0.0
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    cali_idx: int | None = None

    @property
    def rgb(self) -> str:
        return self.color.upper()[:6]

    @property
    def empty(self) -> bool:
        return not self.tray_type

    @property
    def label(self) -> str:
        """界面上那个「A1 / PETG」式的短标签。外置料没有槽位号。"""
        if self.is_external:
            return f"外置 {self.tray_type}".strip()
        return f"{chr(ord('A') + self.unit_id)}{self.slot + 1} {self.tray_type}".strip()


@dataclass(frozen=True, slots=True)
class AmsUnitSnapshot:
    unit_id: int
    humidity: int | None = None   # 1–5 档，**数值越小越干**
    temp: float | None = None
    trays: tuple[TraySnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class AmsSnapshot:
    units: tuple[AmsUnitSnapshot, ...] = ()
    external: TraySnapshot | None = None    # vt_tray，不在 ams.ams[] 里
    tray_now: int | None = None             # 当前在用的托盘（全局编号）
    tray_pre: int | None = None
    tray_tar: int | None = None             # 目标托盘——验证多色映射时看这个
    exist_bits: str = ""
    version: int | None = None

    def all_trays(self) -> tuple[TraySnapshot, ...]:
        """AMS 各槽 + 外置料，按全局编号排序。给映射编辑器铺选项用。"""
        out = [t for u in self.units for t in u.trays]
        if self.external is not None:
            out.append(self.external)
        return tuple(sorted(out, key=lambda t: t.global_id))


@dataclass(frozen=True, slots=True)
class TempSnapshot:
    nozzle: float | None = None
    nozzle_target: float | None = None
    bed: float | None = None
    bed_target: float | None = None
    chamber: float | None = None     # A1 没有腔温传感器，预期恒为 None


@dataclass(frozen=True, slots=True)
class FanSnapshot:
    """风扇档位（0–15，不是百分比）。"""

    cooling: int | None = None
    big_fan1: int | None = None      # 辅助风扇
    big_fan2: int | None = None      # 腔体/滤芯风扇
    heatbreak: int | None = None

    @staticmethod
    def to_percent(level: int | None) -> int | None:
        return None if level is None else round(level / FAN_MAX * 100)


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """当前这一单打到哪了。"""

    gcode_state: PrinterState = PrinterState.UNKNOWN
    print_type: str = ""             # idle / local / cloud
    subtask_name: str = ""           # 当前任务名
    gcode_file: str = ""
    percent: int | None = None       # mc_percent
    remaining_min: int | None = None  # mc_remaining_time
    layer_num: int | None = None
    total_layers: int | None = None
    stage_code: int | None = None    # stg_cur
    print_error: int | None = None
    prepare_percent: int | None = None   # gcode_file_prepare_percent
    hms: tuple[HmsEntry, ...] = ()

    @property
    def stage(self) -> str:
        return stage_name(self.stage_code)

    @property
    def has_error(self) -> bool:
        return bool(self.hms) or bool(self.print_error)


@dataclass(frozen=True, slots=True)
class IpcamSnapshot:
    """摄像头与延时摄影的**设备侧设置**。

    别把 `timelapse` 当成「本次任务是否录像」——那是我们在 project_file 里发的参数，
    存在 Task.options 里。这里是打印机自己的持久设置，两者语义相近但不是一回事，
    界面上要分开显示。（下发 timelapse=true 会不会翻转这里，待真机确认。）
    """

    record: bool | None = None
    timelapse: bool | None = None
    resolution: str = ""
    mode_bits: int | None = None


@dataclass(frozen=True, slots=True)
class PrinterSnapshot:
    """一份完整的打印机状态。跨报文累积而成，见 report.ReportAccumulator。"""

    connected: bool = False
    updated_at: datetime | None = None
    stale: bool = False              # 太久没收到报文，下面的值可能已经过时
    job: JobSnapshot = field(default_factory=JobSnapshot)
    temps: TempSnapshot = field(default_factory=TempSnapshot)
    ams: AmsSnapshot = field(default_factory=AmsSnapshot)
    fans: FanSnapshot = field(default_factory=FanSnapshot)
    lights: dict[str, str] = field(default_factory=dict)
    speed_level: int | None = None   # spd_lvl
    speed_mag: int | None = None     # spd_mag，百分比
    nozzle_diameter: str = ""
    nozzle_type: str = ""
    wifi_signal: str = ""
    sdcard: bool | None = None
    home_flag: int | None = None     # 位域，含义未确认，原样透出不做解释
    ipcam: IpcamSnapshot = field(default_factory=IpcamSnapshot)
    xcam: dict[str, object] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    raw_keys_seen: tuple[str, ...] = ()

    @property
    def state(self) -> PrinterState:
        """给老调用方的快捷方式——v0.1 只关心这一个值。"""
        return self.job.gcode_state

    @property
    def speed_name(self) -> str:
        return SPEED_NAMES.get(self.speed_level or 0, "")

    def to_dict(self) -> dict:
        """给 JSON 序列化用。datetime 转 ISO 字符串，派生属性一并算好给前端。"""
        return {
            "connected": self.connected,
            "stale": self.stale,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "job": {
                "gcode_state": self.job.gcode_state.value,
                "print_type": self.job.print_type,
                "subtask_name": self.job.subtask_name,
                "gcode_file": self.job.gcode_file,
                "percent": self.job.percent,
                "remaining_min": self.job.remaining_min,
                "layer_num": self.job.layer_num,
                "total_layers": self.job.total_layers,
                "stage_code": self.job.stage_code,
                "stage": self.job.stage,
                "print_error": self.job.print_error,
                "prepare_percent": self.job.prepare_percent,
                "hms": [
                    {"attr": h.attr, "code": h.code, "key": h.key,
                     "severity": h.severity, "url": h.url}
                    for h in self.job.hms
                ],
            },
            "temps": {
                "nozzle": self.temps.nozzle,
                "nozzle_target": self.temps.nozzle_target,
                "bed": self.temps.bed,
                "bed_target": self.temps.bed_target,
                "chamber": self.temps.chamber,
            },
            "ams": {
                "units": [
                    {
                        "unit_id": u.unit_id,
                        "humidity": u.humidity,
                        "temp": u.temp,
                        "trays": [_tray_dict(t) for t in u.trays],
                    }
                    for u in self.ams.units
                ],
                "external": _tray_dict(self.ams.external) if self.ams.external else None,
                "tray_now": self.ams.tray_now,
                "tray_tar": self.ams.tray_tar,
                "exist_bits": self.ams.exist_bits,
            },
            "fans": {
                "cooling": FanSnapshot.to_percent(self.fans.cooling),
                "big_fan1": FanSnapshot.to_percent(self.fans.big_fan1),
                "big_fan2": FanSnapshot.to_percent(self.fans.big_fan2),
                "heatbreak": FanSnapshot.to_percent(self.fans.heatbreak),
            },
            "lights": dict(self.lights),
            "speed": {
                "level": self.speed_level,
                "name": self.speed_name,
                "mag": self.speed_mag,
            },
            "nozzle": {"diameter": self.nozzle_diameter, "type": self.nozzle_type},
            "wifi_signal": self.wifi_signal,
            "sdcard": self.sdcard,
            "home_flag": self.home_flag,
            "ipcam": {
                "record": self.ipcam.record,
                "timelapse": self.ipcam.timelapse,
                "resolution": self.ipcam.resolution,
            },
            "xcam": dict(self.xcam),
            "versions": dict(self.versions),
            "raw_keys_seen": list(self.raw_keys_seen),
        }


def _tray_dict(t: TraySnapshot) -> dict:
    return {
        "global_id": t.global_id,
        "unit_id": t.unit_id,
        "slot": t.slot,
        "is_external": t.is_external,
        "label": t.label,
        "tray_type": t.tray_type,
        "tray_sub_brands": t.tray_sub_brands,
        "color": t.color,
        "rgb": t.rgb,
        "info_idx": t.info_idx,
        "remain": t.remain,
        "k": t.k,
        "empty": t.empty,
        "nozzle_temp_min": t.nozzle_temp_min,
        "nozzle_temp_max": t.nozzle_temp_max,
    }
