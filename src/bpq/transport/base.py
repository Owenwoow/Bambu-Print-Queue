"""传输层抽象。通道 A 与 B 统一为 upload / start / get_state 三个动作。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from bpq.models import AmsTray, PrinterState, Task
from bpq.snapshot import PrinterSnapshot

# (新快照, 相对上次的 merge-patch)。在传输层的接收线程里被调用。
ReportListener = Callable[[PrinterSnapshot, dict], None]


class TransportError(RuntimeError):
    """传输层通用错误（连接、认证、上传、启动被拒等）。"""


class PrinterTransport(ABC):
    """一台打印机的最小控制面。

    实现约定：
    - upload() 必须是静默的——只写存储，不得产生任何物理动作。
      这是本项目的地基假设，若某个实现做不到，它不该实现这个接口。
    - start() 之前由调用方负责检查 get_state().is_idle。
    """

    @abstractmethod
    def upload(self, local_path: Path, remote_name: str) -> None:
        """把切好的文件写进打印机存储。不得启动打印。"""

    @abstractmethod
    def start(self, task: Task) -> str:
        """让打印机从自身存储启动指定文件。

        返回实际下发的指令 payload（JSON 字符串），由调用方存进 task.sent_payload。
        「到点触发了，但打印机的行为不是我以为的那样」是这个项目最难查的一类问题，
        把当时真正发出去的东西留下来，排查就不必从复现开始。
        """

    @abstractmethod
    def get_state(self, timeout: float = 10.0) -> PrinterState:
        """读当前 gcode_state。连不上时返回 UNKNOWN，不要抛异常吞掉调度。

        实现约定：状态若要靠异步报文填充（如 LAN 的 MQTT pushall），必须在本方法内
        等到首个有效报文，最多等 timeout 秒；等不到才返回 UNKNOWN。调用方不应自己
        sleep 去绕过这个竞态。
        """

    def get_ams_trays(self) -> dict[int, AmsTray]:
        """AMS 各托盘的实况，键是**全局编号**（unit_id * 4 + slot，外置料 254）。

        v0.1 里这个方法只存在于 LanTransport 上，但 cli.py 早就在 PrinterTransport
        类型的对象上调它了——类型检查器一直在报错，只是没人看。既然它是事实上的接口，
        就写进来，并给一个空实现让不支持的通道（比如 cloud）不必强行实现。
        """
        return {}

    def get_version(self) -> dict[str, str]:
        """固件版本信息。用于锁定「当前能工作的固件版本」，可选实现。"""
        return {}

    def get_snapshot(self) -> PrinterSnapshot:
        """当前完整状态快照。

        实现约定：**只读缓存，绝不建连接、绝不阻塞。** WebUI 每秒都会问它，
        而打印机同一时刻只接受一个 MQTT 客户端——读状态这件事不能有建连的副作用。
        要等首个报文请用 get_state()，那里的等待是有意为之的。
        """
        return PrinterSnapshot()

    def set_report_listener(  # noqa: B027 - 可选钩子，不订阅状态流的实现不必覆盖
        self, fn: ReportListener | None
    ) -> None:
        """注册状态变化回调，用于把变化推给 SSE 订阅者。

        实现约定：回调在传输层自己的接收线程里跑，实现方必须非阻塞——
        卡住它就等于卡住整条打印机状态流。
        """

    def reset_state(self) -> None:  # noqa: B027 - 可选钩子，无累积状态的实现不必覆盖
        """丢弃累积的状态。断线时调用：重连后旧快照可能已完全过时。"""

    def close(self) -> None:  # noqa: B027 - 可选钩子，无连接可关的实现不必覆盖
        """释放连接。注意：打印机同一时刻只接受一个 MQTT 客户端。"""

    def __enter__(self) -> PrinterTransport:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
