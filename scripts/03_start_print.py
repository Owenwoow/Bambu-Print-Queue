"""第 3 步：打通 MQTT 启动打印（本项目最高风险的一步）。

    python scripts/03_start_print.py <本地 3mf 路径> [--plate N] [--no-ams] [--upload]
                                     [--allow-failed]

传**本地文件路径**，不是打印机上的文件名——脚本自己取 basename、自己从 3mf 里
读出该用哪个 plate、自己按 AMS 里的实际料配 ams_mapping，并在下发前确认文件
已经完整躺在打印机上。

前置：**Developer Mode 已开**（固件 01.05.00.00 起有授权控制），打印机 idle。
若上一单以 FAILED 收场，机器其实是闲的，但板子可能还有残骸——
清理干净后加 --allow-failed 继续。

判据：gcode_state 从 IDLE/FINISH 转 RUNNING。
报 HMS 0500-0500-0001-0007 = 没开 Developer Mode 或固件不匹配。

注意：这会真的开始打印。人要在场。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bpq import threemf  # noqa: E402
from bpq.config import load as load_config  # noqa: E402
from bpq.models import Task  # noqa: E402
from bpq.transport.base import TransportError  # noqa: E402
from bpq.transport.lan import ImplicitFTP_TLS, LanTransport, insecure_ssl_context  # noqa: E402


def remote_size(cfg, name: str) -> int | None:
    """打印机上这个文件多大？不存在返回 None。"""
    ftp = ImplicitFTP_TLS(context=insecure_ssl_context())
    try:
        ftp.connect(host=cfg.printer.ip, port=cfg.transport.ftps_port, timeout=15)
        ftp.login(user="bblp", passwd=cfg.printer.access_code)
        ftp.prot_p()
        ftp.set_pasv(True)
        try:
            return ftp.size(name)
        except Exception:
            return None
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2

    local = Path(args[0]).resolve()
    if not local.is_file():
        print(f"本地文件不存在: {local}")
        print("提示：这个参数是**本地 3mf 路径**，不是打印机上的文件名。")
        return 2

    plate_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--plate" and i + 1 < len(sys.argv):
            plate_arg = int(sys.argv[i + 1])

    cfg = load_config()

    # --- 1. 从 3mf 里读出真相，而不是猜 ---
    info = threemf.inspect(local)
    print(f"3mf: {local.name}  ({local.stat().st_size / 1e6:.1f} MB)")
    print(f"  包含的盘: {[p.index for p in info.plates]}")
    if info.aux_bytes:
        print(f"  其中 Auxiliaries/ 占 {info.aux_bytes / 1e6:.1f} MB（说明书等，打印用不到）")
    try:
        plate = info.plate(plate_arg)
    except ValueError as exc:
        print(f"  {exc}")
        return 2
    print(f"  将使用: {plate.gcode_path}")
    print(f"  bed_type: {plate.bed_type}   喷嘴: {plate.nozzle_diameter}")
    print(f"  md5: {plate.md5 or '（3mf 里没有）'}")
    for f in plate.filaments:
        print(f"  耗材 {f.id}: {f.type} #{f.rgb}  {f.info_idx}  {f.used_g}g")

    # --- 2. 确认文件完整躺在打印机上 ---
    size = remote_size(cfg, local.name)
    local_size = local.stat().st_size
    if size is None:
        print(f"\n打印机上没有 {local.name}")
        if "--upload" not in flags:
            print("先跑 scripts/01_ftps_upload.py 传上去，或加 --upload 让本脚本传。")
            return 2
        print("正在上传……")
        LanTransport(cfg).upload(local, local.name)
        print("上传完成")
    elif size != local_size:
        print(f"\n打印机上的 {local.name} 是残缺的：{size} / {local_size} 字节")
        if "--upload" not in flags:
            print("重传一次（scripts/01_ftps_upload.py，或本脚本加 --upload）。")
            return 2
        print("正在重传覆盖……")
        LanTransport(cfg).upload(local, local.name)
        print("重传完成")
    else:
        print(f"\n打印机上已有完整文件（{size} 字节）")

    # --- 3. 配 AMS ---
    with LanTransport(cfg) as tp:
        # get_state() 内部会等首个 pushall 全量报文，这里不用再 sleep。
        state = tp.get_state()

        use_ams = plate.needs_ams and "--no-ams" not in flags
        mapping = [0]
        if use_ams:
            trays = tp.get_ams_trays()
            print("\nAMS 托盘：")
            if trays:
                for tid, t in sorted(trays.items()):
                    print(f"  tray {tid}: {t.type}  #{t.rgb}  {t.info_idx}  "
                          f"剩余 {t.remain}%  k={t.k:.3f}")
            else:
                print("  （读不到）")
            mapping, notes = threemf.match_ams(plate, trays)
            for n in notes:
                print(f"  {n}")
            print(f"  ams_mapping = {mapping}")
        else:
            print("\n不使用 AMS（切片未挂料位或指定了 --no-ams）")

        task = Task(
            source_path=str(local),
            scheduled_at=datetime.now(),
            remote_name=local.name,
            plate=plate.gcode_path,
            md5=plate.md5,
            bed_type=plate.bed_type,
            use_ams=use_ams,
            ams_mapping=mapping,
        )

        print(f"\n减噪 flag: bed_leveling={cfg.print.bed_leveling} "
              f"vibration_cali={cfg.print.vibration_cali} flow_cali={cfg.print.flow_cali}")
        print(f"启动前 gcode_state = {state.value}")
        if state.needs_attention and "--allow-failed" not in flags:
            print("上一单以 FAILED 收场。机器其实是闲的，但板子上可能还留着残骸。")
            print("确认板子已清理后，加 --allow-failed 重跑。")
            return 1
        if not state.is_idle and not state.needs_attention:
            print(f"打印机{'正忙' if state.is_busy else '状态未知'}，中止。")
            return 1
        if input("\n这会真的开始打印。继续？[y/N] ").strip().lower() != "y":
            return 1

        try:
            tp.start(task)
        except TransportError as exc:
            print(f"启动被拒: {exc}")
            return 1

        print("已下发 project_file，观察状态：")
        for _ in range(30):
            time.sleep(2)
            state = tp.get_state()
            print(f"  {time.strftime('%H:%M:%S')}  {state.value}")
            if state.value == "RUNNING":
                print("\n转 RUNNING —— 通道 A 全线打通，可以开始写调度器了。")
                return 0
            if state.value == "FAILED":
                print("\n转 FAILED。查打印机屏幕上的 HMS 码，并核对 param 路径与 AMS 映射。")
                return 1

    print("\n30 秒内没转 RUNNING。查打印机屏幕上有没有 HMS 报错码。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
