"""CLI。v0.1 的任务提交入口。

    bpq submit model.gcode.3mf --at 23:30
    bpq ls
    bpq cancel <id>
    bpq daemon
    bpq status

「切完片之后做什么动作把任务交出去」这一步，v0.1 的答案就是上面第一行。
watch folder（用 watchdog 监听一个目录，切完直接拖进去）是快速跟进项，不在 v0.1。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click

from bpq import __version__
from bpq.config import Config, ConfigError
from bpq.config import load as load_config
from bpq.journal import Journal
from bpq.models import FilamentRef, PrintOptions, Task
from bpq.scheduler import TaskRunner
from bpq.store import TaskStore


def parse_when(value: str, *, now: datetime | None = None) -> datetime:
    """解析触发时刻。支持：

        23:30                 → 今天的 23:30，已过则顺延到明天
        2026-08-25 23:30      → 绝对时刻
        +90m / +2h            → 相对现在（方便测试，不是主要用法）
    """
    now = now or datetime.now()
    value = value.strip()

    m = re.fullmatch(r"\+(\d+)([mh])", value)
    if m:
        n = int(m.group(1))
        return now + (timedelta(minutes=n) if m.group(2) == "m" else timedelta(hours=n))

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if m:
        target = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        # 「今晚 23:30」在 23:40 提交时应指明天，不能立刻触发。
        return target if target > now else target + timedelta(days=1)

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.replace(year=now.year) if fmt == "%m-%d %H:%M" else parsed

    raise click.BadParameter(f"无法解析时刻 {value!r}；用 23:30 / 2026-08-25 23:30 / +2h")


def _load(ctx: click.Context) -> Config:
    try:
        return load_config(ctx.obj.get("config"))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _daemon(ctx: click.Context, cfg: Config):  # noqa: ANN202
    """daemon 在跑就返回它的客户端，否则返回 None。

    走 daemon 的好处不只是省一次连接：它手里有现成的状态快照，
    而 CLI 自己连一次要等首个全量报文（1–3 秒）。
    """
    if ctx.obj.get("no_daemon"):
        return None
    from bpq.client import DaemonClient

    client = DaemonClient(cfg)
    return client if client.probe() else None


def _refuse_if_daemon_holds_lock(cfg: Config) -> None:
    """直连之前确认真的没有 daemon 占着那唯一的 MQTT 连接。

    有一种要命的中间态：daemon 在跑，但它的 web 服务没起来（端口被占、
    配置里关了、或者刚刚崩了）。这时 probe() 失败，CLI 会以为没有 daemon 而去直连，
    结果两边互抢连接——恰恰是这次改动想消灭的那件事。

    文件锁比 HTTP 探活可靠：它就是 daemon 用来保证单实例的那把锁。
    """
    from bpq.daemon import AlreadyRunning, lock_path_for, single_instance

    try:
        with single_instance(lock_path_for(cfg)):
            return  # 拿到了锁，说明确实没有 daemon
    except AlreadyRunning:
        raise click.ClickException(
            "检测到 daemon 正在运行（拿不到 var/bpq.lock），但连不上它的 web 服务。\n"
            "打印机同一时刻只接受一个 MQTT 连接，现在直连会和 daemon 互相抢线，\n"
            "所以这里拒绝执行。\n"
            "先确认 config.toml 的 [web] 段是否 enabled、端口是否被占，"
            "或者停掉 daemon 再重试。"
        ) from None


def _force_utf8_output() -> None:
    """把 stdout/stderr 切成 UTF-8。

    Windows 控制台默认是 GBK，而这个项目的输出里有中文标点和 ⚠——
    match_ams 在「颜色差得远」时给的提示就带这个符号，撞上就是一句
    UnicodeEncodeError 加一屏栈回溯，而不是那条本该看到的警告。
    最讽刺的是：那条警告恰恰是唯一能拦住「打错料」的东西。

    errors="replace" 是兜底：万一终端字体渲染不了某个字符，显示成问号
    也比让整条命令崩掉强。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="bpq")
@click.option("--config", "config_path", type=click.Path(), help="指定 config.toml 路径")
@click.option("--no-daemon", is_flag=True,
              help="不走 daemon，直接连打印机（排障用；daemon 在跑时会互抢连接）")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, no_daemon: bool) -> None:
    """拓竹 A1 定时静默打印调度器。"""
    _force_utf8_output()
    ctx.ensure_object(dict)
    ctx.obj["config"] = config_path
    ctx.obj["no_daemon"] = no_daemon


# 每单打印参数。default=None 表示「跟随 config.toml 的 [print] 默认」——
# 这不是偷懒：提交时不把它固化成布尔值，之后改了全局默认还能对未触发的任务生效。
_OPTION_FLAGS = [
    ("bed_leveling", "--bed-leveling/--no-bed-leveling", "自动热床调平"),
    ("vibration_cali", "--vibration-cali/--no-vibration-cali", "振动补偿（启动时最吵的一段）"),
    ("flow_cali", "--flow-cali/--no-flow-cali", "动态流量校准"),
    ("layer_inspect", "--layer-inspect/--no-layer-inspect", "层间检查"),
    ("timelapse", "--timelapse/--no-timelapse", "延时摄影"),
]


def _print_option_options(fn):  # noqa: ANN001, ANN202
    """把五个开关批量挂成 click 选项，省掉五段一模一样的装饰器。"""
    for name, spec, help_text in reversed(_OPTION_FLAGS):
        fn = click.option(
            spec, name, default=None, help=f"{help_text}（不指定则跟随全局默认）"
        )(fn)
    return fn


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--at", "when", required=True, help="触发时刻，如 23:30")
@click.option("--plate", "plate_no", type=int, default=None,
              help="用哪个盘（盘号）。默认从 3mf 里读；文件含多个盘时必填")
@click.option("--no-ams", is_flag=True, help="不走 AMS，用外部料")
@click.option("--name", "remote_name", default=None, help="打印机存储上的文件名，默认同源文件名")
@_print_option_options
@click.pass_context
def submit(ctx: click.Context, file: Path, when: str, plate_no: int | None,
           no_ams: bool, remote_name: str | None, **flags: bool | None) -> None:
    """提交一个定时打印任务。

    plate 路径与 AMS 映射都从 3mf 里读出来——手填这两个是错误的主要来源。
    """
    from bpq import threemf
    from bpq.daemon import schedule_task

    cfg = _load(ctx)
    try:
        mf = threemf.inspect(file)
        plate = mf.plate(plate_no)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    use_ams = plate.needs_ams and not no_ams
    mapping: list[int] = []
    notes: list[str] = []
    if use_ams:
        client = _daemon(ctx, cfg)
        if client is not None:
            # daemon 手里有现成的快照，问它一句就够了——不必再建一条 MQTT 连接，
            # 更不会把 daemon 自己踢下线。
            trays = _trays_from_api(client.get("/api/printer"))
        else:
            _refuse_if_daemon_holds_lock(cfg)
            click.echo("daemon 没在跑，本次直接连打印机读 AMS。")
            from bpq.transport import build as build_transport
            with build_transport(cfg) as tp:
                # get_state() 内部已经等过首个 pushall 全量报文，这里不需要再 sleep——
                # 那个约定写在 transport/base.py 里，v0.1 曾因为漏等而让任务到点必然失败。
                tp.get_state()
                trays = tp.get_ams_trays()
        mapping, notes = threemf.match_ams(
            plate, trays, external_id=cfg.print.external_spool_id
        )
        for n in notes:
            click.echo(f"  {n}")

    if mf.aux_bytes:
        # FTPS 只有 ~46 KB/s，带 Auxiliaries/ 的原始 3mf 能传上十分钟。
        click.echo(
            f"提示：文件里有 {mf.aux_bytes / 1e6:.1f} MB 的 Auxiliaries/（装配说明、模型图），"
            f"打印机用不到；直传约需 {mf.aux_bytes / 46_000 / 60:.0f} 分钟。"
        )

    task = Task(
        source_path=str(file.resolve()),
        scheduled_at=parse_when(when),
        remote_name=remote_name or file.name,
        plate=plate.gcode_path,
        plate_index=plate.index,
        md5=plate.md5,
        bed_type=plate.bed_type,
        use_ams=use_ams,
        ams_mapping=mapping,
        options=PrintOptions(**flags),
        filaments=[
            FilamentRef(id=f.id, type=f.type, color=f.color,
                        info_idx=f.info_idx, used_g=f.used_g)
            for f in plate.filaments
        ],
        mapping_notes=notes,
        title=file.stem,
        origin="cli",
    )
    store = TaskStore(cfg.daemon.db_path)
    journal = Journal(cfg.daemon.journal_path)
    runner = TaskRunner(cfg, store, journal)
    try:
        runner.submit(task)
        schedule_task(cfg, task)
    finally:
        store.close()

    click.echo(f"已受理 {task.id}  {file.name}")
    click.echo(f"触发时刻 {task.scheduled_at:%Y-%m-%d %H:%M}")
    click.echo(f"plate    {task.plate}")
    click.echo(f"bed_type {task.bed_type}")
    click.echo(f"AMS      {'ams_mapping=' + str(task.ams_mapping) if task.use_ams else '不使用'}")
    if plate.prediction_sec:
        mins, secs = divmod(int(plate.prediction_sec), 60)
        click.echo(f"预计     {mins}m{secs:02d}s   {plate.weight_g:g}g")
    resolved = task.options.resolve(cfg.print)
    click.echo(
        "参数     " + "  ".join(
            f"{label}={'开' if getattr(resolved, name) else '关'}"
            f"{'' if getattr(task.options, name) is not None else '(全局)'}"
            for name, _, label in _OPTION_FLAGS
        )
    )
    if cfg.scheduler.upload_timing == "early":
        click.echo("文件已静默传到打印机存储；在触发前它不会有任何动作。")
    click.echo("提醒：daemon 必须在触发时刻处于运行状态（bpq daemon）。")


@main.command("ls")
@click.option("--all", "show_all", is_flag=True, help="连同已完成/已取消的一起列出")
@click.pass_context
def list_tasks(ctx: click.Context, show_all: bool) -> None:
    """列出任务。"""
    cfg = _load(ctx)
    store = TaskStore(cfg.daemon.db_path)
    try:
        tasks = store.list(pending_only=not show_all)
    finally:
        store.close()

    if not tasks:
        click.echo("没有任务。")
        return
    for t in tasks:
        line = (f"{t.id}  {t.scheduled_at:%m-%d %H:%M}  {t.state.value:<9}  "
                f"{Path(t.source_path).name}")
        click.echo(line + (f"   ({t.error})" if t.error else ""))


@main.command()
@click.argument("task_id")
@click.pass_context
def cancel(ctx: click.Context, task_id: str) -> None:
    """在触发前反悔。"""
    from bpq.daemon import unschedule_task

    cfg = _load(ctx)
    client = _daemon(ctx, cfg)
    if client is not None:
        from bpq.client import DaemonError

        try:
            client.delete(f"/api/tasks/{task_id}")
        except DaemonError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"已取消 {task_id}")
        return

    store = TaskStore(cfg.daemon.db_path)
    journal = Journal(cfg.daemon.journal_path)
    try:
        ok = TaskRunner(cfg, store, journal).cancel(task_id)
    finally:
        store.close()
    if not ok:
        raise click.ClickException(f"任务 {task_id} 不存在或已经不可取消")
    unschedule_task(cfg, task_id)
    click.echo(f"已取消 {task_id}")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """读打印机当前状态。

    daemon 在跑就问它要（它手里有现成的快照）；没跑才自己连一次打印机。
    v0.1 这里总是自己连，daemon 常连之后两边会互相踢线。
    """
    cfg = _load(ctx)
    client = _daemon(ctx, cfg)

    if client is not None:
        _print_status(client.get("/api/printer"))
        return

    _refuse_if_daemon_holds_lock(cfg)
    click.echo("daemon 没在跑，本次直接连打印机。\n")
    from bpq.transport import build as build_transport

    with build_transport(cfg) as tp:
        # get_state() 内部已等 pushall 全量回来，这里不用再 sleep。
        tp.get_state()
        _print_status({**tp.get_snapshot().to_dict(), "link": {}})


def _print_status(d: dict) -> None:
    job, temps, ams = d.get("job", {}), d.get("temps", {}), d.get("ams", {})
    state = job.get("gcode_state", "UNKNOWN")
    names = {"IDLE": "空闲", "RUNNING": "打印中", "PAUSE": "已暂停",
             "FINISH": "已完成", "FAILED": "上一单失败", "UNKNOWN": "未知"}
    click.echo(f"状态     {names.get(state, state)}（{state}）")

    if job.get("subtask_name"):
        click.echo(f"当前     {job['subtask_name']}")
    if state == "RUNNING":
        click.echo(f"进度     {job.get('percent', 0)}%  "
                   f"第 {job.get('layer_num')}/{job.get('total_layers')} 层  "
                   f"剩余 {job.get('remaining_min')} 分钟")
        if job.get("stage"):
            click.echo(f"阶段     {job['stage']}")

    def t(v: object, tgt: object) -> str:
        return f"{v}°" + (f" / {tgt}°" if tgt else "")

    click.echo(f"喷嘴     {t(temps.get('nozzle'), temps.get('nozzle_target'))}")
    click.echo(f"热床     {t(temps.get('bed'), temps.get('bed_target'))}")

    for unit in ams.get("units", []):
        click.echo(f"AMS {chr(65 + unit['unit_id'])}    湿度 {unit.get('humidity')} 档")
        for tray in unit.get("trays", []):
            mark = " ←在用" if ams.get("tray_now") == tray["global_id"] else ""
            click.echo(f"  {tray['label']:<12} #{tray['rgb']}  {tray['info_idx']:<8}"
                       f" 剩余 {tray['remain']}%{mark}")
    if ams.get("external"):
        e = ams["external"]
        click.echo(f"  外置料      #{e['rgb']}  {e['info_idx']}")

    for h in job.get("hms", []):
        click.echo(f"⚠ HMS {h['key']}（{h['severity']}）{h['url']}")

    link = d.get("link") or {}
    if link.get("yielded"):
        click.echo("\n连接已让给 Bambu Studio。定时任务到点会自动抢回。")
    if d.get("stale"):
        click.echo("\n⚠ 太久没收到打印机的报文，上面的读数可能已经过时。")


def _trays_from_api(d: dict) -> dict:
    """把 /api/printer 的 AMS 部分转成 match_ams 认的形状。"""
    from bpq.models import AmsTray

    out = {}
    trays = [t for u in d.get("ams", {}).get("units", []) for t in u.get("trays", [])]
    if d.get("ams", {}).get("external"):
        trays.append(d["ams"]["external"])
    for t in trays:
        out[t["global_id"]] = AmsTray(
            id=t["global_id"], type=t["tray_type"], color=t["color"],
            info_idx=t["info_idx"], remain=t["remain"], k=t["k"],
            unit_id=t["unit_id"], slot=t["slot"], is_external=t["is_external"],
        )
    return out


@main.command("log")
@click.option("-n", "limit", default=20, show_default=True, help="显示最近 N 条")
@click.pass_context
def show_log(ctx: click.Context, limit: int) -> None:
    """看日志——排查「为什么那晚没打起来」。"""
    cfg = _load(ctx)
    for rec in Journal(cfg.daemon.journal_path).read(limit=limit):
        extra = " ".join(f"{k}={v}" for k, v in rec.items() if k not in ("ts", "event"))
        click.echo(f"{rec['ts']}  {rec['event']:<10} {extra}")


@main.command()
@click.pass_context
def web(ctx: click.Context) -> None:
    """显示 WebUI 的地址。"""
    cfg = _load(ctx)
    if not cfg.web.enabled:
        raise click.ClickException(
            "WebUI 在 config.toml 的 [web] 段里被关掉了（enabled = false）。"
        )

    host = cfg.web.host
    shown = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{shown}:{cfg.web.port}"

    from bpq.client import DaemonClient

    running = DaemonClient(cfg).probe()
    click.echo(f"WebUI  {url}")
    if host in ("0.0.0.0", "::"):
        click.echo("       局域网里的其他设备用本机 IP 加同一个端口访问")
    click.echo(f"口令   {'已设置' if cfg.web.password else '未设置'}")
    if running:
        click.echo("daemon 正在运行，现在就能打开。")
    else:
        click.echo("daemon 没在跑。先执行 bpq daemon。")


@main.command()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """启动常驻守护进程。必须在触发时刻保持运行。"""
    from bpq.daemon import AlreadyRunning, serve

    try:
        serve(_load(ctx))
    except AlreadyRunning as exc:
        # 这是用户操作失误（多开），不是 bug，不该甩一屏栈回溯
        raise click.ClickException(str(exc)) from exc


__all__ = ["main", "parse_when"]

if __name__ == "__main__":
    sys.exit(main())
