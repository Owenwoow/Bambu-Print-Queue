"""解析 3mf，把 project_file 需要的字段从文件里读出来，而不是让人手填。

血的教训（2026-08-24）：
- 默认 `param = "Metadata/plate_1.gcode"` 是照抄社区文档的，但 Studio 导出第 3 个盘时，
  3mf 里只有 `Metadata/plate_3.gcode`。打印机按不存在的路径去 SD 卡上找，报存储错误——
  看起来像 SD 卡坏了，其实是参数错。
- 默认 `bed_type = "auto"` 同样是猜的。3mf 的 `Metadata/plate_N.json` 里写着真值
  （本机是 `textured_plate`）。
- AMS 匹配一开始只比颜色是否相等，必然匹配失败：3mf 里的颜色是切片时耗材配置的颜色，
  AMS 里的颜色是用户给槽位手填的（AMS lite 无 RFID，`tag_uid` 全 0）。
  正确依据是 `tray_info_idx`（耗材型号，如 GFG00）+ 颜色**距离最近**。
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from bpq.models import AmsTray

log = logging.getLogger(__name__)

PLATE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")

# 颜色距离超过这个值就提醒人确认。经验值：FF671F 到 F98C36 是 44，到白色是 271。
COLOR_WARN_DISTANCE = 120.0


@dataclass
class Filament:
    """slice_info.config 里的一条耗材记录。id 是 1-based。"""

    id: int
    type: str = ""
    color: str = ""          # 形如 #FF671F
    info_idx: str = ""       # tray_info_idx，如 GFG00 = Bambu PETG Basic。精确匹配就靠它
    used_g: float = 0.0

    @property
    def rgb(self) -> str:
        """归一化成 6 位大写 hex，便于和 AMS 上报的 8 位 RRGGBBAA 比对。"""
        return self.color.lstrip("#").upper()[:6]


@dataclass
class Plate:
    index: int
    gcode_path: str
    filaments: list[Filament] = field(default_factory=list)
    # Studio 会一并打包 Metadata/plate_N.gcode.md5。project_file 有个 md5 字段，
    # 既然文件里现成就有，填上比发空字符串保险。
    md5: str = ""
    bed_type: str = "auto"          # 来自 plate_N.json，如 textured_plate
    nozzle_diameter: float = 0.0
    # 切片器算出的预计耗时与耗材重量，来自 slice_info.config 的 plate metadata。
    # 这两个就是 Studio「发送打印任务」对话框顶上「25m51s / 3.40g」那一行的数据源。
    prediction_sec: float = 0.0
    weight_g: float = 0.0

    @property
    def needs_ams(self) -> bool:
        """有耗材记录就说明切片时挂了料位，需要 use_ams + ams_mapping。"""
        return bool(self.filaments)


@dataclass
class ThreeMF:
    path: Path
    plates: list[Plate]
    aux_bytes: int = 0     # Auxiliaries/ 的体积，Studio 下发时会剥掉

    def plate(self, index: int | None = None) -> Plate:
        """取指定盘；不指定且只有一个盘时取那个，多个盘则要求显式指定。"""
        if index is not None:
            for p in self.plates:
                if p.index == index:
                    return p
            raise ValueError(
                f"{self.path.name} 里没有 plate_{index}；"
                f"有的是 {[p.index for p in self.plates]}"
            )
        if not self.plates:
            raise ValueError(f"{self.path.name} 里没有任何 plate gcode，这不是切好的文件")
        if len(self.plates) > 1:
            raise ValueError(
                f"{self.path.name} 里有多个盘 {[p.index for p in self.plates]}，"
                "请用 --plate 指定一个"
            )
        return self.plates[0]


def inspect(path: str | Path) -> ThreeMF:
    """打开 3mf，读出它真正包含的 plate、耗材、热床类型。"""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        aux = sum(i.file_size for i in z.infolist() if i.filename.startswith("Auxiliaries/"))

        plates: dict[int, Plate] = {}
        for n in sorted(names):
            m = PLATE_RE.match(n)
            if not m:
                continue
            idx = int(m.group(1))
            plate = Plate(index=idx, gcode_path=n)
            if f"{n}.md5" in names:
                plate.md5 = z.read(f"{n}.md5").decode("ascii", "ignore").strip()
            _read_plate_json(z, names, idx, plate)
            plates[idx] = plate

        if "Metadata/slice_info.config" in names:
            _parse_slice_info(z.read("Metadata/slice_info.config"), plates)

    return ThreeMF(path=path, plates=[plates[k] for k in sorted(plates)], aux_bytes=aux)


def _read_plate_json(z: zipfile.ZipFile, names: set[str], idx: int, plate: Plate) -> None:
    """plate_N.json 里有 bed_type 与 nozzle_diameter——比我们猜 "auto" 靠谱。"""
    key = f"Metadata/plate_{idx}.json"
    if key not in names:
        return
    try:
        data = json.loads(z.read(key))
    except ValueError:
        return
    plate.bed_type = data.get("bed_type") or plate.bed_type
    plate.nozzle_diameter = float(data.get("nozzle_diameter") or 0)


def _parse_slice_info(data: bytes, plates: dict[int, Plate]) -> None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return
    for plate_el in root.iter("plate"):
        meta = {md.get("key"): md.get("value", "") for md in plate_el.findall("metadata")}
        raw_idx = meta.get("index")
        idx = int(raw_idx) if raw_idx else None
        if idx is None or idx not in plates:
            continue
        plates[idx].prediction_sec = _as_float(meta.get("prediction"))
        plates[idx].weight_g = _as_float(meta.get("weight"))
        for fl in plate_el.findall("filament"):
            plates[idx].filaments.append(
                Filament(
                    id=int(fl.get("id", "0")),
                    type=fl.get("type", ""),
                    color=fl.get("color", ""),
                    info_idx=fl.get("tray_info_idx", ""),
                    used_g=float(fl.get("used_g", "0") or 0),
                )
            )


def color_distance(a: str, b: str) -> float:
    """两个 hex 颜色的欧氏距离。取不到就返回一个大数，让它排最后。"""
    try:
        ar, ag, ab = (int(a[i:i + 2], 16) for i in (0, 2, 4))
        br, bg, bb = (int(b[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 1e9
    return ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2) ** 0.5


def match_ams(
    plate: Plate,
    trays: dict[int, AmsTray],
    *,
    external_id: int = -1,
    slot_count: int | None = None,
) -> tuple[list[int], list[str]]:
    """把 3mf 的耗材配到 AMS 托盘上，返回 (ams_mapping, 提示列表)。

    **`ams_mapping[i]` 对应 filament id == i+1**（slice_info.config 里的 id 是 1-based，
    而 plate_N.json 的 filament_ids 是 0-based，两者差 1——这是最容易搞错的地方）。
    数组长度取 slot_count，没给就用 plate 里最大的 filament id。
    该盘没用到的位置填 external_id。

    为什么不能直接按顺序 append：v0.1 就是那么写的，单色永远撞不上，但只要某个盘
    用的是项目里的 1 号和 3 号耗材（`slice_info.config` 只列这两条），密集追加就会
    得到 `[ta, tc]`，把 3 号的托盘放到了 2 号的位置上。**打印机会照单全收，
    用错料，而且全程不报错**——等发现的时候件已经打废了。

    选法（和 Studio 下发界面的行为对齐）：
      1. 先按 tray_info_idx 缩小到同型号的托盘（如 GFG00）；没有同型号就退到同 tray_type。
      2. 在候选里挑**颜色距离最近**的那个——不能要求颜色相等，AMS lite 没有 RFID，
         槽位颜色是手填的，和切片时的耗材颜色本来就不会一致。
      3. 距离偏大时给提示，让人自己看一眼。
      4. 没有候选就填 external_id（走外部料）。

    external_id 做成参数是因为它**尚未实测**：社区实现里 -1 和 255 都见过。
    取值来自 config 的 [print] external_spool_id，验证清楚后改一行配置即可。
    """
    notes: list[str] = []
    if not plate.filaments:
        return [], notes

    # 按 filament id 定位下标，而不是按出现顺序追加。没用到的槽位留 external_id。
    length = slot_count or max(f.id for f in plate.filaments)
    mapping: list[int] = [external_id] * length

    used = {f.id for f in plate.filaments}
    gaps = sorted(set(range(1, length + 1)) - used)
    if gaps:
        notes.append(
            f"这个盘只用到耗材 {sorted(used)}，项目里的 {gaps} 没用上，"
            f"对应位置填 {external_id} 占位"
        )

    for f in sorted(plate.filaments, key=lambda x: x.id):
        if not 1 <= f.id <= length:
            notes.append(f"⚠ 耗材编号 {f.id} 超出范围 1–{length}，已跳过")
            continue
        idx = f.id - 1
        same_model = [t for t in trays.values() if f.info_idx and t.info_idx == f.info_idx]
        candidates = same_model or [t for t in trays.values() if t.type == f.type]

        if not candidates:
            mapping[idx] = external_id
            notes.append(
                f"耗材 {f.id}（{f.type} #{f.rgb}）在 AMS 里找不到同型号也找不到同类型，"
                f"映射为 {external_id}（外部料）"
            )
            continue

        best = min(candidates, key=lambda t: color_distance(f.rgb, t.rgb))
        mapping[idx] = best.id

        dist = color_distance(f.rgb, best.rgb)
        how = "同型号" if same_model else f"同类型（AMS 里没有 {f.info_idx}）"
        if dist == 0:
            notes.append(f"耗材 {f.id}（{f.type} #{f.rgb}）→ tray {best.id}，{how}且颜色一致")
        elif dist <= COLOR_WARN_DISTANCE:
            notes.append(
                f"耗材 {f.id}（{f.type} #{f.rgb}）→ tray {best.id}"
                f"（#{best.rgb}），{how}，颜色相近（距离 {dist:.0f}）"
            )
        else:
            notes.append(
                f"⚠ 耗材 {f.id}（{f.type} #{f.rgb}）→ tray {best.id}"
                f"（#{best.rgb}），{how}，但颜色差得远（距离 {dist:.0f}）——确认一下是不是这卷"
            )
        if best.remain == 0:
            notes.append(f"⚠ tray {best.id} 上报剩余量为 0")

    return mapping, notes


def _as_float(value: str | None) -> float:
    """切片器写出来的数值字段偶尔缺失或为空串，取不到就当 0，不要因此让整个解析失败。"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def thumbnail(path: str | Path, index: int, *, small: bool = False) -> bytes | None:
    """取某个盘的预览图。Studio 已经把图打包进 3mf 了，不需要我们自己渲染。

    大图约 20 KB、小图约 5 KB。取不到返回 None——没有预览图不该让提交流程失败。
    """
    big = f"Metadata/plate_{index}.png"
    tiny = f"Metadata/plate_{index}_small.png"
    order = (tiny, big) if small else (big, tiny)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for name in order:
            if name in names:
                return z.read(name)
    return None


@dataclass
class SlimResult:
    """slim() 的结果，调用方拿它打一行「26.0 MB → 0.4 MB」的日志。"""

    path: Path
    before: int
    after: int
    dropped: int          # 丢掉的条目数

    @property
    def saved_ratio(self) -> float:
        return 0.0 if not self.before else 1 - self.after / self.before

    def __str__(self) -> str:
        return (f"{self.before / 1e6:.2f} MB → {self.after / 1e6:.2f} MB"
                f"（省掉 {self.saved_ratio * 100:.0f}%，丢弃 {self.dropped} 个条目）")


def slim(src: str | Path, dst: str | Path) -> SlimResult:
    """重打包一个不含 `Auxiliaries/` 的 3mf，供上传给打印机。

    为什么必须做：FTPS 吞吐只有约 46 KB/s（ESP32 硬件上限）。同一个模型，Studio 导出的
    完整 3mf 带着装配说明 PDF 和模型图共 26 MB，要传 **9 分钟**；剥掉之后 369 KB，8 秒。
    v0.1 靠用户自己手动准备精简版绕过了这件事，但 WebUI 一旦让人从浏览器拖一个原始
    3mf 进来，这个坑就必然踩上。

    只剥 `Auxiliaries/`，别的一个条目都不动——这不是保守，是实测结论：
    拿同一个模型的「Studio 精简版」和「原始导出版」逐条目比对，两者的非 Auxiliaries
    条目**完全一致**（42 个对 42 个，连其他盘的预览图 Studio 都留着）。
    也就是说 Studio 自己的「精简」就等于「删掉 Auxiliaries/」。既然有现成的正确答案，
    就没有理由自己再去猜哪些条目能删——猜错的代价是打印机报一个看起来像硬件故障的
    文件错误。

    返回 SlimResult；即使没有 Auxiliaries/ 可剥也会照常产出 dst，让调用方的路径统一。
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dropped = 0
    with zipfile.ZipFile(src) as zin:
        # 保留原压缩方式与逐条目的压缩级别语义：直接读原始条目再写出去。
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.startswith("Auxiliaries/"):
                    dropped += 1
                    continue
                zout.writestr(item, zin.read(item.filename))
    result = SlimResult(
        path=dst, before=src.stat().st_size, after=dst.stat().st_size, dropped=dropped
    )
    log.info("3mf 瘦身 %s: %s", src.name, result)
    return result
