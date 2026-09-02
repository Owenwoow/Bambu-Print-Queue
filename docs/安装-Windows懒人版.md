# 安装 · Windows 懒人版细节

主流程见项目根 [README.md](../README.md) 的「安装 → 方式一」，这里补充完整细节。

## 双击之后发生了什么

1. 在 exe 旁边生成一份 `config.toml`（第一次运行才生成，之后不会覆盖）；
2. 启动 daemon 并把 WebUI 拉起来；
3. 自动打开浏览器到 `http://127.0.0.1:8710`；
4. **不弹黑框控制台**——程序常驻在系统托盘（任务栏右下角）里，一个圆点图标，
   颜色跟着打印机状态变（灰=未连接、蓝=空闲、绿=打印中、橙=暂停、红=上一单
   失败），鼠标悬停能看到一行状态摘要。

任务库和日志都在 exe 旁边的 `var/` 目录里，重启不丢。

## 右键托盘图标能做的事

- **打开控制台**——重新拉起浏览器指到 WebUI（默认单击图标同样效果）；
- **打印机 / 下一个任务**——两行只读摘要，不用开网页就能扫一眼状态；
- **打开配置文件**——用系统关联的程序打开 `config.toml`（没有关联就会让你选一个）；
- **打开日志文件夹**——直接跳到 `var/` 目录，日志文件是 `var/bpq.log`，
  出问题第一时间看这个文件（没有控制台窗口了，日志文件是唯一的排障入口）；
- **开机自启**——勾上之后开机自动把这个 exe 拉起来，不用每次手动双击；
- **退出**——如果这时候还有任务没触发，会弹一次确认框，避免手滑丢单。

## 两个 Windows exe 的区别

Release 页面有两个 Windows exe：`bpq-<版本号>-windows-x86_64.exe` 是托盘版（README
里说的那个，双击就用，推荐）；`bpq-cli-<版本号>-windows-x86_64.exe` 是控制台版，
完整保留 `bpq-cli.exe submit / ls / cancel / status / log / web / daemon` 这套命令
行，用法见 README「使用教程」。两者共享同一套 daemon/WebUI/调度逻辑，只是入口不同：
托盘版没有命令行子命令能力（双击即用，不认参数）；控制台版零参数双击时走的是黑框
懒人流程（关窗口即关程序），带参数敲命令才是它的正经用法。

## SmartScreen 提示

两个 exe 都没有代码签名，Windows 首次运行大概率会弹 SmartScreen 提示「Windows 已
保护你的电脑」——点「更多信息」，再点「仍要运行」即可，后续运行不会再弹。

## Linux / macOS

**懒人版（托盘 UI）只有 Windows**——`tray.py` / `traymain.py` 深度依赖
`ctypes.windll` / `winreg` / `os.startfile` / `pystray` 的 Windows 后端，没做过
跨平台适配。但**控制台/daemon 版**（`bpq-cli`）现在 Linux 和 macOS（Apple Silicon）
也有预编译二进制可下：`Releases` 页面里的 `bpq-cli-<版本号>-linux-x86_64` 和
`bpq-cli-<版本号>-macos-arm64`，同样不需要装 Python / Node，下载后 `chmod +x` 就能
跑 `bpq-cli --help`。命令行用法见 README「使用教程」；不想用预编译二进制，也可以
照样走 README 的 Docker 或源码方式。
