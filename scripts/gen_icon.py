"""生成 packaging/bpq.ico——exe 文件图标，和 WebUI favicon 同一套设计。

    python scripts/gen_icon.py

一次性脚本，改了 `bpq.icon.draw_mark` 的画法想重新生成再跑，不是构建流程
的一部分（`.github/workflows/release.yml` 只管把生成好的 ico 文件传给
PyInstaller 的 `--icon`，不会调用这个脚本）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bpq.icon import draw_mark  # noqa: E402

# 16x16 太小，从 256 缩放下来的 "b" 笔画容易糊成一团，所以每个尺寸单独现画，
# 而不是画一次大图再缩放。
SIZES = [16, 32, 48, 256]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_path = root / "packaging" / "bpq.ico"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    images = {size: draw_mark(size) for size in SIZES}
    largest = max(SIZES)
    base = images[largest]
    others = [img for size, img in images.items() if size != largest]

    base.save(
        out_path,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=others,
    )
    print(f"已生成 {out_path.relative_to(root)}（{SIZES}）")


if __name__ == "__main__":
    main()
