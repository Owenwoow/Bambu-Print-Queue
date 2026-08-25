"""daemon 内唯一的打印机连接持有者。

为什么必须有这么一层：**打印机同一时刻只接受一个 MQTT 连接**（README「已知坑」
第一条）。v0.1 里每次用完就断，CLI 单次调用没问题；但 v0.2 的 WebUI 要持续显示状态，
就必须常连——而常连意味着这条连接变成了稀缺资源，必须有一个明确的主人。

于是有了三条规矩：

1. **daemon 里任何代码都不许再调 build_transport()。** 要打印机数据就找 PrinterLink
   借；在别的进程里要，就走 daemon 的 HTTP API。违反它会重现 v0.1 验收时踩过的
   「两个实例互抢连接 → 定时任务到点读到 UNKNOWN → 打印机明明空闲却被放弃」。

2. **session() 借出连接但不关它。** 语法上和 `with build_transport(cfg) as tp` 完全
   同形，所以 TaskRunner 只要换一行就能复用长连接，其余逻辑一字不动。

3. **让出连接的优先级低于定时触发。** yield_connection() 是为了让 Bambu Studio 能用；
   但到点触发时 TaskRunner 会无条件先抢回来。否则「让出连接」就成了一个能让定时任务
   静默失灵的开关——那是这次改动里最容易造成事故的地方。
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from bpq.config import Config
from bpq.models import PrinterState
from bpq.snapshot import PrinterSnapshot
from bpq.transport import build as build_transport
from bpq.transport.base import PrinterTransport, TransportError

log = logging.getLogger(__name__)

# (新快照, merge-patch)。在传输层的接收线程里被调用，实现方必须非阻塞。
Listener = Callable[[PrinterSnapshot, dict], None]

TransportFactory = Callable[[Config], PrinterTransport]

WATCHDOG_TICK = 5.0     # 看门狗巡检间隔


@dataclass(frozen=True)
class LinkHealth:
    """这条连接自己的状况——注意和「打印机的状况」是两回事。

    界面上必须把这两者分开显示：「daemon 连不上打印机」和「打印机空闲」是完全
    不同的两件事，混在一起会让排障变成猜谜。
    """

    connected: bool = False
    yielded: bool = False              # 是否主动让给了 Studio
    stale: bool = False                # 太久没收到报文
    last_report_at: datetime | None = None
    opened_at: datetime | None = None
    reconnects: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        return {
            "connected": self.connected,
            "yielded": self.yielded,
            "stale": self.stale,
            "last_report_at": self.last_report_at.isoformat() if self.last_report_at else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
        }


class PrinterLink:
    """长连接的持有者。open() / close() 幂等，可以放心重复调用。"""

    def __init__(self, cfg: Config, *, factory: TransportFactory = build_transport) -> None:
        self.cfg = cfg
        self._factory = factory
        self._tp: PrinterTransport | None = None
        # 借出连接时握着它，把 FTPS 上传和 MQTT 下发串行化——
        # 46 KB/s 的 ESP32 上同时跑这两件事是在给自己找超时。
        self._use_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._yielded = False
        self._opened_at: datetime | None = None
        self._last_report_at: datetime | None = None
        self._reconnects = 0
        self._last_error = ""
        self._snapshot = PrinterSnapshot()
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None

    # ------------------------------------------------------------ 生命周期

    def open(self) -> None:
        """建立长连接。已经连着就什么都不做。"""
        with self._state_lock:
            if self._tp is not None:
                return
            self._yielded = False
        self._connect()
        self._ensure_watchdog()

    def _connect(self) -> None:
        try:
            tp = self._factory(self.cfg)
        except (TransportError, OSError) as exc:
            with self._state_lock:
                self._last_error = str(exc)
            log.warning("连接打印机失败：%s", exc)
            return

        tp.set_report_listener(self._on_report)
        with self._state_lock:
            self._tp = tp
            self._opened_at = datetime.now()
            self._last_error = ""

        # get_state() 内部会等首个全量报文（最多 STATE_TIMEOUT 秒）。
        # 在这里等一次，是为了让 open() 返回之后 snapshot() 立刻就有内容可给 WebUI。
        try:
            state = tp.get_state()
            log.info("连接成功，打印机当前状态：%s", state.value)
        except (TransportError, OSError) as exc:
            with self._state_lock:
                self._last_error = str(exc)
            log.warning("连接已建立，但读取状态失败：%s", exc)
            return

        # 主动把快照拉过来一次。不能只依赖 _on_report 回调：首个全量报文完全可能
        # 在 set_report_listener 之前就已经到了（假打印机是构造时就推，真机是 pushall
        # 秒回），那样 link 自己缓存的快照会一直空着，WebUI 打开就是一片空白，
        # 要等下一条报文才有内容。
        # 顺便要一次固件版本。它不在 pushall 里，要单独发 info.get_version 才会回，
        # 不要的话 WebUI 的「固件版本」卡片会一直是空的——而这个项目明确要求
        # 「在验证通过的版本上锁定不升级」，看不到版本号就无从锁起。
        with contextlib.suppress(Exception):
            tp.get_version()

        snap = tp.get_snapshot()
        with self._state_lock:
            self._snapshot = snap
            if snap.job.gcode_state is not PrinterState.UNKNOWN:
                self._last_report_at = datetime.now()

    def close(self) -> None:
        self._stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=WATCHDOG_TICK + 1)
            self._watchdog = None
        self._drop()

    def _drop(self) -> None:
        """断开底层连接，但保留最后一份快照（标记为 stale）。"""
        with self._state_lock:
            tp, self._tp = self._tp, None
        if tp is None:
            return
        with contextlib.suppress(Exception):
            tp.set_report_listener(None)
        with contextlib.suppress(Exception):
            tp.close()

    def reconfigure(self, cfg: Config) -> None:
        """换一套配置并重连。WebUI 上改完打印机地址后调它。

        先断后连，不做「新连接建好再切」——打印机同一时刻只接受一个连接，
        想优雅切换反而会撞上自己。

        重连失败不抛异常：调用方（HTTP 路由）已经在保存之前试连过一次了，
        这里再失败多半是瞬时问题，watchdog 会继续重试。
        """
        self._drop()
        with self._state_lock:
            self.cfg = cfg
            self._yielded = False
            self._snapshot = PrinterSnapshot()
            self._last_report_at = None
            self._last_error = ""
        self._connect()
        self._ensure_watchdog()

    # ------------------------------------------------------------ 让出/抢回

    @property
    def yielded(self) -> bool:
        with self._state_lock:
            return self._yielded

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._tp is not None

    def yield_connection(self) -> None:
        """把连接让给 Bambu Studio。

        daemon 本身继续跑，定时任务照常在册——到点时 TaskRunner 会自己抢回连接。
        """
        with self._state_lock:
            if self._yielded:
                return
            self._yielded = True
        self._drop()
        log.info("已让出 MQTT 连接，Studio 现在可以连打印机了。到点触发时会自动抢回。")

    def resume_connection(self, *, reason: str = "") -> bool:
        """抢回连接。返回 True 表示这次调用真的做了恢复动作。

        定时触发前会无条件调它——让出连接绝不能变成让定时任务静默失灵的开关。
        """
        with self._state_lock:
            was_yielded = self._yielded
            already = self._tp is not None
            self._yielded = False
        if already:
            return False
        if was_yielded:
            log.info("抢回 MQTT 连接%s", f"（{reason}）" if reason else "")
        self._connect()
        self._ensure_watchdog()
        return True

    # ------------------------------------------------------------ 借用

    @contextlib.contextmanager
    def session(self) -> Iterator[PrinterTransport]:
        """借用连接执行 upload / start。

        **退出时不关连接**，只释放使用锁——这正是它与 build_transport() 的区别，
        也是它能直接替换掉 `with build_transport(cfg) as tp` 的原因。
        """
        with self._use_lock:
            self.resume_connection(reason="需要下发指令")
            with self._state_lock:
                tp = self._tp
            if tp is None:
                raise TransportError(
                    f"连不上打印机（{self._last_error or '原因未知'}）。"
                    "检查打印机是否开机、是否还开着 Bambu Studio 占着那唯一的 MQTT 连接。"
                )
            yield tp

    # ------------------------------------------------------------ 读状态

    def snapshot(self) -> PrinterSnapshot:
        """当前快照。**永不阻塞、永不建连。** WebUI 每秒都会问它。"""
        with self._state_lock:
            snap = self._snapshot
            connected = self._tp is not None
            stale = self._is_stale()
        return _with_link_state(snap, connected=connected, stale=stale)

    def health(self) -> LinkHealth:
        with self._state_lock:
            return LinkHealth(
                connected=self._tp is not None,
                yielded=self._yielded,
                stale=self._is_stale(),
                last_report_at=self._last_report_at,
                opened_at=self._opened_at,
                reconnects=self._reconnects,
                last_error=self._last_error,
            )

    def state(self, timeout: float = 10.0) -> PrinterState:
        """读 gcode_state，必要时等首个报文。调度层用它判断能不能开打。"""
        with self._state_lock:
            tp = self._tp
        if tp is None:
            return PrinterState.UNKNOWN
        return tp.get_state(timeout)

    def request_pushall(self) -> None:
        """主动重拉一次全量。这是只读查询，不会让打印机有任何物理动作。"""
        with self._state_lock:
            tp = self._tp
        if tp is None:
            return
        pull = getattr(tp, "request_pushall", None)
        if callable(pull):
            with contextlib.suppress(Exception):
                pull()

    def _is_stale(self) -> bool:
        """调用方必须已持有 _state_lock。"""
        if self._tp is None or self._last_report_at is None:
            return self._tp is not None
        gap = (datetime.now() - self._last_report_at).total_seconds()
        return gap > self.cfg.link.stale_after

    # ------------------------------------------------------------ 订阅

    def add_listener(self, fn: Listener) -> Callable[[], None]:
        """注册状态变化回调，返回取消订阅的闭包。

        回调在传输层的接收线程里跑，**实现方必须非阻塞**——卡住它就等于卡住
        整条状态流。SSE 那一侧靠一个有界队列来保证这件事。
        """
        with self._state_lock:
            self._listeners.append(fn)

        def _remove() -> None:
            with self._state_lock:
                with contextlib.suppress(ValueError):
                    self._listeners.remove(fn)

        return _remove

    def _on_report(self, snap: PrinterSnapshot, patch: dict) -> None:
        with self._state_lock:
            self._snapshot = snap
            self._last_report_at = datetime.now()
            listeners = list(self._listeners)
        full = _with_link_state(snap, connected=True, stale=False)
        for fn in listeners:
            try:
                fn(full, patch)
            except Exception:  # noqa: BLE001 - 一个订阅者出错不该影响别人和状态流
                log.exception("状态订阅回调出错")

    # ------------------------------------------------------------ 看门狗

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop.clear()
        self._watchdog = threading.Thread(
            target=self._watch, daemon=True, name="printer-link-watchdog"
        )
        self._watchdog.start()

    def _watch(self) -> None:
        """两件事：断了就重连，太久没动静就重拉一次全量。"""
        last_pull = datetime.now()
        while not self._stop.wait(WATCHDOG_TICK):
            with self._state_lock:
                if self._yielded:
                    continue          # 主动让出的，别自作主张连回去
                tp = self._tp
                stale = self._is_stale()
                busy = self._snapshot.job.gcode_state.is_busy

            if tp is None:
                with self._state_lock:
                    self._reconnects += 1
                log.info("检测到连接已断，尝试重连（第 %d 次）", self._reconnects)
                self._connect()
                continue

            if stale:
                log.warning("超过 %ds 没收到任何报文，重连一次", self.cfg.link.stale_after)
                with self._state_lock:
                    self._reconnects += 1
                self._drop()
                self._connect()
                last_pull = datetime.now()
                continue

            # 定期重拉全量兜底，防止漏掉某条增量导致本地快照与实际偏离。
            # 打印进行中不拉：那时增量报文本来就源源不断，而「打印中反复 pushall
            # 是否绝对静默」还没有实测结论，不值得拿这个去冒险。
            gap = (datetime.now() - last_pull).total_seconds()
            if not busy and gap >= self.cfg.link.pushall_interval:
                self.request_pushall()
                last_pull = datetime.now()


def _with_link_state(
    snap: PrinterSnapshot, *, connected: bool, stale: bool
) -> PrinterSnapshot:
    """把连接层的状况盖进快照。

    connected/stale 描述的是**我们和打印机之间那条线**，不是打印机自己的状态；
    但前端要在同一个对象里拿到它们，所以在出口处合并，而不是让快照自己去管连接。
    """
    from dataclasses import replace

    return replace(snap, connected=connected, stale=stale)
