"""第 5 步（v0.2 取数）：dump 一个 3mf 的全部 metadata，为多色映射语义取证。

    python scripts/05_dump_3mf.py path/to/multicolor.gcode.3mf

**零风险、零真机**：只读一个本地 zip 文件，不连打印机、不发任何指令。

要取的证是什么：`project_file` 指令里的 `ams_mapping` 是一个整数数组，但它的语义
有三个未知量，而猜错的后果是「打印机照单全收、用错料、且不报错」：

  1. 数组**长度**是「这个盘用到的耗材数」还是「整个项目的耗材槽数」？
  2. 下标 i 对应的是第 i 号耗材，还是第 i+1 号？
  3. 不使用 AMS 的那一位填 -1 还是 255？

前两个问题这个脚本就能回答：切一个真实的双色（或更好：三色但某个盘只用其中两色）
3mf，对比 `Metadata/plate_N.json` 的 `filament_ids` / `filament_colors` 数组
与 `Metadata/slice_info.config` 里 `<filament id=...>` 的集合——两者的差集直接
说明数组该多长、下标怎么对齐。

第三个问题只能靠真机试（先发 -1，被拒再发 255），见 docs/v0.2-多色映射语义.md。

顺带把 Bambu Studio 那条捷径的结论记在这里：Studio 自己的日志
（%APPDATA%/BambuStudio/log/studio_*_enc_cn.log.0）是 **AES 加密**的——只有一段
明文 header（含 enc_block_size / enc_key_tag），正文可打印字符仅 43%，
grep project_file 零命中。**捞不到它自己发的 payload**，别在那上面浪费时间。
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

PLATE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")

# 直接原样打印的文本条目（小且信息密度高）
DUMP_TEXT = ("Metadata/slice_info.config", "Metadata/filament_sequence.json")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"找不到文件：{path}")
        return 1

    with zipfile.ZipFile(path) as z:
        names = sorted(z.namelist())

        print(f"=== {path.name}（{path.stat().st_size / 1e6:.2f} MB）===\n")

        aux = sum(i.file_size for i in z.infolist() if i.filename.startswith("Auxiliaries/"))
        if aux:
            print(f"⚠ Auxiliaries/ 占 {aux / 1e6:.2f} MB。FTPS 吞吐只有 ~46 KB/s，"
                  f"直传要 {aux / 46_000 / 60:.1f} 分钟——必须先剥掉（threemf.slim）。\n")

        plates = [int(m.group(1)) for n in names if (m := PLATE_RE.match(n))]
        print(f"含 gcode 的盘：{plates or '（没有！这不是切好的文件）'}\n")

        for idx in plates:
            key = f"Metadata/plate_{idx}.json"
            if key not in names:
                print(f"--- plate_{idx}.json 不存在 ---\n")
                continue
            data = json.loads(z.read(key))
            print(f"--- Metadata/plate_{idx}.json 的关键字段 ---")
            for k in ("bed_type", "nozzle_diameter", "first_extruder",
                      "filament_ids", "filament_colors", "version"):
                if k in data:
                    print(f"  {k:<18} {data[k]!r}")
            print()

        if "Metadata/slice_info.config" in names:
            print("--- slice_info.config 里每个盘的耗材与用量 ---")
            root = ET.fromstring(z.read("Metadata/slice_info.config"))
            for plate_el in root.iter("plate"):
                meta = {md.get("key"): md.get("value") for md in plate_el.findall("metadata")}
                idx = meta.get("index", "?")
                print(f"  plate {idx}: prediction={meta.get('prediction')}s "
                      f"weight={meta.get('weight')}g "
                      f"filament_maps={meta.get('filament_maps')!r}")
                ids = []
                for fl in plate_el.findall("filament"):
                    ids.append(fl.get("id"))
                    print(f"    filament id={fl.get('id')} "
                          f"type={fl.get('type')} color={fl.get('color')} "
                          f"tray_info_idx={fl.get('tray_info_idx')} "
                          f"used_g={fl.get('used_g')}")
                print(f"    → 该盘用到的 filament id 集合：{ids}")
                pj = f"Metadata/plate_{idx}.json"
                if pj in names:
                    pdata = json.loads(z.read(pj))
                    fids = pdata.get("filament_ids")
                    if fids is not None:
                        print(f"    → plate_{idx}.json 的 filament_ids：{fids}")
                        print("    ★ 对比这两行：若不一致，说明 ams_mapping 的长度与下标"
                              "必须按项目全局槽位算，而不是按该盘用到的耗材密集排列。")
                print()

        for name in DUMP_TEXT:
            if name in names and name != "Metadata/slice_info.config":
                print(f"--- {name} ---")
                print(z.read(name).decode("utf-8", "ignore"))
                print()

        print("--- 全部条目 ---")
        for i in z.infolist():
            if i.filename.startswith("Auxiliaries/"):
                continue
            print(f"  {i.file_size:>10}  {i.filename}")
        if aux:
            n_aux = sum(1 for i in z.infolist() if i.filename.startswith("Auxiliaries/"))
            print(f"  （另有 Auxiliaries/ 下 {n_aux} 个条目共 {aux / 1e6:.2f} MB，已略）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
