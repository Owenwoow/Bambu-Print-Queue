import { Link } from "react-router-dom";
import { Plug, Plus } from "lucide-react";
import { useApp } from "@/store/app";
import { Button, Card, CardHeader, Empty } from "@/components/ui";
import { PrinterCard } from "@/components/printer";
import { TaskRow } from "@/components/tasks/TaskRow";

export function Dashboard() {
  const { printer, tasks, config, link } = useApp();

  // 打印机从未配置过，或者配置了但当前连不上——两种情况都要把人引去连接设置，
  // 已经连上时绝不显示，免得打扰正常使用。
  const needsConnect = !link?.connected || !config?.printer.ip;

  const pending = tasks
    .filter((t) => t.state === "pending" || t.state === "uploaded")
    .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));
  const recent = tasks
    .filter((t) => !["pending", "uploaded"].includes(t.state))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 5);

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">概览</h1>
        <Link to="/tasks/new">
          <Button variant="primary" size="sm">
            <Plus size={15} /> 新建任务
          </Button>
        </Link>
      </div>

      {needsConnect ? (
        <Card className="border-warn/40 bg-warn/5">
          <div className="flex flex-col items-start gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-semibold">还没连上打印机</div>
              <div className="mt-1 text-xs text-muted">
                填一下 IP、序列号、Access Code 这些连接参数，定时任务到点才有机器可打。
              </div>
            </div>
            <Link to="/settings#printer-connection" className="shrink-0">
              <Button variant="primary" size="sm">
                <Plug size={15} /> 连接打印机
              </Button>
            </Link>
          </div>
        </Card>
      ) : null}

      {printer ? (
        <PrinterCard printer={printer} />
      ) : (
        <Card>
          <Empty>还没读到打印机状态。</Empty>
        </Card>
      )}

      {/* overflow-hidden：最后一行任务悬停时的矩形背景会戳出 Card 的圆角，裁掉它。 */}
      <Card className="overflow-hidden">
        <CardHeader
          title="待触发"
          sub={pending.length ? `${pending.length} 个任务在等` : undefined}
        />
        {pending.length ? (
          <div className="divide-y divide-line">
            {pending.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </div>
        ) : (
          <Empty>
            <div>没有待触发的任务。</div>
            <Link to="/tasks/new" className="text-brand hover:underline">
              新建一个
            </Link>
          </Empty>
        )}
      </Card>

      {recent.length ? (
        <Card className="overflow-hidden">
          <CardHeader title="最近" />
          <div className="divide-y divide-line">
            {recent.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
