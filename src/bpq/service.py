"""业务层：把「提交一个定时打印任务」这件事收敛到一处。

`bpq submit`、`POST /api/tasks`、WebUI 的新建任务表单，做的是同一件事：
读 3mf → 匹配 AMS → 建 Task → 落库 → 写 jobstore。分散在三个入口里各写一遍，
它们必然漂移——而漂移的后果是「命令行提交能打，网页提交打错料」这种最难查的问题。

这一层不认识 click，也不认识 FastAPI。

两个依赖是注入的，因为三个入口的答案不一样：

    ams_source  daemon 内传 `lambda: link.snapshot()`（读缓存，**不建连接**）；
                CLI 在 daemon 没跑时传一个临时建连的实现。
                打印机同一时刻只接受一个 MQTT 连接，这个区别不能含糊。

    schedule    daemon 内直接用运行中的 scheduler，任务立刻生效；
                CLI 走共享 jobstore，daemon 在下一次心跳（30 秒）时看到。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bpq import threemf
from bpq.config import Config
from bpq.journal import Journal
from bpq.models import AmsTray, FilamentRef, PrintOptions, Task, TaskState
from bpq.scheduler import TaskRunner
from bpq.snapshot import PrinterSnapshot
from bpq.store import TaskStore

log = logging.getLogger(__name__)

# FTPS 吞吐约 46 KB/s（ESP32 硬件上限），用它估上传耗时。
FTPS_BYTES_PER_SEC = 46_000


class ServiceError(RuntimeError):
    """调用方传的东西有问题（文件不对、任务状态不允许改等）。"""


@dataclass(frozen=True)
class PlateInfo:
    index: int
    gcode_path: str
    md5: str
    bed_type: str
    prediction_sec: float
    weight_g: float
    filaments: list[FilamentRef]
    needs_ams: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "gcode_path": self.gcode_path,
            "md5": self.md5,
            "bed_type": self.bed_type,
            "prediction_sec": self.prediction_sec,
            "weight_g": self.weight_g,
            "needs_ams": self.needs_ams,
            "filaments": [
                {"id": f.id, "type": f.type, "color": f.color,
                 "rgb": f.color.lstrip("#").upper()[:6],
                 "info_idx": f.info_idx, "used_g": f.used_g}
                for f in self.filaments
            ],
        }


@dataclass(frozen=True)
class FileInfo:
    """一个已经收下的 3mf。"""

    file_id: str
    path: Path
    name: str
    size: int
    plates: list[PlateInfo]
    slimmed_from: int = 0     # 非 0 表示剥过 Auxiliaries/，这是原始体积

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "size": self.size,
            "slimmed_from": self.slimmed_from,
            "upload_seconds": round(self.size / FTPS_BYTES_PER_SEC),
            "plates": [p.to_dict() for p in self.plates],
        }


@dataclass(frozen=True)
class MappingPreview:
    """AMS 自动匹配的建议。**只是建议**——最终以人的选择为准。"""

    mapping: list[int]
    notes: list[str]
    filaments: list[FilamentRef]

    def to_dict(self) -> dict:
        return {
            "mapping": self.mapping,
            "notes": self.notes,
            "filaments": [
                {"id": f.id, "type": f.type, "color": f.color,
                 "rgb": f.color.lstrip("#").upper()[:6],
                 "info_idx": f.info_idx, "used_g": f.used_g}
                for f in self.filaments
            ],
        }


@dataclass
class SubmitRequest:
    file_id: str
    scheduled_at: datetime
    plate_index: int | None = None
    use_ams: bool | None = None            # None = 按 3mf 判定
    ams_mapping: list[int] | None = None   # 给了就是人工覆盖
    options: PrintOptions = field(default_factory=PrintOptions)
    remote_name: str | None = None
    title: str = ""
    origin: str = "web"


@dataclass(frozen=True)
class SubmitResult:
    task: Task
    notes: list[str]


@dataclass
class TaskPatch:
    """改一个还没触发的任务。None 表示这一项不动。"""

    scheduled_at: datetime | None = None
    options: PrintOptions | None = None
    ams_mapping: list[int] | None = None


AmsSource = Callable[[], PrinterSnapshot]
Scheduler = Callable[[Task], None]
Unscheduler = Callable[[str], None]


class TaskService:
    def __init__(
        self,
        cfg: Config,
        store: TaskStore,
        journal: Journal,
        runner: TaskRunner,
        *,
        ams_source: AmsSource,
        schedule: Scheduler,
        unschedule: Unscheduler,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.journal = journal
        self.runner = runner
        self._ams_source = ams_source
        self._schedule = schedule
        self._unschedule = unschedule

    # ------------------------------------------------------------ 文件

    @property
    def spool(self) -> Path:
        return Path(self.cfg.daemon.spool_dir)

    def accept_upload(self, filename: str, data: bytes) -> FileInfo:
        """收下一个浏览器传上来的 3mf。

        存进 spool 而不是直接用用户给的路径，是因为浏览器上传本来就只有字节流；
        顺带解决了 v0.1 的一个隐患——源文件被移走或删掉后，还没触发的任务
        会找不到文件。
        """
        safe = Path(filename).name or "upload.3mf"
        file_id = uuid.uuid4().hex[:12]
        target_dir = self.spool / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        raw = target_dir / safe
        raw.write_bytes(data)
        return self._ingest(file_id, raw)

    def accept_local(self, path: Path) -> FileInfo:
        """收下一个本机路径上的 3mf（CLI 走这条）。

        照样复制进 spool：任务可能几小时后才触发，那时源文件未必还在原处。
        """
        if not path.exists():
            raise ServiceError(f"找不到文件：{path}")
        file_id = uuid.uuid4().hex[:12]
        target_dir = self.spool / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        raw = target_dir / path.name
        shutil.copy2(path, raw)
        return self._ingest(file_id, raw)

    def _ingest(self, file_id: str, raw: Path) -> FileInfo:
        """校验能不能解析，并剥掉 Auxiliaries/。

        瘦身放在收下的这一刻而不是上传给打印机之前，是为了让人在提交界面上
        立刻看到「26 MB → 0.4 MB」——那 26 MB 直传要 9 分钟，等到点了才发现就晚了。
        """
        try:
            mf = threemf.inspect(raw)
        except Exception as exc:  # noqa: BLE001 - zipfile 会抛各种东西
            raise ServiceError(
                f"{raw.name} 不是一个能解析的 3mf（{exc}）。"
                "要的是 Bambu Studio 切好片之后导出的文件。"
            ) from exc

        if not mf.plates:
            raise ServiceError(
                f"{raw.name} 里没有任何 plate gcode——这个文件没切过片，只有模型。"
            )

        original_size = raw.stat().st_size
        slimmed_from = 0
        if mf.aux_bytes:
            slim_path = raw.with_name(raw.stem + ".slim" + raw.suffix)
            result = threemf.slim(raw, slim_path)
            raw.unlink(missing_ok=True)
            slim_path.rename(raw)
            slimmed_from = original_size
            log.info("已剥掉 %s 的 Auxiliaries/：%s", raw.name, result)
            mf = threemf.inspect(raw)

        return FileInfo(
            file_id=file_id,
            path=raw,
            name=raw.name,
            size=raw.stat().st_size,
            slimmed_from=slimmed_from,
            plates=[_plate_info(p) for p in mf.plates],
        )

    def find_file(self, file_id: str) -> FileInfo:
        target_dir = self.spool / file_id
        if not target_dir.is_dir():
            raise ServiceError(f"没有这个文件（{file_id}），可能已被清理，请重新上传。")
        files = [p for p in target_dir.iterdir() if p.is_file()]
        if not files:
            raise ServiceError(f"文件 {file_id} 的目录是空的，请重新上传。")
        raw = files[0]
        mf = threemf.inspect(raw)
        return FileInfo(
            file_id=file_id, path=raw, name=raw.name, size=raw.stat().st_size,
            plates=[_plate_info(p) for p in mf.plates],
        )

    def thumbnail(self, file_id: str, plate_index: int) -> bytes | None:
        return threemf.thumbnail(self.find_file(file_id).path, plate_index)

    # ------------------------------------------------------------ AMS

    def preview_mapping(self, file_id: str, plate_index: int | None = None) -> MappingPreview:
        """给一个盘算出 AMS 映射建议。

        **只是建议。** AMS lite 没有 RFID，槽位里的耗材型号和颜色都是人手填的，
        所以自动匹配只能按「型号优先、颜色最近」去猜。界面上要让人能改，
        并且把 notes 里的警告显示出来——那是唯一能拦住「打错料」的地方。
        """
        info = self.find_file(file_id)
        plate = _pick_plate(info, plate_index)
        trays = _trays_from(self._ams_source())
        mapping, notes = threemf.match_ams(
            _to_plate(plate), trays, external_id=self.cfg.print.external_spool_id
        )
        if not trays:
            notes.insert(0, "⚠ 读不到 AMS 的状态（打印机没连上？），下面的映射只是占位。")
        return MappingPreview(mapping=mapping, notes=notes, filaments=plate.filaments)

    # ------------------------------------------------------------ 任务

    def submit(self, req: SubmitRequest) -> SubmitResult:
        info = self.find_file(req.file_id)
        plate = _pick_plate(info, req.plate_index)

        use_ams = plate.needs_ams if req.use_ams is None else req.use_ams
        mapping: list[int] = []
        notes: list[str] = []
        source = "auto"
        if use_ams:
            if req.ams_mapping is not None:
                mapping, source = list(req.ams_mapping), "manual"
            else:
                preview = self.preview_mapping(req.file_id, plate.index)
                mapping, notes = preview.mapping, preview.notes

        task = Task(
            source_path=str(info.path),
            scheduled_at=req.scheduled_at,
            remote_name=req.remote_name or info.name,
            plate=plate.gcode_path,
            plate_index=plate.index,
            md5=plate.md5,
            bed_type=plate.bed_type,
            use_ams=use_ams,
            ams_mapping=mapping,
            options=req.options,
            filaments=plate.filaments,
            mapping_source=source,
            mapping_notes=notes,
            title=req.title or Path(info.name).stem,
            origin=req.origin,
        )
        self.runner.submit(task)      # upload_timing=early 时当场静默上传
        self._schedule(task)

        # 从库里重读一次再返回。submit() 内部把 uploaded / uploaded_at 写进了数据库，
        # 但内存里这个 Task 对象不知道——直接返回它，网页上提交完会显示 "pending"，
        # 而实际文件已经静默躺在打印机上了。「文件已经在打印机上，触发前它不会有
        # 任何动作」正是这个项目最该让人看到的一句话，不能因为对象没同步就丢掉。
        return SubmitResult(task=self.store.get(task.id) or task, notes=notes)

    def update(self, task_id: str, patch: TaskPatch) -> Task:
        """改一个还没触发的任务。"""
        task = self.store.get(task_id)
        if task is None:
            raise ServiceError(f"没有任务 {task_id}")
        if task.state not in (TaskState.PENDING, TaskState.UPLOADED):
            raise ServiceError(
                f"任务 {task_id} 已经是 {task.state.value} 了，改不动。"
            )

        if patch.scheduled_at is not None:
            task.scheduled_at = patch.scheduled_at
        if patch.options is not None:
            task.options = patch.options
        if patch.ams_mapping is not None:
            task.ams_mapping = list(patch.ams_mapping)
            task.mapping_source = "manual"

        self.store.replace(task)
        if patch.scheduled_at is not None:
            # 重排 job。文件已经在打印机上了，改时间不需要重传。
            self._schedule(task)
            self.journal.write(
                "rescheduled", task=task.id,
                scheduled_at=task.scheduled_at.isoformat(timespec="seconds"),
            )
        return task

    def cancel(self, task_id: str) -> bool:
        ok = self.runner.cancel(task_id)
        if ok:
            self._unschedule(task_id)
        return ok

    def list_tasks(self, *, pending_only: bool = False) -> list[Task]:
        return self.store.list(pending_only=pending_only)

    def get(self, task_id: str) -> Task | None:
        return self.store.get(task_id)

    def delete(self, task_id: str) -> bool:
        """硬删一条**已经结束**的任务记录。

        不允许绕过取消直接删掉一个还没跑完的任务：pending/uploaded 的任务背后
        还挂着 jobstore 里一个等待触发的 job，直接删库会让那个 job 变成孤儿——
        到点它照样会触发，却找不到对应的任务记录了，这比留一条软取消的记录
        更难查。所以未结束的任务必须先 cancel()，这里只收「已经不会再动」的。
        """
        task = self.store.get(task_id)
        if task is None:
            return False
        if task.state in (TaskState.PENDING, TaskState.UPLOADED):
            raise ValueError("任务还没结束，请先取消再删除")

        self._unschedule(task_id)   # 保险：万一 jobstore 里还留着残余 job
        ok = self.store.delete(task_id)
        if ok:
            self.journal.write("deleted", task=task_id)
        return ok


# ---------------------------------------------------------------- 辅助


def _plate_info(p: threemf.Plate) -> PlateInfo:
    return PlateInfo(
        index=p.index,
        gcode_path=p.gcode_path,
        md5=p.md5,
        bed_type=p.bed_type,
        prediction_sec=p.prediction_sec,
        weight_g=p.weight_g,
        needs_ams=p.needs_ams,
        filaments=[
            FilamentRef(id=f.id, type=f.type, color=f.color,
                        info_idx=f.info_idx, used_g=f.used_g)
            for f in p.filaments
        ],
    )


def _to_plate(info: PlateInfo) -> threemf.Plate:
    """PlateInfo 转回 threemf.Plate，好复用 match_ams。"""
    return threemf.Plate(
        index=info.index,
        gcode_path=info.gcode_path,
        md5=info.md5,
        bed_type=info.bed_type,
        filaments=[
            threemf.Filament(id=f.id, type=f.type, color=f.color,
                             info_idx=f.info_idx, used_g=f.used_g)
            for f in info.filaments
        ],
    )


def _pick_plate(info: FileInfo, index: int | None) -> PlateInfo:
    """选盘。不指定且只有一个盘时取那个；多个盘必须显式指定——
    这不是挑剔：v0.1 曾经默认取 plate_1，而那个文件里只有 plate_3，
    打印机报了个看起来像 SD 卡坏了的存储错误。"""
    if index is not None:
        for p in info.plates:
            if p.index == index:
                return p
        raise ServiceError(
            f"{info.name} 里没有 plate_{index}；有的是 {[p.index for p in info.plates]}"
        )
    if len(info.plates) > 1:
        raise ServiceError(
            f"{info.name} 里有多个盘 {[p.index for p in info.plates]}，请指定用哪个"
        )
    return info.plates[0]


def _trays_from(snap: PrinterSnapshot) -> dict[int, AmsTray]:
    """快照里的托盘转成 match_ams 认的形状。"""
    return {
        t.global_id: AmsTray(
            id=t.global_id, type=t.tray_type, color=t.color, info_idx=t.info_idx,
            remain=t.remain, k=t.k, unit_id=t.unit_id, slot=t.slot,
            is_external=t.is_external,
        )
        for t in snap.ams.all_trays()
    }
