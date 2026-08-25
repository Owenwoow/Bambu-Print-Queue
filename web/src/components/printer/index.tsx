import { Thermometer, Droplets, ExternalLink, AlertTriangle } from "lucide-react";
import { Badge, Card, CardHeader, ColorDot, Empty } from "@/components/ui";
import { cn } from "@/lib/cn";
import { PRINTER_STATE_LABEL, humidityText, minutes, temp } from "@/lib/format";
import type { AmsUnit, Printer, Tray } from "@/lib/types";

export function StateBadge({ state }: { state: string }) {
  const tone =
    state === "RUNNING"
      ? "border-brand/40 bg-brand/10 text-brand"
      : state === "FAILED"
        ? "border-danger/40 bg-danger/10 text-danger"
        : state === "PAUSE"
          ? "border-warn/40 bg-warn/10 text-warn"
          : "border-line text-muted";
  return <Badge className={tone}>{PRINTER_STATE_LABEL[state] ?? state}</Badge>;
}

function TempBar({
  label, value, target,
}: { label: string; value: number | null; target: number | null }) {
  const pct = target && target > 0 && value ? Math.min(100, (value / target) * 100) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className="tabular text-sm">
          {temp(value)}
          {target ? <span className="text-muted"> / {temp(target)}</span> : null}
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-2">
        <div
          className="h-full rounded-full bg-brand transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function PrinterCard({ printer }: { printer: Printer }) {
  const { job, temps } = printer;
  const running = job.gcode_state === "RUNNING";

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            打印机
            <StateBadge state={job.gcode_state} />
            {printer.stale ? (
              <Badge className="border-warn/40 bg-warn/10 text-warn">读数可能过时</Badge>
            ) : null}
          </span>
        }
        sub={job.subtask_name || (running ? job.stage : "没有正在进行的任务")}
      />

      <div className="space-y-4 p-4">
        {running ? (
          <div>
            <div className="flex items-baseline justify-between">
              <span className="tabular text-2xl font-semibold">{job.percent ?? 0}%</span>
              <span className="text-xs text-muted">
                第 {job.layer_num ?? 0} / {job.total_layers ?? "?"} 层 ·{" "}
                剩余 {minutes(job.remaining_min)}
              </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full bg-brand transition-all duration-700"
                style={{ width: `${job.percent ?? 0}%` }}
              />
            </div>
            {job.stage ? (
              <div className="mt-1.5 text-xs text-muted">当前阶段：{job.stage}</div>
            ) : null}
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-4">
          <TempBar label="喷嘴" value={temps.nozzle} target={temps.nozzle_target} />
          <TempBar label="热床" value={temps.bed} target={temps.bed_target} />
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-muted">
          {printer.nozzle.diameter ? <span>喷嘴 {printer.nozzle.diameter}mm</span> : null}
          {printer.speed.name ? <span>速度 {printer.speed.name}</span> : null}
          {printer.fans.cooling !== null ? <span>冷却风扇 {printer.fans.cooling}%</span> : null}
          {printer.wifi_signal ? <span>WiFi {printer.wifi_signal}</span> : null}
        </div>

        {job.hms.length ? <HmsList hms={job.hms} /> : null}
      </div>
    </Card>
  );
}

export function HmsList({ hms }: { hms: Printer["job"]["hms"] }) {
  return (
    <div className="space-y-1.5 rounded-lg border border-danger/30 bg-danger/10 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-danger">
        <AlertTriangle size={13} /> 打印机报了 {hms.length} 条告警
      </div>
      {hms.map((h) => (
        <a
          key={h.key}
          href={h.url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 font-mono text-xs text-danger hover:underline"
        >
          {h.key}
          <span className="font-sans">（{h.severity}）</span>
          <ExternalLink size={11} />
        </a>
      ))}
      {/* 社区的 HMS 码表覆盖不全，与其摆一条可能是错的中文，不如给官网链接 */}
      <div className="text-[11px] text-danger/70">点开查官方说明</div>
    </div>
  );
}

export function TrayChip({
  tray, active, target,
}: { tray: Tray; active?: boolean; target?: boolean }) {
  const isEmpty = tray.empty || !tray.tray_type;
  // 型号优先显示「类型 + 子品牌」（如 "PLA · PLA Basic"），子品牌缺失时退回 info_idx
  const model = tray.tray_sub_brands || tray.info_idx || "";
  const typeAndModel = isEmpty
    ? "空槽"
    : [tray.tray_type, model].filter(Boolean).join(" · ") || tray.tray_type;

  return (
    <div
      className={cn(
        "rounded-lg border p-2.5 transition-colors",
        active
          ? "border-brand/60 bg-brand/10"
          : target
            ? "border-warn/50 bg-warn/10"
            : "border-line bg-surface-2",
      )}
    >
      <div className="flex items-center gap-2">
        <div className="flex shrink-0 flex-col items-center gap-0.5">
          <ColorDot rgb={tray.rgb} size={18} />
          {!isEmpty ? (
            <span className="text-[9px] tabular text-muted">
              #{(tray.rgb || "666666").toUpperCase()}
            </span>
          ) : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium">{tray.label}</div>
          <div className="truncate text-[11px] text-muted">{typeAndModel}</div>
        </div>
      </div>
      {/* k 值可能是 0（没校准过），那时整行都不占位，免得每个槽位底下都空一截 */}
      {tray.k ? (
        <div className="mt-2 flex items-center justify-end text-[11px] text-muted">
          <span className="tabular">k={tray.k.toFixed(3)}</span>
        </div>
      ) : null}
    </div>
  );
}

export function AmsPanel({ printer }: { printer: Printer }) {
  const { ams } = printer;
  if (!ams.units.length && !ams.external) {
    return (
      <Card>
        <CardHeader title="AMS 与耗材" />
        <Empty>没有读到 AMS 信息。</Empty>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {ams.units.map((unit: AmsUnit) => (
        <Card key={unit.unit_id}>
          <CardHeader
            title={`AMS ${String.fromCharCode(65 + unit.unit_id)}`}
            sub={
              <span className="flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <Droplets size={12} /> {humidityText(unit.humidity)}
                </span>
                {unit.temp ? (
                  <span className="flex items-center gap-1">
                    <Thermometer size={12} /> {temp(unit.temp)}
                  </span>
                ) : null}
              </span>
            }
          />
          <div className="grid grid-cols-2 gap-2.5 p-3 sm:grid-cols-4">
            {unit.trays.map((t) => (
              <TrayChip
                key={t.global_id}
                tray={t}
                active={ams.tray_now === t.global_id}
                target={ams.tray_tar === t.global_id}
              />
            ))}
          </div>
          {/*
            AMS lite 没有 RFID：型号和颜色都是人在 Studio 里手填的，不是机器读出来的。
            这条必须写在界面上——自动配料的一切判断都建立在这些值之上。
          */}
          <div className="border-t border-line px-3 py-2 text-[11px] text-muted">
            AMS lite 没有 RFID，上面的耗材型号与颜色都是在 Studio 里手填的，不是机器读出来的。
          </div>
        </Card>
      ))}

      {ams.external ? (
        <Card>
          <CardHeader title="外置料" sub="挂在机器外面的那一卷" />
          <div className="grid grid-cols-2 gap-2.5 p-3 sm:grid-cols-4">
            <TrayChip tray={ams.external} active={ams.tray_now === ams.external.global_id} />
          </div>
        </Card>
      ) : null}
    </div>
  );
}
