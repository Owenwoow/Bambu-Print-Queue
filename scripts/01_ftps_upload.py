"""第 1 步：打通 FTPS 上传。

    python scripts/01_ftps_upload.py path/to/model.gcode.3mf

判据：脚本报成功，且文件出现在打印机存储上。
挂住不动 → A1 的数据通道 SSL 问题，把 config.toml 里 ftps_encrypt_data 设 false；
仍不行就用命令行 curl：

    curl --ftp-ssl --insecure --user "bblp:CODE" -T model.gcode.3mf "ftps://IP:990/"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bpq.config import load as load_config  # noqa: E402
from bpq.transport.base import TransportError  # noqa: E402
from bpq.transport.lan import LanTransport  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    local = Path(sys.argv[1]).resolve()
    if not local.is_file():
        print(f"文件不存在: {local}")
        return 2

    cfg = load_config()
    size_mb = local.stat().st_size / 1024 / 1024
    print(f"上传 {local.name} ({size_mb:.1f} MB) → {cfg.printer.ip}:{cfg.transport.ftps_port}")
    print("现在开始盯着打印机：有没有声音、屏幕有没有亮、功率计有没有动。")

    t0 = time.monotonic()
    try:
        LanTransport(cfg).upload(local, local.name)
    except TransportError as exc:
        print(f"失败: {exc}")
        return 1
    print(f"上传成功，耗时 {time.monotonic() - t0:.1f}s")
    print("如果刚才打印机全程无声无光 —— 地基假设成立。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
