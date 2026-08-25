import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store/app";
import { Badge, Button, Card, CardHeader, ColorDot, Empty, Input } from "@/components/ui";
import {
  OPTION_LABEL,
  STATE_LABEL,
  STATE_TONE,
  clock,
  untilText,
} from "@/lib/format";
import type { PrintOptions, Task } from "@/lib/types";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2 text-sm">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="min-w-0 truncate text-right">{value}</span>
    </div>
  );
}

function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const { tasks, config, refreshTasks } = useApp();
  const [task, setTask] = useState<Task | null>(null);
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const local = tasks.find((t) => t.id === id);
    if (local) {
      setTask(local);
      setWhen(toLocalInput(local.scheduled_at));
      return;
    }
    if (id) {
      api.task(id).then(
        (t) => {
          setTask(t);
          setWhen(toLocalInput(t.scheduled_at));
        },
        () => setTask(null),
      );
    }
  }, [id, tasks]);

  if (!task) {
    return (
      <Card>
        <Empty>找不到这个任务。</Empty>
      </Card>
    );
  }

  const editable = task.state === "pending" || task.state === "uploaded";
  const defaults = config?.print_defaults;

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const t = await api.patchTask(task.id, {
        scheduled_at: new Date(when).toISOString(),
      });
      setTask(t);
      await refreshTasks();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!confirm("取消这个任务？文件已经传上去的话会留在打印机存储里。")) return;
    setBusy(true);
    try {
      await api.cancelTask(task.id);
      await refreshTasks();
      nav("/tasks");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "取消失败");
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 pb-10">
      <div className="flex items-center gap-3">
        <Link to="/tasks" className="rounded-lg p-1.5 hover:bg-surface-2">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">
          {task.title || task.remote_name}
        </h1>
        <Badge className={STATE_TONE[task.state]}>{STATE_LABEL[task.state]}</Badge>
      </div>

      {task.state === "uploaded" ? (
        <div className="flex items-start gap-2 rounded-lg border border-brand/35 bg-brand/10 p-3 text-sm text-brand">
          <ShieldCheck size={16} className="mt-0.5 shrink-0" />
          <div>
            文件已就位于打印机存储中，在触发时刻之前不会执行任何动作。
            <div className="mt-0.5 text-xs opacity-80">
              {untilText(task.scheduled_at)}触发
            </div>
          </div>
        </div>
      ) : null}

      {task.error ? (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          {task.error}
        </div>
      ) : null}

      <Card>
        <CardHeader title="触发时刻" />
        <div className="space-y-3 p-4">
          {editable ? (
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="datetime-local"
                value={when}
                onChange={(e) => setWhen(e.target.value)}
                className="max-w-[16rem]"
              />
              <Button
                size="sm"
                variant="primary"
                onClick={save}
                disabled={busy || toLocalInput(task.scheduled_at) === when}
              >
                改时间
              </Button>
            </div>
          ) : (
            <div className="text-sm">{clock(task.scheduled_at)}</div>
          )}
          {editable ? (
            <p className="text-xs text-muted">
              文件已经在打印机上了，改时间不需要重新上传。
            </p>
          ) : null}
        </div>
      </Card>

      {task.filaments.length ? (
        <Card>
          <CardHeader
            title="耗材映射"
            sub={task.mapping_source === "manual" ? "人工指定" : "自动匹配"}
          />
          <div className="divide-y divide-line">
            {task.filaments.map((f, i) => (
              <div key={f.id} className="flex items-center gap-2.5 px-4 py-3 text-sm">
                <ColorDot rgb={f.rgb} size={16} />
                <span className="flex-1">
                  耗材 {f.id}
                  <span className="ml-2 text-muted">
                    {f.type} · {f.info_idx} · {f.used_g} g
                  </span>
                </span>
                <span className="font-mono text-xs text-muted">
                  → tray {task.ams_mapping[i] ?? "?"}
                </span>
              </div>
            ))}
          </div>
          {task.mapping_notes.length ? (
            <div className="space-y-1 border-t border-line bg-surface-2/50 p-3">
              {task.mapping_notes.map((n, i) => (
                <div key={i} className="text-xs text-muted">
                  {n}
                </div>
              ))}
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="本次将下发的参数"
          sub="这是我们发给打印机的值，不是从打印机读回来的状态"
        />
        <div className="divide-y divide-line">
          {(Object.keys(OPTION_LABEL) as Array<keyof PrintOptions>).map((k) => {
            const v = task.options[k];
            const fallback = defaults?.[k] ?? false;
            return (
              <Row
                key={k}
                label={OPTION_LABEL[k as keyof typeof OPTION_LABEL]}
                value={
                  v === null ? (
                    // null 和 false 是两回事：前者会跟着全局默认走
                    <span className="text-muted">
                      跟随全局（当前：{fallback ? "开" : "关"}）
                    </span>
                  ) : v ? (
                    <span className="text-brand">开</span>
                  ) : (
                    "关"
                  )
                }
              />
            );
          })}
        </div>
      </Card>

      <Card>
        <CardHeader title="细节" />
        <div className="divide-y divide-line">
          <Row label="任务 ID" value={<span className="font-mono text-xs">{task.id}</span>} />
          <Row label="打印机上的文件名" value={task.remote_name} />
          <Row label="盘" value={task.plate} />
          <Row label="板材" value={task.bed_type} />
          <Row label="来源" value={task.origin === "web" ? "网页" : "命令行"} />
          <Row label="创建于" value={clock(task.created_at)} />
          <Row label="上传于" value={clock(task.uploaded_at)} />
          <Row label="触发于" value={clock(task.triggered_at)} />
        </div>
      </Card>

      {task.sent_payload ? (
        <Card>
          <CardHeader
            title="实际下发的指令"
            sub="留着它，是为了「打印机行为不对」时不必从复现开始查"
          />
          <pre className="overflow-x-auto p-4 font-mono text-[11px] leading-relaxed text-muted">
            {JSON.stringify(JSON.parse(task.sent_payload), null, 2)}
          </pre>
        </Card>
      ) : null}

      {err ? <div className="text-sm text-danger">{err}</div> : null}

      {editable ? (
        <Button variant="danger" onClick={cancel} disabled={busy}>
          <Trash2 size={15} /> 取消这个任务
        </Button>
      ) : null}
    </div>
  );
}
