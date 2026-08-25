"""一台假打印机，**只给测试用**。

v0.2 期间它是产品代码的一部分（`bpq daemon --fake-printer`），因为那时还没接真机，
WebUI 只能对着它开发。v0.3 接上真机之后那个理由消失了，于是搬到这里——
留在 src/ 下反而是个陷阱：对着一台合成的打印机调半天真问题，
而日志里那行橙色横幅未必每次都有人看见。

它按真实报文的形状合成状态流：温度会爬、进度会走、层数会加。走的是和 LanTransport
完全一样的出口（ReportAccumulator + set_report_listener），所以被它驱动的上层
（PrinterLink → SSE）和真机路径拿到的是一样的东西。

AMS 那四槽照抄 docs/验证记录-通道A.md 里记的本机真实配置
（三卷 GFG00 PETG 橙/白/黑 + 一卷 GFA18 PLA），好让配料逻辑在测试里
也表现得像真的。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from bpq.config import Config
from bpq.models import AmsTray, PrinterState, Task
from bpq.report import ReportAccumulator
from bpq.snapshot import PrinterSnapshot
from bpq.transport.base import PrinterTransport, ReportListener, TransportError

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0        # 合成报文的推送间隔
FAKE_TOTAL_LAYERS = 25
NOZZLE_TARGET = 255.0     # PETG
BED_TARGET = 70.0


def _initial_report() -> dict:
    """一台刚开机、装了四卷料的 A1。形状照着实测记录来。

    AMS 那四槽是 docs/验证记录-通道A.md 里记下的本机真实配置（三卷 PETG 一卷 PLA），
    照抄真值能让「颜色距离最近」那套匹配逻辑在假机上也表现得像真的。
    """
    return {
        "print": {
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
            "nozzle_target_temper": 0.0,
            "bed_temper": 23.8,
            "bed_target_temper": 0.0,
            "nozzle_diameter": "0.4",
            "nozzle_type": "stainless_steel",
            "cooling_fan_speed": "0",
            "big_fan1_speed": "0",
            "big_fan2_speed": "0",
            "spd_lvl": 2,
            "spd_mag": 100,
            "wifi_signal": "-52dBm",
            "sdcard": True,
            "lights_report": [{"node": "chamber_light", "mode": "on"}],
            "hms": [],
            "ipcam": {"ipcam_record": "enable", "timelapse": "disable",
                      "resolution": "1080p"},
            "xcam": {"first_layer_inspector": False, "spaghetti_detector": False},
            "ams": {
                "ams_exist_bits": "1",
                "tray_now": "255",
                "tray_tar": "255",
                "version": 1,
                "ams": [{
                    "id": "0", "humidity": "4", "temp": "28.5",
                    "tray": [
                        {"id": "0", "tray_type": "PETG", "tray_sub_brands": "PETG HF",
                         "tray_color": "F98C36FF", "tray_info_idx": "GFG00",
                         "remain": 100, "k": 0.04, "nozzle_temp_min": 220,
                         "nozzle_temp_max": 270},
                        {"id": "1", "tray_type": "PETG", "tray_sub_brands": "PETG HF",
                         "tray_color": "FFFFFFFF", "tray_info_idx": "GFG00",
                         "remain": 100, "k": 0.073, "nozzle_temp_min": 220,
                         "nozzle_temp_max": 270},
                        {"id": "2", "tray_type": "PLA", "tray_sub_brands": "PLA Basic",
                         "tray_color": "FFFFFFFF", "tray_info_idx": "GFA18",
                         "remain": 100, "k": 0.02, "nozzle_temp_min": 190,
                         "nozzle_temp_max": 240},
                        {"id": "3", "tray_type": "PETG", "tray_sub_brands": "PETG HF",
                         "tray_color": "000000FF", "tray_info_idx": "GFG00",
                         "remain": 100, "k": 0.073, "nozzle_temp_min": 220,
                         "nozzle_temp_max": 270},
                    ],
                }],
            },
        },
        "info": {"module": [
            {"name": "ota", "sw_ver": "01.08.01.00"},
            {"name": "esp32", "sw_ver": "01.16.33.15"},
            {"name": "mc", "sw_ver": "00.01.30.58"},
            {"name": "th", "sw_ver": "00.01.07.70"},
            {"name": "ams_f1/0", "sw_ver": "00.00.08.15"},
        ]},
    }


class FakePrinterTransport(PrinterTransport):
    """假打印机。接口与 LanTransport 完全一致，可以直接塞进 PrinterLink。"""

    def __init__(
        self,
        cfg: Config,
        *,
        state: PrinterState = PrinterState.IDLE,
        upload_seconds: float = 0.5,
        fail_upload: bool = False,
        fail_start: bool = False,
        speed: float = 30.0,
    ) -> None:
        """speed 是时间加速倍数，作用于整个流程（预热和逐层）。

        默认 30 大约是「开发时盯着屏幕看进度条动」的节奏；测试里调到几百，
        整场打印一秒内演完。
        """
        self.cfg = cfg
        self.upload_seconds = upload_seconds
        self.fail_upload = fail_upload
        self.fail_start = fail_start
        self.speed = speed

        self._acc = ReportAccumulator()
        self._lock = threading.RLock()
        self._listener: ReportListener | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._layer = 0
        self.uploaded: list[str] = []      # 测试可以断言「传了哪些文件」
        self.started: list[str] = []

        self._feed(_initial_report())
        if state is not PrinterState.IDLE:
            self._feed({"print": {"gcode_state": state.value}})

    # ------------------------------------------------------------ 内部

    def _feed(self, payload: dict) -> None:
        """喂一条合成报文，走的路径和真机收到 MQTT 消息完全一样。"""
        with self._lock:
            changed = self._acc.apply(payload)
            listener = self._listener
        if changed is not None and listener is not None:
            snap, patch = changed
            try:
                listener(snap, patch)
            except Exception:  # noqa: BLE001 - 监听方出错不该拖垮状态流
                log.exception("假打印机的 report 监听回调出错")

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="fake-printer")
        self._thread.start()

    @property
    def _tick(self) -> float:
        return TICK_SECONDS * 30 / max(self.speed, 1)

    def _run(self) -> None:
        """把一次打印演完：预热 → 逐层 → 完成。"""
        try:
            self._feed({"print": {
                "gcode_state": "RUNNING", "print_type": "local", "stg_cur": 1,
                "nozzle_target_temper": NOZZLE_TARGET, "bed_target_temper": BED_TARGET,
                "total_layer_num": FAKE_TOTAL_LAYERS, "layer_num": 0, "mc_percent": 0,
            }})
            # 预热：温度线性爬上去，顺便让前端的温度曲线有东西可画
            for i in range(1, 6):
                if self._stop.wait(self._tick):
                    return
                self._feed({"print": {
                    "nozzle_temper": round(24.5 + (NOZZLE_TARGET - 24.5) * i / 5, 1),
                    "bed_temper": round(23.8 + (BED_TARGET - 23.8) * i / 5, 1),
                    "stg_cur": 2 if i < 3 else 7,
                }})

            self._feed({"print": {"cooling_fan_speed": "10", "stg_cur": 0}})
            for layer in range(1, FAKE_TOTAL_LAYERS + 1):
                if self._stop.wait(self._tick):
                    return
                self._layer = layer
                self._feed({"print": {
                    "layer_num": layer,
                    "mc_percent": round(layer / FAKE_TOTAL_LAYERS * 100),
                    "mc_remaining_time": FAKE_TOTAL_LAYERS - layer,
                }})

            self._feed({"print": {
                "gcode_state": "FINISH", "stg_cur": -1, "mc_percent": 100,
                "mc_remaining_time": 0, "nozzle_target_temper": 0.0,
                "bed_target_temper": 0.0, "cooling_fan_speed": "0",
                "print_type": "idle",
            }})
        except Exception:  # noqa: BLE001 - 后台线程炸了要留下痕迹，别静默死掉
            log.exception("假打印机的状态推进线程出错")

    # ------------------------------------------------- PrinterTransport

    def upload(self, local_path: Path, remote_name: str) -> None:
        if self.fail_upload:
            raise TransportError(f"（假打印机）故意让上传失败: {remote_name}")
        # 真机 FTPS 只有 ~46 KB/s，装装样子好让 UI 的进度提示有机会显示出来
        time.sleep(self.upload_seconds)
        self.uploaded.append(remote_name)
        log.info("（假打印机）已接收 %s（%d 字节）", remote_name,
                 local_path.stat().st_size if local_path.exists() else 0)

    def start(self, task: Task) -> str:
        if self.fail_start:
            raise TransportError("（假打印机）故意让启动失败")
        with self._lock:
            state = self._acc.snapshot().job.gcode_state
        if state.is_busy:
            raise TransportError(f"（假打印机）当前状态 {state.value}，拒绝启动")

        opt = task.options.resolve(self.cfg.print)
        payload = {"print": {
            "sequence_id": task.id, "command": "project_file", "param": task.plate,
            "url": f"file:///sdcard/{task.remote_name}", "subtask_name": task.remote_name,
            "md5": task.md5, "bed_type": task.bed_type,
            "bed_leveling": opt.bed_leveling, "bed_levelling": opt.bed_leveling,
            "vibration_cali": opt.vibration_cali, "flow_cali": opt.flow_cali,
            "layer_inspect": opt.layer_inspect, "timelapse": opt.timelapse,
            "use_ams": task.use_ams, "ams_mapping": task.ams_mapping,
        }}
        self.started.append(task.id)
        self._feed({"print": {"subtask_name": task.remote_name,
                              "gcode_file": task.plate,
                              "ams": {"tray_tar": str((task.ams_mapping or [255])[0])}}})
        self._ensure_thread()
        log.info("（假打印机）开始打印 %s", task.remote_name)
        return json.dumps(payload, ensure_ascii=False)

    def get_state(self, timeout: float = 10.0) -> PrinterState:
        with self._lock:
            return self._acc.snapshot().job.gcode_state

    def get_snapshot(self) -> PrinterSnapshot:
        with self._lock:
            return self._acc.snapshot()

    def get_version(self) -> dict[str, str]:
        with self._lock:
            return dict(self._acc.snapshot().versions)

    def get_ams_trays(self) -> dict[int, AmsTray]:
        with self._lock:
            trays = self._acc.snapshot().ams.all_trays()
        return {
            t.global_id: AmsTray(
                id=t.global_id, type=t.tray_type, color=t.color, info_idx=t.info_idx,
                remain=t.remain, k=t.k, unit_id=t.unit_id, slot=t.slot,
                is_external=t.is_external,
            )
            for t in trays
        }

    def set_report_listener(self, fn: ReportListener | None) -> None:
        with self._lock:
            self._listener = fn

    def reset_state(self) -> None:
        with self._lock:
            self._acc.reset()
            self._feed(_initial_report())

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
