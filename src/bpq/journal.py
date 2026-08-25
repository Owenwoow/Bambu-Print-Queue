"""最薄的一层日志：append-only JSONL，外加一层简单的筛选/分页/清理能力。

目的只有一个——排查「为什么那晚没打起来」。出问题时人在睡觉，现场什么都看不到。

v0.3 起 WebUI 有了专门的日志页面，纯粹「打开文件从头看到尾」已经不够用，于是加了
`query()`（按事件名/日期筛选 + 分页 + 倒序）、`event_names()`（供前端筛选下拉用）、
`clear()`（按日期批量删除）。但仍然坚持不上数据库：一年也就几百到几千行，
`query()` 里全量读进内存再过滤切片完全够用，为这点数据量引入一个数据库反而多了
一层要维护的 schema 和迁移。也仍然不做真正的日志轮转——文件小到可以整篇读入，
`clear(before=...)` 已经足够充当「按日期归档/清理」的手动版本。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 一条刚写入的日志。WebUI 靠它知道「任务状态变了」。
Listener = Callable[[dict[str, Any]], None]


class Journal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._listener: Listener | None = None

    def set_listener(self, fn: Listener | None) -> None:
        """注册写入回调，用于把状态变化实时推给 WebUI。

        挂在这里而不是 TaskRunner 上：任务的每一次状态流转都会写一条日志，
        这是所有变化的必经之路，比在 runner 里逐个调用点埋钩子更难漏。
        尤其是到点触发——它由 APScheduler 在后台线程里执行，不经过任何 HTTP 路由，
        没有这条通路的话，任务真的开打了网页上还显示「等待中」，得手动刷新。

        实现方必须非阻塞：回调跑在 APScheduler 的 worker 线程里，
        卡住它就等于卡住任务触发本身。
        """
        self._listener = fn

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        if self._listener is not None:
            try:
                self._listener(record)
            except Exception:  # noqa: BLE001 - 推送失败绝不能影响日志本身或任务流转
                log.exception("日志监听回调出错")

    # 约定的事件名，便于日后 grep：
    #   submitted  受理任务（记 发送时间 / 触发时间）
    #   uploaded   文件已静默传到打印机存储
    #   triggered  到点，开始处理
    #   started    启动指令被接受
    #   aborted    到点机器不空闲，放弃
    #   cancelled  用户反悔
    #   failed     上传或启动出错（记 reason）
    #   rescheduled  改了触发时刻或参数（还没触发的任务）
    #   connection_reclaimed  到点时把让给 Studio 的连接抢了回来
    #   deleted    硬删一条已经结束的任务记录

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def query(
        self,
        *,
        events: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """按条件筛选，返回 (这一页的记录, 过滤后的总条数)。

        `since` / `until` 接受 `YYYY-MM-DD`（含当天）。记录里的 `ts` 是
        `datetime.isoformat(timespec="seconds")`（形如 `2026-08-25T13:20:05`），
        和 `YYYY-MM-DD` 恰好共享同一种从左到右、定长数字段的格式，所以可以直接
        做字符串比较——不需要先解析成 datetime 再比。

        **按时间倒序返回**（最新在前）：WebUI 的日志页面本来就是想先看最近发生的事，
        offset/limit 在倒序之后才切片，翻页翻的是「从最新往回数第几条」。
        """
        records = self.read()

        if events:
            wanted = set(events)
            records = [r for r in records if r.get("event") in wanted]
        if since:
            records = [r for r in records if str(r.get("ts", "")) >= since]
        if until:
            # ts 精确到秒，补一个当天最晚的时刻作为上界，让 until 当天的记录也算在内。
            until_bound = f"{until}T23:59:59"
            records = [r for r in records if str(r.get("ts", "")) <= until_bound]

        records.reverse()
        total = len(records)
        page = records[offset : offset + limit]
        return page, total

    def event_names(self) -> list[str]:
        """日志文件里实际出现过的全部事件名，供前端筛选下拉用。"""
        names = {r["event"] for r in self.read() if isinstance(r.get("event"), str)}
        return sorted(names)

    def clear(self, *, before: str | None = None) -> int:
        """删日志，返回删掉的条数。

        `before="YYYY-MM-DD"` 时只删该日期**之前**（不含当天）的记录；不给 `before`
        就整个清空。原子重写：先写临时文件再 `os.replace`，避免半路断电/被杀
        留下一个截断到一半、后续 `read()` 会跳过坏行但已经丢了数据的文件。
        """
        if not self.path.exists():
            return 0

        records = self.read()
        if before is None:
            keep: list[dict[str, Any]] = []
        else:
            keep = [r for r in records if str(r.get("ts", "")) >= before]

        deleted = len(records) - len(keep)
        if deleted == 0:
            return 0

        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return deleted
