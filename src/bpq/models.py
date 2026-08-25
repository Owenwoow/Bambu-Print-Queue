"""领域模型：任务与打印机状态。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 只在类型检查时导入，避免 models.py 与 config.py 相互 import——
    # config.py 目前不依赖 models.py，但反过来这里只是要个类型注解，
    # 用 TYPE_CHECKING 就不会在运行时产生真实的模块依赖。
    from bpq.config import PrintConfig


class PrinterState(StrEnum):
    """MQTT report 里 `gcode_state` 的取值，打印机只报这五个。"""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSE = "PAUSE"
    FINISH = "FINISH"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 本地补充：连不上或还没收到 pushall 全量时

    @property
    def is_busy(self) -> bool:
        """真的在干活。只有这两个状态是「机器忙」。"""
        return self in (PrinterState.RUNNING, PrinterState.PAUSE)

    @property
    def is_idle(self) -> bool:
        """可以直接下发启动指令的状态。

        注意 FAILED 不在此列：它表示上一个任务的**结局**而非机器在忙，机器其实是闲的，
        但板子上很可能还留着失败的残骸。放行它需要显式许可，见 needs_attention。
        """
        return self in (PrinterState.IDLE, PrinterState.FINISH)

    @property
    def needs_attention(self) -> bool:
        """机器不忙，但上一单没有善终——要人确认板子清了没。"""
        return self is PrinterState.FAILED


class TaskState(StrEnum):
    """任务生命周期。"""

    PENDING = "pending"      # 已受理，等待触发时刻
    UPLOADED = "uploaded"    # 文件已静默躺在打印机存储上（upload_timing=early）
    STARTED = "started"      # 已下发 project_file 且打印机转 RUNNING
    CANCELLED = "cancelled"  # 用户在触发前反悔
    ABORTED = "aborted"      # 到点机器不空闲，按配置放弃
    FAILED = "failed"        # 上传或启动出错


@dataclass
class AmsTray:
    """AMS 里一个托盘的实况，来自 MQTT report 的 ams 字段。

    注意 AMS lite 没有 RFID（tag_uid / tray_uuid 全 0），type / color / info_idx
    都是用户在 Studio 里手填的，不要当成权威真值。

    `id` 是**全局编号**（unit_id * 4 + slot），不是单元内 0–3 的编号——多 AMS 单元时
    单元内编号会互相覆盖，调用方（比如 match_ams）应该只认这个全局 id。
    `unit_id` / `slot` 保留下来是为了在需要按物理单元分组展示时还原原始信息。
    """

    id: int
    type: str = ""           # PETG / PLA / ...
    color: str = ""          # 8 位 RRGGBBAA
    info_idx: str = ""       # tray_info_idx，耗材型号，如 GFG00
    remain: int = -1         # 剩余百分比，-1 表示未知
    k: float = 0.0           # 流量校准系数
    unit_id: int = 0         # 物理 AMS 单元编号，来自 report 里 unit["id"]
    slot: int = 0            # 单元内槽位编号（0–3），来自 tray["id"]
    is_external: bool = False  # True 表示这不是 AMS 槽位，而是外置料（vt_tray）

    @property
    def rgb(self) -> str:
        return self.color.upper()[:6]


@dataclass(frozen=True)
class PrintOptions:
    """Studio「发送打印任务」对话框里的开关，任务级覆盖。

    None 的语义是「跟随全局 [print] 默认」，不是 False——存 None 而不是提交时就把它
    固化成一个具体的布尔值，是为了让「之后改了全局默认」这件事也能对还没触发的
    任务生效（比如提交时全局默认是关振动校准，后来手动开了，已提交但未触发的
    任务应该跟着变，而不是被提交那一刻的值锁死）。
    """

    bed_leveling: bool | None = None
    vibration_cali: bool | None = None
    flow_cali: bool | None = None
    layer_inspect: bool | None = None
    timelapse: bool | None = None

    def resolve(self, defaults: PrintConfig) -> ResolvedPrintOptions:
        """把 None 换成全局默认值，得到五个确定的布尔值。"""
        return ResolvedPrintOptions(
            bed_leveling=(
                self.bed_leveling if self.bed_leveling is not None else defaults.bed_leveling
            ),
            vibration_cali=(
                self.vibration_cali
                if self.vibration_cali is not None
                else defaults.vibration_cali
            ),
            flow_cali=self.flow_cali if self.flow_cali is not None else defaults.flow_cali,
            layer_inspect=(
                self.layer_inspect if self.layer_inspect is not None else defaults.layer_inspect
            ),
            timelapse=self.timelapse if self.timelapse is not None else defaults.timelapse,
        )


@dataclass(frozen=True)
class ResolvedPrintOptions:
    """PrintOptions.resolve() 的结果：五个全部确定下来的布尔值，直接进 project_file。"""

    bed_leveling: bool
    vibration_cali: bool
    flow_cali: bool
    layer_inspect: bool
    timelapse: bool


@dataclass(frozen=True)
class FilamentRef:
    """提交时从 3mf 抄下的耗材快照。

    存这份快照是为了给 WebUI 渲染映射编辑器用——不必每次重开 3mf 现读，
    因为源文件提交之后可能被用户删掉或挪走了，但任务还没触发，映射还得能改。
    """

    id: int = 0   # 1-based，与 ams_mapping 下标 +1 对齐（ams_mapping[i] 对应 id == i+1）
    type: str = ""
    color: str = ""
    info_idx: str = ""
    used_g: float = 0.0


@dataclass
class Task:
    """一个待触发的打印任务。

    必须可持久化：服务重启、电脑睡眠唤醒之后待发任务要还在。
    这条是把项目从「一个脚本」抬到「有状态的常驻进程」的那条需求。
    """

    source_path: str          # 提交时的本地 3mf/gcode 绝对路径
    scheduled_at: datetime    # 绝对触发时刻（本地时区）
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    remote_name: str = ""     # 打印机存储上的文件名，默认取 source_path 的 basename
    # project_file 的 param 字段。默认值只是兜底——真实值应由 threemf.inspect() 从
    # 文件里读出来，因为 3mf 里未必有 plate_1（导出第 3 个盘时就只有 plate_3）。
    plate: str = "Metadata/plate_1.gcode"
    plate_index: int = 0      # plate 对应的盘号（如 3），供 WebUI / 日志展示，不参与下发
    md5: str = ""             # plate gcode 的 md5，取自 3mf 内的 .md5 文件
    bed_type: str = "auto"    # 取自 3mf 的 plate_N.json，如 textured_plate
    use_ams: bool = False
    # 空列表表示不使用 AMS（对齐「不用 AMS 就不该发一个假的 [0] 映射」）；
    # 旧版默认值 [0] 在没有 AMS 或单色场景下没有意义，还会让 match_ams 的下标
    # 语义混乱，故 v0.2 起改为空表示「无映射」。
    ams_mapping: list[int] = field(default_factory=list)
    options: PrintOptions = field(default_factory=PrintOptions)
    filaments: list[FilamentRef] = field(default_factory=list)
    mapping_source: str = "auto"   # auto | manual —— 映射是自动匹配还是人工改过
    mapping_notes: list[str] = field(default_factory=list)
    title: str = ""            # 展示用标题，默认可回退到文件名
    origin: str = "cli"        # 任务来源：cli | web，排障时区分入口
    state: TaskState = TaskState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    triggered_at: datetime | None = None
    uploaded_at: datetime | None = None
    error: str | None = None
    # 实际下发给打印机的 project_file JSON（start() 里的 payload），排障用——
    # 到点触发但打印机行为不对时，不用再靠猜就能看到当时到底发了什么。
    sent_payload: str | None = None
