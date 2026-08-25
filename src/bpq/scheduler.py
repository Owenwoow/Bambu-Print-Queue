"""调度：APScheduler + SQLite jobstore，一次性 date trigger。

注意这一层是整个项目里最不需要担心的部分——闭着眼睛也能写出来的 CRUD。
真正的风险在 transport/lan.py 那三件事能不能跑通。先验通道，再打磨这里。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from bpq.config import Config
from bpq.journal import Journal
from bpq.models import Task, TaskState
from bpq.store import TaskStore
from bpq.transport import build as build_transport
from bpq.transport.base import PrinterTransport, TransportError

log = logging.getLogger(__name__)

# 「借一条连接来用」的工厂。默认是 build_transport（用完就断），
# daemon 注入 PrinterLink.session（复用长连接，退出时不关）。
TransportFactory = Callable[[], AbstractContextManager[PrinterTransport]]

HEARTBEAT_SECONDS = 30  # daemon 借它感知别的进程（CLI）新写进 jobstore 的任务


def build_scheduler(cfg: Config, *, background: bool = True):  # noqa: ANN201
    """构造一个共享同一份 SQLite jobstore 的 scheduler。

    CLI 与 daemon 是两个进程，靠这个共享 jobstore 通信：
    CLI 写 job，daemon 在下一次心跳时看到它。
    """
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    if background:
        from apscheduler.schedulers.background import BackgroundScheduler as Sched
    else:
        from apscheduler.schedulers.base import BaseScheduler as Sched  # type: ignore[assignment]

    url = f"sqlite:///{cfg.daemon.db_path}"
    return Sched(
        jobstores={"default": SQLAlchemyJobStore(url=url)},
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": cfg.scheduler.misfire_grace_time,
            "max_instances": 1,
        },
    )


class TaskRunner:
    """到点之后真正干活的那段逻辑。

    默认语义（项目框架已定）：
      - 机器不空闲 → 放弃，写日志。不做「十分钟后再试」。
      - 无论成败都写日志。
    """

    def __init__(
        self,
        cfg: Config,
        store: TaskStore,
        journal: Journal,
        *,
        transport: TransportFactory | None = None,
    ) -> None:
        """transport 是一个「借一条连接来用」的上下文管理器工厂。

        默认值保持 v0.1 的行为（每次用完就断），daemon 会注入 PrinterLink.session
        来复用那条唯一的长连接。做成注入而不是让 TaskRunner 自己去查全局，
        是为了让它在测试里依然可以完全离线地跑。
        """
        self.cfg = cfg
        self.store = store
        self.journal = journal
        self._transport: TransportFactory = transport or (
            lambda: build_transport(self.cfg)
        )

    def submit(self, task: Task) -> Task:
        """受理任务。upload_timing=early 时当场静默上传。"""
        self.store.add(task)
        self.journal.write(
            "submitted",
            task=task.id,
            file=task.source_path,
            scheduled_at=task.scheduled_at.isoformat(timespec="seconds"),
        )
        if self.cfg.scheduler.upload_timing == "early":
            self._upload(task)
        return task

    def _upload(self, task: Task) -> None:
        from pathlib import Path

        try:
            with self._transport() as tp:
                tp.upload(Path(task.source_path), task.remote_name or Path(task.source_path).name)
        except (TransportError, OSError) as exc:
            self.store.set_state(task.id, TaskState.FAILED, error=str(exc))
            self.journal.write("failed", task=task.id, stage="upload", reason=str(exc))
            raise
        self.store.set_state(task.id, TaskState.UPLOADED, uploaded_at=datetime.now())
        self.journal.write("uploaded", task=task.id, remote=task.remote_name)

    def fire(self, task_id: str) -> None:
        """到点触发。这个函数由 APScheduler 在 daemon 进程里调用。"""
        task = self.store.get(task_id)
        if task is None:
            log.error("触发了不存在的任务 %s", task_id)
            return
        if task.state in (TaskState.CANCELLED, TaskState.STARTED):
            log.info("任务 %s 状态为 %s，跳过", task_id, task.state.value)
            return

        now = datetime.now()
        self.journal.write("triggered", task=task.id)
        self._reclaim_connection(task.id)

        try:
            with self._transport() as tp:
                state = tp.get_state()
                ok = state.is_idle or (
                    state.needs_attention and self.cfg.scheduler.start_after_failure
                )
                if not ok:
                    reason = (
                        "上一单以 FAILED 收场，板子可能没清"
                        if state.needs_attention
                        else f"printer_state={state.value}"
                    )
                    self.store.set_state(
                        task.id, TaskState.ABORTED, error=reason, triggered_at=now,
                    )
                    self.journal.write("aborted", task=task.id,
                                       printer_state=state.value, reason=reason)
                    return

                if task.state is not TaskState.UPLOADED:
                    # upload_timing=late，或 early 上传失败后的补传
                    self._upload(task)

                sent = tp.start(task)
        except (TransportError, OSError) as exc:
            self.store.set_state(task.id, TaskState.FAILED, error=str(exc), triggered_at=now)
            self.journal.write("failed", task=task.id, stage="start", reason=str(exc))
            return

        # 把实际下发的 payload 留下来：这条链路上「指令发出去了但打印机行为不对」
        # 是最难查的一类问题，存了就不必从复现开始查。
        self.store.set_state(
            task.id, TaskState.STARTED, triggered_at=now, sent_payload=sent
        )
        self.journal.write("started", task=task.id)

    def _reclaim_connection(self, task_id: str) -> None:
        """到点了，把可能被让给 Studio 的连接抢回来。

        「让出连接」是给人用 Bambu Studio 方便的，但它绝不能变成一个能让定时任务
        静默失灵的开关——到点触发的优先级高于让出。真的抢回来了就记一笔日志，
        免得事后看到「Studio 突然断了」时不知道为什么。
        """
        from bpq import runtime

        ctx = runtime.current()
        if ctx is None:
            return
        was_yielded = ctx.link.yielded
        ctx.link.resume_connection(reason=f"任务 {task_id} 到点")
        if was_yielded:
            self.journal.write("connection_reclaimed", task=task_id)

    def cancel(self, task_id: str) -> bool:
        """触发前反悔。"""
        task = self.store.get(task_id)
        if task is None or task.state not in (TaskState.PENDING, TaskState.UPLOADED):
            return False
        self.store.set_state(task_id, TaskState.CANCELLED)
        self.journal.write("cancelled", task=task_id)
        # TODO: 已 early 上传的文件还躺在打印机存储里，是否顺手删掉？
        #       删要走 FTPS DELE，先确认 A1 允许。v0.1 先留着不管，不影响静默。
        return True
