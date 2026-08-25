"""get_state() 的等待行为回归测试。

背景：`_ensure_mqtt()` 一连上就返回，但 `_state` 要等 pushall 全量报文（实测 1–3 秒）
才有值，导致 `TaskRunner.fire()` 里「连上就读」几乎必然拿到 UNKNOWN，定时任务到点被
误判为「打印机状态未知」而放弃。等待现在内置在 `get_state()` 里，这两条用例分别钉住
「等到了报文」和「等不到就超时」两条路径。

全程 mock，不建立任何网络连接。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from bpq.config import (
    Config,
    DaemonConfig,
    PrintConfig,
    PrinterConfig,
    SchedulerConfig,
    TransportConfig,
)
from bpq.models import PrinterState
from bpq.transport.lan import LanTransport

PUSHALL = (
    b'{"print": {"gcode_state": "IDLE", "mc_percent": 0, "layer_num": 0, '
    b'"subtask_name": "x", "print_type": "idle"}}'
)


def _transport() -> LanTransport:
    """一个绝不会碰网络的 LanTransport：_ensure_mqtt 被换成空桩。"""
    cfg = Config(
        printer=PrinterConfig(ip="192.0.2.1", serial="TEST0000", access_code="00000000"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(),
        path=Path("config.toml"),
    )
    tp = LanTransport(cfg)
    tp._ensure_mqtt = lambda: None  # type: ignore[method-assign]
    return tp


def test_get_state_waits_for_first_report():
    """报文晚到几百毫秒，get_state() 要等到它，而不是抢答 UNKNOWN。"""
    tp = _transport()

    def feed() -> None:
        time.sleep(0.3)
        tp._on_message(None, None, SimpleNamespace(payload=PUSHALL))

    worker = threading.Thread(target=feed)
    worker.start()
    try:
        assert tp.get_state(timeout=5.0) is PrinterState.IDLE
    finally:
        worker.join()


def test_get_state_returns_immediately_once_known():
    """状态已经拿到过，后续调用不该再等——哪怕 timeout 给得很大。"""
    tp = _transport()
    tp._on_message(None, None, SimpleNamespace(payload=PUSHALL))

    started = time.monotonic()
    assert tp.get_state(timeout=30.0) is PrinterState.IDLE
    assert time.monotonic() - started < 1.0


def test_get_state_times_out_to_unknown():
    """一条报文都没有：等满 timeout 后返回 UNKNOWN，不能永久阻塞 daemon。"""
    tp = _transport()

    started = time.monotonic()
    assert tp.get_state(timeout=0.2) is PrinterState.UNKNOWN
    elapsed = time.monotonic() - started
    assert 0.15 <= elapsed < 2.0
