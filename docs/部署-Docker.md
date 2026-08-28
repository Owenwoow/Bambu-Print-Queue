# Docker 部署教程

面向想把 bpq 长期挂在 NAS / 群晖 / 软路由 / 家庭服务器上跑的场景。不想用 Docker 的话，
直接看项目根 [README.md](../README.md) 的「部署」一节（systemd 或直接 `bpq daemon`）。

## 前提

- 打印机已经按 README「安装」一节开好 **LAN Only 模式** 和 **Developer Mode**。
- 宿主机能装 Docker（Docker Engine 或 Docker Desktop），且和打印机在同一局域网，
  能直接 ping 通打印机 IP。bpq 只主动连出去（MQTT 8883 / FTPS 990），不需要打印机
  反过来连宿主机，Docker 默认的 bridge 网络对这种「容器主动连局域网设备」的场景
  开箱可用，不需要 `--network host`。

## 一、准备配置文件

配置文件不打进镜像（含 IP / SERIAL / Access Code，属于每个人的私有信息），
用 volume 挂进容器：

```bash
git clone https://github.com/Owenwoow/Bambu-Print-Queue.git
cd Bambu-Print-Queue
cp config.example.toml config.toml
```

按 README「安装 → 打印机与配置」填好 `[printer]` 段的 `ip` / `serial` / `access_code`。

**Docker 场景下再额外改两处 `[web]` / `[daemon]`：**

```toml
[web]
host = "0.0.0.0"        # 必须。127.0.0.1 只认容器内部回环，端口映射再怎么发布外面也进不来
password = "换成你自己的口令"   # host 非 127.0.0.1 时 daemon 会拒绝启动，必须填

[daemon]
inhibit_sleep = false    # 容器管不了宿主机的电源状态，改用宿主机自己的方式保持在线
```

> 顺带一提：即使只想本机访问，走 Docker 端口映射进来的请求在容器看来源 IP
> 也不是 `127.0.0.1`（会经过 NAT），`allow_local_no_auth` 免鉴权在这条路径上不生效，
> 所以 `password` 无论如何都要填。

## 二、启动

### 用 docker compose（推荐）

仓库自带 [`docker-compose.yml`](../docker-compose.yml)，默认拉取 GitHub Actions
打好的镜像（每个 `vX.Y.Z` 标签发布时自动构建并推到 GHCR）：

```bash
docker compose up -d
```

打开 `http://<宿主机 IP>:8710` 应该能看到登录页。

### 用 docker run

不想用 compose 也行，等价的一条命令：

```bash
docker run -d \
  --name bpq \
  --restart unless-stopped \
  -p 8710:8710 \
  -v "$(pwd)/config.toml:/app/config.toml:ro" \
  -v bpq-data:/app/var \
  ghcr.io/owenwoow/bambu-print-queue:latest
```

### 自己构建镜像

改过代码，或不想用远端镜像：

```bash
docker compose build   # 或：docker build -t bpq .
docker compose up -d
```

## 三、日常操作

```bash
docker compose logs -f bpq          # 看 daemon 日志（等价于 bpq log，但是原始 stdout）
docker compose exec bpq bpq status  # 容器内跑 CLI 子命令，走的是同一个 daemon
docker compose exec bpq bpq ls
docker compose restart bpq          # 重启（任务在 SQLite 里，不会丢）
docker compose pull && docker compose up -d   # 升级到最新镜像
```

`bpq submit` 需要把 3mf 文件也带进容器才能用，命令行场景不如直接用 WebUI 上传方便——
日常提交任务建议走 WebUI，`docker compose exec` 里的 CLI 主要用来查状态、查日志、取消任务。

## 四、数据持久化与备份

`var/`（任务库 `bpq.sqlite3`、日志 `bpq.jsonl`、上传暂存 `tasks/`）落在具名 volume
`bpq-data` 里，`docker compose down` 不会删它，只有显式 `docker compose down -v`
或 `docker volume rm bpq-data` 才会。

备份：

```bash
docker run --rm -v bpq-data:/data -v "$(pwd):/backup" alpine \
  tar czf /backup/bpq-data-backup.tar.gz -C /data .
```

恢复同理，把 tar 解到新的 volume 里再启动容器。

## 五、故障排查

- **容器起来了，但 WebUI 打不开**：先确认 `[web].host` 是不是 `0.0.0.0`
  （见上面「准备配置文件」），这是 Docker 场景下最容易漏的一步。
- **日志里连不上打印机 / MQTT 一直重连**：先在宿主机上（不进容器）直接
  `ping <打印机IP>` 和 `telnet <打印机IP> 8883` 确认网络本身通不通；
  容器网络通常跟宿主机共享同一段路由，宿主机都连不上容器更连不上。
  个别 Docker 网络配置（比如某些 rootless / 自定义 bridge）会挡住到局域网其它
  主机的出站流量，这种情况下 Linux 上可以换 `network_mode: host`
  （`docker-compose.yml` 里加一行，去掉 `ports` 映射），但 Docker Desktop
  （Mac / Windows）不支持 host 网络模式，需要另想办法（比如把 Docker Desktop
  的网络设成桥接到物理网卡）。
- **改了 `config.toml` 不生效**：`config.toml` 是只读挂载（`:ro`），改完宿主机上的
  文件后 `docker compose restart bpq` 一次；WebUI「设置」页里改的话不需要重启，
  但注意 WebUI 写配置也是写回这同一个挂载的文件。
- **健康检查一直不健康**：`Dockerfile` 里的 `HEALTHCHECK` 默认查
  `http://127.0.0.1:8710/api/health`，如果改了 `[web].port` 或关掉了
  `[web].enabled`，这条检查也要跟着改（或者直接删掉，不影响功能，只影响
  `docker ps` 里显示的健康状态）。

## 六、镜像从哪来

`.github/workflows/release.yml` 在每次推 `vX.Y.Z` 标签时会自动把镜像构建并推到
`ghcr.io/owenwoow/bambu-print-queue`，同时打 `latest` 和对应版本号两个 tag。
`Dockerfile` 是多阶段构建：先在 Node 里编前端，再拷进只装了 Python 运行依赖的
最终镜像，不含 Node/npm，体积和攻击面都小一圈。

> 仓库维护者注意：`GITHUB_TOKEN` 推上去的 GHCR 包默认是 **private**，第一次发布后
> 要去仓库的 Packages 页面把 `bambu-print-queue` 这个包的可见性手动改成 Public，
> 否则别人 `docker compose pull` 会因为鉴权失败拉不到镜像。
