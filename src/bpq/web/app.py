"""WebUI 的 HTTP 接口。

几条贯穿全文的约定：

- **路由一律写成同步 `def`。** Starlette 会把同步路由丢进线程池，于是里面那些阻塞的
  paho / ftplib 调用不会卡住事件循环。唯一的 `async def` 是 SSE 那个端点。

- **读状态的路由绝不建连接。** 一律走 `link.snapshot()` 读缓存。打印机同一时刻只
  接受一个 MQTT 连接，一个每秒被轮询的接口如果会建连，等于把连接反复踢来踢去。

- **「我们发了什么」和「机器说了什么」分开返回。** 任务里的 options 是前者，
  /api/printer 是后者，两者不混在一个对象里——五个开关里有三个根本没有对应的
  上报字段，混着给前端，界面上就会长出一种编造的确定性。
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse

from bpq.config import Config
from bpq.journal import Journal
from bpq.link import PrinterLink
from bpq.models import PrintOptions, Task
from bpq.service import ServiceError, SubmitRequest, TaskPatch, TaskService
from bpq.web import auth as auth_mod
from bpq.web.events import EventBroker
from bpq.web.static import mount_frontend

log = logging.getLogger(__name__)

# 上传大小上限。带 Auxiliaries/ 的原始 3mf 能有几十 MB，但 200 MB 之外基本可以
# 断定是传错文件了。（我们收下之后会立刻剥掉 Auxiliaries/。）
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# 配置被改动后的回调：daemon 用它把新配置换给进程里所有持有者。
ConfigChanged = Callable[[Config], None]

# 打印机状态的中文对照，只用来拼给人看的 detail 文案——返回体里的 state 字段
# 保持英文原值不变，前端还要拿它做逻辑判断（比如按状态决定按钮能不能点）。
_STATE_LABELS = {
    "IDLE": "空闲",
    "RUNNING": "打印中",
    "PAUSE": "已暂停",
    "FINISH": "已完成",
    "FAILED": "上一单失败",
    "UNKNOWN": "未知",
}

# 串行化「试连」：慢路径会先把 PrinterLink 唯一的那条连接让出去、探测完再抢回来，
# 两个请求同时做这件事会互相踩（都以为自己让出前是「没让出」，都去恢复）。
_probe_lock = threading.Lock()


def create_app(
    cfg: Config,
    *,
    link: PrinterLink,
    service: TaskService,
    journal: Journal,
    broker: EventBroker | None = None,
    var_dir: str | None = None,
    on_config_change: ConfigChanged | None = None,
) -> FastAPI:
    auth_mod.check_exposure(cfg.web.host, cfg.web.password)

    from pathlib import Path

    secret = auth_mod.load_secret(Path(var_dir or Path(cfg.daemon.db_path).parent))
    throttle = auth_mod.LoginThrottle()
    broker = broker or EventBroker()

    app = FastAPI(title="bpq", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.broker = broker

    # 配置是可变的：改完要重新 load 一次，再把新的那份换给进程里所有持有者。
    # 用一个可变容器装着，好让下面的闭包都看到最新的值。
    state = {"cfg": cfg}

    def current_cfg() -> Config:
        return state["cfg"]

    def reload_config() -> Config:
        from bpq.config import load as load_config

        fresh = load_config(cfg.path)
        state["cfg"] = fresh
        if on_config_change is not None:
            on_config_change(fresh)
        return fresh

    # ---------------------------------------------------------- 鉴权

    def authed(request: Request) -> bool:
        if cfg.web.allow_local_no_auth and auth_mod.is_loopback(
            request.client.host if request.client else None
        ):
            return True
        if not cfg.web.password:
            # 没设口令且只绑回环（check_exposure 已经保证了这一点）——本机自用模式
            return True
        token = request.cookies.get(auth_mod.COOKIE_NAME, "")
        return auth_mod.verify_token(secret, token, cfg.web.password)

    def require_auth(request: Request) -> None:
        if not authed(request):
            raise HTTPException(status_code=401, detail="请先登录")

    # 需要口令的接口都挂在这个 router 上。
    # 不用「函数参数 + Depends」的写法：那样 FastAPI 会把它当成一个必需的请求参数，
    # 未登录时返回 422 而不是 401，前端就没法据此跳登录页。
    api = APIRouter(dependencies=[Depends(require_auth)])

    # ---------------------------------------------------------- 免鉴权

    @app.get("/api/health")
    def health() -> dict:
        """CLI 靠它探活。故意不返回任何敏感信息。"""
        h = link.health()
        return {
            "ok": True,
            "app": "bpq",
            "link": {"connected": h.connected, "yielded": h.yielded, "stale": h.stale},
        }

    @app.post("/api/auth/login")
    def login(request: Request, response: Response,
              password: Annotated[str, Body(embed=True)] = "") -> dict:
        ip = request.client.host if request.client else "?"
        if (wait := throttle.locked_for(ip)) > 0:
            raise HTTPException(
                status_code=429, detail=f"失败次数太多，请 {wait:.0f} 秒后再试"
            )
        if not cfg.web.password:
            return {"ok": True, "note": "未设置口令"}

        import time

        if not auth_mod.hmac.compare_digest(password, cfg.web.password):
            time.sleep(auth_mod.FAILURE_DELAY)
            throttle.record_failure(ip)
            raise HTTPException(status_code=401, detail="口令不对")

        throttle.record_success(ip)
        response.set_cookie(
            auth_mod.COOKIE_NAME,
            auth_mod.issue_token(secret, cfg.web.password, days=cfg.web.session_days),
            httponly=True,
            samesite="lax",
            max_age=cfg.web.session_days * 86400,
            path="/",
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(auth_mod.COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(request: Request) -> dict:
        return {
            "authed": authed(request),
            "password_required": bool(cfg.web.password),
        }

    # ---------------------------------------------------------- 打印机

    @api.get("/api/printer")
    def printer() -> dict:
        """完整快照。读缓存，不建连接。"""
        return {**link.snapshot().to_dict(), "link": link.health().to_dict()}

    @api.post("/api/printer/refresh")
    def refresh() -> dict:
        """主动重拉一次全量。只读查询，打印机不会有任何物理动作。"""
        link.request_pushall()
        return {"ok": True}

    @api.post("/api/printer/yield")
    def yield_link() -> dict:
        """把 MQTT 连接让给 Bambu Studio。

        定时任务不受影响——到点时 TaskRunner 会自己抢回来，并往 journal 记一笔。
        """
        link.yield_connection()
        return _link_changed()

    @api.post("/api/printer/resume")
    def resume_link() -> dict:
        link.resume_connection(reason="用户手动恢复")
        return _link_changed()

    def _link_changed() -> dict:
        """让出/抢回之后把新的 link 状态广播出去。

        只把它塞进 HTTP 响应是不够的：让出之后打印机不再上报，`patch` 事件也就
        不会再来，别的标签页（以及本页里不读响应体的地方）会一直停在「还连着」
        那个旧值上——顶栏那盏指示灯和概览页的「还没连上打印机」引导卡都要靠它。
        借用 patch 事件通道，printer 给一个空 merge-patch 表示「快照没变，只是
        link 变了」。
        """
        health = link.health().to_dict()
        broker.publish_threadsafe("patch", {"printer": {}, "link": health})
        return {"ok": True, "link": health}

    # ---------------------------------------------------------- 文件

    @api.post("/api/files")
    def upload(file: Annotated[UploadFile, File()]) -> dict:
        data = file.file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件太大（{len(data) / 1e6:.0f} MB）。"
                       "请确认这是 Bambu Studio 切好片导出的 3mf。",
            )
        try:
            info = service.accept_upload(file.filename or "upload.3mf", data)
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return info.to_dict()

    @api.get("/api/files/{file_id}")
    def file_info(file_id: str) -> dict:
        return _ok(lambda: service.find_file(file_id).to_dict())

    @api.get("/api/files/{file_id}/thumbnail")
    def thumbnail(file_id: str, plate: int) -> Response:
        data = _ok(lambda: service.thumbnail(file_id, plate))
        if not data:
            raise HTTPException(status_code=404, detail="这个盘没有预览图")
        return Response(content=data, media_type="image/png",
                        headers={"Cache-Control": "max-age=86400"})

    @api.get("/api/files/{file_id}/mapping")
    def mapping(file_id: str, plate: int | None = None) -> dict:
        return _ok(lambda: service.preview_mapping(file_id, plate).to_dict())

    # ---------------------------------------------------------- 任务

    @api.get("/api/tasks")
    def list_tasks(pending: bool = False) -> list[dict]:
        return [task_dict(t) for t in service.list_tasks(pending_only=pending)]

    @api.post("/api/tasks")
    def create_task(body: Annotated[dict, Body()]) -> dict:
        try:
            req = SubmitRequest(
                file_id=str(body["file_id"]),
                scheduled_at=_parse_when(body.get("scheduled_at")),
                plate_index=body.get("plate_index"),
                use_ams=body.get("use_ams"),
                ams_mapping=body.get("ams_mapping"),
                options=_parse_options(body.get("options")),
                remote_name=body.get("remote_name"),
                title=body.get("title", ""),
                origin="web",
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"缺少字段 {exc}") from exc

        result = _ok(lambda: service.submit(req))
        broker.publish_threadsafe("tasks", [task_dict(t) for t in service.list_tasks()])
        return {"task": task_dict(result.task), "notes": result.notes}

    @api.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = service.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"没有任务 {task_id}")
        return task_dict(task)

    @api.patch("/api/tasks/{task_id}")
    def patch_task(task_id: str, body: Annotated[dict, Body()]) -> dict:
        patch = TaskPatch(
            scheduled_at=_parse_when(body["scheduled_at"]) if "scheduled_at" in body else None,
            options=_parse_options(body["options"]) if "options" in body else None,
            ams_mapping=body.get("ams_mapping"),
        )
        task = _ok(lambda: service.update(task_id, patch))
        broker.publish_threadsafe("tasks", [task_dict(t) for t in service.list_tasks()])
        return task_dict(task)

    @api.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str, purge: bool = False) -> dict:
        """默认软取消（不变）；`purge=true` 时改成硬删已结束的任务记录。

        硬删不允许绕过取消——`service.delete()` 会对还没结束的任务抛 `ValueError`，
        这里翻成 409：pending/uploaded 背后还挂着 jobstore 的 job，直接删库会留下
        一个到点还会触发、却找不到任务记录的孤儿 job。
        """
        if purge:
            try:
                ok = service.delete(task_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if not ok:
                raise HTTPException(status_code=404, detail=f"没有任务 {task_id}")
        elif not service.cancel(task_id):
            raise HTTPException(
                status_code=409, detail=f"任务 {task_id} 不存在，或已经不能取消了"
            )
        broker.publish_threadsafe("tasks", [task_dict(t) for t in service.list_tasks()])
        return {"ok": True}

    # ---------------------------------------------------------- 其他

    @api.get("/api/journal")
    def read_journal(
        event: str | None = None,
        since: str | None = None,
        until: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """`event` 是逗号分隔的事件名列表，不给就不筛选事件。

        返回体从「裸数组」改成了带分页信息的对象——前端之前把 `GET /api/journal`
        的返回值直接当数组用，这是预期内的破坏性变更，前端会在另一张任务卡里跟上。
        """
        events = [e for e in event.split(",") if e] if event else None
        capped = min(limit, 500)
        items, total = journal.query(
            events=events, since=since, until=until, offset=offset, limit=capped
        )
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": capped,
            "events": journal.event_names(),
        }

    @api.delete("/api/journal")
    def delete_journal(before: str | None = None) -> dict:
        return {"ok": True, "deleted": journal.clear(before=before)}

    @api.get("/api/config")
    def public_config() -> dict:
        """前端要用的那部分配置。**不返回 access_code 和 password。**"""
        c = current_cfg()
        return {
            "printer": {"model": c.printer.model, "ip": c.printer.ip,
                        "serial_masked": _mask(c.printer.serial, keep=4)},
            "print_defaults": {
                "bed_leveling": c.print.bed_leveling,
                "vibration_cali": c.print.vibration_cali,
                "flow_cali": c.print.flow_cali,
                "layer_inspect": c.print.layer_inspect,
                "timelapse": c.print.timelapse,
            },
            "scheduler": {
                "upload_timing": c.scheduler.upload_timing,
                "start_after_failure": c.scheduler.start_after_failure,
                "misfire_grace_time": c.scheduler.misfire_grace_time,
            },
            "config_path": str(c.path),
        }

    # ------------------------------------------------------ 配置读写

    @api.get("/api/config/printer")
    def get_printer_config() -> dict:
        """打印机连接参数。access_code 打码——它是明文存在配置里的，
        没必要让每次刷新页面都把它完整地送一遍。"""
        pc = current_cfg().printer
        return {
            "ip": pc.ip,
            "serial": pc.serial,
            "model": pc.model,
            "access_code_set": bool(pc.access_code),
            "access_code_masked": _mask(pc.access_code),
        }

    @api.post("/api/config/printer/test")
    def test_printer_config(body: Annotated[dict, Body()]) -> dict:
        """只试连，不保存。让人在按「保存」之前就知道参数对不对。"""
        return _probe_printer(current_cfg(), body, link=link)

    @api.patch("/api/config/printer")
    def patch_printer_config(body: Annotated[dict, Body()]) -> dict:
        """改打印机连接参数。

        **先试连再落盘。** 填错 IP 却存进去、等到下次启动甚至等到半夜任务
        到点才发现连不上，是这条链路上最难查的一类问题。
        """
        probe = _probe_printer(current_cfg(), body, link=link)
        if not probe["ok"] and not body.get("force"):
            raise HTTPException(status_code=400, detail=probe["detail"])

        values = {k: body.get(k) for k in ("ip", "serial", "access_code", "model")}
        values = {k: v for k, v in values.items() if v not in (None, "")}
        if not values:
            raise HTTPException(status_code=400, detail="没有要改的内容")

        _write_config(current_cfg(), "printer", values)
        fresh = reload_config()
        # 让新参数立刻生效，不必重启 daemon
        link.reconfigure(fresh)
        broker.publish_threadsafe("config", {"reason": "printer"})
        return {"ok": True, "probe": probe, **get_printer_config()}

    @api.patch("/api/config")
    def patch_config(body: Annotated[dict, Body()]) -> dict:
        """改全局默认值。

        [print] 的五个开关只影响「跟随全局」的任务——显式设过开/关的任务不受影响，
        这正是 PrintOptions 用三态而不是布尔的原因。
        """
        wrote = False
        if isinstance(body.get("print_defaults"), dict):
            keys = ("bed_leveling", "vibration_cali", "flow_cali",
                    "layer_inspect", "timelapse")
            values: dict[str, Any] = {
                k: bool(body["print_defaults"][k])
                for k in keys if k in body["print_defaults"]
            }
            if values:
                _write_config(current_cfg(), "print", values)
                wrote = True

        if isinstance(body.get("scheduler"), dict):
            src = body["scheduler"]
            values = {}
            if "start_after_failure" in src:
                values["start_after_failure"] = bool(src["start_after_failure"])
            if "upload_timing" in src:
                if src["upload_timing"] not in ("early", "late"):
                    raise HTTPException(
                        status_code=400, detail="upload_timing 只能是 early 或 late"
                    )
                values["upload_timing"] = src["upload_timing"]
            if "misfire_grace_time" in src:
                values["misfire_grace_time"] = int(src["misfire_grace_time"])
            if values:
                _write_config(current_cfg(), "scheduler", values)
                wrote = True

        if not wrote:
            raise HTTPException(status_code=400, detail="没有要改的内容")

        reload_config()
        broker.publish_threadsafe("config", {"reason": "settings"})
        return public_config()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        if not authed(request):
            raise HTTPException(status_code=401, detail="请先登录")
        initial = {
            "printer": link.snapshot().to_dict(),
            "link": link.health().to_dict(),
            "tasks": [task_dict(t) for t in service.list_tasks()],
        }
        return StreamingResponse(
            broker.stream(initial),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # 没有这一条，中间的代理（含 Vite dev server）会把事件攒着不发
                "X-Accel-Buffering": "no",
            },
        )

    # router 必须在 mount_frontend 之前注册：那个函数会挂一个 catch-all，
    # 之后再加的路由永远匹配不到。
    app.include_router(api)
    mount_frontend(app)
    return app


# ---------------------------------------------------------------- 辅助


def _mask(value: str, keep: int = 2) -> str:
    """打码。留头留尾便于人确认「是不是我填的那个」，中间抹掉。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def _probe_printer(cfg: Config, body: dict, *, link: PrinterLink) -> dict:
    """拿一组候选参数试连一次，只读状态，不下发任何指令。

    **为什么必须先让出连接再探测，而不是直接另建一条**：CLAUDE.md 的硬红线写得很
    明白——「任何代码路径都不得在 daemon 运行期间另建 MQTT 连接」。打印机同一时刻
    只接受一个 MQTT 连接，而 daemon 一起来那条唯一的连接就一直握在 PrinterLink 手
    里。如果这里直接 `LanTransport(probe_cfg).get_state()`，就是两个连接同时抢一台
    打印机，会重现 v0.1 验收时踩过的坑：「互抢连接 → 到点读到 UNKNOWN → 打印机明明
    空闲却被放弃」。表现到这个接口上就是「改 IP/序列号一直提示连不上、保存被拒绝」，
    因为 PrinterLink 那条常驻连接和这里临时开的这条互相把对方的报文都搅乱了。

    所以分两条路：

    - **快路径**：候选参数和当前生效配置完全一致，且 PrinterLink 那条连接活着——
      这就是「什么都没改，只是想再确认一次」，直接读 `link.state()`（走的是已经
      建好的那条连接，不新开任何连接）。
    - **慢路径**：参数确有变化，或者 PrinterLink 当前没连上——这两种情况都必须
      真的去试连一次，但**必须先把 PrinterLink 那条连接让出去**（`yield_connection`），
      试完**无条件**恢复原状（`finally` 里做，不管试连成功还是失败）：原来没让出过
      就抢回来，原来就是让出状态的就保持让出，绝不能因为这次试连把用户手动让给
      Bambu Studio 的连接状态篡改掉。用一把模块级锁把整段串行化，避免两个并发的
      试连请求互相踩到对方「让出/恢复」的状态。

    没填 access_code 就沿用现有的——界面上它是打码显示的，
    要求人为了改 IP 而重新输一遍密码是没道理的。
    """
    import dataclasses

    printer = dataclasses.replace(
        cfg.printer,
        ip=str(body.get("ip") or cfg.printer.ip),
        serial=str(body.get("serial") or cfg.printer.serial),
        access_code=str(body.get("access_code") or cfg.printer.access_code),
        model=str(body.get("model") or cfg.printer.model),
    )

    if printer == cfg.printer and link.connected:
        # 快路径：参数没变、连接也活着，直接读缓存，完全不建连接。
        return _probe_result(link.state())

    # 慢路径：真的要建一条临时连接去试，先把唯一的那条让出去。
    from bpq.transport.lan import LanTransport

    with _probe_lock:
        was_yielded = link.yielded
        link.yield_connection()
        try:
            probe_cfg = dataclasses.replace(cfg, printer=printer)
            tp = LanTransport(probe_cfg)
            try:
                state = tp.get_state(timeout=8.0)
            except Exception as exc:  # noqa: BLE001 - 试连失败的原因五花八门，都要翻成人话
                return {"ok": False, "detail": f"连接失败：{exc}"}
            finally:
                with contextlib.suppress(Exception):
                    tp.close()
        finally:
            # 无条件恢复：原先没让出过就抢回来；原先就是让出状态的，保持让出。
            if not was_yielded:
                link.resume_connection(reason="配置试连结束")

    return _probe_result(state)


def _probe_result(state: Any) -> dict:
    """把一次探测得到的 PrinterState 翻成给人看的返回体。"""
    if state.value == "UNKNOWN":
        return {
            "ok": False,
            "detail": (
                "连接已建立，但未收到状态上报。常见原因：SERIAL 填错（topic 对不上就什么都收不到）、"
                "Access Code 不对、或者 Bambu Studio 正占着那唯一的 MQTT 连接。"
            ),
            "state": state.value,
        }
    label = _STATE_LABELS.get(state.value, state.value)
    return {
        "ok": True,
        "detail": f"连接成功 · 打印机当前状态：{label}（{state.value}）",
        "state": state.value,
    }


def _write_config(cfg: Config, section: str, values: dict) -> None:
    from bpq.configwrite import ConfigWriteError
    from bpq.configwrite import update as write_config

    try:
        write_config(cfg.path, section, values)
    except ConfigWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _ok(fn: Any) -> Any:
    """把 ServiceError 翻成 400。业务层的报错文案本来就是写给人看的，直接透出去。"""
    try:
        return fn()
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_when(value: Any) -> datetime:
    """接受 ISO 时间串，也接受 CLI 那套 "23:30" / "+2h" 的写法。

    复用 cli.parse_when 而不是另写一份：两个入口对「今晚 23:30」的理解必须一致，
    尤其是「已经过了就顺延到明天」这条。
    """
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="没给触发时刻")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    from bpq.cli import parse_when

    try:
        return parse_when(text)
    except Exception as exc:  # noqa: BLE001 - click 的异常类型不该漏到 HTTP 层
        raise HTTPException(status_code=400, detail=f"看不懂的时刻：{text}") from exc


def _parse_options(raw: Any) -> PrintOptions:
    """None 表示「跟随全局默认」，和 False 是两回事，别在这里折叠掉。"""
    if not isinstance(raw, dict):
        return PrintOptions()
    keys = ("bed_leveling", "vibration_cali", "flow_cali", "layer_inspect", "timelapse")
    return PrintOptions(**{
        k: (None if raw.get(k) is None else bool(raw[k]))
        for k in keys if k in raw
    })


def task_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title or t.remote_name,
        "source_path": t.source_path,
        "remote_name": t.remote_name,
        "plate": t.plate,
        "plate_index": t.plate_index,
        "md5": t.md5,
        "bed_type": t.bed_type,
        "use_ams": t.use_ams,
        "ams_mapping": t.ams_mapping,
        "mapping_source": t.mapping_source,
        "mapping_notes": t.mapping_notes,
        "filaments": [
            {"id": f.id, "type": f.type, "color": f.color,
             "rgb": f.color.lstrip("#").upper()[:6],
             "info_idx": f.info_idx, "used_g": f.used_g}
            for f in t.filaments
        ],
        # 注意这里给的是**原始的三态值**：None = 跟随全局。前端要照实显示成
        # 「跟随全局（当前：关）」而不是「关」，否则人会以为这一单显式关掉了。
        "options": {
            "bed_leveling": t.options.bed_leveling,
            "vibration_cali": t.options.vibration_cali,
            "flow_cali": t.options.flow_cali,
            "layer_inspect": t.options.layer_inspect,
            "timelapse": t.options.timelapse,
        },
        "state": t.state.value,
        "origin": t.origin,
        "scheduled_at": t.scheduled_at.isoformat(),
        "created_at": t.created_at.isoformat(),
        "triggered_at": t.triggered_at.isoformat() if t.triggered_at else None,
        "uploaded_at": t.uploaded_at.isoformat() if t.uploaded_at else None,
        "error": t.error,
        "sent_payload": t.sent_payload,
    }


__all__ = ["create_app", "task_dict"]
