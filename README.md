# Bambu-Print-Queue — 拓竹预约打印调度器

[![CI](https://github.com/Owenwoow/Bambu-Print-Queue/actions/workflows/ci.yml/badge.svg)](https://github.com/Owenwoow/Bambu-Print-Queue/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Owenwoow/Bambu-Print-Queue)](https://github.com/Owenwoow/Bambu-Print-Queue/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

> 提交任务时指定几点开始，触发前打印机对这个任务完全不知情——不预热、不出声，
> 到点了才真正开始。

打印机放在书房，就在工位旁边。不管是白天在这儿干活还是晚上准备睡觉，打印机一启动
就会吵到人。需求很简单：想现在提交，让它在我不在书房的时候开始打印——但正确做法
是睡前回书房手动点开始，而睡前太容易忘。

现成方案一提交就会让打印机进入待机、开始加热，算不上真正的"定时"。bpq 把这一步
拆开：先静默把任务和文件交给打印机存着，到约定时刻才真正启动。

## 状态

**v0.3，真机验证通过，日常可用。** 提交 → 静默上传 → 到点触发 → 真的开始打印，这条
链路已经在真机上跑通，触发时刻零误差；WebUI、AMS 识别、连接让出/抢回均已验证，细节
见 [`docs/验证记录-通道A.md`](docs/验证记录-通道A.md)。

多色打印（AMS 外部料映射语义）还没实测完，目前只验证过单色 `[0]`，进度见
[`docs/v0.2-多色映射语义.md`](docs/v0.2-多色映射语义.md)。

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

## 安装

三种方式任选一种：Windows 上想开箱即用就下懒人版 exe，双击就完事；长期挂在
NAS/服务器上推荐 Docker；改代码或想跟着源码走用 pip。

### 方式一：下载即用的懒人版（Windows，不需要装 Python / Node）

去 [Releases](https://github.com/Owenwoow/Bambu-Print-Queue/releases) 下
`bpq-<版本号>-windows-x86_64.exe`，放进一个你打算长期留着它的文件夹（比如
`E:\bpq\`，别放桌面或下载目录），双击运行。

它会自动生成配置、启动 daemon 和 WebUI、打开浏览器到 `http://127.0.0.1:8710`，
自己常驻在系统托盘里（不弹黑框控制台），图标颜色跟着打印机状态变。之后新建
任务、看状态、查日志全在网页里做，打印机 IP / SERIAL / Access Code 在「设置」页
填（怎么拿见下面「打印机与配置」），命令行一句都不用敲。

关掉浏览器标签页不等于关掉程序——daemon 在托盘那边继续跑，定时任务照常触发；
真正要退出，右键托盘图标选「退出」。

托盘菜单完整说明、Windows SmartScreen 提示、Linux/macOS 命令行版下载，见
[`docs/安装-Windows懒人版.md`](docs/安装-Windows懒人版.md)。

### 方式二：Docker

见下面「[部署 → Docker](#docker)」一节，`docker compose up -d` 一条命令起服务。

### 方式三：源码安装

需要 Python 3.11+（`pyproject.toml` 里 `requires-python` 写死了这条下限）。

```bash
git clone https://github.com/Owenwoow/Bambu-Print-Queue.git
cd Bambu-Print-Queue
```

#### 后端

建议先建虚拟环境，避免跟系统 Python 装的包冲突：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux / macOS: source .venv/bin/activate

pip install -e ".[dev]"
```

只想跑起来、不打算改代码的话 `pip install -e .`（不带 `[dev]`）就够了——`[dev]`
多装的是 `pytest` / `ruff` / `mypy` 这套开发工具，改代码时才用得上，细节见
[`CONTRIBUTING.md`](CONTRIBUTING.md)。

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

装完接着看下面「打印机与配置」填好 `config.toml`，再照「使用教程」跑 `bpq daemon`。

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
  lazy.py         控制台版兜底流程：bpq-cli.exe 零参数双击时自动建配置、起 daemon（黑框），
                  ensure_config() 也被 traymain.py 复用
  tray.py         托盘图标逻辑：状态圆点随打印机状态变色、菜单、开机自启；不做通知、
                  不重复 WebUI 已有的任何功能
  traymain.py     GUI 托盘版 exe 入口（--windowed，无控制台窗口，唯一排障途径是 var/bpq.log）
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

- 触发前必须完全静默——项目唯一的存在理由。
- 任务持久化在 SQLite，服务重启 / 睡眠唤醒后待发任务还在。
- 到点打印机不空闲就放弃、写日志，不重试、不排队。
- 传输层只做本地 MQTT + FTPS（通道 A），不接云端 API（通道 B，只占位）。
- 打印机只认一条 MQTT 连接，daemon 内唯一入口是 `PrinterLink`，别的进程一律走
  daemon 的 HTTP API。
- 「我们发了什么」（`Task.options`）和「机器说了什么」（`PrinterSnapshot`）分开存，
  界面上也不做对照。
- WebUI 是 daemon 的前端，不是独立产品：不做「立即打印」、不做 SD 卡文件管理、
  不做多打印机。

明确不做：云端部署、多任务多打印机、参与切片、推送通知、升级打印机固件。

每条背后的取舍见 [`docs/`](docs/)，尤其 [`验证记录-通道A.md`](docs/验证记录-通道A.md)。

## 已知坑

- 打印机同一时刻只接受一个 MQTT 连接：daemon 在跑时别同时开 Studio/OrcaSlicer；
  要用 Studio 就点 WebUI 里的「让给 Studio」，到点会自动抢回来。
- 先不要升级固件，在验证通过的版本上锁定——授权控制和 LAN 行为在持续变动。
- 减噪只能砍掉振动扫频和探床，homing、purge line 免不了：「触发前静默」是
  100%，「触发后立即安静」只是好很多。
- 本机默认免密码访问（`allow_local_no_auth`），多人共用的电脑上记得关掉；
  局域网跑的是明文 http，密码不要跟别处复用。
- 上一单失败（`gcode_state = FAILED`）不代表机器忙，但默认也不会自动接着打
  （怕往没清理的板子上打），需要人确认板子干净后手动放行一次。

更细的坑（TLS 握手细节、FTPS 限速、SERIAL 怎么拿到）都是真机踩出来的实测结论，
记在 [`docs/验证记录-通道A.md`](docs/验证记录-通道A.md)，改传输层之前必读。

## 贡献

欢迎 issue 和 PR。提交前请先看 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
版本变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

[MIT](LICENSE)
