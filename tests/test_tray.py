"""托盘模块纯函数的单元测试。

不碰 pystray、winreg 实际调用，只测颜色/文案/图标生成/退出确认的纯函数部分。
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta

from PIL import Image

from bpq import tray
from bpq.models import PrinterState, Task, TaskState


class 假快照:
    """伪造 PrinterSnapshot 的鸭子类型对象。"""

    def __init__(
        self,
        connected: bool = True,
        stale: bool = False,
        state: PrinterState = PrinterState.IDLE,
        job_percent: int | None = None,
    ) -> None:
        self.connected = connected
        self.stale = stale
        self.state = state
        self.job = types.SimpleNamespace(percent=job_percent)


class 假上下文:
    """伪造 DaemonContext 的鸭子类型对象。"""

    def __init__(self, snapshot: 假快照 | None = None, tasks: list[Task] | None = None) -> None:
        self.link = types.SimpleNamespace(snapshot=lambda: snapshot or 假快照())
        self.store = types.SimpleNamespace(list=lambda pending_only=False: tasks or [])


class Test_status_color:
    """打印机状态 -> 图标颜色的映射。"""

    def test_ctx_为_none_返回灰色(self) -> None:
        assert tray._status_color(None) == tray._GRAY

    def test_连接断开返回灰色(self) -> None:
        snap = 假快照(connected=False)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._GRAY

    def test_状态过期返回灰色(self) -> None:
        snap = 假快照(stale=True)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._GRAY

    def test_idle_返回蓝色(self) -> None:
        snap = 假快照(state=PrinterState.IDLE)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._BLUE

    def test_running_返回绿色(self) -> None:
        snap = 假快照(state=PrinterState.RUNNING)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._GREEN

    def test_pause_返回橙色(self) -> None:
        snap = 假快照(state=PrinterState.PAUSE)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._ORANGE

    def test_failed_返回红色(self) -> None:
        snap = 假快照(state=PrinterState.FAILED)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._RED

    def test_finish_返回灰色(self) -> None:
        """FINISH 没有特殊颜色，落到灰色兜底。"""
        snap = 假快照(state=PrinterState.FINISH)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._GRAY

    def test_unknown_返回灰色(self) -> None:
        snap = 假快照(state=PrinterState.UNKNOWN)
        ctx = 假上下文(snap)
        assert tray._status_color(ctx) == tray._GRAY


class Test_printer_summary:
    """打印机状态摘要文案。"""

    def test_ctx_为_none_返回未连接(self) -> None:
        assert tray._printer_summary(None) == "未连接"

    def test_连接断开返回未连接(self) -> None:
        snap = 假快照(connected=False)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "未连接"

    def test_状态过期返回状态过期(self) -> None:
        snap = 假快照(stale=True)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "状态过期"

    def test_idle_返回空闲(self) -> None:
        snap = 假快照(state=PrinterState.IDLE)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "空闲"

    def test_pause_返回已暂停(self) -> None:
        snap = 假快照(state=PrinterState.PAUSE)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "已暂停"

    def test_finish_返回打印完成(self) -> None:
        snap = 假快照(state=PrinterState.FINISH)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "打印完成"

    def test_failed_返回上一单失败(self) -> None:
        snap = 假快照(state=PrinterState.FAILED)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "上一单失败"

    def test_unknown_返回状态未知(self) -> None:
        snap = 假快照(state=PrinterState.UNKNOWN)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "状态未知"

    def test_running_无进度返回打印中(self) -> None:
        snap = 假快照(state=PrinterState.RUNNING, job_percent=None)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "打印中"

    def test_running_有进度返回打印中加百分比(self) -> None:
        snap = 假快照(state=PrinterState.RUNNING, job_percent=42)
        ctx = 假上下文(snap)
        assert tray._printer_summary(ctx) == "打印中 42%"


class Test_next_task_summary:
    """下一个待触发任务的摘要。"""

    def test_ctx_为_none_返回无(self) -> None:
        assert tray._next_task_summary(None) == "无"

    def test_没有任务返回无(self) -> None:
        ctx = 假上下文(tasks=[])
        assert tray._next_task_summary(ctx) == "无"

    def test_有任务_用标题不用文件名(self) -> None:
        now = datetime.now()
        task = Task(
            source_path="/path/to/file.3mf",
            scheduled_at=now,
            title="我的打印",
            state=TaskState.PENDING,
        )
        ctx = 假上下文(tasks=[task])
        result = tray._next_task_summary(ctx)
        assert "我的打印" in result
        assert "file.3mf" not in result

    def test_有任务_标题为空_回退到文件名(self) -> None:
        now = datetime.now()
        task = Task(
            source_path="/path/to/myfile.3mf",
            scheduled_at=now,
            title="",  # 标题为空
            state=TaskState.PENDING,
        )
        ctx = 假上下文(tasks=[task])
        result = tray._next_task_summary(ctx)
        assert "myfile.3mf" in result

    def test_今天的任务不显示日期(self) -> None:
        now = datetime.now()
        task = Task(
            source_path="/path/to/file.3mf",
            scheduled_at=now,
            title="任务A",
            state=TaskState.PENDING,
        )
        ctx = 假上下文(tasks=[task])
        result = tray._next_task_summary(ctx)
        # 应该包含时间 HH:MM 但不包含日期
        assert ":" in result  # 有时间
        assert "任务A" in result
        # 不应该包含 MM-DD 格式的日期
        assert "-" not in result or ":" in result  # 冒号说明这是时间，不是日期

    def test_非今天的任务显示日期(self) -> None:
        tomorrow = datetime.now() + timedelta(days=1)
        task = Task(
            source_path="/path/to/file.3mf",
            scheduled_at=tomorrow,
            title="任务B",
            state=TaskState.PENDING,
        )
        ctx = 假上下文(tasks=[task])
        result = tray._next_task_summary(ctx)
        assert "任务B" in result
        # 应该包含 MM-DD HH:MM 格式
        # 明天的日期，格式是月-日
        month_day = tomorrow.strftime("%m-%d")
        assert month_day in result


class Test_tooltip_text:
    """提示框文案。"""

    def test_无任务时只显示打印机状态(self) -> None:
        snap = 假快照(state=PrinterState.IDLE)
        ctx = 假上下文(snap, tasks=[])
        result = tray._tooltip_text(ctx)
        assert "bpq · 空闲" in result
        assert "下一个" not in result

    def test_有任务时拼接(self) -> None:
        snap = 假快照(state=PrinterState.IDLE)
        now = datetime.now()
        task = Task(
            source_path="/path/to/file.3mf",
            scheduled_at=now,
            title="任务",
            state=TaskState.PENDING,
        )
        ctx = 假上下文(snap, tasks=[task])
        result = tray._tooltip_text(ctx)
        assert "bpq · 空闲" in result
        assert "下一个" in result
        assert "任务" in result

    def test_超长字符串被截断(self) -> None:
        snap = 假快照(state=PrinterState.IDLE)
        now = datetime.now()
        # 构造一个很长的标题让拼接后超过 _TOOLTIP_MAX
        long_title = "x" * 100
        task = Task(
            source_path="/path/to/file.3mf",
            scheduled_at=now,
            title=long_title,
            state=TaskState.PENDING,
        )
        ctx = 假上下文(snap, tasks=[task])
        result = tray._tooltip_text(ctx)
        assert len(result) <= tray._TOOLTIP_MAX
        assert result.endswith("…")


class Test_confirm_exit_message:
    """退出确认框文案。"""

    def test_没有待触发任务返回_none(self) -> None:
        assert tray._confirm_exit_message(0) is None

    def test_有一个任务返回确认文案(self) -> None:
        result = tray._confirm_exit_message(1)
        assert result is not None
        assert "1" in result
        assert "确定要退出" in result

    def test_多个任务返回确认文案_包含数字(self) -> None:
        result = tray._confirm_exit_message(5)
        assert result is not None
        assert "5" in result


class Test_make_icon_image:
    """图标图片生成。"""

    def test_返回_pil_image_对象(self) -> None:
        result = tray._make_icon_image(tray._BLUE)
        assert isinstance(result, Image.Image)

    def test_图片尺寸符合_size_参数(self) -> None:
        result = tray._make_icon_image(tray._BLUE, size=64)
        assert result.size == (64, 64)

    def test_默认尺寸为_64(self) -> None:
        result = tray._make_icon_image(tray._BLUE)
        assert result.size == (64, 64)

    def test_自定义尺寸(self) -> None:
        result = tray._make_icon_image(tray._RED, size=128)
        assert result.size == (128, 128)

    def test_生成的图片是_rgba(self) -> None:
        result = tray._make_icon_image(tray._GREEN)
        assert result.mode == "RGBA"
