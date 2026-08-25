import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { useApp } from "@/store/app";
import { Button, Card, Empty } from "@/components/ui";
import { TaskRow } from "@/components/tasks/TaskRow";
import { cn } from "@/lib/cn";
import { api, Unauthorized } from "@/lib/api";
import { STATE_LABEL } from "@/lib/format";
import type { Task, TaskState } from "@/lib/types";

const TABS = [
  { key: "waiting", label: "待触发" },
  { key: "done", label: "已结束" },
  { key: "all", label: "全部" },
] as const;

/** 「已结束」涵盖的状态，按钮/筛选 chip 用得到，别再散落硬编码。 */
const FINISHED_STATES: TaskState[] = ["started", "cancelled", "aborted", "failed"];

const isWaiting = (t: Task) => t.state === "pending" || t.state === "uploaded";

export function TaskList() {
  const tasks = useApp((s) => s.tasks);
  const setAuthed = useApp((s) => s.setAuthed);
  const refreshTasks = useApp((s) => s.refreshTasks);

  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("waiting");
  const [statusFilter, setStatusFilter] = useState<Set<TaskState>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [purging, setPurging] = useState(false);
  const [purgeResult, setPurgeResult] = useState<string | null>(null);

  function changeTab(k: (typeof TABS)[number]["key"]) {
    setTab(k);
    setStatusFilter(new Set());
    setSelected(new Set());
    setPurgeResult(null);
  }

  function toggleStatus(s: TaskState) {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  let base = tasks.filter((t) =>
    tab === "all" ? true : tab === "waiting" ? isWaiting(t) : !isWaiting(t),
  );
  // 状态细分筛选只在「已结束」tab 生效，避免把「全部」tab 也搞复杂。
  if (tab === "done" && statusFilter.size) {
    base = base.filter((t) => statusFilter.has(t.state));
  }
  const shown = [...base].sort((a, b) =>
    tab === "waiting"
      ? a.scheduled_at.localeCompare(b.scheduled_at)
      : b.created_at.localeCompare(a.created_at),
  );

  // 当前视图里「已结束」的子集——批量清理只对这些生效，待触发的任务没有删除记录一说。
  const finishedInView = shown.filter((t) => !isWaiting(t));

  async function handlePurge() {
    const targets = selected.size
      ? finishedInView.filter((t) => selected.has(t.id))
      : finishedInView;
    if (!targets.length) return;

    const confirmed = window.confirm(
      selected.size
        ? `确定删除选中的 ${targets.length} 条记录？这些行会从数据库里抹掉，不可恢复。`
        : `确定清理这 ${targets.length} 条已结束的任务记录？这些行会从数据库里抹掉，不可恢复。`,
    );
    if (!confirmed) return;

    setPurging(true);
    setPurgeResult(null);
    try {
      const results = await Promise.allSettled(targets.map((t) => api.deleteTask(t.id)));
      const failedResults = results.filter(
        (r): r is PromiseRejectedResult => r.status === "rejected",
      );
      if (failedResults.some((r) => r.reason instanceof Unauthorized)) {
        setAuthed(false);
        return;
      }
      setPurgeResult(`已删除 ${results.length - failedResults.length} 条，失败 ${failedResults.length} 条`);
      setSelected(new Set());
      await refreshTasks();
    } finally {
      setPurging(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">任务</h1>
        <Link to="/tasks/new">
          <Button variant="primary" size="sm">
            <Plus size={15} /> 新建任务
          </Button>
        </Link>
      </div>

      <div className="flex gap-1 rounded-lg border border-line bg-surface p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => changeTab(t.key)}
            className={cn(
              "flex-1 rounded-md px-3 py-1.5 text-sm transition-colors",
              tab === t.key ? "bg-surface-2 font-medium" : "text-muted hover:text-fg",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab !== "waiting" ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            {tab === "done" ? (
              <div className="flex flex-wrap gap-1.5">
                {FINISHED_STATES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleStatus(s)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs transition-colors",
                      statusFilter.has(s)
                        ? "border-brand/40 bg-brand/10 text-brand"
                        : "border-line text-muted hover:text-fg",
                    )}
                  >
                    {STATE_LABEL[s]}
                  </button>
                ))}
              </div>
            ) : (
              <div />
            )}

            <Button
              variant="danger"
              size="sm"
              disabled={purging || !finishedInView.length}
              onClick={handlePurge}
            >
              <Trash2 size={15} />
              {selected.size ? `删除选中的 ${selected.size} 条` : "清理已结束的任务"}
            </Button>
          </div>
          {purgeResult ? <div className="text-xs text-muted">{purgeResult}</div> : null}
        </div>
      ) : null}

      {/* overflow-hidden 裁掉每一行悬停背景的直角——不加的话首尾两行的矩形
          背景会戳出 Card 自己的圆角，看起来像溢出了边框。 */}
      <Card className="overflow-hidden">
        {shown.length ? (
          <div className="divide-y divide-line">
            {shown.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                selectable={!isWaiting(t)}
                selected={selected.has(t.id)}
                onToggle={toggleSelect}
              />
            ))}
          </div>
        ) : (
          <Empty>这里还没有任务。</Empty>
        )}
      </Card>
    </div>
  );
}
