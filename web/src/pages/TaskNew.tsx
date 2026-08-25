import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, Check, Clock, Upload, Wand2 } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store/app";
import { Button, Card, CardHeader, ColorDot, Field, Input, TriSwitch } from "@/components/ui";
import { cn } from "@/lib/cn";
import { OPTION_LABEL, bytes, duration } from "@/lib/format";
import type { FileInfo, Filament, Plate, PrintOptions, Tray } from "@/lib/types";

const EXTERNAL_ID = 254;

/** 默认给「今晚 23:30」——这个应用绝大多数任务就是睡前那一单。 */
function defaultWhen(): string {
  const d = new Date();
  d.setHours(23, 30, 0, 0);
  if (d.getTime() < Date.now()) d.setDate(d.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const EMPTY_OPTIONS: PrintOptions = {
  bed_leveling: null,
  vibration_cali: null,
  flow_cali: null,
  layer_inspect: null,
  timelapse: null,
};

/* ------------------------------------------------------------------ 上传区 */

function Dropzone({ onPick, busy }: { onPick: (f: File) => void; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onPick(f);
      }}
      onClick={() => input.current?.click()}
      className={cn(
        "cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-colors",
        over ? "border-brand bg-brand/5" : "border-line hover:border-brand/50",
        busy && "pointer-events-none opacity-60",
      )}
    >
      <input
        ref={input}
        type="file"
        accept=".3mf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onPick(f);
          e.target.value = "";
        }}
      />
      <Upload size={26} className="mx-auto mb-3 text-muted" />
      <div className="text-sm font-medium">
        {busy ? "正在处理…" : "把切好片的 3mf 拖进来，或点击选择"}
      </div>
      <div className="mt-1.5 text-xs text-muted">
        文件里带的装配说明和模型图会自动剥掉——它们能占几十 MB，
        而打印机的传输速度只有约 46 KB/s。
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- 槽位选择器 */

function TrayPicker({
  trays, value, onChange,
}: { trays: Tray[]; value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {trays.map((t) => (
        <button
          key={t.global_id}
          type="button"
          onClick={() => onChange(t.global_id)}
          className={cn(
            "flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs transition-colors",
            value === t.global_id
              ? "border-brand bg-brand/12 text-brand"
              : "border-line bg-surface-2 hover:border-brand/40",
          )}
          title={`${t.tray_sub_brands || t.info_idx}｜剩余 ${t.remain}%`}
        >
          <ColorDot rgb={t.rgb} size={12} />
          {t.label}
        </button>
      ))}
      <button
        type="button"
        onClick={() => onChange(EXTERNAL_ID)}
        className={cn(
          "rounded-lg border px-2 py-1.5 text-xs transition-colors",
          value === EXTERNAL_ID
            ? "border-brand bg-brand/12 text-brand"
            : "border-line bg-surface-2 hover:border-brand/40",
        )}
      >
        外置料
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------- 页面 */

export function TaskNew() {
  const nav = useNavigate();
  const { printer, config, refreshTasks } = useApp();

  const [file, setFile] = useState<FileInfo | null>(null);
  const [plateIndex, setPlateIndex] = useState<number | null>(null);
  const [when, setWhen] = useState(defaultWhen);
  const [title, setTitle] = useState("");
  const [options, setOptions] = useState<PrintOptions>(EMPTY_OPTIONS);
  const [mapping, setMapping] = useState<number[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [manual, setManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const plate: Plate | null = useMemo(
    () => file?.plates.find((p) => p.index === plateIndex) ?? file?.plates[0] ?? null,
    [file, plateIndex],
  );

  const trays: Tray[] = useMemo(
    () => printer?.ams.units.flatMap((u) => u.trays).filter((t) => !t.empty) ?? [],
    [printer],
  );

  const loadMapping = useCallback(
    async (fileId: string, idx: number) => {
      try {
        const m = await api.mapping(fileId, idx);
        setMapping(m.mapping);
        setNotes(m.notes);
        setManual(false);
      } catch {
        setMapping([]);
        setNotes([]);
      }
    },
    [],
  );

  useEffect(() => {
    if (file && plate?.needs_ams) void loadMapping(file.file_id, plate.index);
  }, [file, plate, loadMapping]);

  const pick = async (f: File) => {
    setBusy(true);
    setErr("");
    try {
      const info = await api.upload(f);
      setFile(info);
      setPlateIndex(info.plates[0]?.index ?? null);
      setTitle(info.name.replace(/\.gcode\.3mf$|\.3mf$/i, ""));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!file || !plate) return;
    setBusy(true);
    setErr("");
    try {
      const { task } = await api.createTask({
        file_id: file.file_id,
        scheduled_at: new Date(when).toISOString(),
        plate_index: plate.index,
        title,
        options,
        ...(plate.needs_ams ? { use_ams: true, ams_mapping: mapping } : {}),
      });
      await refreshTasks();
      nav(`/tasks/${task.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "提交失败");
      setBusy(false);
    }
  };

  if (!file) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <h1 className="text-lg font-semibold">新建任务</h1>
        <Dropzone onPick={pick} busy={busy} />
        {err ? <div className="text-sm text-danger">{err}</div> : null}
      </div>
    );
  }

  const defaults = config?.print_defaults;

  return (
    <div className="mx-auto max-w-3xl space-y-4 pb-10">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">新建任务</h1>
        <Button size="sm" variant="ghost" onClick={() => setFile(null)}>
          换一个文件
        </Button>
      </div>

      {/* ---------------------------------------------------------- 文件 */}
      <Card>
        <div className="flex gap-4 p-4">
          <img
            src={api.thumbnailUrl(file.file_id, plate?.index ?? 1)}
            alt=""
            className="h-24 w-24 shrink-0 rounded-lg border border-line bg-surface-2 object-cover"
            onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")}
          />
          <div className="min-w-0 flex-1">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full truncate border-none bg-transparent p-0 text-sm font-medium focus:outline-none"
            />
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              {plate?.prediction_sec ? <span>耗时 {duration(plate.prediction_sec)}</span> : null}
              {plate?.weight_g ? <span>耗材 {plate.weight_g} g</span> : null}
              <span>{bytes(file.size)}</span>
              <span>上传约需 {file.upload_seconds}s</span>
            </div>
            {file.slimmed_from ? (
              <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-brand/10 px-2 py-1 text-[11px] text-brand">
                <Check size={12} />
                已剥掉装配说明等附件：{bytes(file.slimmed_from)} → {bytes(file.size)}，
                上传从约 {Math.round(file.slimmed_from / 46000 / 60)} 分钟缩短到 {file.upload_seconds} 秒
              </div>
            ) : null}
          </div>
        </div>

        {file.plates.length > 1 ? (
          <div className="border-t border-line p-4">
            <Field label="用哪个盘">
              <div className="flex flex-wrap gap-1.5">
                {file.plates.map((p) => (
                  <button
                    key={p.index}
                    type="button"
                    onClick={() => setPlateIndex(p.index)}
                    className={cn(
                      "rounded-lg border px-3 py-1.5 text-xs",
                      p.index === plate?.index
                        ? "border-brand bg-brand/12 text-brand"
                        : "border-line bg-surface-2",
                    )}
                  >
                    盘 {p.index}
                  </button>
                ))}
              </div>
            </Field>
          </div>
        ) : null}
      </Card>

      {/* ------------------------------------------------------ 打印机与板材 */}
      <Card>
        <CardHeader title="打印机" />
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm">
          <span className="font-medium">
            {config?.printer.model ?? "A1"}
            <span className="ml-2 text-xs text-muted">{config?.printer.ip}</span>
          </span>
          <span className="text-muted">
            喷嘴 {printer?.nozzle.diameter || "0.4"} mm
          </span>
          <span className="text-muted">
            板材 {plate?.bed_type ?? "—"}
            <span className="ml-1 text-xs">（取自 3mf，不是猜的）</span>
          </span>
        </div>
      </Card>

      {/* ---------------------------------------------------------- 耗材丝 */}
      {plate?.needs_ams ? (
        <Card>
          <CardHeader
            title="耗材丝"
            sub={
              manual
                ? "已人工指定"
                : "按耗材型号优先、颜色最接近自动匹配——这只是建议，可以改"
            }
            right={
              manual && file ? (
                <Button size="sm" variant="ghost" onClick={() => loadMapping(file.file_id, plate.index)}>
                  <Wand2 size={13} /> 重新自动匹配
                </Button>
              ) : null
            }
          />
          <div className="divide-y divide-line">
            {plate.filaments.map((f: Filament, i: number) => {
              const chosen = mapping[i] ?? EXTERNAL_ID;
              const tray = trays.find((t) => t.global_id === chosen);
              const mismatch =
                tray && f.info_idx && tray.info_idx && tray.info_idx !== f.info_idx;
              return (
                <div key={f.id} className="space-y-2 p-4">
                  <div className="flex items-center gap-2.5">
                    <ColorDot rgb={f.rgb} size={20} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm">
                        耗材 {f.id}
                        <span className="ml-2 text-muted">
                          {f.type} · {f.info_idx || "型号未知"} · {f.used_g} g
                        </span>
                      </div>
                    </div>
                  </div>
                  <TrayPicker
                    trays={trays}
                    value={chosen}
                    onChange={(v) => {
                      const next = [...mapping];
                      next[i] = v;
                      setMapping(next);
                      setManual(true);
                    }}
                  />
                  {mismatch ? (
                    <div className="flex items-start gap-1.5 text-xs text-warn">
                      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                      切片用的是 {f.info_idx}，选中的槽位里是 {tray?.info_idx}。
                      确认一下是不是这一卷。
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>

          {notes.length && !manual ? (
            <div className="space-y-1 border-t border-line bg-surface-2/50 p-3">
              {notes.map((n, i) => (
                <div
                  key={i}
                  className={cn("text-xs", n.startsWith("⚠") ? "text-warn" : "text-muted")}
                >
                  {n}
                </div>
              ))}
            </div>
          ) : null}

          {!trays.length ? (
            <div className="border-t border-line p-3 text-xs text-warn">
              现在读不到 AMS 的状态（打印机没连上？），上面的选择只是占位。
            </div>
          ) : null}
        </Card>
      ) : null}

      {/* ------------------------------------------------------------ 参数 */}
      <Card>
        <CardHeader
          title="打印参数"
          sub="这一单要下发给打印机的值。全局默认在 config.toml 的 [print] 段"
        />
        <div className="divide-y divide-line">
          {(Object.keys(OPTION_LABEL) as Array<keyof typeof OPTION_LABEL>).map((k) => (
            <div key={k} className="flex flex-wrap items-center justify-between gap-3 p-3">
              <div className="text-sm">
                {OPTION_LABEL[k]}
                {k === "vibration_cali" ? (
                  <span className="ml-2 text-xs text-muted">启动时最吵的一段</span>
                ) : null}
              </div>
              <TriSwitch
                value={options[k]}
                fallback={defaults?.[k] ?? false}
                onChange={(v) => setOptions({ ...options, [k]: v })}
              />
            </div>
          ))}
        </div>
        <div className="border-t border-line p-3 text-[11px] leading-relaxed text-muted">
          减噪提示：关掉调平与振动补偿能砍掉启动时最吵的那两段，
          但归零（homing）和挤出线是免不掉的——「触发前完全静默」是 100% 的，
          「触发后立刻安静」只是显著改善。
        </div>
      </Card>

      {/* ------------------------------------------------------ 触发时刻 */}
      <Card className="border-brand/40">
        <CardHeader
          title={
            <span className="flex items-center gap-1.5">
              <Clock size={15} className="text-brand" />
              触发时刻
            </span>
          }
          sub="到点自动开始打印"
        />
        <div className="space-y-3 p-4">
          <Input
            type="datetime-local"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
            className="max-w-[16rem]"
          />
          <div className="flex flex-wrap gap-1.5">
            {[
              ["今晚 23:30", 23, 30],
              ["今晚 00:30", 24, 30],
              ["明早 07:00", 31, 0],
            ].map(([label, h, m]) => (
              <Button
                key={label as string}
                size="sm"
                variant="outline"
                onClick={() => {
                  const d = new Date();
                  d.setHours(h as number, m as number, 0, 0);
                  if (d.getTime() < Date.now()) d.setDate(d.getDate() + 1);
                  const pad = (n: number) => String(n).padStart(2, "0");
                  setWhen(
                    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`,
                  );
                }}
              >
                {label as string}
              </Button>
            ))}
          </div>
        </div>
      </Card>

      {err ? (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          {err}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-muted">
          提交后文件会立刻静默传到打印机存储，但在触发时刻之前不会有任何动作。
        </p>
        <Button variant="primary" onClick={submit} disabled={busy} className="shrink-0">
          {busy ? "提交中…" : "提交任务"}
        </Button>
      </div>
    </div>
  );
}
