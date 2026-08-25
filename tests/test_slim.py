"""3mf 瘦身与元信息的测试。

用 assets/ 下两个**真实**的 3mf 做对照组，它们是同一个模型的两个版本：
    studio_reference.gcode.3mf   Studio 自己下发时用的精简版，369 KB
    test.gcode.3mf               Studio 导出的完整版，带 Auxiliaries/，26 MB

assets/ 在 .gitignore 里（3mf 动辄几十 MB），所以文件不在时要 skip 而不是失败。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from bpq import threemf

ASSETS = Path(__file__).resolve().parents[1] / "assets"
FULL = ASSETS / "test.gcode.3mf"                    # 带 Auxiliaries/
STUDIO = ASSETS / "studio_reference.gcode.3mf"      # Studio 的精简版

needs_assets = pytest.mark.skipif(
    not (FULL.exists() and STUDIO.exists()),
    reason="assets/ 下的真实 3mf 不在（它在 .gitignore 里）",
)


def entry_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


@needs_assets
def test_slim_剥掉_auxiliaries_且体积大降(tmp_path: Path) -> None:
    dst = tmp_path / "slim.gcode.3mf"
    result = threemf.slim(FULL, dst)

    assert dst.exists()
    assert result.after < result.before
    # 实测 26 MB → 0.37 MB。放宽到 90% 是为了不让这个断言因为换一个模型就红，
    # 但低于 90% 就说明剥离逻辑没生效，值得停下来看看。
    assert result.saved_ratio > 0.9
    assert result.dropped > 0
    assert not any(n.startswith("Auxiliaries/") for n in entry_names(dst))


@needs_assets
def test_slim_产物与_studio_精简版条目完全一致(tmp_path: Path) -> None:
    """这是防止「剥过头」的关键断言。

    Studio 的精简版是已知能被打印机正常接受的版本。实测两者的非 Auxiliaries 条目
    完全相同——也就是说 Studio 自己的「精简」就等于「删掉 Auxiliaries/」。
    这个测试把那个结论钉死：一旦有人日后往 slim() 里加「顺便也删掉别的盘」之类的
    优化，这里会立刻红，提醒他那是在拿打印机报文件错误的风险换几百 KB。
    """
    dst = tmp_path / "slim.gcode.3mf"
    threemf.slim(FULL, dst)

    ours, studio = entry_names(dst), entry_names(STUDIO)
    assert not (studio - ours), f"比 Studio 精简版少了这些条目，可能剥过头：{sorted(studio - ours)}"
    assert ours == studio


@needs_assets
def test_slim_不改变解析结果(tmp_path: Path) -> None:
    """瘦身只该影响体积，不该影响 project_file 要用的任何字段。"""
    dst = tmp_path / "slim.gcode.3mf"
    threemf.slim(FULL, dst)

    before = threemf.inspect(FULL).plate()
    after = threemf.inspect(dst).plate()

    assert after.index == before.index
    assert after.gcode_path == before.gcode_path
    assert after.md5 == before.md5
    assert after.bed_type == before.bed_type
    assert [f.info_idx for f in after.filaments] == [f.info_idx for f in before.filaments]
    # 瘦身后 aux_bytes 应该归零——这正是瘦身的目的
    assert threemf.inspect(dst).aux_bytes == 0
    assert threemf.inspect(FULL).aux_bytes > 0


@needs_assets
def test_slim_对已经精简的文件也能跑(tmp_path: Path) -> None:
    """没有 Auxiliaries/ 可剥时也要正常产出，让调用方的路径统一，不必先判断。"""
    dst = tmp_path / "slim.gcode.3mf"
    result = threemf.slim(STUDIO, dst)
    assert result.dropped == 0
    assert dst.exists()
    assert entry_names(dst) == entry_names(STUDIO)


@needs_assets
def test_耗时与重量被解析出来() -> None:
    """Studio 发送对话框顶上「25m51s / 3.40g」那一行的数据源。"""
    plate = threemf.inspect(STUDIO).plate()
    assert plate.prediction_sec == pytest.approx(773.0)
    assert plate.weight_g == pytest.approx(0.57)


@needs_assets
def test_缩略图() -> None:
    plate = threemf.inspect(STUDIO).plate()
    big = threemf.thumbnail(STUDIO, plate.index)
    small = threemf.thumbnail(STUDIO, plate.index, small=True)

    assert big and small
    assert big.startswith(b"\x89PNG")
    assert small.startswith(b"\x89PNG")
    assert len(small) < len(big)
    # 不存在的盘不该抛异常——没有预览图不是提交任务的阻塞理由
    assert threemf.thumbnail(STUDIO, 99) is None


def test_耗时重量缺失时不炸(tmp_path: Path) -> None:
    """切片器的数值字段偶尔缺失或为空串，不能因此让整个解析失败。"""
    path = tmp_path / "x.gcode.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "G28\n")
        z.writestr(
            "Metadata/slice_info.config",
            '<?xml version="1.0"?><config><plate>'
            '<metadata key="index" value="1"/>'
            '<metadata key="prediction" value=""/>'
            "</plate></config>",
        )
    plate = threemf.inspect(path).plate()
    assert plate.prediction_sec == 0.0
    assert plate.weight_g == 0.0
