"""常驻守护进程：持有 APScheduler，到点调 TaskRunner.fire。

v0.1 形态：手动 `python -m bpq daemon`（或装好后 `bpq daemon`）。
迁到家庭服务器后配 systemd unit（Restart=on-failure），见 deploy/ 里的模板。
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from bpq.config import Config
from bpq.config import load as load_config
from bpq.journal import Journal
from bpq.models import Task
from bpq.power import keep_awake
from bpq.scheduler import HEARTBEAT_SECONDS, TaskRunner, build_scheduler
from bpq.store import TaskStore

log = logging.getLogger(__name__)

JOB_FUNC = "bpq.daemon:run_task"  # APScheduler 持久化 job 时存的是这个字符串引用

LOCK_NAME = "bpq.lock"


class AlreadyRunning(RuntimeError):
    """已经有另一个 daemon 实例持有锁。"""


def lock_path_for(cfg: Config) -> Path:
    """锁文件与 jobstore 同目录：两个 daemon 争的本来就是同一份运行时数据。
    var/ 已在 .gitignore 里，不会误入库。"""
    return Path(cfg.daemon.db_path).parent / LOCK_NAME


def _lock_fd(fd: int) -> None:
    """非阻塞地独占锁住 fd 的第 0 字节；拿不到直接抛 OSError。

    为什么用 OS 级文件锁而不是 PID 文件：daemon 被强杀（任务管理器 / kill -9）时
    不会执行任何清理代码，PID 文件会残留，下次启动就永远被自己的尸体挡住；
    而 OS 级锁在进程消失时由内核释放，正是我们要的语义。
    """
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)  # msvcrt 锁的是「当前文件位置」起的 n 字节
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    """显式解锁。close(fd) 本身也会释放，这里只是让释放时机明确。"""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def single_instance(lock_path: str | Path) -> Iterator[Path]:
    """保证同一时刻只有一个 bpq daemon 在跑，拿不到锁抛 AlreadyRunning。

    这不是洁癖：打印机同一时刻只接受一个 MQTT 客户端连接，两个 daemon 会互相把
    对方踢下线，结果就是定时任务到点读不到打印机状态（printer_state=UNKNOWN）而被放弃。
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 用 os.open 而不是 open(path, "w")：后者会截断文件，
    # 在抢锁失败的那条路径上不该对别人的锁文件做任何写操作。
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock_fd(fd)
    except OSError as exc:
        os.close(fd)
        raise AlreadyRunning(
            f"已经有一个 bpq daemon 在运行了（锁文件 {path}）。\n"
            "打印机同一时刻只接受一个 MQTT 连接，同时跑两个 daemon 会互相抢连接，"
            "导致任务到点读不到打印机状态而被放弃。\n"
            "先停掉已有的那个（Ctrl-C / 结束进程）再重试。"
        ) from exc

    try:
        yield path
    finally:
        _unlock_fd(fd)
        os.close(fd)
        # 故意不删锁文件。POSIX 上 unlink 之后 inode 还活着：若此时另一个进程已经
        # 打开了这个路径却没抢到锁，而第三个进程重新创建同名文件并加锁，
        # 那两个进程锁的就是不同 inode，会同时以为自己独占。
        # 留一个 0 字节文件在 var/ 里没有任何代价，换掉这个竞态是划算的。


def run_task(task_id: str) -> None:
    """APScheduler 的 job 入口。必须是模块级函数，jobstore 才能序列化引用它。

    在 daemon 进程里跑时复用现成的 runner（也就复用了那条唯一的 MQTT 长连接）。
    v0.1 这里每次都新建连接，在 v0.2 有长连接的前提下，那等于 daemon 每次触发
    都把自己踢下线——打印机同一时刻只接受一个 MQTT 客户端。

    查不到运行时（理论上不该发生，但要有确定行为）就走 v0.1 的老路。
    """
    from bpq import runtime

    ctx = runtime.current()
    if ctx is not None:
        ctx.runner.fire(task_id)
        return

    log.warning("触发任务 %s 时没找到运行中的 daemon，退回临时建连模式", task_id)
    cfg = load_config()
    store = TaskStore(cfg.daemon.db_path)
    journal = Journal(cfg.daemon.journal_path)
    try:
        TaskRunner(cfg, store, journal).fire(task_id)
    finally:
        store.close()


def schedule_task(cfg: Config, task: Task, *, scheduler: object | None = None) -> None:
    """把任务写进共享 jobstore。CLI 与 daemon 两个进程都可以调。

    给了 scheduler 就用运行中的那个——WebUI 建的任务立刻生效，不必等下一次心跳。
    没给则沿用 v0.1 的做法：临时起一个 scheduler 写进共享 jobstore，
    daemon 在 30 秒心跳时看到它。

    注意 start(paused=True)：scheduler 未启动时 add_job 只会挂在内存 pending 列表里，
    不落盘，另一个进程就看不到。
    """
    kwargs = {
        "trigger": "date",
        "run_date": task.scheduled_at,
        "args": [task.id],
        "id": task.id,
        "replace_existing": True,
    }
    if scheduler is not None:
        scheduler.add_job(JOB_FUNC, **kwargs)  # type: ignore[attr-defined]
        return

    sched = build_scheduler(cfg)
    sched.start(paused=True)
    try:
        sched.add_job(JOB_FUNC, **kwargs)
    finally:
        sched.shutdown(wait=False)


def unschedule_task(cfg: Config, task_id: str, *, scheduler: object | None = None) -> None:
    if scheduler is not None:
        try:
            scheduler.remove_job(task_id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - job 可能已触发或不存在，不是错误
            pass
        return

    sched = build_scheduler(cfg)
    sched.start(paused=True)
    try:
        sched.remove_job(task_id)
    except Exception:  # noqa: BLE001 - job 可能已触发或不存在，不是错误
        pass
    finally:
        sched.shutdown(wait=False)


def _heartbeat() -> None:
    """空 job。它的唯一作用是让 daemon 定期重新查询 jobstore，
    从而感知 CLI 进程刚写进去的新任务。"""


def serve(cfg: Config) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # 单实例检查放在最外层：重复启动要在连打印机、建 jobstore、阻止睡眠之前就失败，
    # 否则第二个实例已经把第一个的 MQTT 连接挤掉了，再报错也晚了。
    with single_instance(lock_path_for(cfg)):
        _serve_locked(cfg)


def _serve_locked(cfg: Config) -> None:
    from bpq import runtime

    journal = Journal(cfg.daemon.journal_path)
    journal.write("daemon_start", pid_time=datetime.now().isoformat(timespec="seconds"))

    store = TaskStore(cfg.daemon.db_path)      # 顺带触发 schema 迁移
    from bpq.link import PrinterLink

    link = PrinterLink(cfg)
    # 这条注入是整个 v0.2 连接架构的落点：TaskRunner 从此借用长连接，
    # 不再自己建。daemon 里任何别的代码也都不该再调 build_transport()。
    runner = TaskRunner(cfg, store, journal, transport=link.session)

    sched = build_scheduler(cfg)
    sched.add_job(
        "bpq.daemon:_heartbeat",
        trigger="interval",
        seconds=HEARTBEAT_SECONDS,
        id="__heartbeat__",
        replace_existing=True,
    )

    stop = threading.Event()

    def _on_signal(signum, frame) -> None:  # noqa: ANN001
        log.info("收到信号 %s，准备退出", signum)
        stop.set()

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except ValueError:
        # signal 只能在主线程注册。正常启动（bpq daemon）总是在主线程，
        # 但被嵌进别的线程跑时（集成测试、以后可能的宿主进程）不该因此直接崩——
        # 那种场景下由调用方负责让 stop 事件置位。
        log.debug("不在主线程，跳过信号处理注册；退出要靠调用方置位 stop")

    service = _build_service(cfg, store, journal, runner, link, sched)
    ctx = runtime.DaemonContext(
        cfg=cfg, store=store, journal=journal, link=link, runner=runner,
        service=service, scheduler=sched, started_at=datetime.now(),
    )

    web = None
    with keep_awake(cfg.daemon.inhibit_sleep):
        link.open()
        sched.start()
        # 登记必须在 scheduler.start() 之后：一登记，到点的 job 就会走复用长连接
        # 这条路，而那要求 runner 和 link 都已经就绪。
        runtime.set_current(ctx)
        try:
            web = _start_web(cfg, link, service, journal, ctx)
            log.info("bpq daemon 已启动，jobstore=%s", cfg.daemon.db_path)
            for job in sched.get_jobs():
                if job.id != "__heartbeat__":
                    log.info("  待触发: %s @ %s", job.id, job.next_run_time)
            # 不能写成不带超时的 stop.wait()：Windows 上它是一次性的
            # WaitForSingleObject，主线程整段时间都陷在这个系统调用里，不会回到
            # 解释器循环——而 Ctrl+C 注册的信号处理函数只有解释器执行字节码时才有
            # 机会被调度。结果是 _on_signal 永远等不到运行的时机，stop.set() 从
            # 没被真正调用过，Ctrl+C 看起来毫无反应。改成短超时轮询，让主线程
            # 每秒都短暂交还控制权，信号才有落地的窗口。
            while not stop.wait(timeout=1):
                pass
        finally:
            # 反序关停：先摘掉注册表（别让触发中的 job 摸到正在拆的对象），
            # 再停 web、停调度、断连接、关库。
            runtime.set_current(None)
            if web is not None:
                web.stop()
            sched.shutdown(wait=False)
            link.close()
            store.close()

    journal.write("daemon_stop")


def _build_service(cfg: Config, store, journal, runner, link, sched):  # noqa: ANN001, ANN202
    """组装业务层。

    两个注入点是 v0.2 连接架构的关键：
      - ams_source 读的是 link 的缓存快照，**不建连接**。WebUI 每次算 AMS 映射
        都会用到它，要是这里会建连，等于把打印机的连接反复踢来踢去。
      - schedule 直接用运行中的 scheduler，于是网页上建的任务立刻生效，
        不必等下一次 30 秒心跳。
    """
    from bpq.service import TaskService

    return TaskService(
        cfg, store, journal, runner,
        ams_source=link.snapshot,
        schedule=lambda task: schedule_task(cfg, task, scheduler=sched),
        unschedule=lambda task_id: unschedule_task(cfg, task_id, scheduler=sched),
    )


def _start_web(cfg: Config, link, service, journal, ctx):  # noqa: ANN001, ANN202
    """挂上 WebUI。它跑在后台线程，主线程留着响应 Ctrl-C。

    [web] enabled = false 时整个跳过，daemon 退回 v0.1 的纯调度器形态。
    """
    if not cfg.web.enabled:
        log.info("WebUI 已关闭（config.toml 的 [web] enabled = false）")
        return None

    from bpq.web.app import create_app
    from bpq.web.auth import AuthError
    from bpq.web.events import EventBroker
    from bpq.web.server import run_in_thread, wait_until_started

    broker = EventBroker()

    def _on_config_change(fresh: Config) -> None:
        """网页上改了配置之后，把新的那份换给进程里所有持有者。

        Config 是 frozen 的，改配置 = 造一份新的替换引用。漏掉任何一个持有者，
        症状都是「界面上改了但行为没变」，而且不会报错。
        """
        ctx.replace_config(fresh)
        log.info("配置已更新并热加载（%s）", fresh.path)

    try:
        app = create_app(cfg, link=link, service=service, journal=journal,
                         broker=broker, on_config_change=_on_config_change)
    except AuthError as exc:
        # 配置本身不安全（暴露到局域网却没设口令）。daemon 本体照常跑，
        # 定时任务不受影响——只是不开网页。
        log.error("WebUI 没有启动：\n%s", exc)
        return None

    handle = run_in_thread(app, host=cfg.web.host, port=cfg.web.port)
    if not wait_until_started(handle):
        log.error("WebUI 在 10 秒内没起来，端口 %d 可能被占用了", cfg.web.port)
        handle.stop()
        return None

    # broker 会在第一个 SSE 订阅者连上时自己认领事件循环（见 events.bind），
    # 这里只需把两条状态流接上去。没人在看网页时，投递是无声的空操作。
    link.add_listener(
        lambda snap, patch: broker.publish_threadsafe(
            "patch", {"printer": patch, "link": link.health().to_dict()}
        )
    )

    # 任务状态变化。到点触发是 APScheduler 在后台线程里干的，不经过任何 HTTP 路由——
    # 没有这条通路，任务真的开打了网页上还显示「等待中」，而「到点了到底打起来没有」
    # 恰恰是这个应用最该实时告诉人的一件事。
    def _on_journal(record: dict) -> None:
        from bpq.web.app import task_dict

        broker.publish_threadsafe("journal", {"record": record})
        broker.publish_threadsafe(
            "tasks", [task_dict(t) for t in service.list_tasks()]
        )

    journal.set_listener(_on_journal)

    log.info("WebUI: %s", handle.url)
    if cfg.web.host not in ("127.0.0.1", "localhost", "::1"):
        log.info("  局域网里的其他设备可以用本机 IP 加 :%d 打开", cfg.web.port)
    return handle
