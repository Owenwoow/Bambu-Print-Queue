"""第 4 步（v0.2 取数）：把一份**真实的 pushall 全量报文**落盘。

    python scripts/04_dump_pushall.py [等待秒数，默认 15]

产出两个文件：
    docs/samples/pushall_raw.json          原样存档（含 SERIAL 等，已被 .gitignore 挡住）
    tests/fixtures/reports/pushall_full.json   脱敏版，给 snapshot/report 模块当测试夹具

**这是取数，不是验收。** 全程只订阅、只发 pushall 查询，不下发任何指令，
打印机不会有任何物理动作，也不需要人站在旁边。

为什么必须做这一步：A1 的 report 字段名只能靠实测。这个项目已经被「社区文档与实测
相反」坑过两次（FTPS 数据通道必须加密、plate_N.gcode 路径不一定是 plate_1），
拿着真样本建模，比照着社区文档猜字段名靠谱得多。

注意打印机同一时刻只接受一个 MQTT 客户端：跑这个脚本时别开着 Studio/OrcaSlicer，
也别同时开着 bpq daemon。
"""

from __future__ import annotations

import json
import ssl
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bpq.config import load as load_config  # noqa: E402

# 认为「全量报文」的判据：print 段里同时有这几个键。增量包不会这么全。
FULL_MARKERS = ("gcode_state", "ams", "nozzle_temper")

# 脱敏时要抹掉的键名（大小写不敏感）。值替换成同长度的占位符，保留结构与类型。
SECRET_KEYS = {
    "sn", "serial", "dev_sn", "access_code", "password", "passwd",
    "ssid", "wifi_ssid", "user_id", "userid", "project_id", "task_id",
    "subtask_id", "profile_id", "tag_uid", "tray_uuid", "dev_id",
}


def redact(obj: object, serial: str) -> object:
    """递归脱敏。保留结构和类型，只把敏感值换掉——夹具要的是形状，不是真值。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in SECRET_KEYS and isinstance(v, str):
                out[k] = "X" * len(v) if v else v
            else:
                out[k] = redact(v, serial)
        return out
    if isinstance(obj, list):
        return [redact(i, serial) for i in obj]
    if isinstance(obj, str) and serial and serial in obj:
        return obj.replace(serial, "REDACTED_SERIAL")
    return obj


def main() -> int:
    import paho.mqtt.client as mqtt

    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    cfg = load_config()
    topic_report = f"device/{cfg.printer.serial}/report"
    topic_request = f"device/{cfg.printer.serial}/request"

    got = threading.Event()
    best: dict = {}
    count = 0
    lock = threading.Lock()

    def on_connect(client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        print(f"已连接，订阅 {topic_report}")
        client.subscribe(topic_report)
        # A1 是增量上报，连上先拉一次全量。这是只读查询，不破坏静默。
        client.publish(topic_request, json.dumps(
            {"pushing": {"sequence_id": "0", "command": "pushall",
                         "version": 1, "push_target": 1}}
        ))
        # 顺带把固件版本也要一份，info.module 里的 sw_ver 是锁版本用的
        client.publish(topic_request, json.dumps(
            {"info": {"sequence_id": "1", "command": "get_version"}}
        ))

    def on_message(client, userdata, msg):  # noqa: ANN001
        nonlocal best, count
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        with lock:
            count += 1
            report = payload.get("print", {})
            n_keys = len(report)
            print(f"  收到报文 #{count}：print 段 {n_keys} 个键"
                  f"{' ← 看起来是全量' if all(m in report for m in FULL_MARKERS) else ''}")
            # 挑 print 段键最多的那条留作样本；info 段（固件版本）另外并进来
            if n_keys > len(best.get("print", {})):
                best = {**best, **payload} if best else dict(payload)
            elif "info" in payload:
                best.setdefault("info", payload["info"])
            if all(m in best.get("print", {}) for m in FULL_MARKERS):
                got.set()

    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    cli.username_pw_set("bblp", cfg.printer.access_code)
    cli.tls_set(cert_reqs=ssl.CERT_NONE)
    cli.tls_insecure_set(True)
    cli.on_connect = on_connect
    cli.on_message = on_message

    print(f"连接 {cfg.printer.ip}:{cfg.transport.mqtt_port} …")
    try:
        cli.connect(cfg.printer.ip, cfg.transport.mqtt_port, keepalive=60)
    except OSError as exc:
        print(f"连接失败：{exc}")
        print("检查 IP / Access Code，以及是不是有别的客户端（Studio / bpq daemon）占着连接。")
        return 1
    cli.loop_start()

    # 拿到全量就可以停了，但多等一会儿好把 get_version 的回执一起收进来
    got.wait(wait)
    time.sleep(2)
    cli.loop_stop()
    cli.disconnect()

    if not best:
        print("\n一条报文都没收到 —— 检查 SERIAL 是否正确（topic 对不上就什么都收不到）。")
        return 1

    n = len(best.get("print", {}))
    if not all(m in best.get("print", {}) for m in FULL_MARKERS):
        missing = [m for m in FULL_MARKERS if m not in best.get("print", {})]
        print(f"\n⚠ 收到的报文里缺 {missing}，可能不是全量。样本仍会写出，但建议重跑。")

    root = Path(__file__).resolve().parents[1]
    raw_path = root / "docs" / "samples" / "pushall_raw.json"
    fx_path = root / "tests" / "fixtures" / "reports" / "pushall_full.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    fx_path.parent.mkdir(parents=True, exist_ok=True)

    raw_path.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
    fx_path.write_text(
        json.dumps(redact(best, cfg.printer.serial), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n共收到 {count} 条报文，最全的一条 print 段有 {n} 个键。")
    print(f"  原始存档  {raw_path.relative_to(root)}")
    print(f"  脱敏夹具  {fx_path.relative_to(root)}")
    print("\n顶层键：", list(best.keys()))
    print("print 段的键：")
    for k in sorted(best.get("print", {})):
        v = best["print"][k]
        kind = type(v).__name__
        preview = "" if isinstance(v, (dict, list)) else f" = {v!r}"
        print(f"  {k:<28} {kind}{preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
