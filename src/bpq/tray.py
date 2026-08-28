"""托盘常驻：懒人模式的下一代形态。

`lazy.py` 双击起个黑框控制台，关掉窗口 = 关掉 daemon，v0.2/v0.3 期间够用；
但黑框本身就是一种「看起来像没做完」的观感，而且没法在后台一直挂着——
关浏览器标签页很容易顺手点掉整个控制台窗口，定时任务也就跟着没了。

托盘版把「进程还活着」这件事从控制台窗口挪到系统托盘图标：关浏览器不影响
daemon，只有从托盘菜单里明确点「退出」才真正停。所有实际操作（建任务、看
AMS、改配置）依然只在 WebUI 里做——托盘只负责状态一览和几个快捷入口，
不做通知、不重复 WebUI 已有的任何功能。

本模块分两层：
  - 纯函数（颜色/文案/图标生成/退出确认文案）：不碰 pystray、不碰 winreg
    的实际调用逻辑之外的东西，方便直接单元测试。
  - glue（`run()` 和菜单构建）：真正起 `pystray.Icon`，只在这里触碰
    pystray 的运行时对象。

读打印机状态和下一个任务，一律走 `bpq.runtime.current()` 拿到的
`DaemonContext`——`ctx.link.snapshot()`、`ctx.store.list(pending_only=True)`
都是同进程内存读取，不建任何新连接。这是硬约束：daemon 运行期间任何代码路径
都不许另建 MQTT 连接。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bpq.models import PrinterState

if TYPE_CHECKING:
    from bpq.config import Config
    from bpq.runtime import DaemonContext

log = logging.getLogger(__name__)

# --------------------------------------------------------------------- 纯函数

_GRAY = (148, 148, 148)    # 未连接 / 状态过期 / 拿不到 ctx
_BLUE = (0, 122, 204)      # 空闲
_GREEN = (34, 139, 34)     # 打印中
_ORANGE = (230, 140, 20)   # 暂停
_RED = (200, 40, 40)       # 上一单失败

_STATE_COLORS = {
    PrinterState.IDLE: _BLUE,
    PrinterState.RUNNING: _GREEN,
    PrinterState.PAUSE: _ORANGE,
    PrinterState.FAILED: _RED,
}

_TOOLTIP_MAX = 120   # Windows 托盘 tooltip 硬限 128，留点余量


def _status_color(ctx: DaemonContext | None) -> tuple[int, int, int]:
    """打印机状态 -> 图标颜色。ctx 是 DaemonContext 或 None（daemon 还没就绪时）。"""
    if ctx is None:
        return _GRAY
    snap = ctx.link.snapshot()
    if not snap.connected or snap.stale:
        return _GRAY
    return _STATE_COLORS.get(snap.state, _GRAY)  # FINISH/UNKNOWN 落到灰色兜底


def _printer_summary(ctx: DaemonContext | None) -> str:
    """打印机状态的一句话摘要，tooltip 和菜单项共用同一份判断逻辑。"""
    if ctx is None:
        return "未连接"
    snap = ctx.link.snapshot()
    if not snap.connected:
        return "未连接"
    if snap.stale:
        return "状态过期"
    state = snap.state
    if state is PrinterState.RUNNING:
        pct = snap.job.percent
        return f"打印中 {pct}%" if pct is not None else "打印中"
    if state is PrinterState.PAUSE:
        return "已暂停"
    if state is PrinterState.IDLE:
        return "空闲"
    if state is PrinterState.FINISH:
        return "打印完成"
    if state is PrinterState.FAILED:
        return "上一单失败"
    return "状态未知"


def _next_task_summary(ctx: DaemonContext | None) -> str:
    """下一个待触发任务的一句话摘要，没有就是「无」。"""
    if ctx is None:
        return "无"
    pending = ctx.store.list(pending_only=True)
    if not pending:
        return "无"
    task = pending[0]
    name = task.title or Path(task.source_path).name
    fmt = "%H:%M" if task.scheduled_at.date() == datetime.now().date() else "%m-%d %H:%M"
    return f"{task.scheduled_at.strftime(fmt)} {name}"


def _tooltip_text(ctx: DaemonContext | None) -> str:
    """给托盘图标 hover 的一行摘要，Windows 限 128 字符，这里控制在更短。"""
    text = f"bpq · {_printer_summary(ctx)}"
    next_task = _next_task_summary(ctx)
    if next_task != "无":
        text += f" · 下一个 {next_task}"
    if len(text) > _TOOLTIP_MAX:
        text = text[: _TOOLTIP_MAX - 1] + "…"
    return text


def _confirm_exit_message(pending_count: int) -> str | None:
    """要不要弹二次确认。没有待触发任务返回 None（不用确认），
    有的话返回确认框文案。"""
    if pending_count == 0:
        return None
    return f"还有 {pending_count} 个任务待触发，现在退出这些任务不会打印。确定要退出吗？"


def _make_icon_image(color: tuple[int, int, int], size: int = 64):  # noqa: ANN201
    """程序生成一个实心圆图标，不依赖任何外部图片资源。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin = size // 8
    bbox = (margin, margin, size - margin, size - margin)
    ImageDraw.Draw(image).ellipse(bbox, fill=(*color, 255))
    return image


# --------------------------------------------------------------------- 开机自启

# HKCU 而不是 HKLM：不需要管理员权限，且只影响当前用户，符合「单用户本地小工具」的定位。
_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE = "bpq"


def is_autostart_enabled() -> bool:
    """读注册表，读不到/平台不对就返回 False，不抛异常。"""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as key:
            winreg.QueryValueEx(key, _AUTOSTART_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    """开/关开机自启。只在 sys.frozen 时才有意义（写的是 sys.executable 的路径）；
    非打包环境下调用直接 no-op 并记日志警告，不报错——开发机上没必要真的写注册表。
    """
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False):
        log.warning("非打包环境下调用 set_autostart 是 no-op，开发机上没必要写注册表")
        return

    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _AUTOSTART_VALUE, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, _AUTOSTART_VALUE)
            except FileNotFoundError:
                pass  # 本来就没有值，删不到不是错误


# --------------------------------------------------------------------- glue


def _open_path(path: str | Path) -> None:
    """用系统关联程序打开一个文件/文件夹。

    这个模块只会在 Windows 打包出的托盘 exe 里跑，但 `os.startfile` 在
    typeshed 里只在 `sys.platform == "win32"` 分支下才有定义——mypy 在非
    Windows 平台（比如 CI 的 ubuntu-latest）上检查这个文件时会报
    "Module has no attribute" 找不到符号。这层判断纯粹是为了让 mypy 用
    `sys.platform` 做静态可达性分析时能剪掉非 win32 分支，不是运行时会真的
    走到 else（daemon.py 的 `_lock_fd` 也是同样的手法）。
    """
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606


def _confirm_yesno(message: str, *, title: str = "bpq") -> bool:
    """原生 Yes/No 确认框，返回是否选了「是」。sys.platform 判断的理由同 `_open_path`。"""
    if sys.platform != "win32":
        return True  # 理论上跑不到这条分支，兜底不挡住调用方
    mb_yesno_iconwarning = 0x00000004 | 0x00000030  # MB_YESNO | MB_ICONWARNING
    idyes = 6
    result = ctypes.windll.user32.MessageBoxW(0, message, title, mb_yesno_iconwarning)
    return result == idyes


def _on_toggle_autostart(icon, item) -> None:  # noqa: ANN001, ARG001
    set_autostart(not is_autostart_enabled())


def _on_quit(icon, item, stop_event: threading.Event) -> None:  # noqa: ANN001, ARG001
    """退出前看一眼有没有待触发的任务——静默丢掉这些任务是「意外」，得让人确认一次。"""
    from bpq import runtime

    ctx = runtime.current()
    pending_count = len(ctx.store.list(pending_only=True)) if ctx is not None else 0
    message = _confirm_exit_message(pending_count)
    if message is not None and not _confirm_yesno(message):
        return
    stop_event.set()
    icon.stop()


def _build_menu(cfg: Config, stop_event: threading.Event, web_url: str | None):  # noqa: ANN201
    import pystray

    def _printer_text(item):  # noqa: ANN001, ARG001
        from bpq import runtime

        return f"打印机：{_printer_summary(runtime.current())}"

    def _next_task_text(item):  # noqa: ANN001, ARG001
        from bpq import runtime

        return f"下一个任务：{_next_task_summary(runtime.current())}"

    items = []
    if web_url is not None:
        items.append(
            pystray.MenuItem(
                "打开控制台",
                lambda icon, item: webbrowser.open(web_url),  # noqa: B023
                default=True,
            )
        )
        items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem(_printer_text, None, enabled=False))
    items.append(pystray.MenuItem(_next_task_text, None, enabled=False))
    items.append(pystray.Menu.SEPARATOR)
    items.append(
        pystray.MenuItem("打开配置文件", lambda icon, item: _open_path(cfg.path))  # noqa: ANN001
    )
    items.append(
        pystray.MenuItem(
            "打开日志文件夹",
            lambda icon, item: _open_path(Path(cfg.daemon.db_path).parent),  # noqa: ANN001
        )
    )
    items.append(
        pystray.MenuItem(
            "开机自启",
            _on_toggle_autostart,
            checked=lambda item: is_autostart_enabled(),  # noqa: ANN001, ARG005
            enabled=lambda item: getattr(sys, "frozen", False),  # noqa: ANN001, ARG005
        )
    )
    items.append(pystray.Menu.SEPARATOR)
    items.append(
        pystray.MenuItem("退出", lambda icon, item: _on_quit(icon, item, stop_event))  # noqa: ANN001
    )
    return pystray.Menu(*items)


def run(cfg: Config, *, stop_event: threading.Event, web_url: str | None) -> None:
    """起托盘图标，阻塞直到用户选退出（或外部把 stop_event 置位）。

    web_url 为 None 表示 [web] enabled=false，托盘菜单里「打开控制台」要隐藏。

    必须在调用方的主线程里同步调用到底——Windows 上 pystray 的默认后端要有一个
    消息泵占住主线程抽消息，`icon.run()` 内部就是在做这件事，不能挪到子线程里跑。
    traymain.py 依赖这条语义，把它放在 main() 的最后一步调用。
    """
    import pystray

    from bpq import runtime

    color = _status_color(None)
    tooltip = _tooltip_text(None)
    icon = pystray.Icon("bpq", _make_icon_image(color), tooltip)
    icon.menu = _build_menu(cfg, stop_event, web_url)

    def _refresh_loop() -> None:
        nonlocal color, tooltip
        while not stop_event.is_set():
            ctx = runtime.current()
            new_color = _status_color(ctx)
            new_tooltip = _tooltip_text(ctx)
            if new_color != color:
                icon.icon = _make_icon_image(new_color)
                color = new_color
            if new_tooltip != tooltip:
                icon.title = new_tooltip
                tooltip = new_tooltip
            time.sleep(2)

    threading.Thread(target=_refresh_loop, name="bpq-tray-refresh", daemon=True).start()
    icon.run()
