"""多色 AMS 配料。

这里守的是一个**不会报错的**错误：`ams_mapping` 的下标错位之后，打印机照单全收，
用错料把件打废，全程没有任何提示。单色永远撞不上，所以 v0.1 一直没发现。
"""

from __future__ import annotations

import pytest

from bpq.models import AmsTray
from bpq.threemf import Filament, Plate, match_ams

# 一台装了四卷料的 AMS，照抄 docs/验证记录-通道A.md 里记的本机真实配置：
# 三卷 GFG00 PETG（橙/白/黑）+ 一卷 GFA18 PLA。
TRAYS = {
    0: AmsTray(id=0, type="PETG", color="F98C36FF", info_idx="GFG00", remain=100),
    1: AmsTray(id=1, type="PETG", color="FFFFFFFF", info_idx="GFG00", remain=100),
    2: AmsTray(id=2, type="PLA", color="FFFFFFFF", info_idx="GFA18", remain=100),
    3: AmsTray(id=3, type="PETG", color="000000FF", info_idx="GFG00", remain=100),
}


def plate(*filaments: Filament) -> Plate:
    return Plate(index=1, gcode_path="Metadata/plate_1.gcode", filaments=list(filaments))


def fil(fid: int, color: str, ftype: str = "PETG", idx: str = "GFG00") -> Filament:
    return Filament(id=fid, type=ftype, color=color, info_idx=idx, used_g=1.0)


# --------------------------------------------------------------- 索引错位


def test_盘只用一部分耗材时下标不错位() -> None:
    """项目里配了 3 卷料，这个盘只用了 1 号和 3 号。

    密集追加会得到 [橙, 黑]，把 3 号耗材的托盘放到了 2 号的位置上。
    正确的是 [橙, 占位, 黑]。
    """
    mapping, _ = match_ams(
        plate(fil(1, "#FF671F"), fil(3, "#000000")), TRAYS, external_id=-1
    )
    assert len(mapping) == 3, "长度要按最大的 filament id 算，不是按用到几个"
    assert mapping[0] == 0, "1 号耗材（橙）→ 橙色那卷"
    assert mapping[1] == -1, "2 号没用到，留占位"
    assert mapping[2] == 3, "3 号耗材（黑）→ 黑色那卷，不能被挤到下标 1"


def test_跳过的耗材会给出提示() -> None:
    _, notes = match_ams(plate(fil(1, "#FF671F"), fil(3, "#000000")), TRAYS)
    assert any("没用上" in n for n in notes)


def test_slot_count_可以显式指定() -> None:
    """项目里有 4 个槽位但这个盘只用了 1 号——数组长度该由项目决定还是由盘决定，
    是一个待真机确认的语义。做成参数，实测后改调用方即可。"""
    mapping, _ = match_ams(plate(fil(1, "#FF671F")), TRAYS, slot_count=4)
    assert len(mapping) == 4
    assert mapping[0] == 0
    assert mapping[1:] == [-1, -1, -1]


def test_耗材编号乱序也按_id_归位() -> None:
    """slice_info 里的顺序不保证。"""
    mapping, _ = match_ams(
        plate(fil(3, "#000000"), fil(1, "#FF671F")), TRAYS
    )
    assert mapping == [0, -1, 3]


# ------------------------------------------------------------------ 匹配


def test_双色各自匹配到最近的颜色() -> None:
    mapping, notes = match_ams(
        plate(fil(1, "#FF671F"), fil(2, "#FFFFFF")), TRAYS
    )
    assert mapping == [0, 1], "橙 → 橙那卷，白 → 白那卷"
    assert all("同型号" in n for n in notes if "→" in n)


def test_型号优先于颜色() -> None:
    """AMS 里有一卷白 PETG 和一卷白 PLA，颜色完全一样。

    切片用的是 PLA，就该配到 PLA 那卷——型号是硬约束，颜色只是在同型号里挑。
    """
    mapping, notes = match_ams(
        plate(fil(1, "#FFFFFF", ftype="PLA", idx="GFA18")), TRAYS
    )
    assert mapping == [2]
    assert "同型号" in notes[0]


def test_没有同型号时退到同类型并说明() -> None:
    trays = {0: AmsTray(id=0, type="PETG", color="FF671FFF", info_idx="GFG99")}
    _, notes = match_ams(plate(fil(1, "#FF671F", idx="GFG00")), trays)
    assert "同类型" in notes[0]
    assert "GFG00" in notes[0], "要说清楚是缺哪个型号才退化的"


def test_颜色差得远时给警告() -> None:
    """这是唯一能拦住「打错料」的地方——AMS lite 没有 RFID，
    槽位里的颜色是人手填的，自动匹配只能猜。"""
    trays = {0: AmsTray(id=0, type="PETG", color="FFFFFFFF", info_idx="GFG00")}
    _, notes = match_ams(plate(fil(1, "#FF671F")), trays)
    assert any("⚠" in n and "颜色差得远" in n for n in notes)


def test_剩余量为零时提醒() -> None:
    trays = {0: AmsTray(id=0, type="PETG", color="FF671FFF", info_idx="GFG00", remain=0)}
    _, notes = match_ams(plate(fil(1, "#FF671F")), trays)
    assert any("剩余量为 0" in n for n in notes)


def test_完全没有候选时填外部料() -> None:
    mapping, notes = match_ams(plate(fil(1, "#FF671F", ftype="ABS", idx="GFB00")), TRAYS)
    assert mapping == [-1]
    assert any("找不到" in n for n in notes)


def test_外部料哨兵值可配() -> None:
    """-1 还是 255 尚未实测，做成配置项就是为了改一行而不改代码。"""
    mapping, _ = match_ams(
        plate(fil(1, "#FF671F", ftype="ABS", idx="GFB00")), TRAYS, external_id=255
    )
    assert mapping == [255]


def test_ams_读不到时全填外部料而不是崩() -> None:
    mapping, notes = match_ams(plate(fil(1, "#FF671F"), fil(2, "#FFFFFF")), {})
    assert mapping == [-1, -1]
    assert len(notes) == 2


def test_没有耗材记录时返回空映射() -> None:
    """不用 AMS 的任务不该发一个假的映射过去。"""
    mapping, notes = match_ams(plate(), TRAYS)
    assert mapping == []
    assert notes == []


def test_三色全用上() -> None:
    mapping, _ = match_ams(
        plate(fil(1, "#FF671F"), fil(2, "#FFFFFF"), fil(3, "#000000")), TRAYS
    )
    assert mapping == [0, 1, 3]
    assert len(set(mapping)) == 3, "三种不同的颜色不该配到同一卷上"


@pytest.mark.parametrize("bad_id", [0, -1, 99])
def test_越界的耗材编号不炸(bad_id: int) -> None:
    """切片器写出来的东西不总是干净的；一条坏记录不该让整次配料失败。"""
    mapping, notes = match_ams(plate(fil(1, "#FF671F"), fil(bad_id, "#000000")), TRAYS)
    assert mapping[0] == 0
    if bad_id <= 0:
        assert any("超出范围" in n for n in notes)
