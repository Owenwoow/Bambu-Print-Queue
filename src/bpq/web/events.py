"""SSE 事件分发。

为什么是 SSE 而不是 WebSocket：需求是单向的（打印机 → 浏览器），所有写操作都走
REST POST，WebSocket 的双向能力在这里是纯负担。EventSource 自带断线重连；不需要
额外依赖（uvicorn 可以装不带 [standard] 的精简版）；而且能直接
`curl -N http://.../api/events` 肉眼调试——在只有假打印机的开发阶段这一条很值钱。

唯一的代价是 EventSource 不能自定义请求头，所以不能用 Authorization: Bearer。
这恰好和 Cookie 鉴权吻合，也顺带避免了把口令塞进 URL query。

这个模块是**唯一的跨线程边界**：状态变化来自 paho 的接收线程，而消费者在
asyncio 事件循环里。所以往队列投递一律走 loop.call_soon_threadsafe，
且队列是有界的——一个卡住的浏览器绝不能把打印机的状态流一起拖停。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 64
KEEPALIVE_SECONDS = 15.0   # 一行注释，穿透中间代理的空闲超时


def encode(event: str, data: Any, event_id: int | None = None) -> bytes:
    """编成一帧 SSE。"""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


class EventBroker:
    """把状态变化广播给所有 SSE 订阅者。"""

    def __init__(self, *, queue_size: int = QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[tuple[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next_id = 0

    def bind(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """记住事件循环，跨线程投递要用它。

        不传参数就认领当前正在跑的那个。stream() 开头会调一次——那时我们本来就
        在事件循环里，比从 uvicorn 内部去捞一个私有属性可靠得多
        （uvicorn 没有公开接口暴露它，各版本藏的地方还不一样）。
        """
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is not None:
            self._loop = loop

    # ------------------------------------------------------------ 生产端

    def publish_threadsafe(self, event: str, data: Any) -> None:
        """从别的线程投递。paho 的接收线程走这条。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._publish, event, data)

    def _publish(self, event: str, data: Any) -> None:
        """在事件循环线程里真正投递。"""
        self._next_id += 1
        for q in list(self._subscribers):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                # 这个订阅者跟不上了。清空它的积压再压一条 resync，
                # 让它下次拿一份完整快照重新对齐——**绝不阻塞生产端**。
                _drain(q)
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(("resync", {"reason": "queue_overflow"}))

    # ------------------------------------------------------------ 消费端

    async def stream(self, initial: dict) -> AsyncIterator[bytes]:
        """一个订阅者的字节流。首帧永远是完整快照。"""
        self.bind()
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        try:
            yield encode("snapshot", initial)
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    # 没有新事件也要定期发点什么，否则中间的代理会把连接掐掉
                    yield b": keepalive\n\n"
                    continue
                self._next_id += 1
                yield encode(event, data, self._next_id)
        except asyncio.CancelledError:
            raise
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def _drain(q: asyncio.Queue[tuple[str, Any]]) -> None:
    while not q.empty():
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
