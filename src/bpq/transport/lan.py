"""通道 A：本地 MQTT(8883) + FTPS(990)。v0.1 主线。

前置条件（缺一不可，见技术报告第二节）：
1. 打印机开启 **LAN Only 模式**（A1 需开启后才显示 Access Code；显示全 0 就关掉再开）
2. 打印机开启 **Developer Mode**。固件 01.05.00.00 起 A1 有「授权控制」，
   不开会以 HMS 0500-0500-0001-0007（MQTT command verification failed）拒绝启动指令。
3. 同一时刻只能有一个 MQTT 客户端连打印机——daemon 常连时别同时开着 Studio/OrcaSlicer。

状态：本模块的代码路径尚未在真机上验证过。请先按 scripts/README.md 跑第 1-3 步。
"""

from __future__ import annotations

import ftplib
import json
import logging
import re
import ssl
import threading
from pathlib import Path

from bpq.config import Config
from bpq.models import AmsTray, PrinterState, Task
from bpq.report import ReportAccumulator
from bpq.snapshot import PrinterSnapshot
from bpq.transport.base import PrinterTransport, ReportListener, TransportError

log = logging.getLogger(__name__)

FTP_USER = "bblp"          # 所有拓竹机型固定
MQTT_USER = "bblp"
START_TIMEOUT = 15.0       # 等 project_file 回执的秒数
UNWRAP_TIMEOUT = 5.0       # 等数据通道 TLS close_notify 的耐心，见 storbinary
# 连上 MQTT 后等首个 pushall 全量报文的秒数。实测 1–3 秒回来，给到 10 秒是留足余量：
# 宁可多等几秒，也不能让定时任务因为「状态还没到」被误判成 UNKNOWN 而放弃。
STATE_TIMEOUT = 10.0
# 回执字典的上限。长连接下它只增不减会缓慢泄漏。
MAX_REPLIES = 32


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """支持 implicit FTPS 的 FTP_TLS 子类——ftplib 原生只支持 explicit。

    做法是在 sock setter 里立即把控制连接包成 TLS。数据通道是否加密由
    是否调用 prot_p() 决定——实测本机 A1 必须加密，明文数据通道会被直接断开。
    """

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._sock: ssl.SSLSocket | None = None

    @property
    def sock(self):  # noqa: ANN201
        return self._sock

    @sock.setter
    def sock(self, value) -> None:  # noqa: ANN001
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):  # noqa: ANN001, ANN201
        """覆盖 ftplib 的实现，容忍 A1 不回 TLS close_notify。

        标准 ftplib 传完数据后调 `conn.unwrap()` 做 TLS 优雅关闭，会一直等对方的
        close_notify。A1 不发——它直接把数据连接扔了。于是上传明明已经**完整成功**，
        却卡在 unwrap 上抛 TimeoutError，看起来像传输失败。

        （这大概就是社区所谓「A1 数据通道 SSL 会挂起」的真身：不是传不动，
        是收尾握手不回话。所以解法不是关掉数据通道加密——那样连接直接被拒。）
        """
        self.voidcmd("TYPE I")
        conn = self.transfercmd(cmd, rest)
        try:
            while buf := fp.read(blocksize):
                conn.sendall(buf)
                if callback:
                    callback(buf)
            if isinstance(conn, ssl.SSLSocket):
                try:
                    conn.settimeout(UNWRAP_TIMEOUT)
                    conn.unwrap()
                except (TimeoutError, OSError, ssl.SSLError) as exc:
                    # 数据已经发完了，对方不肯好好道别而已。
                    log.debug("数据通道 TLS 关闭握手无响应（%s），直接关闭", exc)
        finally:
            conn.close()
        return self.voidresp()


def insecure_ssl_context() -> ssl.SSLContext:
    """打印机用自签名证书，只能关校验。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# logger/ 目录下的文件名格式（实测，见 docs/验证记录-通道A.md 第 13 行）：
#   {SERIAL}_{MM-DD}_{HH_MM_SS.mmm}_v{固件版本}_idx_{N}.log
# 目录里偶尔还有一个叫 latest 的文件，不匹配这个模式，_parse_serial_from_logger_listing
# 会自然跳过它，不当错误处理。
_LOGGER_NAME_RE = re.compile(r"^([0-9A-Za-z]+)_\d{2}-\d{2}_.*_v[\d.]+_idx_\d+\.log$")


def _parse_serial_from_logger_listing(names: list[str]) -> str | None:
    """从 logger/ 目录的文件名列表里提取 SERIAL。

    拆成纯函数是为了能不碰网络就单元测试这一步——真正的正确性风险全在这条正则
    上，FTPS 握手本身已经在 upload() 里验证过很多次了。
    """
    for name in names:
        m = _LOGGER_NAME_RE.match(name)
        if m:
            return m.group(1)
    return None


def discover_serial(ip: str, access_code: str, *, port: int = 990, timeout: float = 15.0) -> str:
    """只用 IP + Access Code 连一次 FTPS，从 logger/ 目录文件名里读 SERIAL。

    不需要预先知道 SERIAL——FTPS 登录用固定用户名 bblp + Access Code，这正是能
    做「自动发现」的原因（对比 MQTT：topic 是 device/<SERIAL>/report，不知道
    SERIAL 就没法订阅，这条路走不通，见 docs/验证记录-通道A.md 第 84-86 行）。

    这是一次性探测连接，用完即关，不进 PrinterLink，不占用那条常驻 MQTT 连接的
    任何资源——FTPS 和 MQTT 是两个独立端口，互不影响，这条连接的开关顺序照抄
    upload() 里验证过的握手顺序。
    """
    ftp = ImplicitFTP_TLS(context=insecure_ssl_context())
    try:
        try:
            ftp.connect(host=ip, port=port, timeout=timeout)
            ftp.login(user=FTP_USER, passwd=access_code)
            ftp.prot_p()        # 同 upload()：数据通道必须加密，明文会被直接断开
            ftp.set_pasv(True)  # A1 只支持被动模式
        except (OSError, ftplib.Error) as exc:
            raise TransportError(
                f"FTPS 连接失败 ({ip}:{port}): {exc}。请检查 IP 和 Access Code 是否正确"
            ) from exc

        try:
            names = ftp.nlst("logger")
        except ftplib.error_perm as exc:
            raise TransportError(f"连接成功，但读取 logger 目录失败：{exc}") from exc
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 - 关连接出错不该盖掉发现结果/原始错误
            ftp.close()

    serial = _parse_serial_from_logger_listing([Path(n).name for n in names])
    if serial is None:
        raise TransportError(
            "连接成功，但 logger 目录里没有找到能识别出 SERIAL 的日志文件名"
            "（目录为空，或文件名格式与预期不一致），需要手动填写序列号"
        )
    return serial


class LanTransport(PrinterTransport):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # 惰性连接：CLI 只是列个任务时，不该去占用唯一的那个 MQTT 连接位。
        self._mqtt = None
        # 整份状态都由累积器维护：A1 是增量上报，必须跨报文累积才能有完整快照。
        self._acc = ReportAccumulator()
        self._listener: ReportListener | None = None
        self._lock = threading.Lock()
        self._replies: dict[str, dict] = {}
        self._reply_event = threading.Event()
        # 首个带 gcode_state 的报文到达时置位，get_state() 靠它避开「连上但还没收到」的空窗。
        self._state_event = threading.Event()

    # ---------------------------------------------------------------- FTPS

    def upload(self, local_path: Path, remote_name: str) -> None:
        """FTPS 上传。只写存储，不产生任何物理动作——本项目的地基。

        备用手段（数据通道仍挂起时用命令行）：
            curl --ftp-ssl --insecure --user "bblp:CODE" -T model.gcode.3mf ftps://IP:990/
        """
        p = self.cfg.printer
        t = self.cfg.transport
        ftp = ImplicitFTP_TLS(context=insecure_ssl_context())
        try:
            ftp.connect(host=p.ip, port=t.ftps_port, timeout=30)
            ftp.login(user=FTP_USER, passwd=p.access_code)
            if t.ftps_encrypt_data:
                ftp.prot_p()       # 本机 A1 实测必需；若大文件 STOR 挂起再关掉试
            ftp.set_pasv(True)     # A1 只支持被动模式，PORT 会被拒
            if t.ftps_remote_dir not in ("", "/"):
                ftp.cwd(t.ftps_remote_dir)
            with local_path.open("rb") as f:
                ftp.storbinary(f"STOR {remote_name}", f)

            # 校验大小。上传中断（Ctrl-C、网络抖动）会在打印机上留下一个残缺文件，
            # 而打印机不会告诉你它残缺——只会在启动时报一个看起来像硬件故障的错。
            local_size = local_path.stat().st_size
            try:
                remote_size = ftp.size(remote_name)
            except (OSError, ftplib.Error) as exc:
                log.warning("上传后无法校验大小（%s），跳过校验", exc)
            else:
                if remote_size != local_size:
                    raise TransportError(
                        f"上传不完整：本地 {local_size} 字节，打印机上只有 {remote_size} 字节。"
                        "打印机上那个残缺文件需要重传覆盖。"
                    )
        except (OSError, ftplib.Error) as exc:
            raise TransportError(f"FTPS 上传失败 ({p.ip}:{t.ftps_port}): {exc}") from exc
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001 - 关连接出错不该盖掉上传本身的结果
                ftp.close()

    # ---------------------------------------------------------------- MQTT

    def _topic(self, kind: str) -> str:
        return f"device/{self.cfg.printer.serial}/{kind}"

    def _ensure_mqtt(self):  # noqa: ANN202
        if self._mqtt is not None:
            return self._mqtt
        import paho.mqtt.client as mqtt

        p = self.cfg.printer
        port = self.cfg.transport.mqtt_port
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        cli.username_pw_set(MQTT_USER, p.access_code)
        cli.tls_set(cert_reqs=ssl.CERT_NONE)
        cli.tls_insecure_set(True)
        cli.on_connect = self._on_connect
        cli.on_message = self._on_message
        try:
            cli.connect(p.ip, port, keepalive=60)
        except OSError as exc:
            raise TransportError(f"MQTT 连接失败 ({p.ip}:{port}): {exc}") from exc
        cli.loop_start()
        self._mqtt = cli
        return cli

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:  # noqa: ANN001
        client.subscribe(self._topic("report"))
        # A1 是增量上报（只发变化字段），连上先拉一次全量。
        self._publish(
            {"pushing": {"sequence_id": "0", "command": "pushall",
                         "version": 1, "push_target": 1}}
        )

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            report = payload.get("print") or {}
            changed = self._acc.apply(payload)

            # ⚠ 只有真的解析出 gcode_state 才置位。A1 的大量增量报文不含这个字段，
            # 一律置位会让 get_state() 的等待被第一条无关的增量包提前结束——
            # 定时任务到点就会读到 UNKNOWN 而放弃。这是 v0.1 验收前修的最严重的 bug，
            # 改动这一段之前请先看 README「已知坑」的最后一条。
            if isinstance(report, dict) and report.get("gcode_state"):
                self._state_event.set()

            if isinstance(report, dict) and "result" in report and "sequence_id" in report:
                self._replies[str(report["sequence_id"])] = report
                # 长连接下这个字典只增不减会缓慢泄漏，只留最近的若干条。
                while len(self._replies) > MAX_REPLIES:
                    self._replies.pop(next(iter(self._replies)))
                self._reply_event.set()

            listener = self._listener

        # 回调放在锁外调用：监听方（PrinterLink → SSE）不该有机会把 paho 的
        # 接收线程连同这把锁一起卡住。
        if changed is not None and listener is not None:
            snap, patch = changed
            try:
                listener(snap, patch)
            except Exception:  # noqa: BLE001 - 监听方出错不能拖垮报文接收
                log.exception("report 监听回调出错")

    def _publish(self, payload: dict) -> None:
        cli = self._ensure_mqtt()
        cli.publish(self._topic("request"), json.dumps(payload))

    def get_state(self, timeout: float = STATE_TIMEOUT) -> PrinterState:
        """读当前 gcode_state；必要时等首个 pushall 全量报文到达。

        `_ensure_mqtt()` 一建好连接就返回，但 `self._state` 要等 `_on_message()` 收到
        全量报文（实测 1–3 秒）才有值。不等的话，连接后第一次调用几乎必然返回
        UNKNOWN——调度层会把它当成「打印机状态未知」而放弃任务。所以等待做在这里，
        而不是让每个调用方各自 sleep。

        拿到过状态之后 event 已置位，后续调用立即返回，不会重复等待。
        连不上或超时都返回 UNKNOWN，让调度层按「非空闲」处理，而不是炸掉 daemon。
        """
        try:
            self._ensure_mqtt()
        except TransportError as exc:
            log.warning("读取打印机状态失败: %s", exc)
            return PrinterState.UNKNOWN
        if not self._state_event.wait(timeout):
            log.warning("等待打印机状态报文超时（%.1fs），按 UNKNOWN 处理", timeout)
        with self._lock:
            return self._acc.snapshot().job.gcode_state

    def get_version(self) -> dict[str, str]:
        """info.get_version。锁定「当前能工作的固件版本」用，升级固件前后都该记一次。"""
        self._publish({"info": {"sequence_id": "0", "command": "get_version"}})
        with self._lock:
            return dict(self._acc.snapshot().versions)

    def get_ams_trays(self) -> dict[int, AmsTray]:
        """AMS 各托盘的实况（键是全局编号）。给 threemf.match_ams() 配料用。

        **只读缓存，不建连接。** 调用方要先 get_state()——它内部已经等过首个 pushall
        全量报文，等这一下回来 AMS 数据也就到了。

        v0.1 这里会顺手 _ensure_mqtt()，那在 CLI 单次调用下无害，但到了 v0.2 就不行：
        WebUI 每刷新一次状态都读一遍 AMS，而打印机同一时刻只接受一个 MQTT 连接——
        读缓存这种事绝不能有建连的副作用。
        """
        with self._lock:
            trays = self._acc.snapshot().ams.all_trays()
        return {
            t.global_id: AmsTray(
                id=t.global_id, type=t.tray_type, color=t.color, info_idx=t.info_idx,
                remain=t.remain, k=t.k, unit_id=t.unit_id, slot=t.slot,
                is_external=t.is_external,
            )
            for t in trays
        }

    def request_pushall(self) -> None:
        """主动重拉一次全量报文。

        只读查询，不产生任何物理动作——v0.1 每次连上都会发一次，已经实测过。
        （「打印进行中反复发」还没验过，所以 PrinterLink 在打印期间不调它。）
        """
        self._publish(
            {"pushing": {"sequence_id": "0", "command": "pushall",
                         "version": 1, "push_target": 1}}
        )

    def get_snapshot(self) -> PrinterSnapshot:
        """当前完整快照。**只读缓存，绝不建连接、绝不阻塞**——WebUI 每秒都会问它。"""
        with self._lock:
            return self._acc.snapshot()

    def set_report_listener(self, fn: ReportListener | None) -> None:
        """注册报文回调，用于把变化推给 SSE 订阅者。

        回调在 paho 的接收线程里跑，实现方必须非阻塞——卡住它就等于卡住
        整条打印机状态流。
        """
        with self._lock:
            self._listener = fn

    def reset_state(self) -> None:
        """断线时清掉累积状态：重连后旧快照可能已完全过时，等新的 pushall 重建。"""
        with self._lock:
            self._acc.reset()
            self._state_event.clear()

    # ---------------------------------------------------------------- AMS

    # ---------------------------------------------------------------- 启动

    def _build_project_file(self, task: Task) -> dict:
        """组出 project_file 指令的 payload。

        抽成独立函数是为了能离线断言每个字段——这个 payload 里任何一处填错，症状都是
        「打印机接受了指令但行为不对」，或者一个看起来像硬件故障的报错，而不是一句
        清楚的参数校验失败。v0.1 就在 param 上栽过一次：照抄社区文档写了 plate_1，
        而那个 3mf 里只有 plate_3，打印机报存储错误，看起来像 SD 卡坏了。

        五个开关取自 task.options（每单独立），全局 [print] 只在任务没指定时兜底。
        """
        opt = task.options.resolve(self.cfg.print)
        remote = task.remote_name or Path(task.source_path).name
        return {
            "print": {
                "sequence_id": task.id,
                "command": "project_file",
                "param": task.plate,                  # 3mf 内的 plate gcode 路径
                "url": f"file:///sdcard/{remote}",    # 存储上的文件路径
                "subtask_name": remote,
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "md5": task.md5,
                # 取自 3mf 的 plate_N.json（如 textured_plate）。写死 "auto" 是猜的。
                "bed_type": task.bed_type,
                "bed_leveling": opt.bed_leveling,
                # OpenBambuAPI 文档写 bed_levelling，实抓包是 bed_leveling；两个都发。
                "bed_levelling": opt.bed_leveling,
                "vibration_cali": opt.vibration_cali,
                "flow_cali": opt.flow_cali,
                "layer_inspect": opt.layer_inspect,
                "timelapse": opt.timelapse,
                # AMS 跟着任务走：同一台机器可能这次用 AMS、下次用外部料。
                "use_ams": task.use_ams,
                "ams_mapping": task.ams_mapping,
            }
        }

    def start(self, task: Task) -> str:
        """下发 project_file，让打印机从自身存储启动。

        减噪三个 flag 在这里生效：能砍掉振动扫频与探床，
        但 homing(G28) 与 purge line 不可免。

        返回实际下发的 payload JSON，调用方应把它存进 task.sent_payload——
        到点触发了但打印机行为不对时，不用再靠猜就能看到当时究竟发了什么。
        """
        payload = self._build_project_file(task)
        sent = json.dumps(payload, ensure_ascii=False)
        self._reply_event.clear()
        self._publish(payload)

        # 等同 sequence_id 的回执。收不到不等于失败，但值得记一笔。
        got = self._reply_event.wait(START_TIMEOUT)
        with self._lock:
            reply = self._replies.get(task.id)
        if not got or reply is None:
            log.warning("任务 %s 未在 %.0fs 内收到 project_file 回执", task.id, START_TIMEOUT)
            return sent
        if reply.get("result") != "success":
            raise TransportError(
                f"打印机拒绝启动指令: {reply}。"
                "若伴随 HMS 0500-0500-0001-0007，说明没开 Developer Mode 或固件不匹配。"
            )
        return sent

    def close(self) -> None:
        if self._mqtt is not None:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
            self._mqtt = None
