import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  ChevronDown,
  ClipboardList,
  FileClock,
  Github,
  LayoutDashboard,
  Layers,
  Plus,
  Printer,
  Settings,
  Boxes,
} from "lucide-react";
import { cn } from "@/lib/cn";

interface NavChild {
  to: string;
  label: string;
  icon: typeof Plus;
}
interface NavGroup {
  key: string;
  label: string;
  icon: typeof Plus;
  to?: string;
  children?: NavChild[];
}

const NAV: NavGroup[] = [
  { key: "dash", label: "概览", icon: LayoutDashboard, to: "/" },
  {
    key: "tasks",
    label: "任务",
    icon: ClipboardList,
    children: [
      { to: "/tasks/new", label: "新建任务", icon: Plus },
      { to: "/tasks", label: "全部任务", icon: Layers },
    ],
  },
  {
    key: "printer",
    label: "打印机",
    icon: Printer,
    children: [
      { to: "/printer", label: "实时状态", icon: Printer },
      { to: "/printer/ams", label: "AMS 与耗材", icon: Boxes },
    ],
  },
  { key: "journal", label: "日志", icon: FileClock, to: "/journal" },
  { key: "settings", label: "设置", icon: Settings, to: "/settings" },
];

const STORAGE_KEY = "bpq.nav.open";

function loadOpen(): Record<string, boolean> {
  try {
    // JSON.parse("") 会抛异常走 catch；已经存过值的话这里拿到的是真实结果。
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "") ?? {};
  } catch {
    // 默认全部收起——用户点开哪个菜单，才展开哪个，记忆下来。
    return {};
  }
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>(loadOpen);
  const { pathname } = useLocation();

  const toggle = (key: string) => {
    const next = { ...open, [key]: !open[key] };
    setOpen(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  return (
    <nav className="flex h-full w-[248px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-5">
        <div className="grid h-7 w-7 place-items-center rounded-md bg-brand text-brand-fg">
          <span className="text-[13px] font-bold">b</span>
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold">Bambu Print Queue</div>
          <div className="text-[11px] text-muted">拓竹打印任务预约</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {NAV.map((group) => {
          if (!group.children) {
            return (
              <NavLink
                key={group.key}
                to={group.to!}
                end
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    "mb-0.5 flex h-9 items-center gap-2.5 rounded-lg px-3 text-sm transition-colors",
                    isActive
                      ? "bg-brand/12 font-medium text-brand"
                      : "text-muted hover:bg-surface-2 hover:text-fg",
                  )
                }
              >
                <group.icon size={16} />
                {group.label}
              </NavLink>
            );
          }

          const expanded = open[group.key] ?? false;
          const hasActive = group.children.some((c) => pathname === c.to);

          return (
            <div key={group.key} className="mb-0.5">
              <button
                type="button"
                onClick={() => toggle(group.key)}
                className={cn(
                  "flex h-9 w-full items-center gap-2.5 rounded-lg px-3 text-sm transition-colors",
                  hasActive && !expanded
                    ? "text-brand"
                    : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <group.icon size={16} />
                <span className="flex-1 text-left">{group.label}</span>
                <ChevronDown
                  size={14}
                  className={cn("transition-transform", expanded ? "" : "-rotate-90")}
                />
              </button>

              {expanded ? (
                <div className="relative ml-[22px] mt-0.5 space-y-0.5 border-l border-line pl-3">
                  {group.children.map((child) => (
                    <NavLink
                      key={child.to}
                      to={child.to}
                      end
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        cn(
                          "flex h-8 items-center gap-2 rounded-lg px-2.5 text-[13px] transition-colors",
                          isActive
                            ? "bg-brand/12 font-medium text-brand"
                            : "text-muted hover:bg-surface-2 hover:text-fg",
                        )
                      }
                    >
                      <child.icon size={14} />
                      {child.label}
                    </NavLink>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="border-t border-line px-5 py-3">
        <a
          href="https://github.com/Owenwoow/Bambu-Print-Queue"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2 text-xs text-muted transition-colors hover:text-fg"
        >
          <Github size={14} />
          GitHub
        </a>
      </div>
    </nav>
  );
}
