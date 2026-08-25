"""任务持久化（SQLite）。

为什么不用内存 dict：服务重启、电脑睡眠唤醒之后待发任务必须还在。
APScheduler 的 jobstore 只存「什么时候调谁」，任务本身的元数据存这里，
两边用同一个 task.id 对齐，且共用同一个 .sqlite3 文件——**绝对不能碰
`apscheduler_jobs` 表**，那是 APScheduler 自己的地盘。

v0.2 起两个坑：

1. **线程不安全。** v0.1 只有 CLI 单线程用它，一条 `sqlite3.connect()` 够了。
   v0.2 里 FastAPI 线程池 + APScheduler worker 线程 + 主线程会共用同一个
   `TaskStore` 实例，`sqlite3.Connection` 默认 `check_same_thread=True`，第一个
   跨线程调用就抛 `ProgrammingError`。改成 `threading.local()` 惰性建连——
   每个线程第一次用到时才开自己的连接，天然满足「一条连接只在开它的线程里用」，
   不需要靠 `check_same_thread=False` 去关掉安全检查（那样反而会真的并发写坏数据）。
   配 `journal_mode=WAL` + `busy_timeout=5000`，多连接并发读写时靠这个而不是异常。

2. **schema 会随版本演进。** 用 `PRAGMA user_version` 记录当前 schema 版本，
   打开旧库时自动迁移，全程走一次事务、迁移前先备份文件——库只有几十 KB，
   备份是白拿的保险。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from bpq.models import FilamentRef, PrintOptions, Task, TaskState

log = logging.getLogger(__name__)


SCHEMA_VERSION = 1

# 全新库（没有 tasks 表）直接按这份完整 schema 建表，不需要走迁移路径。
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    source_path     TEXT NOT NULL,
    remote_name     TEXT NOT NULL,
    plate           TEXT NOT NULL,
    plate_index     INTEGER NOT NULL DEFAULT 0,
    md5             TEXT NOT NULL DEFAULT '',
    bed_type        TEXT NOT NULL DEFAULT 'auto',
    use_ams         INTEGER NOT NULL DEFAULT 0,
    ams_mapping     TEXT NOT NULL DEFAULT '[]',
    bed_leveling    INTEGER,
    vibration_cali  INTEGER,
    flow_cali       INTEGER,
    layer_inspect   INTEGER,
    timelapse       INTEGER,
    filaments       TEXT NOT NULL DEFAULT '[]',
    mapping_source  TEXT NOT NULL DEFAULT 'auto',
    mapping_notes   TEXT NOT NULL DEFAULT '[]',
    title           TEXT NOT NULL DEFAULT '',
    origin          TEXT NOT NULL DEFAULT 'cli',
    scheduled_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    triggered_at    TEXT,
    uploaded_at     TEXT,
    state           TEXT NOT NULL,
    error           TEXT,
    sent_payload    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, scheduled_at);
"""

# v0.1 库（user_version=0）缺的列，迁移时按需 ADD COLUMN。
# 五个开关用 INTEGER 且不给 DEFAULT——NULL 就是「跟随全局」，旧行迁移后自动是 NULL，
# 语义正好对：老任务本来就没有单独覆盖过这几个开关。
NEW_COLUMNS: list[tuple[str, str]] = [
    ("plate_index", "INTEGER NOT NULL DEFAULT 0"),
    ("bed_leveling", "INTEGER"),
    ("vibration_cali", "INTEGER"),
    ("flow_cali", "INTEGER"),
    ("layer_inspect", "INTEGER"),
    ("timelapse", "INTEGER"),
    ("filaments", "TEXT NOT NULL DEFAULT '[]'"),
    ("mapping_source", "TEXT NOT NULL DEFAULT 'auto'"),
    ("mapping_notes", "TEXT NOT NULL DEFAULT '[]'"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("origin", "TEXT NOT NULL DEFAULT 'cli'"),
    ("uploaded_at", "TEXT"),
    ("sent_payload", "TEXT"),
]


class TaskStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

        # 建表/迁移只在构造时跑一次，用一条独立的临时连接，跑完就关掉——
        # 不能让每个线程各自的惰性连接都去重复探测/迁移一遍 schema。
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            self._migrate(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------ 连接

    def _conn(self) -> sqlite3.Connection:
        """当前线程的连接，没有就惰性开一条。

        每个线程只用自己开的连接，天然满足 sqlite3 「连接不能跨线程」的约束，
        不需要 `check_same_thread=False` 去关掉这条安全检查。
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # WAL：多个连接（CLI 进程 / daemon 里的多个线程）并发读写时，
            # 读不再被写阻塞；写写冲突交给下面的 busy_timeout 重试解决。
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """关闭调用线程持有的连接。

        连接是线程本地的，`close()` 天然只能关调用者自己这条——sqlite3 连接不允许
        跨线程操作，包括关闭。其它线程若持有各自的连接，会在那些线程自己调用
        `close()`（或线程结束、连接被垃圾回收）时收尾，这里没必要也没办法替它们关。
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ 迁移

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """把库升级到 SCHEMA_VERSION。幂等：跑多次结果一样，不会重复 ALTER。

        绝对不碰 apscheduler_jobs——那张表和 tasks 共享同一个 .sqlite3 文件，
        但完全是 APScheduler 自己的地盘，这里的迁移逻辑只认 tasks 表。
        """
        table_exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            is not None
        )

        if not table_exists:
            # 全新库：没有旧数据要保护，直接按最新 schema 建表，不必备份、不必 ALTER。
            conn.executescript(BASE_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return

        cur_version = conn.execute("PRAGMA user_version").fetchone()[0]
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        missing = [(name, ddl) for name, ddl in NEW_COLUMNS if name not in existing_cols]

        if not missing and cur_version >= SCHEMA_VERSION:
            return  # 真正的快路径：列都在、版本号也对，重复调用不做任何事

        if missing:
            # 只有真的要动 schema 时才备份——仅仅补齐 user_version（列已存在的
            # 那种手工不同步场景）不改数据，不需要。
            self._backup_before_migrate(conn)

        # 手动管理事务：ALTER TABLE 在 SQLite 里是事务性的 DDL，和 PRAGMA user_version
        # 放进同一个事务，要么全部生效要么全部回滚，不会出现「列加了一半」的中间态。
        conn.isolation_level = None
        conn.execute("BEGIN")
        try:
            for name, ddl in missing:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _backup_before_migrate(self, conn: sqlite3.Connection) -> Path:
        """迁移前把整个库复制一份。库只有几十 KB，这份保险白拿。"""
        try:
            # 若库已经是 WAL 模式，数据可能还没落回主文件；先做一次 checkpoint，
            # 保证只复制主文件也能拿到完整数据，不用连 -wal/-shm 一起备份。
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error as exc:
            log.debug("迁移前 checkpoint 失败（%s），继续备份", exc)

        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.db_path.with_name(f"{self.db_path.name}.bak-{ts}")
        shutil.copy2(self.db_path, backup_path)
        log.info("迁移前已备份数据库到 %s", backup_path)
        return backup_path

    # ------------------------------------------------------------------ 写

    def add(self, task: Task) -> Task:
        conn = self._conn()
        opt = task.options
        conn.execute(
            "INSERT INTO tasks (id, source_path, remote_name, plate, plate_index, md5,"
            " bed_type, use_ams, ams_mapping, bed_leveling, vibration_cali, flow_cali,"
            " layer_inspect, timelapse, filaments, mapping_source, mapping_notes, title,"
            " origin, scheduled_at, created_at, triggered_at, uploaded_at, state, error,"
            " sent_payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.id,
                task.source_path,
                task.remote_name or Path(task.source_path).name,
                task.plate,
                task.plate_index,
                task.md5,
                task.bed_type,
                int(task.use_ams),
                json.dumps(task.ams_mapping),
                _bool_to_col(opt.bed_leveling),
                _bool_to_col(opt.vibration_cali),
                _bool_to_col(opt.flow_cali),
                _bool_to_col(opt.layer_inspect),
                _bool_to_col(opt.timelapse),
                json.dumps([asdict(f) for f in task.filaments]),
                task.mapping_source,
                json.dumps(task.mapping_notes),
                task.title,
                task.origin,
                task.scheduled_at.isoformat(),
                task.created_at.isoformat(),
                task.triggered_at.isoformat() if task.triggered_at else None,
                task.uploaded_at.isoformat() if task.uploaded_at else None,
                task.state.value,
                task.error,
                task.sent_payload,
            ),
        )
        conn.commit()
        return task

    def set_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        error: str | None = None,
        triggered_at: datetime | None = None,
        uploaded_at: datetime | None = None,
        sent_payload: str | None = None,
    ) -> None:
        """更新状态。时间戳与 sent_payload 用 COALESCE 只增不减——

        它们记录的是「这件事发生过」，一旦写进去就不该被后续的状态流转抹掉：
        任务失败后回看，最想知道的恰恰是「当时传上去了没、到底发了什么」。
        """
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET state = ?, error = ?,"
            " triggered_at = COALESCE(?, triggered_at),"
            " uploaded_at = COALESCE(?, uploaded_at),"
            " sent_payload = COALESCE(?, sent_payload) WHERE id = ?",
            (
                state.value,
                error,
                triggered_at.isoformat() if triggered_at else None,
                uploaded_at.isoformat() if uploaded_at else None,
                sent_payload,
                task_id,
            ),
        )
        conn.commit()

    def replace(self, task: Task) -> Task:
        """整条覆盖写回。改一个还没触发的任务（改时间、改参数、改 AMS 映射）时用。

        不走 set_state：那个只碰状态和几个时间戳，且时间戳是 COALESCE 只增不减的。
        """
        opt = task.options
        self._conn().execute(
            "UPDATE tasks SET source_path=?, remote_name=?, plate=?, plate_index=?,"
            " md5=?, bed_type=?, use_ams=?, ams_mapping=?, bed_leveling=?,"
            " vibration_cali=?, flow_cali=?, layer_inspect=?, timelapse=?,"
            " filaments=?, mapping_source=?, mapping_notes=?, title=?, origin=?,"
            " scheduled_at=?, state=?, error=? WHERE id=?",
            (
                task.source_path,
                task.remote_name or Path(task.source_path).name,
                task.plate,
                task.plate_index,
                task.md5,
                task.bed_type,
                int(task.use_ams),
                json.dumps(task.ams_mapping),
                _bool_to_col(opt.bed_leveling),
                _bool_to_col(opt.vibration_cali),
                _bool_to_col(opt.flow_cali),
                _bool_to_col(opt.layer_inspect),
                _bool_to_col(opt.timelapse),
                json.dumps([asdict(f) for f in task.filaments]),
                task.mapping_source,
                json.dumps(task.mapping_notes),
                task.title,
                task.origin,
                task.scheduled_at.isoformat(),
                task.state.value,
                task.error,
                task.id,
            ),
        )
        self._conn().commit()
        return task

    # ------------------------------------------------------------------ 读

    def get(self, task_id: str) -> Task | None:
        row = self._conn().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list(self, *, pending_only: bool = False) -> list[Task]:
        sql = "SELECT * FROM tasks"
        params: tuple[str, ...] = ()
        if pending_only:
            sql += " WHERE state IN (?, ?)"
            params = (TaskState.PENDING.value, TaskState.UPLOADED.value)
        sql += " ORDER BY scheduled_at"
        return [_row_to_task(r) for r in self._conn().execute(sql, params)]

    # ------------------------------------------------------------------ 删除

    def delete(self, task_id: str) -> bool:
        """真删这一行。返回 True 表示确实删掉了一行。

        这里不检查任务状态——「哪些状态允许硬删」是业务规则，属于 service 层
        （删早了会让 jobstore 里的 job 变孤儿），store 只负责照办。
        """
        conn = self._conn()
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0


def _bool_to_col(v: bool | None) -> int | None:
    return None if v is None else int(v)


def _col_to_bool(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """兼容用手工 SQL 造的、可能缺列的旧库行——正常路径里迁移已经补全所有列。

    返回 Any 而不是精确类型：sqlite3.Row 本来就是动态取值，硬套 TypeVar 只会让
    每个调用点都得再 cast 一次，得不偿失。
    """
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _row_to_task(row: sqlite3.Row) -> Task:
    filaments_raw = _row_value(row, "filaments", "[]") or "[]"
    notes_raw = _row_value(row, "mapping_notes", "[]") or "[]"
    triggered_at = row["triggered_at"]
    uploaded_at = _row_value(row, "uploaded_at")
    return Task(
        id=row["id"],
        source_path=row["source_path"],
        remote_name=row["remote_name"],
        plate=row["plate"],
        plate_index=_row_value(row, "plate_index", 0) or 0,
        md5=row["md5"],
        bed_type=row["bed_type"],
        use_ams=bool(row["use_ams"]),
        ams_mapping=json.loads(row["ams_mapping"]),
        options=PrintOptions(
            bed_leveling=_col_to_bool(_row_value(row, "bed_leveling")),
            vibration_cali=_col_to_bool(_row_value(row, "vibration_cali")),
            flow_cali=_col_to_bool(_row_value(row, "flow_cali")),
            layer_inspect=_col_to_bool(_row_value(row, "layer_inspect")),
            timelapse=_col_to_bool(_row_value(row, "timelapse")),
        ),
        filaments=[FilamentRef(**f) for f in json.loads(filaments_raw)],
        mapping_source=_row_value(row, "mapping_source", "auto") or "auto",
        mapping_notes=json.loads(notes_raw),
        title=_row_value(row, "title", "") or "",
        origin=_row_value(row, "origin", "cli") or "cli",
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        triggered_at=datetime.fromisoformat(triggered_at) if triggered_at else None,
        uploaded_at=datetime.fromisoformat(uploaded_at) if uploaded_at else None,  # type: ignore[arg-type]
        state=TaskState(row["state"]),
        error=row["error"],
        sent_payload=_row_value(row, "sent_payload"),  # type: ignore[arg-type]
    )
