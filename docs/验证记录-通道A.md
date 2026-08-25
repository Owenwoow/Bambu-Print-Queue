# 通道 A 验证记录

对应 [`scripts/README.md`](../scripts/README.md) 的第 0–3 步。**结论以本文件为准**，
与两份调研文档冲突的地方，以这里的实测为准。

## 机器

| 项 | 值 |
|---|---|
| 型号 | A1 |
| IP | 局域网内固定地址（LAN Only 模式）。真值见 config.toml，不入库 |
| SERIAL | `03919D5…`（15 位，真值见 config.toml，不入库） |
| 发现方式 | FTPS `logger/` 目录的文件名里带 SERIAL 与固件版本 |

固件（2026-08-24 由 `info.get_version` 读得）：

| 模块 | 版本 |
|---|---|
| ota | **01.08.01.00** |
| esp32 | 01.16.33.15 |
| mc | 00.01.30.58 |
| th | 00.01.07.70 |
| ams_f1/0 | 00.00.08.15 |

**在这个版本上锁定，先不要升级固件。** 授权控制与 LAN 行为在持续变动，
升级前查 changelog，升级后重跑第 3 步。

## 逐步结果

### 第 0 步 · 上传静默 — 未做

需要人在书房听 + 看功率计，见 [`scripts/00_silent_upload_check.md`](../scripts/00_silent_upload_check.md)。
这一步的结论决定 `upload_timing` 填 `early` 还是 `late`。

### 第 1 步 · FTPS 上传 — 部分完成（连通性已验，STOR 未验）

- TCP 990 可达，implicit TLS 握手成功，欢迎信息 `220 BBL-P003 FTP Server`
- `bblp` + Access Code 登录成功
- PASV + `LIST`/`NLST` 成功

**与调研文档相反的一处实测**：社区文档（Bambuddy）称 A1 数据通道加密会挂起，
应"控制通道加密、数据通道跳过 SSL"。本机恰好相反——

| 数据通道 | 结果 |
|---|---|
| 明文（不发 PROT P） | 服务器直接断开，`EOFError` |
| 加密（`prot_p()`） | 正常 |

因此 `ftps_encrypt_data` 默认已改为 `true`。
若后续大文件 `STOR` 出现挂起，再回退到明文或改用 curl 兜底。

存储根目录结构：`logger/ recorder/ cache/ model/ corelogger/ image/ ipcam/ timelapse/`。
`model/` 是内置示例模型，`cache/` 是历史打印任务。

#### A1 FTPS 的真正坑：传完不回 TLS close_notify

第一次真正跑 `STOR` 时报 `TimeoutError: The read operation timed out`，
栈顶是 `conn.unwrap()`。但检查发现**文件已经完整传上去了**（369282 / 369282 字节）。

原因：标准 ftplib 在 `storbinary` 传完数据后调 `conn.unwrap()` 做 TLS 优雅关闭，
会一直等对方回 `close_notify`。A1 不回——它直接把数据连接扔了。
于是上传明明成功，却卡在收尾握手上抛异常。

**这很可能就是社区所谓「A1 数据通道 SSL 会挂起」的真身。** 但据此推出的
「跳过数据通道 SSL」是错的解法——本机明文数据通道会被直接拒。
正确解法是保持 PROT P，只是不要死等 close_notify：
`ImplicitFTP_TLS.storbinary()` 已覆盖为 unwrap 超时即放弃、直接关连接。

#### 吞吐：约 46 KB/s

369,282 字节耗时 7.9 秒。ESP32 的硬件上限，急不得。推论：

- 26 MB 的原始 3mf 要传约 **9 分钟**。所以**别直传带 `Auxiliaries/` 的完整 3mf**，
  用 Studio 导出的精简版（同一模型 369 KB vs 26 MB，70 倍差距）。
- 这个速度也是 `upload_timing = "late"`（到点才传）不可取的一个理由：
  把 9 分钟的上传压进触发时刻，等于把触发时刻推迟 9 分钟且失败无处可退。

### 第 2 步 · MQTT 读状态 — 通过

- `mqtts://<打印机IP>:8883`，`bblp` + Access Code，自签名证书需关校验
- 订阅 `device/<SERIAL>/report`，`pushall` 后读到 `gcode_state = FINISH`
- `info.get_version` 回执正常（即上表）

**踩到的坑**：不知道 SERIAL 时想用 `#` 通配订阅来发现它——不行。
broker 的 ACL 只允许订阅自己 SERIAL 的 topic，订阅 `#` 会被静默踢下线并无限重连。
SERIAL 只能从别处拿（屏幕、App、或本项目用的 FTPS 日志文件名）。

### 第 3 步 · MQTT 启动打印 — **通过**（2026-08-24）

修完下面列的四类问题后重试，`project_file` 被接受，打印机真的开始打印。
**通道 A 全线打通，项目最大的不确定性清除。**

关键结论：**授权控制没有拦截我们**。固件 01.08.01.00 高于 A1 引入授权控制的
01.05.00.00，原本预期会被 HMS `0500-0500-0001-0007` 拒绝，实际没有。
（尚待确认是因为打印机已开 Developer Mode，还是该固件在 LAN Only 下不强制校验。
这一条影响未来升级固件后的行为，值得确认后补记。）

验证时用的是 Studio 自己打包的 369 KB 参考文件，单色、走 AMS tray 0。
打印开始后由人工暂停，未跑完。

#### 之前失败的两次，以及根因

2026-08-24 12:25 / 12:26 各下发一次 `project_file`，打印机屏幕报 SD 卡错误，
状态在 IDLE 上不动。事后排查出四个原因，**其中第一条是决定性的**：

1. **`param` 指向了不存在的 plate。**
   `assets/test.gcode.3mf` 里只有 `Metadata/plate_3.gcode`，没有 `plate_1.gcode`
   （Studio 导出的是第 3 个盘）。而脚本默认发 `Metadata/plate_1.gcode`。
   打印机按这个路径去 SD 卡上找文件，找不到 → 报存储错误。
   **看起来像 SD 卡故障，其实是参数错。**
2. **`url` 里混进了本地路径。** 脚本第一个参数当时是「打印机上的文件名」，
   实际传的是 `.\assets\test.gcode.3mf`，于是 url 变成
   `file:///sdcard/.\assets\test.gcode.3mf`。
3. **打印机上的文件残缺。** 上传被 Ctrl-C 打断，21,078,016 / 26,074,777 字节。
   FTPS 不会因此报错，打印机也不会说它残缺。
4. **AMS 配置不符。** 该 plate 用 AMS 一个料位（PETG #FF671F，对应 tray 0 的橙色 PETG），
   而当时发的是 `use_ams=false`。这条不会导致存储错误，但会在真正开打后取不到料。

已做的修复（让这四类错误不可能再犯）：

- 新增 [`src/bpq/threemf.py`](../src/bpq/threemf.py)：从 3mf 里读出真正存在的 plate 与耗材。
  文件里没有你要的盘就在**提交时**报错，而不是留到半夜触发时。
- `upload()` 上传后校验 `SIZE`，大小不符直接报错。
- `scripts/03_start_print.py` 改为接受**本地路径**，自己取 basename、自己定 plate、
  自己按 AMS 实际料配 `ams_mapping`，并在下发前确认文件完整躺在打印机上。
- `use_ams` / `ams_mapping` 移到 Task 上（跟着任务走，不再是全局配置）。
- 回归测试见 [`tests/test_threemf.py`](../tests/test_threemf.py)。

这四类修复之后，第 3 步一次通过。

### 旁证：Studio 自己下发的那次也是 FAILED

12:27 Studio 下发 `Owen方案_ParaBlock_插座整理系统.gcode.3mf`（369,282 字节，根目录），
但 pushall 显示 `gcode_state = FAILED`、`layer_num = 0` / `total_layer_num = 25`、
`gcode_file_prepare_percent = '100'`——文件准备好了但一层没打就结束。
可能是手动取消，也可能不是，没有进一步排查。

顺带两个从对比里得到的事实：

- **Studio 下发时把文件放在存储根目录**，不是 `cache/`。我们的路径约定和它一致。
- **Studio 会重新打包**，把 `Auxiliaries/` 剥掉：同一个模型，Studio 传 369 KB，
  我们直传原始 3mf 是 26 MB（其中 26 MB 几乎全是说明书 PDF，gcode 只有 579 KB）。
  直传能用，但慢且没必要。

### 另一条死路：打印机日志读不了

`logger/*.log` 是加密的二进制（可打印字符占比 21%），不是文本，
拿不到 HMS 码或错误详情。`REST` 断点续传也不支持（502），只能整个下载。
排查错误还是得靠 pushall 里的 `hms` / `print_error` 字段，以及打印机屏幕。

## AMS 实况与匹配依据

`ams_exist_bits = 1`，一个 AMS lite，四个托盘：

| tray | tray_info_idx | 类型 | 颜色 | 剩余 | k |
|---|---|---|---|---|---|
| 0 | GFG00 | PETG | F98C36FF | 100 | 0.040 |
| 1 | GFG00 | PETG | FFFFFFFF | 100 | 0.073 |
| 2 | GFA18 | PLA | FFFFFFFF | 100 | 0.020 |
| 3 | GFG00 | PETG | 000000FF | 100 | 0.073 |

**AMS lite 没有 RFID**：`tag_uid` 与 `tray_uuid` 全 0，`tray_is_bbl_bits = f`。
type / color / info_idx 全都是用户在 Studio 里手填的，不是机器读出来的。

因此**不能要求颜色相等**。3mf 里的颜色是切片时耗材配置的颜色（`#FF671F`），
AMS 里的是槽位手填的颜色（`F98C36`）——同一卷料，两个值，永远对不上。
一开始按颜色相等匹配，必然失败。

正确依据（与 Studio 下发界面的行为对齐）：

1. **`tray_info_idx` 优先**——耗材型号 ID，如 `GFG00` = PETG Basic。
   3mf 的 `slice_info.config` 里每条 filament 都带这个字段，AMS 上报里也有。
2. 同型号可能有多卷（本机 GFG00 就有三卷：橙/白/黑），在其中按**颜色距离最近**选。
3. 没有同型号则退到同 `tray_type`，并说明退化了。
4. 都没有 → -1（外部料）。

实测：`#FF671F` 到 tray 0 的 `F98C36` 距离 44，到白色 271，到黑色 277，
tray 0 压倒性胜出。`threemf.match_ams()` 现在会打印出选择理由与距离。

## 还有一个填错的字段：bed_type

原来写死 `"auto"`。3mf 的 `Metadata/plate_N.json` 里有真值：

```json
{"bed_type": "textured_plate", "filament_colors": ["#FF671F"], "nozzle_diameter": 0.4}
```

（`project_settings.config` 里对应 `curr_bed_type = "Textured PEI Plate"`。）
现已改为从 3mf 读。

## gcode_state = FAILED 不等于「机器忙」

第三次尝试卡在这里：上一单失败后 `gcode_state` 停在 `FAILED`，
而当时的 `is_idle` 只认 IDLE / FINISH，于是所有任务都会被判定为「机器不空闲」而放弃。

`gcode_state` 表示的是**上一个任务的结局**，不是机器当前忙不忙。
真正的「忙」只有 RUNNING 和 PAUSE。现在的区分：

- `is_busy` = RUNNING / PAUSE
- `is_idle` = IDLE / FINISH，可直接启动
- `needs_attention` = FAILED，机器是闲的，但板子上可能还有残骸

FAILED 之后是否照常触发由 `[scheduler].start_after_failure` 决定，默认 `false`
（半夜往废墟上再打一层，不如放弃）。手动重试时用 `--allow-failed`。

### 第 4 步 · 端到端演练 — 链路跑通，但暴露一个致命 bug（已修）

2026-08-24 16:02 首次跑完整链路（`bpq submit` + `bpq daemon`）：

```
15:59:09  submitted    定时 16:02:09
15:59:17  uploaded     369 KB / 8 秒
15:59:49  daemon_start daemon 比任务晚 40 秒才起
16:02:09  triggered    准点，误差 0 秒
16:02:10  aborted      printer_state=UNKNOWN
```

调度链路本身全对：任务持久化住了（daemon 在提交之后才启动，仍正确接管），
心跳感知到了跨进程写入的 job，触发时刻零误差。

**但放弃原因是 `UNKNOWN`，而不是当时打印机真实的 `PAUSE`。** 两个 bug：

#### bug 1（致命）：get_state() 不等报文，到点必然读到 UNKNOWN

`_ensure_mqtt()` 一建好连接就返回，而 `gcode_state` 要等 pushall 全量报文
（实测 1–3 秒）才被填上。所以连接后第一次 `get_state()` 几乎必然是 UNKNOWN。

`cli.py status` 和 `scripts/03_start_print.py` 里都手写了 `sleep(2~3)` 绕过它，
**唯独 `TaskRunner.fire()` 漏了**——日志里 `triggered` 到 `aborted` 只隔 1 秒，
报文根本来不及到。

后果是最不能接受的那种：打印机好端端空闲着，定时任务到点照样被判「状态未知」
而放弃，人第二天早上只在日志里看到一行 UNKNOWN。

修法：等待内置进 `get_state(timeout=STATE_TIMEOUT)`，而不是让每个调用方各自 sleep。
`_state_event` 只在真的解析出 `gcode_state` 时置位——A1 是增量上报，
大量报文不含该字段，若一律置位则第一条增量包就会让等待提前结束。
回归测试见 `tests/test_get_state_wait.py`。

#### bug 2：daemon 没有单实例保护

当时不小心起了两个 daemon，它们互抢那唯一的 MQTT 连接。打印机只接受一个连接
是硬约束，所以同时跑两个永远是错的，应该在启动时就拦住。

修法：`single_instance()` 用 **OS 级文件锁**（Windows `msvcrt.locking`，
POSIX `fcntl.flock`），套在 `serve()` 最外层，比连打印机、建 jobstore、
阻止睡眠都早。不用 PID 文件是因为 daemon 被强杀时不执行清理代码，
PID 文件会残留并把自己永久挡在门外；OS 级锁随进程消亡由内核释放。

锁文件**故意不删**：POSIX 上 unlink 之后 inode 还活着，
若此时另一进程已打开该路径却没抢到锁，而第三个进程重建同名文件并加锁，
两者锁的是不同 inode，会同时以为自己独占。留个 0 字节文件在 `var/` 里没有代价。
测试见 `tests/test_single_instance.py`（含跨进程互斥与强杀后自动释放）。

## 待解决

- **第 0 步的静默验证仍未正式做**。`upload_timing = "early"` 目前是假设不是结论。
  实践中已经跑过十余次上传（含一次 26 MB 的），若上传会惊动机器早该发现，
  但没有一次是带着功率计正式记录的。这是唯一还没落实的地基假设。
- **Developer Mode 的状态待确认**。第 3 步没被授权控制拦，但不知道是因为它开着，
  还是这版固件在 LAN Only 下本就不强制。升级固件前必须搞清楚。
- **多色任务未验证**。`ams_mapping` 在单色下确认为 `[0]` 可用，多色的长度与顺序语义
  还没实测。真要做多色，可以临时起一个 MQTT 中继假装成打印机，抓一次 Studio 的
  原始 `project_file` 对照（Studio 不校验打印机证书，技术上可行）。
### 第 5 步 · 端到端验收 — **通过**（2026-08-24 16:32）

修完两个 bug 后重新验证。中间还发现一个此前没遇到过的分支：重新提交时打印机
仍是 `FAILED`（上一次手动暂停的任务被取消后停留在这个状态），被 `needs_attention`
拦截，这是设计好的行为，不是新 bug——只是第一次实际触发这条路径。

清板并确认原因后，临时把 `start_after_failure` 设 `true` 放行一次，重新提交：

```
16:30:21  submitted   task=9dda1273ae9d  定时 16:32:21
16:30:29  uploaded    369 KB
16:32:21  triggered   零误差
16:32:24  started
```

`submitted → uploaded → triggered → started` 齐全，触发时刻与提交时一致。
**v0.1 的验收标准（当场交任务、静默等待、到点自己开打、全程可回溯）达成。**
验收后 `start_after_failure` 已改回默认值 `false`。

## 遗留（不阻塞 v0.1，记录以便后续跟进）

- **第 0 步的静默验证仍未正式记录**。`upload_timing = "early"` 目前是"实践中跑了
  十余次没出问题"的经验，不是带功率计的正式结论。
- **Developer Mode 的状态未确认**。第 3 步没被授权控制拦，但不知道是因为它开着，
  还是这版固件在 LAN Only 下本就不强制。升级固件前必须先搞清楚。
- **多色任务未验证**。`ams_mapping` 只在单色（`[0]`）下确认可用。
