import { useState } from "react";
import { Link } from "react-router-dom";
import { X, Trash2 } from "lucide-react";
import { Badge, ColorDot } from "@/components/ui";
import { STATE_LABEL, STATE_TONE, clock, untilText } from "@/lib/format";
import { api, Unauthorized } from "@/lib/api";
import { useApp } from "@/store/app";
import { cn } from "@/lib/cn";
import type { Task } from "@/lib/types";

/**
 * 一行任务。
 *
 * 行内有两种互斥的操作，取决于任务是否已经结束（见 CLAUDE.md 里
 * cancelTask / deleteTask 的语义区分）：
 *   - 待触发（pending / uploaded）→「取消」：软取消，记录还在。
 *   - 已结束（started / cancelled / aborted / failed）→「删除记录」：
 *     真的从库里抹掉，后端对未结束任务调这个接口会 409。
 *
 * 操作按钮不放在 <Link> 内部——整行还是可点进详情页的 <a>，按钮是它的
 * 兄弟节点，绝对定位到右侧。这样按钮和跳转天然不会互相干扰，不需要在
 * onClick 里 preventDefault/stopPropagation 去拦截冒泡。
 */
export function TaskRow({
  task,
  selectable = false,
  selected = false,
  onToggle,
}: {
  task: Task;
  /** 批量删除用的多选模式（TaskList 6.2），不传就是普通单行，不出复选框。 */
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (id: string) => void;
}) {
  const waiting = task.state === "pending" || task.state === "uploaded";
  const setAuthed = useApp((s) => s.setAuthed);
  const refreshTasks = useApp((s) => s.refreshTasks);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAction() {
    const confirmed = window.confirm(
      waiting
        ? "确定取消这个任务？文件如果已经传上去了会留在打印机存储里，但不会被打印。"
        : "确定删除这条记录？这一行会从数据库里抹掉，不可恢复。",
    );
    if (!confirmed) return;

    setError(null);
    setBusy(true);
    try {
      if (waiting) {
        await api.cancelTask(task.id);
      } else {
        await api.deleteTask(task.id);
      }
      // SSE 会推 tasks 事件自动更新列表，这里再补一次保证不遗漏。
      await refreshTasks();
    } catch (e) {
      if (e instanceof Unauthorized) {
        setAuthed(false);
        return;
      }
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    // 悬停背景放在整行（含复选框）上，不能只挂在 <Link> 上——复选框是它前面
    // 的兄弟节点，背景矩形从 Link 才开始的话，鼠标划过整行时复选框那一小块
    // 会像被生硬切掉一样露出底色，看起来很突兀。
    <div className="group relative flex items-center transition-colors hover:bg-surface-2">
      {selectable ? (
        <input
          type="checkbox"
          className="ml-4 shrink-0 accent-brand"
          checked={selected}
          onChange={() => onToggle?.(task.id)}
          aria-label="选择这条任务用于批量删除"
        />
      ) : null}

      <Link
        to={`/tasks/${task.id}`}
        className="flex min-w-0 flex-1 items-center gap-3 py-3 pl-4 pr-11"
      >
        <div className="flex shrink-0 -space-x-1">
          {task.filaments.length ? (
            task.filaments.map((f) => <ColorDot key={f.id} rgb={f.rgb} size={14} />)
          ) : (
            <ColorDot rgb="4a5058" size={14} />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{task.title || task.remote_name}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
            <span>{clock(task.scheduled_at)}</span>
            {waiting ? <span className="text-brand">{untilText(task.scheduled_at)}</span> : null}
            {task.error ? <span className="text-warn">{task.error}</span> : null}
          </div>
          {error ? <div className="mt-0.5 text-xs text-danger">{error}</div> : null}
        </div>

        <Badge className={STATE_TONE[task.state]}>{STATE_LABEL[task.state]}</Badge>
      </Link>

      <button
        type="button"
        disabled={busy}
        onClick={handleAction}
        title={waiting ? "取消任务" : "删除记录"}
        aria-label={waiting ? "取消任务" : "删除记录"}
        className={cn(
          "absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-md p-1.5 transition-opacity",
          // 手机没有 hover，常驻显示；桌面端悬停/键盘聚焦才出现，避免列表太吵。
          "pointer-events-auto opacity-100",
          "sm:pointer-events-none sm:opacity-0",
          "sm:group-hover:pointer-events-auto sm:group-hover:opacity-100",
          "focus-visible:pointer-events-auto focus-visible:opacity-100",
          "disabled:pointer-events-none disabled:opacity-40",
          waiting ? "text-muted hover:text-warn" : "text-muted hover:text-danger",
        )}
      >
        {waiting ? <X size={16} /> : <Trash2 size={16} />}
      </button>
    </div>
  );
}
