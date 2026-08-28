# bpq — 拓竹 A1 定时静默打印调度器

[![CI](https://github.com/Owenwoow/Bambu-Print-Queue/actions/workflows/ci.yml/badge.svg)](https://github.com/Owenwoow/Bambu-Print-Queue/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Owenwoow/Bambu-Print-Queue)](https://github.com/Owenwoow/Bambu-Print-Queue/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

> 替我记住「睡前再打」这件事的中间人。

打印机在书房，启动例程（尤其振动补偿那段）很吵，晚上开工会吵到休息。
真正的痛点不是「我想定时」，而是**不想把动作留给未来的自己**——有灵感时人在电脑前，
但正确做法是睡前离开书房再点开始，而睡前经常忘。

bpq 接下这个动作：当场把任务交出去，指定一个绝对时刻，**在那之前打印机对它一无所知**——
不预热、不转风扇、不出声。到点了自己开打。

## 目录

- [状态](#状态)
- [安装](#安装)（[懒人版 exe](#方式一下载即用的懒人版windows不需要装-python--node) /
  [Docker](#方式二docker) / [源码](#方式三源码安装)）
- [使用教程](#使用教程)（[CLI](#cli) / [WebUI](#webui)）
- [部署](#部署)（[Docker](#docker) / [systemd](#家庭服务器systemd) / [普通电脑](#普通电脑开发机--笔记本)）
- [目录结构](#目录结构)
- [设计约定](#设计约定)
- [已知坑](#已知坑)
- [贡献](#贡献)

## 状态

**v0.3，真机验证通过，日常可用。**

端到端链路已经在真机上跑通：`bpq submit` 定时提交 → 静默上传 → daemon 到点触发 →
打印机真的开始打印，`submitted → uploaded → triggered → started` 全部落地，触发时刻
零误差。MQTT 状态读取、AMS 四槽识别、FTPS 静默上传、局域网 WebUI、连接的「让出/给
Studio 用」与「到点自动抢回」、CLI 经由 daemon 的 HTTP API 走——这些都已经过真机验证，
详细过程见 [`docs/验证记录-通道A.md`](docs/验证记录-通道A.md)。

WebUI 从 v0.2 起就是产品的一部分：寄生在 daemon 进程里，复用同一条 MQTT 长连接，能新建
任务、看打印机实时状态、看 AMS/耗材、查日志、管理任务列表；打印机连接参数与全局默认值
也能在网页上改。架构细节见 [`docs/v0.2-WebUI-架构.md`](docs/v0.2-WebUI-架构.md)。

多色打印按需求排在最后：`ams_mapping` 里「外部料该填 -1 还是 255」这类语义细节尚未
实测，目前只验证过单色 `[0]`。进度见 [`docs/v0.2-多色映射语义.md`](docs/v0.2-多色映射语义.md)。

## 安装

三种方式任选一种：Windows 上想开箱即用就下懒人版 exe，双击就完事；长期挂在
NAS/服务器上推荐 Docker；改代码或想跟着源码走用 pip。

### 方式一：下载即用的懒人版（Windows，不需要装 Python / Node）

去 [Releases](https://github.com/Owenwoow/Bambu-Print-Queue/releases) 下
`bpq-<版本号>-windows-x86_64.exe`，**放进一个你打算长期留着它的文件夹**
（比如 `E:\bpq\`，别放桌面或下载目录），然后双击。

双击之后它会自己做完这些事：

1. 在 exe 旁边生成一份 `config.toml`（第一次运行才生成，之后不会覆盖）；
2. 启动 daemon 并把 WebUI 拉起来；
3. 自动打开浏览器到 `http://127.0.0.1:8710`。

剩下的就在网页上做：打开「设置」页填打印机的 IP / SERIAL / Access Code
（怎么拿见下面「打印机与配置」），保存时会先试连一次。之后新建任务、看状态、
查日志全在网页里，命令行一句都不用敲。

黑色的控制台窗口要**一直开着**——它就是 daemon 本体，关掉了定时任务不会触发。
任务库和日志都在 exe 旁边的 `var/` 目录里，重启不丢。

> 同一个 exe 也是完整的 CLI：从终端敲 `bpq-....exe submit model.3mf --at 23:30`
> 之类的命令，走的还是下面「使用教程」里那套，只有「双击、不带任何参数」时
> 才进懒人版流程。

Linux / macOS 目前不出预编译版本，用下面的 Docker 或源码方式。

### 方式二：Docker

见下面「[部署 → Docker](#docker)」一节，`docker compose up -d` 一条命令起服务。

### 方式三：源码安装

```bash
git clone https://github.com/Owenwoow/Bambu-Print-Queue.git
cd Bambu-Print-Queue
```

#### 后端

```bash
pip install -e ".[dev]"
```

`pyproject.toml` 里已经声明了 `bpq` 这个 CLI 入口，装完可以直接跑 `bpq --help`。

#### 前端（WebUI，可选）

前端产物不入库，第一次用之前要自己构建一次（需要 Node）：

```bash
cd web
npm install
npm run build
```

构建产物是 `web/dist`，由 daemon 进程直接 serve。没构建也不影响 daemon 和 CLI 本身，
只是访问网页会看到一页构建说明。

### 打印机与配置

不管用哪种方式安装，打印机上都要先开 **LAN Only 模式**（这样才会显示 Access Code；
如果显示全 0，关掉再开一次）和 **Developer Mode**（关闭授权控制，否则第三方发起的
启动指令会被拒，报 `HMS 0500-0500-0001-0007`）。

三个值在打印机屏幕上：`ip`（网络设置里）、`access_code`（LAN Only 模式那一页）、
`serial`（机器序列号）。

- **懒人版 exe**：不用碰配置文件，双击起来之后在网页的「设置」页里填这三个值，
  保存时会先试连一次打印机。
- **Docker / 源码**：

  ```bash
  cp config.example.toml config.toml
  ```

  填进 `[printer]` 段的 `ip` / `serial` / `access_code`。

`config.toml` 已在 `.gitignore` 里不会入库，文件里其余配置项都带注释，
可以先用默认值。

## 使用教程

### CLI

daemon 是唯一真正做「调度」的进程，必须在触发时刻保持运行：

```bash
bpq daemon
```

提交一个定时任务——读 3mf 里的 plate / AMS 映射，当场静默传到打印机存储，等到点
才会真正开始打印：

```bash
bpq submit model.gcode.3mf --at 23:30
```

常用参数：

- `--plate <n>`：文件含多个盘时选一个，默认从 3mf 自动读。
- `--no-ams`：不走 AMS，用外部料。
- `--name <文件名>`：打印机存储上的文件名，默认同源文件名。
- 五个打印开关（`--bed-leveling/--no-bed-leveling`、`--vibration-cali/--no-vibration-cali`
  等）：不指定则跟随 `config.toml` 里 `[print]` 的全局默认，之后改全局默认，
  未触发的任务会跟着变。

其余命令：

| 命令 | 作用 |
|---|---|
| `bpq ls` | 列出待触发任务（加 `--all` 连已完成/已取消一起列出） |
| `bpq cancel <id>` | 在触发前反悔 |
| `bpq status` | 读打印机当前状态（daemon 在跑就问它要现成快照，没跑才直接连一次） |
| `bpq log` | 查日志——排查「为什么那晚没打起来」，`-n` 控制显示条数 |
| `bpq web` | 打出 WebUI 的访问地址 |

daemon 没在跑时，`status` 等命令会退化成直接连打印机；如果这时 daemon 其实还持有
连接锁（比如刚被杀但锁没释放），会被明确拒绝，而不是静默互抢打印机唯一的 MQTT 连接。

### WebUI

daemon 起来之后，浏览器打开 `bpq web` 打出的地址（默认 `http://127.0.0.1:8710`）。
想在手机/平板上用就把 `config.toml` 里 `[web].host` 改成 `0.0.0.0`——**那时
`password` 必须非空，否则 daemon 会拒绝启动 web 服务**。

登录口令在 `config.toml` 的 `[web]` 段配置。局域网内跑的是明文 http（没有
TLS），登录页上也会提示这一点——请用一个不与其他服务复用的口令。本机
（127.0.0.1）访问默认免鉴权（`allow_local_no_auth`），方便 CLI 和本地操作，
多人共用的电脑上应该关掉。

网页里能做的事：

- **新建任务**：上传 3mf，选盘、选 AMS 映射、设触发时刻和打印开关。
- **打印机实时状态**：温度、进度、当前阶段、HMS 告警。
- **AMS / 耗材**：四槽状态、颜色、剩余量；打印机里手填的耗材型号/颜色能手动重拉。
- **日志**：按类型/日期筛选、分页、分档清理。
- **任务列表**：管理待触发任务，删除已结束的任务记录。
- **设置**：打印机 IP / SERIAL / Access Code、全局打印默认值、调度选项都能改，
  **保存前会先试连一次打印机**，写回 `config.toml` 时保留原有注释（那些注释
  几乎全是实测结论，比配置值本身更难重新获得），改完即时生效，不用重启。

WebUI 不提供「立即打印」、不做打印机 SD 卡文件管理、不做多打印机——这是刻意的边界，
见下面「设计约定」。

## 部署

### Docker

NAS / 群晖 / 软路由这类常年开机的设备推荐用 Docker：

```bash
cp config.example.toml config.toml   # 按上面「打印机与配置」填好
docker compose up -d
```

`docker-compose.yml` 默认拉取 GitHub Actions 自动构建的镜像
（`ghcr.io/owenwoow/bambu-print-queue:latest`），也可以 `docker compose build`
自己从源码构建。Docker 场景下 `config.toml` 有两处必须改（`[web].host` 必须是
`0.0.0.0`、`[web].password` 必须非空，否则端口映射进不来），详细教程见
[`docs/部署-Docker.md`](docs/部署-Docker.md)。

### 家庭服务器（systemd）

`deploy/bpq.service` 是一个 systemd unit 模板：

```bash
sudo cp deploy/bpq.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bpq
```

迁到服务器后建议把 `config.toml` 里 `[daemon].inhibit_sleep` 设成 `false`，
改用 systemd timer 的 `WakeSystem=true` 做 RTC 定时唤醒——比让整台机器不睡更省电。
注意系统被唤醒后有大约 2 分钟的空闲计时器窗口，需要在窗口内让 daemon 声明忙碌，
否则会立刻回睡（模板文件顶部的注释里有同样的提醒）。

### 普通电脑（开发机 / 笔记本）

不需要上面这一套。直接 `bpq daemon` 常驻即可——`config.toml` 里
`[daemon].inhibit_sleep` 保持默认的 `true`，daemon 会自己用 `wakepy` 阻止系统睡眠。

## 目录结构

```
src/bpq/
  cli.py          CLI：submit / ls / cancel / status / log / web / daemon
  lazy.py         懒人版：双击 exe 时自动建配置、起 daemon、开浏览器
  client.py       CLI 找 daemon 说话（stdlib urllib，零新依赖）
  daemon.py       常驻进程：文件锁 + 长连接 + 调度器 + WebUI，四者同一个进程
  runtime.py      进程内的运行时注册表，让到点的 job 能摸到活的连接
  link.py         PrinterLink：那条唯一的 MQTT 长连接，谁要用都找它借
  service.py      业务层，CLI / HTTP / 网页表单三个入口共用，防止漂移
  configwrite.py  把配置写回 config.toml，保留注释（tomlkit）
  scheduler.py    TaskRunner：到点后的上传→查状态→启动→写日志
  snapshot.py     打印机状态快照的数据结构（只装「机器说了什么」）
  report.py       把 A1 的增量 report 合并成完整快照
  store.py        SQLite 任务持久化 + schema 迁移
  journal.py      append-only JSONL 日志，兼作状态变化的推送源；带筛选/分页/清理
  threemf.py      解析 3mf、剥 Auxiliaries/、AMS 配料
  power.py        阻止系统睡眠（普通电脑场景；服务器上换 RTC 唤醒）
  notify.py       推送接口占位，不实现
  transport/
    base.py       upload / start / get_state 三个动作的抽象
    lan.py        通道 A：本地 MQTT(8883) + FTPS(990)，唯一实现
    cloud.py      通道 B：逆向云 API，只占位不实现
  web/            FastAPI：REST + SSE + 口令，寄生在 daemon 进程里
web/              前端源码（React + Vite + Tailwind），产物 web/dist 不入库
scripts/          真机验证 + 取数用的手动脚本
deploy/           systemd unit 模板（家庭服务器线）
docs/             部署教程、实测记录、架构决策；索引见 docs/README.md
Dockerfile        多阶段构建：Node 编前端 → 只带 Python 运行依赖的最终镜像
```

## 设计约定

这些是定了的，改之前先回去看 [`docs/`](docs/)：

- **触发前必须完全静默。** 这是项目唯一的存在理由。任何让打印机提前有动作的方案都出局
  （已因此排除 gcode 里插 `G4` 延时）。
- **任务必须持久化。** 服务重启、电脑睡眠唤醒之后待发任务要还在。这条把项目从
  「一个脚本」抬到「有状态的常驻进程」。
- **到点机器不空闲就放弃**，写日志。不做「十分钟后再试」，不做队列。
- **传输层可插拔**，但只实现通道 A。通道 B 有封号风险、证书约一年过期，只占位不实现。
- **先验通道，再写调度器。** 调度那部分闭着眼也能写；「发送」这一下不是自己说了算。
- **打印机只接受一个 MQTT 连接，所以那条连接必须有唯一的主人。** 是
  `PrinterLink`：daemon 里任何代码都不许再自己 `build_transport()`，
  别的进程要数据就走 daemon 的 HTTP API。
- **「我们发了什么」和「机器说了什么」在数据结构上分开，界面上也不混排。**
  五个打印开关里有三个根本没有对应的上报字段（见
  [`src/bpq/report.py`](src/bpq/report.py) 的说明），把两者画成对照表
  就是在编造确定性。
- **WebUI 是 daemon 的一个前端，不是独立产品。** 寄生在 daemon 进程里，复用同一条
  MQTT 长连接，绝不另开连接；不提供「立即打印」，不做打印机 SD 卡文件管理，
  不做多打印机。

明确不做：云端部署、多任务多打印机、参与切片、推送通知（只留接口）、升级打印机固件。

## 已知坑

- 打印机同一时刻**只接受一个 MQTT 连接**。daemon 常连时别同时开着 Studio/OrcaSlicer。
- A1 的 FTPS 数据通道**必须加密**（`ftps_encrypt_data = true`）——明文会被直接断开。
  这与部分社区文档的说法相反，以 `docs/验证记录-通道A.md` 的实测为准。
- 不知道 SERIAL 时**不能用 `#` 通配订阅**去发现它：broker ACL 会把你静默踢下线。
  SERIAL 可以从 FTPS 的 `logger/` 文件名里读到。
- A1 只支持**被动模式**（PASV）。
- A1 传完文件后**不回 TLS `close_notify`**，标准 ftplib 会卡在 `conn.unwrap()` 上，
  让一次成功的上传看起来像超时失败。`ImplicitFTP_TLS.storbinary()` 已覆盖处理。
- FTPS 吞吐约 **46 KB/s**（ESP32 硬件上限）。别直传带 `Auxiliaries/` 的完整 3mf——
  同一模型 Studio 精简版 369 KB，原始版 26 MB，后者要传 9 分钟。
- 减噪 flag 能砍掉振动扫频与探床，但 **homing 与 purge line 不可免**——
  「触发前静默」是 100% 的，「触发后立即安静」只是显著改善。
- **先不要升级固件。** 在验证通过的版本上锁定；授权控制与 LAN 行为在持续变动。
- `gcode_state = FAILED` 是上一单的**结局**，不是"机器正忙"——机器其实空闲，
  但板子上可能有残骸。默认不会在 FAILED 之后自动触发（`start_after_failure = false`），
  需要人确认板子干净后临时改配置放行一次。
- **同一时刻只能有一个 daemon**。重复启动会被拒绝并给出明确提示，而不是静默
  互抢打印机的 MQTT 连接。
- `get_state()` 会内置等待首个状态报文（最多 `STATE_TIMEOUT` 秒），调用方不需要
  再自己 `sleep`——之前手写的 `sleep` 曾在 `TaskRunner.fire()` 里漏掉一处，
  导致定时任务到点必然读到 `UNKNOWN` 而被放弃，是验收前修复的最严重的 bug。
- **daemon 常连着 MQTT，Bambu Studio 就连不上。** 要用 Studio 时点 WebUI 顶栏的
  「让给 Studio」（或 `POST /api/printer/yield`）。让出期间定时任务照常在册：
  到点会自动抢回连接再启动，日志里记一条 `connection_reclaimed`。
- **本机免鉴权默认是开的**（`[web] allow_local_no_auth`）。方便 CLI 零配置走 HTTP，
  代价是这台电脑上任何进程、任何用户都能不经口令操作打印任务。多人共用的机器上关掉它。
- **口令是明文过局域网的**（内网跑的是 http，没做 TLS）。用一个不与别处复用的口令。
- **Bambu Studio 自己的日志是 AES 加密的**（`%APPDATA%/BambuStudio/log/*_enc_*.log`），
  别指望从里面捞它发出去的 `project_file` payload——试过了，正文可打印字符只有 43%。
- **上传给打印机之前要剥掉 `Auxiliaries/`。** 同一个模型带装配说明 PDF 是 26 MB，
  46 KB/s 要传 9 分钟；剥掉之后 369 KB，8 秒。`threemf.slim()` 会做这件事，
  而且只剥这一个目录——实测 Studio 自己的「精简」就等于删掉它，
  非 Auxiliaries 条目一个不差（42 对 42），没有理由自己再去猜哪些能删。

## 贡献

欢迎 issue 和 PR。提交前请先看 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
版本变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

[MIT](LICENSE)
