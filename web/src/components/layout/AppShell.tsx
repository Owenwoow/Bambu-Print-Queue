import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { AlertTriangle, Menu, Monitor, Moon, Plug, PlugZap, RefreshCw, Sun, X } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { useApp } from "@/store/app";
import { api } from "@/lib/api";
import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";
import { useTheme, type ThemeChoice } from "@/lib/theme";

/**
 * T8 浅色主题 demo：跟随系统 / 浅色 / 深色 三态切换器。
 * 放顶栏、紧挨刷新按钮，图标用 Monitor / Sun / Moon，选中态用品牌色高亮。
 * 具体存储 / 应用逻辑在 lib/theme.ts 的 useTheme 里，这里只管画 UI。
 */
const THEME_OPTS: Array<{ v: ThemeChoice; icon: typeof Monitor; label: string }> = [
  { v: "system", icon: Monitor, label: "跟随系统" },
  { v: "light", icon: Sun, label: "浅色" },
  { v: "dark", icon: Moon, label: "深色" },
];

function ThemeSwitcher() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {THEME_OPTS.map(({ v, icon: Icon, label }) => (
        <button
          key={v}
          type="button"
          onClick={() => setTheme(v)}
          title={label}
          aria-label={label}
          aria-pressed={theme === v}
          className={cn(
            "rounded-md p-1.5 transition-colors",
            theme === v ? "bg-brand text-brand-fg" : "text-muted hover:text-fg",
          )}
        >
          <Icon size={14} />
        </button>
      ))}
    </div>
  );
}

const CRUMBS: Array<[string, string]> = [
  ["/tasks/new", "任务 / 新建任务"],
  ["/tasks", "任务 / 全部任务"],
  ["/printer/ams", "打印机 / AMS 与耗材"],
  ["/printer", "打印机 / 实时状态"],
  ["/journal", "日志"],
  ["/settings", "设置"],
  ["/", "概览"],
];

function crumb(pathname: string): string {
  if (pathname.startsWith("/tasks/") && pathname !== "/tasks/new") return "任务 / 任务详情";
  return CRUMBS.find(([p]) => pathname === p || pathname.startsWith(p + "/"))?.[1] ?? "";
}

/** 一个指示灯。两条连接分开显示——混在一起会让排障变成猜谜。 */
function Dot({ ok, label, warn }: { ok: boolean; label: string; warn?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted" title={label}>
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          ok ? "bg-brand" : warn ? "bg-warn animate-dot" : "bg-danger animate-dot",
        )}
      />
      <span className="hidden sm:inline">{label}</span>
    </span>
  );
}

export function AppShell() {
  const { pathname } = useLocation();
  const { streamConnected, link, connect, disconnect, loadConfig } = useApp();
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    connect();
    void loadConfig();
    return disconnect;
  }, [connect, disconnect, loadConfig]);

  useEffect(() => setDrawer(false), [pathname]);

  const toggleLink = async () => {
    setBusy(true);
    try {
      if (link?.yielded) await api.resumeLink();
      else await api.yieldLink();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full">
      <div className="hidden md:block">
        <Sidebar />
      </div>

      {/* 手机上侧栏收进抽屉。「睡前躺床上改一下时间」是这个界面存在的理由之一。 */}
      {drawer ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
          <div className="absolute left-0 top-0 h-full">
            <Sidebar onNavigate={() => setDrawer(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
          <button
            className="rounded-lg p-1.5 hover:bg-surface-2 md:hidden"
            onClick={() => setDrawer((v) => !v)}
            aria-label="菜单"
          >
            {drawer ? <X size={18} /> : <Menu size={18} />}
          </button>

          <div className="min-w-0 flex-1 truncate text-sm text-muted">{crumb(pathname)}</div>

          <div className="flex items-center gap-3">
            <Dot ok={streamConnected} label="服务" />
            <Dot
              ok={!!link?.connected && !link?.stale}
              warn={!!link?.yielded || !!link?.stale}
              label={
                link?.yielded ? "已让出" : link?.stale ? "无报文" : "打印机"
              }
            />

            <Button
              size="sm"
              variant={link?.yielded ? "primary" : "outline"}
              onClick={toggleLink}
              disabled={busy}
              title={
                link?.yielded
                  ? "抢回连接。让出期间定时任务照常到点触发，会自动抢回。"
                  : "断开 MQTT，让 Bambu Studio 能连打印机。定时任务不受影响。"
              }
            >
              {link?.yielded ? <PlugZap size={14} /> : <Plug size={14} />}
              <span className="hidden sm:inline">
                {link?.yielded ? "抢回连接" : "让给 Studio"}
              </span>
            </Button>

            <ThemeSwitcher />

            <Button
              size="sm"
              variant="ghost"
              onClick={() => void api.refresh()}
              title="重新拉一次全量状态（只读查询，打印机不会有动作）"
            >
              <RefreshCw size={14} />
            </Button>
          </div>
        </header>

        {link && !link.connected && !link.yielded ? (
          <div className="flex items-center gap-2 border-b border-danger/30 bg-danger/10 px-4 py-1.5 text-xs text-danger">
            <AlertTriangle size={14} />
            <span className="flex-1">
              连不上打印机{link.last_error ? `：${link.last_error}` : ""}。
              定时任务仍在册，但到点若仍连不上会被放弃。
            </span>
            <Link to="/settings#printer-connection" className="shrink-0">
              <Button size="sm" variant="primary">
                连接打印机
              </Button>
            </Link>
          </div>
        ) : null}

        <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function NotFound() {
  return (
    <div className="grid h-full place-items-center text-sm text-muted">
      <div className="space-y-3 text-center">
        <div>没有这个页面。</div>
        <Link to="/" className="text-brand hover:underline">
          回到概览
        </Link>
      </div>
    </div>
  );
}
