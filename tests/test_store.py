from datetime import datetime, timedelta

from bpq.models import Task, TaskState
from bpq.store import TaskStore


def _task(**kw) -> Task:
    return Task(
        source_path=kw.pop("source_path", r"E:\models\benchy.gcode.3mf"),
        scheduled_at=kw.pop("scheduled_at", datetime.now() + timedelta(hours=3)),
        **kw,
    )


def test_roundtrip_survives_reopen(tmp_path):
    """核心需求：服务重启之后待发任务要还在。"""
    db = tmp_path / "bpq.sqlite3"
    store = TaskStore(db)
    task = store.add(_task())
    store.close()

    reopened = TaskStore(db)
    got = reopened.get(task.id)
    assert got is not None
    assert got.source_path == task.source_path
    assert got.scheduled_at == task.scheduled_at
    assert got.state is TaskState.PENDING
    reopened.close()


def test_remote_name_defaults_to_basename(tmp_path):
    store = TaskStore(tmp_path / "bpq.sqlite3")
    task = store.add(_task())
    assert store.get(task.id).remote_name == "benchy.gcode.3mf"
    store.close()


def test_pending_only_filters_terminal_states(tmp_path):
    store = TaskStore(tmp_path / "bpq.sqlite3")
    pending = store.add(_task())
    done = store.add(_task())
    store.set_state(done.id, TaskState.STARTED, triggered_at=datetime.now())

    assert [t.id for t in store.list(pending_only=True)] == [pending.id]
    assert len(store.list()) == 2
    store.close()


def test_set_state_records_error(tmp_path):
    store = TaskStore(tmp_path / "bpq.sqlite3")
    task = store.add(_task())
    store.set_state(task.id, TaskState.ABORTED, error="printer_state=RUNNING")
    got = store.get(task.id)
    assert got.state is TaskState.ABORTED
    assert got.error == "printer_state=RUNNING"
    store.close()


def test_delete_真删这一行(tmp_path):
    store = TaskStore(tmp_path / "bpq.sqlite3")
    task = store.add(_task())

    assert store.delete(task.id) is True
    assert store.get(task.id) is None
    assert store.list() == []
    store.close()


def test_delete_不存在的_id_返回_false(tmp_path):
    store = TaskStore(tmp_path / "bpq.sqlite3")
    assert store.delete("nope") is False
    store.close()
