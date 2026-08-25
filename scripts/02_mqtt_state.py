"""第 2 步：打通 MQTT 读状态 + 记固件版本。

    python scripts/02_mqtt_state.py [持续秒数，默认 30]

判据：能稳定读到 gcode_state，并打印出固件版本号。
**把版本号记进 docs/** —— 后面要在这个版本上锁定不升级。

注意打印机同一时刻只接受一个 MQTT 客户端：跑这个脚本时别开着 Studio/OrcaSlicer。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bpq.config import load as load_config  # noqa: E402
from bpq.transport.lan import LanTransport  # noqa: E402


def main() -> int:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cfg = load_config()
    print(f"连接 {cfg.printer.ip}:{cfg.transport.mqtt_port}，"
          f"订阅 device/{cfg.printer.serial}/report")

    with LanTransport(cfg) as tp:
        tp.get_state()   # 触发连接 + pushall
        time.sleep(3)

        versions = tp.get_version()
        time.sleep(2)
        versions = tp.get_version()
        if versions:
            print("固件版本：")
            for name, ver in versions.items():
                print(f"  {name:<12} {ver}")
        else:
            print("没收到 get_version 回执（不影响后续步骤，但版本号要另想办法记下来）")

        print(f"\n持续读状态 {duration}s（Ctrl-C 停）：")
        last = None
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            state = tp.get_state()
            if state != last:
                print(f"  {time.strftime('%H:%M:%S')}  gcode_state = {state.value}")
                last = state
            time.sleep(1)

    if last is None:
        print("一次状态都没读到 —— 检查 Access Code / SERIAL / 端口。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
