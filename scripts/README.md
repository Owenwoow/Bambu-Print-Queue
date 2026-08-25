# 手动验证脚本（第 0–3 步）

**先跑完这四步，再碰调度器。** 调度器是闭着眼睛也能写的 CRUD，
唯独「发送」这一下不是自己说了算——先做完调度器再发现通道不通，等于白做。

前置：打印机开启 **LAN Only 模式**（才会显示 Access Code）+ **Developer Mode**
（关闭授权控制，否则启动指令被拒）。把 IP / SERIAL / Access Code 填进项目根的 `config.toml`。

| 步骤 | 做什么 | 判据 | 失败意味着 |
|---|---|---|---|
| 0 | [`00_silent_upload_check.md`](00_silent_upload_check.md) 手动传一个大 3mf，站旁边听 | 无声、屏不亮、功耗保持 5W 基线、状态仍 idle | **地基假设不成立**，架构翻转为「到点才上传」（`upload_timing = "late"`） |
| 1 | `python scripts/01_ftps_upload.py <文件>` | 文件出现在打印机存储 | 数据通道 SSL 问题，改用脚本里给的 curl 备用手段 |
| 2 | `python scripts/02_mqtt_state.py` | 稳定读到 `gcode_state`，并打出固件版本号 | 证书/端口/Access Code 问题 |
| 3 | `python scripts/03_start_print.py <文件名>` | `gcode_state` 从 IDLE 转 RUNNING | 报 HMS `0500-0500-0001-0007` = 没开 Developer Mode 或固件不匹配 |

第 3 步过了，项目最大的不确定性就清除了。

## v0.2 的取数脚本（第 4–5 步）

这两个是**取数，不是验收**——都不下发任何指令，打印机不会有物理动作。
它们的产出是后续建模的依据：不做的话，状态字段名和多色映射语义就只能照抄社区文档，
而这个项目已经被「社区文档与实测相反」坑过两次。

| 步骤 | 做什么 | 产出 | 需要真机 |
|---|---|---|---|
| 4 | `python scripts/04_dump_pushall.py` | `docs/samples/pushall_raw.json`（原始）+ `tests/fixtures/reports/pushall_full.json`（脱敏夹具） | 是，但只订阅，零动作 |
| 5 | `python scripts/05_dump_3mf.py <多色3mf>` | 打印 3mf 的耗材/盘/映射相关字段，供 `docs/v0.2-多色映射语义.md` 取证 | 否，纯读本地文件 |

第 5 步**最好用一个「项目里配了 3 卷料、但某个盘只用其中第 1 和第 3 卷」的 3mf**
——只有这种文件才能分辨 `ams_mapping` 数组该按「该盘用到的耗材数」还是
「项目全局槽位数」来排。结论写进 [`docs/v0.2-多色映射语义.md`](../docs/v0.2-多色映射语义.md)。

**把第 2 步打出的固件版本号记进 `docs/`。** 在验证通过的版本上锁定不升级；
授权控制与 LAN 行为在持续变动，升级前先查 changelog，升级后重跑第 3 步。
