"""回归：3mf 里未必有 plate_1。

2026-08-24 的真实故障——默认 param 写死 Metadata/plate_1.gcode，
而 Studio 导出第 3 个盘时 3mf 里只有 plate_3.gcode。
打印机按不存在的路径去 SD 卡上找，报了个看起来像硬件故障的存储错误。
"""

import zipfile

import pytest

from bpq import threemf
from bpq.models import AmsTray

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="3"/>
    <filament id="1" tray_info_idx="GFG00" type="PETG" color="#FF671F" used_g="0.57"/>
  </plate>
</config>
"""

PLATE_JSON = '{"bed_type":"textured_plate","nozzle_diameter":0.4}'


def tray(i, type_, color, info_idx="", remain=100):
    return AmsTray(id=i, type=type_, color=color, info_idx=info_idx, remain=remain)


# 本机 AMS 实况：三卷 GFG00 PETG（橙/白/黑）+ 一卷 GFA18 PLA
REAL_TRAYS = {
    0: tray(0, "PETG", "F98C36FF", "GFG00"),
    1: tray(1, "PETG", "FFFFFFFF", "GFG00"),
    2: tray(2, "PLA", "FFFFFFFF", "GFA18"),
    3: tray(3, "PETG", "000000FF", "GFG00"),
}


def make_3mf(path, plates=(3,), slice_info=SLICE_INFO, aux=False, plate_json=PLATE_JSON):
    with zipfile.ZipFile(path, "w") as z:
        for n in plates:
            z.writestr(f"Metadata/plate_{n}.gcode", "; gcode\n")
            z.writestr(f"Metadata/plate_{n}.gcode.md5", "0" * 32)
            z.writestr(f"Metadata/plate_{n}.png", b"\x89PNG")
            if plate_json:
                z.writestr(f"Metadata/plate_{n}.json", plate_json)
        if slice_info:
            z.writestr("Metadata/slice_info.config", slice_info)
        if aux:
            z.writestr("Auxiliaries/Assembly Guide/manual.pdf", b"x" * 1000)
    return path


def test_finds_the_plate_that_actually_exists(tmp_path):
    f = make_3mf(tmp_path / "t.3mf", plates=(3,))
    info = threemf.inspect(f)
    assert [p.index for p in info.plates] == [3]
    assert info.plate().gcode_path == "Metadata/plate_3.gcode"


def test_asking_for_a_missing_plate_fails_loudly(tmp_path):
    """宁可在提交时报错，也不要让打印机在半夜报 SD 卡错误。"""
    f = make_3mf(tmp_path / "t.3mf", plates=(3,))
    with pytest.raises(ValueError, match="plate_1"):
        threemf.inspect(f).plate(1)


def test_multiple_plates_require_explicit_choice(tmp_path):
    f = make_3mf(tmp_path / "t.3mf", plates=(1, 2, 5))
    info = threemf.inspect(f)
    with pytest.raises(ValueError, match="多个盘"):
        info.plate()
    assert info.plate(5).gcode_path == "Metadata/plate_5.gcode"


def test_reads_filaments_and_flags_ams(tmp_path):
    info = threemf.inspect(make_3mf(tmp_path / "t.3mf"))
    plate = info.plate()
    assert plate.needs_ams
    assert plate.filaments[0].type == "PETG"
    assert plate.filaments[0].rgb == "FF671F"


def test_counts_auxiliaries(tmp_path):
    """24.9MB 的 3mf 里 24MB 是说明书 PDF——值得提醒，别白传。"""
    info = threemf.inspect(make_3mf(tmp_path / "t.3mf", aux=True))
    assert info.aux_bytes == 1000


def test_reads_bed_type_and_md5(tmp_path):
    """bed_type 写死 "auto" 是猜的，3mf 的 plate_N.json 里有真值。"""
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    assert plate.bed_type == "textured_plate"
    assert plate.nozzle_diameter == 0.4
    assert plate.md5 == "0" * 32


def test_bed_type_falls_back_when_json_missing(tmp_path):
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf", plate_json=None)).plate()
    assert plate.bed_type == "auto"


def test_ams_picks_nearest_color_within_same_model(tmp_path):
    """真实场景：3mf 是 #FF671F，AMS 槽 0 手填的是 #F98C36，两者永远不会相等。

    三个槽都是 GFG00 PETG，必须靠颜色距离选中橙色那个，而不是撞上白色或黑色。
    """
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    mapping, notes = threemf.match_ams(plate, REAL_TRAYS)
    assert mapping == [0]
    assert any("相近" in n for n in notes)


def test_ams_prefers_model_id_over_type(tmp_path):
    """同为 PETG，但只有 GFG00 那个是同型号——即使另一个颜色更接近也该选同型号。"""
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    trays = {
        0: tray(0, "PETG", "FF671FFF", "GFXXX"),   # 颜色完全一致，但型号不对
        1: tray(1, "PETG", "F98C36FF", "GFG00"),   # 型号对，颜色略差
    }
    mapping, _ = threemf.match_ams(plate, trays)
    assert mapping == [1]


def test_ams_exact_color_match_reports_no_warning(tmp_path):
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    mapping, notes = threemf.match_ams(plate, {0: tray(0, "PETG", "FF671FFF", "GFG00")})
    assert mapping == [0]
    assert any("颜色一致" in n for n in notes)


def test_ams_falls_back_to_same_type_when_model_absent(tmp_path):
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    mapping, notes = threemf.match_ams(plate, {1: tray(1, "PETG", "000000FF", "GFOTHER")})
    assert mapping == [1]
    assert any("同类型" in n for n in notes)


def test_ams_no_match_maps_to_external(tmp_path):
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    mapping, notes = threemf.match_ams(plate, {0: tray(0, "PLA", "FFFFFFFF", "GFA18")})
    assert mapping == [-1]
    assert any("找不到" in n for n in notes)


def test_ams_flags_empty_spool(tmp_path):
    plate = threemf.inspect(make_3mf(tmp_path / "t.3mf")).plate()
    _, notes = threemf.match_ams(plate, {0: tray(0, "PETG", "F98C36FF", "GFG00", remain=0)})
    assert any("剩余量为 0" in n for n in notes)


def test_color_distance():
    assert threemf.color_distance("FF671F", "FF671F") == 0
    # 橙对橙 远小于 橙对白
    assert threemf.color_distance("FF671F", "F98C36") < threemf.color_distance("FF671F", "FFFFFF")
