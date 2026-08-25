"""daemon 进程内的运行时注册表。

存在理由是一个很具体的问题：APScheduler 的 job 存在 jobstore 里的只是一个字符串
`"bpq.daemon:run_task"`，到点时它会以模块级函数的方式被调用，拿不到 daemon 里
那些活的对象。v0.1 靠 run_task 自己 load_config() + 新建连接来绕过——但在有长连接
的 v0.2 里，那等于 daemon 每次触发都把自己踢下线。

于是需要一个进程级的落脚点：daemon 启动时把自己登记进来，run_task 到点时先查一下，
查到就复用现成的连接和 store，查不到（比如被非 daemon 进程调用）走 v0.1 的老路。

刻意做成模块级单例而不是往 job 的 args 里塞对象：jobstore 要序列化 args，
活的连接和数据库句柄根本序列化不了。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

    from bpq.config import Config
    from bpq.journal import Journal
    from bpq.link import PrinterLink
    from bpq.scheduler import TaskRunner
    from bpq.service import TaskService
    from bpq.store import TaskStore


@dataclass
class DaemonContext:
    """一个正在运行的 daemon 的全部活对象。"""

    cfg: Config
    store: TaskStore
    journal: Journal
    link: PrinterLink
    runner: TaskRunner
    service: TaskService
    scheduler: BackgroundScheduler
    started_at: datetime
    fake: bool = False        # 是否在假打印机模式下——界面要显著提示

    def uptime_seconds(self) -> float:
        return (datetime.now() - self.started_at).total_seconds()

    def replace_config(self, cfg: Config) -> None:
        """换掉整个进程共用的那份配置。

        Config 是 frozen 的，改配置意味着造一份新的再替换引用。持有旧引用的
        对象要一个个换过去——漏掉任何一个，症状都是「界面上改了但行为没变」，
        而且不会报错。所以这件事集中在这里做，不散落在各个路由里。
        """
        self.cfg = cfg
        self.runner.cfg = cfg
        self.service.cfg = cfg
        self.link.cfg = cfg


_lock = threading.RLock()
_current: DaemonContext | None = None


def set_current(ctx: DaemonContext | None) -> None:
    """daemon 启动时登记自己，退出时传 None 注销。"""
    global _current
    with _lock:
        _current = ctx


def current() -> DaemonContext | None:
    """取当前进程里运行着的 daemon。不在 daemon 进程里就是 None。"""
    with _lock:
        return _current
