"""journal.py 的 query() / event_names() / clear() 测试。

写入用 write()（走真实的 ts=now()），但涉及日期范围筛选的用例需要精确控制
时间戳，所以那几个用例绕开 write()，直接拼一行 JSONL 写进去——
和真实写入走的是同一个文件格式，只是不依赖当前时刻。
"""

from __future__ import annotations

import json
from pathlib import Path

from bpq.journal import Journal


def _write_at(j: Journal, ts: str, event: str, **fields: object) -> None:
    record = {"ts": ts, "event": event, **fields}
    with j.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- query


def test_query_按事件筛选(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    j.write("uploaded", task="a")
    j.write("submitted", task="b")

    items, total = j.query(events=["submitted"])
    assert total == 2
    assert all(r["event"] == "submitted" for r in items)


def test_query_多个事件名(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    j.write("uploaded", task="a")
    j.write("cancelled", task="a")

    items, total = j.query(events=["submitted", "cancelled"])
    assert total == 2
    assert {r["event"] for r in items} == {"submitted", "cancelled"}


def test_query_按日期范围(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    _write_at(j, "2026-08-01T10:00:00", "submitted", task="old")
    _write_at(j, "2026-08-10T10:00:00", "submitted", task="mid")
    _write_at(j, "2026-08-20T10:00:00", "submitted", task="new")

    items, total = j.query(since="2026-08-05", until="2026-08-15")
    assert total == 1
    assert items[0]["task"] == "mid"


def test_query_until_覆盖到当天结束(tmp_path: Path) -> None:
    """until="2026-08-10" 要包含 2026-08-10 当天发生的记录，不能只到 00:00。"""
    j = Journal(tmp_path / "j.jsonl")
    _write_at(j, "2026-08-10T23:59:00", "submitted", task="late_same_day")
    _write_at(j, "2026-08-11T00:00:01", "submitted", task="next_day")

    items, total = j.query(until="2026-08-10")
    assert total == 1
    assert items[0]["task"] == "late_same_day"


def test_query_倒序返回最新在前(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="1")
    j.write("submitted", task="2")
    j.write("submitted", task="3")

    items, total = j.query()
    assert total == 3
    assert [r["task"] for r in items] == ["3", "2", "1"]


def test_query_分页在倒序之后切(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    for i in range(5):
        j.write("submitted", task=str(i))

    items, total = j.query(offset=2, limit=2)
    assert total == 5
    # 倒序后是 4,3,2,1,0；offset=2 从下标 2 开始取 2 条 -> 2,1
    assert [r["task"] for r in items] == ["2", "1"]


def test_query_文件不存在(tmp_path: Path) -> None:
    j = Journal(tmp_path / "nope.jsonl")
    j.path.unlink(missing_ok=True)  # Journal() 构造时会 mkdir 但不会建文件
    items, total = j.query()
    assert items == [] and total == 0


# ---------------------------------------------------------------- event_names


def test_event_names_去重并排序(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    j.write("failed", task="a", reason="x")
    j.write("submitted", task="b")

    assert j.event_names() == ["failed", "submitted"]


# ---------------------------------------------------------------- clear


def test_clear_不给_before_清空全部(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    j.write("submitted", task="b")

    assert j.clear() == 2
    assert j.read() == []


def test_clear_只删指定日期之前的(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    _write_at(j, "2026-08-01T10:00:00", "submitted", task="old")
    _write_at(j, "2026-08-20T10:00:00", "submitted", task="new")

    deleted = j.clear(before="2026-08-10")
    assert deleted == 1
    remaining = j.read()
    assert len(remaining) == 1
    assert remaining[0]["task"] == "new"


def test_clear_before_当天不删(tmp_path: Path) -> None:
    """before="2026-08-10" 是「之前」，不含当天——当天的记录要留着。"""
    j = Journal(tmp_path / "j.jsonl")
    _write_at(j, "2026-08-10T00:00:00", "submitted", task="same_day")

    assert j.clear(before="2026-08-10") == 0
    assert len(j.read()) == 1


def test_clear_没有匹配时不改文件(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    before_mtime = j.path.stat().st_mtime_ns

    assert j.clear(before="2000-01-01") == 0
    assert j.path.stat().st_mtime_ns == before_mtime


def test_clear_文件不存在(tmp_path: Path) -> None:
    j = Journal(tmp_path / "nope.jsonl")
    j.path.unlink(missing_ok=True)
    assert j.clear() == 0


def test_clear_原子写不留临时文件(tmp_path: Path) -> None:
    j = Journal(tmp_path / "j.jsonl")
    j.write("submitted", task="a")
    j.write("submitted", task="b")

    j.clear(before="2026-01-01")
    assert list(j.path.parent.glob("*.tmp")) == []
