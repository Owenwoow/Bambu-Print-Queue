"""打包成单文件 exe 之后的路径解析。

这组用例守的是一条硬约束：**任务必须持久化**。打包后如果还按 __file__ 去找
项目根，config.toml 和 var/ 会落在 PyInstaller 每次启动重建、退出就删的临时
解包目录里——任务库一关就没，而且不报任何错。
"""

from __future__ import annotations

import shutil

import pytest

from bpq import config as config_mod
from bpq.config import bundled_example, find_config_path, project_root


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """假装自己是 PyInstaller 打出来的单文件 exe。"""
    exe = tmp_path / "dist" / "bpq.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    meipass = tmp_path / "_MEI12345"
    meipass.mkdir()

    monkeypatch.setattr(config_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_mod.sys, "executable", str(exe))
    monkeypatch.setattr(config_mod.sys, "_MEIPASS", str(meipass), raising=False)
    return exe.parent, meipass


def test_project_root_is_exe_dir_when_frozen(frozen):
    exe_dir, _ = frozen
    assert project_root() == exe_dir


def test_config_lands_next_to_exe_not_in_temp(frozen, monkeypatch):
    """config.toml 要在 exe 旁边找，不能在临时解包目录里找。"""
    exe_dir, meipass = frozen
    monkeypatch.delenv("BPQ_CONFIG", raising=False)

    path = find_config_path()
    assert path.parent == exe_dir
    assert meipass not in path.parents


def test_var_paths_resolve_next_to_exe(frozen, monkeypatch):
    """任务库 / 日志 / 上传缓存都要跟着 config.toml 落在 exe 旁边。

    这条直接对应「任务必须持久化」——解错了地方就是每次启动一套空库。
    """
    exe_dir, meipass = frozen
    monkeypatch.delenv("BPQ_CONFIG", raising=False)

    shutil.copyfile(
        config_mod.Path(__file__).resolve().parents[1] / "config.example.toml",
        exe_dir / "config.toml",
    )

    cfg = config_mod.load()
    for p in (cfg.daemon.db_path, cfg.daemon.journal_path, cfg.daemon.spool_dir):
        resolved = config_mod.Path(p)
        assert resolved.is_absolute()
        assert exe_dir in resolved.parents
        assert meipass not in resolved.parents


def test_explicit_config_still_wins_when_frozen(frozen, tmp_path):
    """--config 显式指定的路径优先级最高，冻结与否都一样。"""
    elsewhere = tmp_path / "somewhere" / "my.toml"
    assert find_config_path(elsewhere) == elsewhere.expanduser().resolve()


def test_bundled_example_read_from_meipass(frozen):
    """懒人版第一次启动要能从解包目录里拿到模板。"""
    _, meipass = frozen
    assert bundled_example() is None  # 还没放进去

    (meipass / "config.example.toml").write_text("[printer]\n", encoding="utf-8")
    found = bundled_example()
    assert found is not None
    assert found.parent == meipass


def test_not_frozen_uses_repo_root():
    """源码运行时的行为不能被上面的改动带偏。"""
    assert (project_root() / "pyproject.toml").is_file()
