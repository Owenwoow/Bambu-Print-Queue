import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle, Check, Loader2, Plug } from "lucide-react";
import { api, Unauthorized } from "@/lib/api";
import { useApp } from "@/store/app";
import { Button, Card, CardHeader, Field, Input, Switch } from "@/components/ui";
import { OPTION_LABEL } from "@/lib/format";
import type { PrinterConfig, PrintOptions, ProbeResult } from "@/lib/types";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2 text-sm">
      <span className="text-muted">{label}</span>
      <span className="truncate text-right">{value}</span>
    </div>
  );
}

/** 一行「标签 + 开关」，改完立刻存。 */
function ToggleRow({
  label, hint, checked, onChange, busy,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  busy?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm">{label}</div>
        {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
      </div>
      <Switch checked={checked} onChange={onChange} disabled={busy} />
    </div>
  );
}

/** 配置还没加载完时的占位骨架，避免开关先闪一下「关」再跳到真值。 */
function ToggleRowSkeleton({ rows }: { rows: number }) {
  return (
    <div className="divide-y divide-line">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex animate-pulse items-center justify-between gap-4 px-4 py-3">
          <div className="h-3.5 w-32 rounded bg-surface-2" />
          <div className="h-5 w-9 shrink-0 rounded-full bg-surface-2" />
        </div>
      ))}
    </div>
  );
}

/* -------------------------------------------------------- 打印机连接 */

const MODEL_OPTIONS = ["A1", "A1 mini", "P1S", "P1P", "X1C"];

function validateForm(f: { ip: string; serial: string; model: string }) {
  const errs: { ip?: string; serial?: string; model?: string } = {};
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(f.ip.trim())) {
    errs.ip = "IP 格式不对，应该形如 192.168.1.100";
  }
  if (!f.serial.trim()) errs.serial = "序列号不能为空";
  if (!f.model.trim()) errs.model = "请选择打印机型号";
  return errs;
}

function PrinterConnection() {
  const [cfg, setCfg] = useState<PrinterConfig | null>(null);
  const [form, setForm] = useState({ ip: "", serial: "", access_code: "", model: "" });
  const [fieldErr, setFieldErr] = useState<{ ip?: string; serial?: string; model?: string }>({});
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [busy, setBusy] = useState<"" | "test" | "save">("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.printerConfig().then(
      (d) => {
        setCfg(d);
        setForm({ ip: d.ip, serial: d.serial, access_code: "", model: d.model });
      },
      () => undefined,
    );
  }, []);

  const payload = () => ({
    ip: form.ip.trim(),
    serial: form.serial.trim(),
    model: form.model.trim(),
    // 留空表示「不改」——界面上它是打码显示的，
    // 为了改一个 IP 而要求人重新输一遍 access code 是没道理的
    ...(form.access_code ? { access_code: form.access_code.trim() } : {}),
  });

  const test = async () => {
    const errs = validateForm(form);
    setFieldErr(errs);
    if (Object.keys(errs).length) return;

    setBusy("test");
    setErr("");
    setSaved(false);
    try {
      setProbe(await api.testPrinter(payload()));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "试连失败");
    } finally {
      setBusy("");
    }
  };

  const save = async (force = false) => {
    const errs = validateForm(form);
    setFieldErr(errs);
    if (Object.keys(errs).length) return;

    setBusy("save");
    setErr("");
    try {
      const d = await api.savePrinter({ ...payload(), force });
      setCfg(d);
      setProbe(d.probe);
      setForm((f) => ({ ...f, access_code: "" }));
      setSaved(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy("");
    }
  };

  return (
    <Card id="printer-connection" className="scroll-mt-20">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Plug size={15} className="text-brand" />
            打印机连接
          </span>
        }
        sub="改完即时生效，不用重启服务"
      />

      <div className="grid gap-4 p-4 sm:grid-cols-2">
        <Field label="IP 地址" hint="打印机屏幕上「设置 → 网络」里能看到，例如 192.168.1.100">
          <Input
            value={form.ip}
            onChange={(e) => setForm({ ...form, ip: e.target.value })}
            placeholder="192.168.1.100"
          />
          {fieldErr.ip ? <div className="text-xs text-danger">{fieldErr.ip}</div> : null}
        </Field>

        <Field
          label="序列号 SERIAL"
          hint="打印机屏幕「设置 → 关于」里能看到，也可以从 FTPS 上传日志的文件名里读到"
        >
          <Input
            value={form.serial}
            onChange={(e) => setForm({ ...form, serial: e.target.value })}
            placeholder="AC12309BH109"
          />
          {fieldErr.serial ? <div className="text-xs text-danger">{fieldErr.serial}</div> : null}
        </Field>

        <Field
          label="Access Code"
          hint={
            cfg?.access_code_set
              ? `已设置（${cfg.access_code_masked}）。留空表示不修改，这里的语义和保存 IP/序列号时不填其它字段一样`
              : "打印机开启 LAN Only 模式后，屏幕上会显示这串 8 位数字，例如 12345678"
          }
        >
          <Input
            type="password"
            value={form.access_code}
            onChange={(e) => setForm({ ...form, access_code: e.target.value })}
            placeholder={cfg?.access_code_set ? "不改就留空" : "12345678"}
          />
        </Field>

        <Field label="型号" hint="决定发给打印机的参数格式，选错可能导致部分功能不生效">
          <select
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            className="h-9 w-full rounded-lg border border-line bg-surface-2 px-3 text-sm focus:border-brand/60 focus:outline-none"
          >
            <option value="" disabled>
              请选择型号
            </option>
            {(form.model && !MODEL_OPTIONS.includes(form.model)
              ? [form.model, ...MODEL_OPTIONS]
              : MODEL_OPTIONS
            ).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          {fieldErr.model ? <div className="text-xs text-danger">{fieldErr.model}</div> : null}
        </Field>
      </div>

      {probe ? (
        <div
          className={
            probe.ok
              ? "mx-4 mb-3 flex items-start gap-2 rounded-lg border border-brand/40 bg-brand/10 p-3 text-xs text-brand"
              : "mx-4 mb-3 flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 p-3 text-xs text-warn"
          }
        >
          {probe.ok ? (
            <Check size={14} className="mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          )}
          <div className="min-w-0">
            <div>{probe.detail}</div>
            {!probe.ok ? (
              <button
                type="button"
                onClick={() => void save(true)}
                className="mt-1.5 underline hover:no-underline"
              >
                打印机现在关着？仍然保存这组参数
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {err ? <div className="mx-4 mb-3 text-xs text-danger">{err}</div> : null}
      {saved && !err ? (
        <div className="mx-4 mb-3 text-xs text-brand">已保存并重新连接。</div>
      ) : null}

      <div className="flex items-center gap-2 border-t border-line p-3">
        <Button size="sm" onClick={test} disabled={!!busy}>
          {busy === "test" ? <Loader2 size={14} className="animate-spin" /> : null}
          测试连接
        </Button>
        <Button size="sm" variant="primary" onClick={() => void save()} disabled={!!busy}>
          {busy === "save" ? <Loader2 size={14} className="animate-spin" /> : null}
          保存
        </Button>
        <span className="ml-auto text-[11px] text-muted">
          保存前会先试连一次
        </span>
      </div>

      {/*
        为什么保存前一定要试连：填错 IP 却存进去，要等到下次启动、
        甚至等到半夜任务到点才发现连不上——那是这条链路上最难查的一类问题。
      */}
    </Card>
  );
}

/* ------------------------------------------------------------ 页面 */

type SchedulerPending = { start_after_failure?: boolean; upload_timing?: string };

export function SettingsPage() {
  const { config, link, setAuthed } = useApp();
  const location = useLocation();

  // 从 AppShell / Dashboard 的「连接打印机」按钮跳过来时，把连接卡滚到可见范围。
  // react-router 的 <Link> 不会自动处理 hash 滚动，得自己来。
  useEffect(() => {
    if (location.hash !== "#printer-connection") return;
    document.getElementById("printer-connection")?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [location.hash]);

  /* ---- 全局打印参数默认值：按开关粒度做乐观更新 + 独立 busy + 就近报错 ---- */
  const [defPending, setDefPending] = useState<Partial<Record<keyof PrintOptions, boolean>>>({});
  const [defBusy, setDefBusy] = useState<Partial<Record<keyof PrintOptions, boolean>>>({});
  const [defErr, setDefErr] = useState("");

  const patchOption = async (k: keyof PrintOptions, v: boolean) => {
    setDefBusy((b) => ({ ...b, [k]: true }));
    setDefErr("");
    setDefPending((p) => ({ ...p, [k]: v })); // 立刻反映到 UI，不等网络

    try {
      // saveConfig 的返回值本身就是权威的最新配置，直接写回 store，
      // 不用再发一次 GET——这也是原先「串行两个往返」的根源。
      const d = await api.saveConfig({ print_defaults: { [k]: v } });
      useApp.setState({ config: d });
      setDefPending((p) => {
        const next = { ...p };
        delete next[k];
        return next;
      });
    } catch (e) {
      // 失败：撤回乐观值，UI 弹回服务器上的真实状态
      setDefPending((p) => {
        const next = { ...p };
        delete next[k];
        return next;
      });
      if (e instanceof Unauthorized) {
        setAuthed(false);
        return;
      }
      setDefErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setDefBusy((b) => ({ ...b, [k]: false }));
    }
  };

  /* ---- 调度卡片：同样的模式，独立一份状态，错误不会跑到别的卡片里 ---- */
  const [schedPending, setSchedPending] = useState<SchedulerPending>({});
  const [schedBusy, setSchedBusy] = useState<{ start_after_failure?: boolean; upload_timing?: boolean }>({});
  const [schedErr, setSchedErr] = useState("");

  const patchScheduler = async (
    key: keyof SchedulerPending,
    optimistic: SchedulerPending,
    body: Parameters<typeof api.saveConfig>[0],
  ) => {
    setSchedBusy((b) => ({ ...b, [key]: true }));
    setSchedErr("");
    setSchedPending((p) => ({ ...p, ...optimistic }));

    try {
      const d = await api.saveConfig(body);
      useApp.setState({ config: d });
      setSchedPending((p) => {
        const next = { ...p };
        delete next[key];
        return next;
      });
    } catch (e) {
      setSchedPending((p) => {
        const next = { ...p };
        delete next[key];
        return next;
      });
      if (e instanceof Unauthorized) {
        setAuthed(false);
        return;
      }
      setSchedErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSchedBusy((b) => ({ ...b, [key]: false }));
    }
  };

  const uploadTiming = schedPending.upload_timing ?? config?.scheduler.upload_timing;

  return (
    <div className="mx-auto max-w-3xl space-y-4 pb-10">
      <h1 className="text-lg font-semibold">设置</h1>

      <PrinterConnection />

      <Card>
        <CardHeader
          title="全局打印参数默认值"
          sub="每个任务都能单独覆盖这些值"
        />
        {config === null ? (
          <ToggleRowSkeleton rows={5} />
        ) : (
          <div className="divide-y divide-line">
            {(Object.keys(OPTION_LABEL) as Array<keyof PrintOptions>).map((k) => (
              <ToggleRow
                key={k}
                label={OPTION_LABEL[k as keyof typeof OPTION_LABEL]}
                hint={k === "vibration_cali" ? "启动时最吵的一段" : undefined}
                checked={defPending[k] ?? !!config?.print_defaults?.[k]}
                busy={!!defBusy[k]}
                onChange={(v) => void patchOption(k, v)}
              />
            ))}
          </div>
        )}
        {defErr ? (
          <div className="mx-4 mt-3 text-xs text-danger">{defErr}</div>
        ) : null}
        <div className="border-t border-line px-4 py-2 text-[11px] leading-relaxed text-muted">
          改了这里，所有「跟随全局」的待触发任务都会跟着变；
          显式设过开或关的任务不受影响。
        </div>
      </Card>

      <Card>
        <CardHeader title="调度" />
        {config === null ? (
          <ToggleRowSkeleton rows={2} />
        ) : (
          <div className="divide-y divide-line">
            <ToggleRow
              label="上一单失败后仍照常触发"
              hint="FAILED 表示上一单的结局，机器其实是闲的，但板子上可能还有残骸"
              checked={schedPending.start_after_failure ?? !!config?.scheduler.start_after_failure}
              busy={!!schedBusy.start_after_failure}
              onChange={(v) =>
                void patchScheduler(
                  "start_after_failure",
                  { start_after_failure: v },
                  { scheduler: { start_after_failure: v } },
                )
              }
            />
            <ToggleRow
              label="提交时就把文件传上去"
              hint={
                uploadTiming === "early"
                  ? "当前：提交任务时就传（推荐，上传过程本身是静默的）。关闭后改为「到点才传」"
                  : "当前：到点触发时才传。开启后改为「提交时就传」（推荐）"
              }
              checked={uploadTiming === "early"}
              busy={!!schedBusy.upload_timing}
              onChange={(v) =>
                void patchScheduler(
                  "upload_timing",
                  { upload_timing: v ? "early" : "late" },
                  { scheduler: { upload_timing: v ? "early" : "late" } },
                )
              }
            />
          </div>
        )}
        {schedErr ? (
          <div className="mx-4 mt-3 text-xs text-danger">{schedErr}</div>
        ) : null}
        <div className="border-t border-line px-4 py-2 text-[11px] leading-relaxed text-muted">
          到点时打印机不空闲就放弃并记日志——不重试、不排队。这是定下来的行为。
        </div>
      </Card>

      <Card>
        <CardHeader title="与 Bambu Studio 共存" />
        <div className="space-y-3 p-4 text-sm">
          <p className="text-muted">
            打印机同一时刻只接受一个 MQTT 连接。服务常连着，Studio 就连不上。
            要用 Studio 时点顶栏那个「让给 Studio」，用完再抢回来。
          </p>
          <p className="text-muted">
            让出期间定时任务照常在册：到点会自动抢回连接再启动，日志里会记一条
            「抢回了连接」。
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void api.yieldLink()} disabled={link?.yielded}>
              让给 Studio
            </Button>
            <Button size="sm" onClick={() => void api.resumeLink()} disabled={!link?.yielded}>
              抢回连接
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="关于" />
        <div className="divide-y divide-line">
          <Row label="配置文件" value={
            <span className="font-mono text-[11px]">{config?.config_path ?? "加载中…"}</span>
          } />
          <Row
            label="错过触发的宽限"
            value={config ? `${config.scheduler.misfire_grace_time} 秒` : "—"}
          />
        </div>
        <div className="border-t border-line px-4 py-2 text-[11px] leading-relaxed text-muted">
          这里没列出的项要直接改配置文件，改完重启服务。
        </div>
      </Card>

      <Button
        variant="outline"
        onClick={async () => {
          await api.logout();
          setAuthed(false);
        }}
      >
        退出登录
      </Button>
    </div>
  );
}
