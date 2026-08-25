# bpq 的前端

React + Vite + TypeScript + Tailwind。产物 `dist/` **不入库**。

## 构建

```bash
npm install
npm run build
```

产物落在 `web/dist/`，由 daemon 里的 FastAPI 直接托管。没构建的话访问网页会得到
一页构建说明——不影响 daemon 和 CLI 本身。

## 开发

两个终端：

```bash
bpq daemon --fake-printer
```

```bash
npm run dev
```

然后开 `http://localhost:5173`。Vite 把 `/api` 代理到 `127.0.0.1:8710`，同源，
所以后端不需要配 CORS。

`--fake-printer` 起一台合成的假打印机：温度会爬、进度会走、AMS 有四卷料
（照抄 `docs/验证记录-通道A.md` 里记的本机真实配置，好让配料逻辑表现得像真的）。
不占用真打印机那唯一的 MQTT 连接，所以开发时 Bambu Studio 照用不误。

**改后端要手动重启 daemon。** 不能开 uvicorn 的 reload——它 fork 出的子进程会让
daemon 的单实例文件锁失效，两个进程抢打印机的连接。

## 结构

```
src/
  lib/api.ts       fetch 封装。401 抛 Unauthorized，上层跳登录页
  lib/types.ts     与后端 to_dict() 一一对应，改之前先看 src/bpq/snapshot.py
  lib/format.ts    温度/时长/剩余量/状态名的中文格式化
  store/app.ts     zustand + EventSource。merge patch 深合并，断线指数退避重连
  components/
    layout/        AppShell（顶栏 + 主区）、Sidebar（二级菜单）
    ui/            Button / Card / Switch / TriSwitch / …
    printer/       打印机卡片、AMS 面板、托盘色块、HMS 列表
    tasks/         任务行
  pages/           每个路由一个
```

## 三条不要改掉的东西

1. **五个打印开关是三态**（`TriSwitch`）。`null` 表示「跟随全局」，和 `false` 是
   两回事——前者会跟着 `config.toml` 变。把它折叠成两态会让人以为某一单显式关过。
2. **「本次将下发」和「设备当前设置」分开显示，不做对照。** 五个开关里有三个
   根本没有对应的上报字段，画对照表是在编造确定性。
3. **顶栏两个分开的指示灯**：浏览器↔服务、服务↔打印机。这是两件不同的事，
   混成一个会让排障变成猜谜。

## 配色

全部集中在 `src/styles.css` 的 `:root`，别处一律用 Tailwind 的语义名
（`bg` / `surface` / `brand` …）。要换配色只改那一处。

`--brand: #00ae42` 是拓竹绿。配色可以参考，但项目里不放拓竹的 logo 或商标图形。
