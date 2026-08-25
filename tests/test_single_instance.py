"""单实例保护的测试。

重点是跨进程互斥——单进程内自己锁自己没有意义，真实故障是用户开了两个 daemon，
它们互相抢打印机那唯一的 MQTT 连接，导致任务到点 printer_state=UNKNOWN 被放弃。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bpq.config import (
    Config,
    DaemonConfig,
    PrintConfig,
    PrinterConfig,
    SchedulerConfig,
    TransportConfig,
)
from bpq.daemon import AlreadyRunning, lock_path_for, single_instance

SRC = str(Path(__file__).resolve().parents[1] / "src")

# 子进程：拿到锁后打印一行让父进程知道「现在开始抢是有效的」，然后赖着不放。
# 只做加锁 + sleep，绝不启动 daemon（那会去连真实打印机）。
HOLDER_CODE = """
import sys, time
from bpq.daemon import single_instance

with single_instance(sys.argv[1]):
    print("LOCKED", flush=True)
    time.sleep(30)
"""


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = tmp_path / "bpq.lock"
    with single_instance(lock) as p:
        assert p == lock
        assert lock.exists()


def test_can_reacquire_after_exit(tmp_path: Path) -> None:
    """正常退出后必须能再次获取，否则重启 daemon 就废了。"""
    lock = tmp_path / "bpq.lock"
    for _ in range(3):
        with single_instance(lock):
            pass


def test_lock_file_persists_on_exit(tmp_path: Path) -> None:
    """锁文件故意不删——删它会引入 unlink/create 竞态，见 single_instance 的注释。

    留下的空文件不影响下次获取，这条一并钉住。
    """
    lock = tmp_path / "bpq.lock"
    with single_instance(lock):
        pass
    assert lock.exists()
    with single_instance(lock):
        pass


def test_creates_parent_dir(tmp_path: Path) -> None:
    """首次运行时 var/ 可能还不存在。"""
    lock = tmp_path / "var" / "bpq.lock"
    with single_instance(lock):
        assert lock.exists()


def test_lock_path_next_to_db() -> None:
    cfg = Config(
        printer=PrinterConfig(ip="0.0.0.0", serial="X", access_code="Y"),
        transport=TransportConfig(),
        print=PrintConfig(),
        scheduler=SchedulerConfig(),
        daemon=DaemonConfig(db_path="var/bpq.sqlite3"),
        path=Path("config.toml"),
    )
    assert lock_path_for(cfg) == Path("var") / "bpq.lock"


def test_cross_process_mutual_exclusion(tmp_path: Path) -> None:
    """子进程持锁期间，本进程获取同一把锁必须抛 AlreadyRunning。"""
    lock = tmp_path / "bpq.lock"
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER_CODE, str(lock)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # 等子进程明确报告已持锁；它若启动失败会 EOF，这里不会永久阻塞。
        line = proc.stdout.readline() if proc.stdout else ""
        err = proc.stderr.read() if (proc.stderr and line.strip() != "LOCKED") else ""
        assert line.strip() == "LOCKED", f"子进程没能拿到锁: {err}"
        assert proc.poll() is None

        with pytest.raises(AlreadyRunning) as exc:
            with single_instance(lock):
                pass
        # 错误信息要能自解释，用户看到就知道该去关掉另一个 daemon
        assert "MQTT" in str(exc.value)
    finally:
        proc.kill()
        proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


def test_lock_released_when_holder_dies(tmp_path: Path) -> None:
    """被强杀（没有任何清理代码跑过）之后锁必须自动释放——
    这正是用 OS 级锁而不是 PID 文件的理由。"""
    lock = tmp_path / "bpq.lock"
    env = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER_CODE, str(lock)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        assert line.strip() == "LOCKED"
    finally:
        proc.kill()
        proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

    with single_instance(lock):
        pass
