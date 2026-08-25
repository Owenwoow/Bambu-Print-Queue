"""睡眠处理。

v0.1 跑在会睡眠的电脑上，用「阻止睡眠」保正确性（防不了用户主动合盖/按睡眠）。
迁到家庭服务器后应换成 systemd timer 的 WakeSystem=true 定时唤醒，更省电——
届时这个模块退化成 no-op。
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator

log = logging.getLogger(__name__)

# Windows SetThreadExecutionState 标志位
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_awake(enabled: bool = True) -> Iterator[None]:
    """在 with 块内阻止系统睡眠，退出时复位。"""
    if not enabled:
        yield
        return

    try:
        from wakepy import keep  # 跨平台，零依赖，MIT

        with keep.running():
            log.info("已阻止系统睡眠（wakepy）")
            yield
        return
    except ImportError:
        pass

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log.info("已阻止系统睡眠（SetThreadExecutionState）")
        try:
            yield
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        return

    # TODO: Linux 用 systemd-inhibit 包一层；家庭服务器线上改走 systemd timer WakeSystem。
    log.warning("当前平台未实现阻止睡眠，机器睡过去就会错过触发时刻")
    yield


def declare_busy() -> None:
    """系统自动唤醒后有约 2 分钟的「无人值守空闲计时器」，
    需要在这个窗口内声明忙碌，否则会立刻回睡。RTC 唤醒路线才用得上。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
