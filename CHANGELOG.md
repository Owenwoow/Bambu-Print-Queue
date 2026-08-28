# 更新日志

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.3.1] — 2026-08-28

### 修复

- **修复 Windows 上双击 Release 里发布的 `bpq-windows-x86_64.exe` 会闪退的问题。**
  本次修复原因：v0.3.0 发布后有用户反馈双击可执行文件后窗口一闪就没了，看起来像
  程序崩溃。排查发现是两个问题叠在一起：
  1. 双击等价于不带任何参数运行程序，而 `bpq` 的命令行是 click 的 `group`，
     没有子命令时 click 会在参数解析阶段就打印用法说明并以 exit code 2 退出——
     控制台窗口跟着自动关闭，用户根本来不及看清内容，这是正常退出，不是崩溃。
  2. 项目里原本已经有把控制台输出切到 UTF-8 的兜底逻辑（Windows 控制台默认是
     GBK，直接输出中文标点会乱码甚至报错），但它挂在 `main()` 的回调函数体里；
     偏偏"没有子命令"这条早退路径根本不会执行到回调体，于是双击时看到的用法
     说明还全是乱码，进一步坐实了"程序坏了"的错觉。
  修复方式：把 UTF-8 切换逻辑挪到 click 解析参数之前的模块级调用，覆盖包括
  用法说明在内的所有输出路径；再针对性地加了一条规则——只有「打包成冻结的单文件
  可执行程序」且「零参数运行」（也就是双击）时，才在退出前暂停等待按回车，
  从终端敲命令或者脚本里调用完全不受影响。
- 本地重新构建 Windows exe 验证：双击场景下中文正常显示，窗口会等待按键，
  不再自动关闭。

### 变更

- CI 通过后打的 `v0.3.1` 标签会重新触发 Release 工作流，产出修好的三平台
  二进制和 Docker 镜像。

## [0.3.0] — 2026-08-28

首个公开发布版本。端到端链路（定时提交 → 静默上传 → 到点触发 → 打印机开打）
已在真机上验证通过，日常可用；详细过程见
[`docs/验证记录-通道A.md`](docs/验证记录-通道A.md)。

### 新增

- **局域网 WebUI**：新建任务、打印机实时状态、AMS/耗材面板、日志查询、任务管理、
  设置页都能在网页上完成，寄生在 daemon 进程里，复用同一条 MQTT 长连接。
- **连接让出 / 自动抢回**：daemon 常连着 MQTT 时 Bambu Studio 连不上；WebUI 上
  可以主动「让给 Studio」，到点会自动抢回连接再触发，不影响已在册的任务。
- **Docker 部署**：多阶段 `Dockerfile` + `docker-compose.yml`，详细教程见
  [`docs/部署-Docker.md`](docs/部署-Docker.md)。
- **预编译二进制发布**：GitHub Actions 在打 `vX.Y.Z` 标签时自动构建 Linux /
  Windows / macOS 三平台单文件可执行程序和 Docker 镜像，一并挂到对应 Release。

### 修复

- 修复调度器到点触发时必然读到 `UNKNOWN` 而放弃任务的 bug（`TaskRunner.fire()`
  漏掉一处等待首个状态报文的逻辑）——验收前发现的最严重问题。
- 修复 Windows 上 `Ctrl+C` 杀不掉 daemon 进程（`stop.wait()` 不能不带超时）。
- 修复 setuptools ≥81 移除 `pkg_resources` 后 `apscheduler` 启动即崩溃的问题
  （钉住 `setuptools<81`）。

### 变更

- README 按当前实际状态重写，补齐安装 / 使用 / 部署章节。
- 开发过程中的可行性调研、阶段性任务清单归档到 `docs/archive/`，不再和当前
  有效的技术文档混在一起；新增 [`docs/README.md`](docs/README.md) 作为文档索引。
