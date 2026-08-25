import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Calendar, ChevronDown, Filter, Trash2 } from "lucide-react";
import { api, Unauthorized } from "@/lib/api";
import { useApp } from "@/store/app";
import { Button, Card, CardHeader, Empty, Input } from "@/components/ui";
import { cn } from "@/lib/cn";
import type { JournalPage } from "@/lib/types";

/** 事件名 → 中文 + 色调。这些名字定义在 src/bpq/journal.py 的约定里。 */
const EVENTS: Record<string, [string, string]> = {
  submitted: ["已受理", "text-muted"],
  uploaded: ["已静默上传", "text-brand"],
  triggered: ["到点触发", "text-fg"],
  started: ["已开始打印", "text-brand"],
  aborted: ["放弃", "text-warn"],
  cancelled: ["已取消", "text-muted"],
  failed: ["失败", "text-danger"],
  rescheduled: ["改了时间", "text-muted"],
  connection_reclaimed: ["抢回了连接", "text-warn"],
  daemon_start: ["服务启动", "text-muted"],
  daemon_stop: ["服务停止", "text-muted"],
};

const PAGE_SIZES = [25, 50, 100, 200] as const;

/** 显示到秒，排障时经常要精确对时间。 */
function fullTime(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function daysAgo(n: number): string {
  return isoDate(new Date(Date.now() - n * 86400_000));
}

const QUICK_RANGES: Array<[string, number | null]> = [
  ["全部时间", null],
  ["今天", 1],
  ["近 7 天", 7],
  ["近 30 天", 30],
];

/** 弹出面板的通用外壳：一个触发按钮 + 点击外部关闭的透明遮罩 + 面板本体。
 * 筛选栏原来是三行常驻控件，改成按需弹出，平时只占一行。 */
function FilterPopover({
  trigger, open, onOpenChange, align = "left", children,
}: {
  trigger: ReactNode;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  align?: "left" | "right";
  children: ReactNode;
}) {
  return (
    <div className="relative">
      <Button size="sm" variant="outline" onClick={() => onOpenChange(!open)}>
        {trigger}
      </Button>
      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => onOpenChange(false)} />
          <div
            className={cn(
              "absolute top-full z-20 mt-1 w-72 space-y-2 rounded-lg border border-line bg-surface p-2.5 shadow-lg",
              align === "left" ? "left-0" : "right-0",
            )}
          >
            {children}
          </div>
        </>
      ) : null}
    </div>
  );
}

/** 「清理日志」的二次确认面板。默认不给「一键全清」的快捷路径——日志是排查
 * 「为什么那晚没打起来」的唯一现场，每一种粒度都单独二次确认。 */
function ClearJournalControl({ onDone }: { onDone: (deleted: number) => void }) {
  const setAuthed = useApp((s) => s.setAuthed);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async (before: string | undefined, confirmText: string) => {
    if (!confirm(confirmText)) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.clearJournal(before);
      setOpen(false);
      onDone(r.deleted);
    } catch (e) {
      if (e instanceof Unauthorized) {
        setAuthed(false);
        return;
      }
      setErr(e instanceof Error ? e.message : "清理失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative">
      <Button size="sm" variant="danger" onClick={() => setOpen((v) => !v)}>
        <Trash2 size={14} /> 清理日志
      </Button>
      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 w-72 space-y-1 rounded-lg border border-line bg-surface p-2 shadow-lg">
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(daysAgo(30), "清理 30 天前的日志？此操作不可恢复。")
            }
            className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-surface-2 disabled:opacity-50"
          >
            清理 30 天前的日志
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(daysAgo(7), "清理 7 天前的日志？此操作不可恢复。")
            }
            className="w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-surface-2 disabled:opacity-50"
          >
            清理 7 天前的日志
          </button>
          <div className="my-1 border-t border-line" />
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(
                undefined,
                "全部清空日志？此操作不可恢复，且日志是排查「为什么那晚没打起来」的唯一现场，请谨慎确认。",
              )
            }
            className="w-full rounded-md px-2 py-1.5 text-left text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
          >
            全部清空
          </button>
          {err ? <div className="px-2 pt-1 text-xs text-danger">{err}</div> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

export function JournalPage() {
  const setAuthed = useApp((s) => s.setAuthed);

  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [data, setData] = useState<JournalPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // 两个筛选面板同一时刻只开一个，省得屏幕上同时飘着两块浮层。
  const [typeOpen, setTypeOpen] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);
  const openType = (v: boolean) => {
    setTypeOpen(v);
    if (v) setDateOpen(false);
  };
  const openDate = (v: boolean) => {
    setDateOpen(v);
    if (v) setTypeOpen(false);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await api.journalPage({
        events: selectedEvents.length ? selectedEvents : undefined,
        since: since || undefined,
        until: until || undefined,
        offset,
        limit,
      });
      setData(page);
      setExpanded(new Set());
    } catch (e) {
      if (e instanceof Unauthorized) {
        setAuthed(false);
        return;
      }
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [selectedEvents, since, until, offset, limit, setAuthed]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleEvent = (name: string) => {
    setOffset(0);
    setSelectedEvents((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    );
  };

  const setRange = (days: number | null) => {
    setOffset(0);
    if (days === null) {
      setSince("");
      setUntil("");
      return;
    }
    setSince(daysAgo(days - 1));
    setUntil(isoDate(new Date()));
  };

  const toggleExpand = (i: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const onClearDone = (deleted: number) => {
    setNotice(`已删除 ${deleted} 条`);
    if (offset !== 0) setOffset(0);
    else void load();
  };

  const hasFilter = selectedEvents.length > 0 || !!since || !!until;
  const events = data?.events ?? [];

  const typeLabel =
    selectedEvents.length === 0
      ? "全部"
      : selectedEvents.length === 1
        ? (EVENTS[selectedEvents[0]] ?? [selectedEvents[0]])[0]
        : `已选 ${selectedEvents.length} 项`;

  const isQuickRange = (days: number) =>
    since === daysAgo(days - 1) && until === isoDate(new Date());

  const dateLabel = (() => {
    if (!since && !until) return "全部时间";
    if (isQuickRange(1)) return "今天";
    if (isQuickRange(7)) return "近 7 天";
    if (isQuickRange(30)) return "近 30 天";
    if (since && until) return `${since.slice(5)} ~ ${until.slice(5)}`;
    if (since) return `${since.slice(5)} 起`;
    return `至 ${until.slice(5)}`;
  })();

  const clearFilters = () => {
    setOffset(0);
    setSelectedEvents([]);
    setSince("");
    setUntil("");
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">日志</h1>
      <Card>
        <CardHeader
          title="事件流"
          sub="排查「为什么那晚没打起来」"
          right={<ClearJournalControl onDone={onClearDone} />}
        />

        {/* 筛选栏：原来是三行常驻控件（类型 chip、日期区间、每页大小），
            平时不筛选也占着一大块地方。改成两个弹出面板，默认只占一行；
            「每页」挪到底部分页栏——那本来就是分页的一部分，不是筛选条件。 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <FilterPopover
            open={typeOpen}
            onOpenChange={openType}
            align="left"
            trigger={
              <>
                <Filter size={13} />
                类型：{typeLabel}
                <ChevronDown size={13} />
              </>
            }
          >
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setOffset(0);
                  setSelectedEvents([]);
                }}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs transition-colors",
                  selectedEvents.length === 0
                    ? "border-brand/50 bg-brand/10 font-medium text-brand"
                    : "border-line text-muted hover:text-fg",
                )}
              >
                全部
              </button>
              {events.map((name) => {
                const [label] = EVENTS[name] ?? [name, ""];
                const active = selectedEvents.includes(name);
                return (
                  <button
                    key={name}
                    type="button"
                    onClick={() => toggleEvent(name)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs transition-colors",
                      active
                        ? "border-brand/50 bg-brand/10 font-medium text-brand"
                        : "border-line text-muted hover:text-fg",
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </FilterPopover>

          <FilterPopover
            open={dateOpen}
            onOpenChange={openDate}
            align="left"
            trigger={
              <>
                <Calendar size={13} />
                时间：{dateLabel}
                <ChevronDown size={13} />
              </>
            }
          >
            <div className="flex flex-wrap gap-1.5">
              {QUICK_RANGES.map(([label, days]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    setRange(days);
                    setDateOpen(false);
                  }}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs transition-colors",
                    dateLabel === label
                      ? "border-brand/50 bg-brand/10 font-medium text-brand"
                      : "border-line text-muted hover:text-fg",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2 border-t border-line pt-2">
              <Input
                type="date"
                value={since}
                onChange={(e) => {
                  setOffset(0);
                  setSince(e.target.value);
                }}
                className="w-auto text-xs"
              />
              <span className="text-xs text-muted">至</span>
              <Input
                type="date"
                value={until}
                onChange={(e) => {
                  setOffset(0);
                  setUntil(e.target.value);
                }}
                className="w-auto text-xs"
              />
            </div>
          </FilterPopover>

          {hasFilter ? (
            <button
              type="button"
              onClick={clearFilters}
              className="text-xs text-muted underline-offset-2 hover:text-fg hover:underline"
            >
              清除筛选
            </button>
          ) : null}
        </div>

        {notice ? (
          <div className="border-b border-line bg-brand/10 px-4 py-2 text-xs text-brand">
            {notice}
          </div>
        ) : null}

        {loading && !data ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-8 animate-pulse rounded-md bg-surface-2" />
            ))}
          </div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{error}</div>
        ) : data && data.items.length ? (
          <div className={cn("divide-y divide-line", loading && "opacity-60")}>
            {data.items.map((r, i) => {
              const [label, tone] = EVENTS[r.event] ?? [r.event, "text-muted"];
              const extra = Object.entries(r).filter(
                ([k]) => !["ts", "event"].includes(k),
              );
              const isOpen = expanded.has(i);
              return (
                <div key={`${r.ts}-${i}`} className="px-4 py-2 text-sm">
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => toggleExpand(i)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") toggleExpand(i);
                    }}
                    className="flex cursor-pointer flex-wrap items-baseline gap-x-3"
                  >
                    <span className="tabular shrink-0 font-mono text-xs text-muted">
                      {fullTime(String(r.ts))}
                    </span>
                    <span className={cn("shrink-0 font-medium", tone)}>{label}</span>
                    <span className="min-w-0 truncate font-mono text-xs text-muted">
                      {extra.map(([k, v]) => `${k}=${String(v)}`).join("  ")}
                    </span>
                  </div>
                  {isOpen && extra.length ? (
                    <div className="mt-1.5 space-y-0.5 pl-1 font-mono text-xs text-muted">
                      {extra.map(([k, v]) => (
                        <div key={k}>
                          {k}=
                          {k === "task" ? (
                            <Link
                              to={`/tasks/${String(v)}`}
                              className="text-brand hover:underline"
                            >
                              {String(v)}
                            </Link>
                          ) : (
                            String(v)
                          )}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <Empty>{hasFilter ? "当前筛选条件下没有记录。" : "还没有日志。"}</Empty>
        )}

        {data ? (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2 text-xs text-muted">
            <span>
              {data.total === 0
                ? "共 0 条"
                : `第 ${data.offset + 1}–${Math.min(data.offset + data.items.length, data.total)} 条 / 共 ${data.total} 条`}
            </span>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5">
                每页
                <select
                  value={limit}
                  onChange={(e) => {
                    setOffset(0);
                    setLimit(Number(e.target.value));
                  }}
                  className="h-7 rounded-md border border-line bg-surface-2 px-1.5 text-xs focus:border-brand/60 focus:outline-none"
                >
                  {PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                上一页
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={loading || offset + limit >= data.total}
                onClick={() => setOffset(offset + limit)}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
