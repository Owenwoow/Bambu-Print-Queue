# 更新日志

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
