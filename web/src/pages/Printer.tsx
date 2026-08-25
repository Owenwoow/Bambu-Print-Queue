import { useApp } from "@/store/app";
import { Card, CardHeader, Empty } from "@/components/ui";
import { PrinterCard } from "@/components/printer";
import { clock } from "@/lib/format";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2 text-sm">
      <span className="text-muted">{label}</span>
      <span className="truncate text-right">{value}</span>
    </div>
  );
}

export function PrinterPage() {
  const { printer, link } = useApp();

  if (!printer) {
    return (
      <Card>
        <Empty>还没读到打印机状态。</Empty>
      </Card>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="text-lg font-semibold">打印机状态</h1>

      <PrinterCard printer={printer} />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader title="连接" sub="这是 daemon 与打印机之间那条线的状况" />
          <div className="divide-y divide-line">
            <Row label="已连接" value={link?.connected ? "是" : "否"} />
            <Row label="让给 Studio" value={link?.yielded ? "是" : "否"} />
            <Row label="最后一条报文" value={clock(link?.last_report_at ?? null)} />
            <Row label="连接建立于" value={clock(link?.opened_at ?? null)} />
            <Row label="重连次数" value={link?.reconnects ?? 0} />
            {link?.last_error ? <Row label="最后错误" value={link.last_error} /> : null}
          </div>
        </Card>

        <Card>
          <CardHeader title="设备" />
          <div className="divide-y divide-line">
            <Row label="喷嘴" value={`${printer.nozzle.diameter || "?"} mm ${printer.nozzle.type}`} />
            <Row label="速度档" value={printer.speed.name || "—"} />
            <Row label="WiFi" value={printer.wifi_signal || "—"} />
            <Row label="SD 卡" value={printer.sdcard === null ? "—" : printer.sdcard ? "在位" : "未插"} />
            <Row
              label="腔灯"
              value={printer.lights.chamber_light === "on" ? "开" : "关"}
            />
            <Row
              label="延时摄影（设备设置）"
              value={
                printer.ipcam.timelapse === null
                  ? "—"
                  : printer.ipcam.timelapse
                    ? "开"
                    : "关"
              }
            />
          </div>
          {/*
            这里显示的是打印机自己的持久设置，不等于「某一单任务下发了什么」。
            那五个开关里有三个根本没有对应的上报字段，所以两者在界面上分开显示，
            也不做对照——否则会显示出一种编造的确定性。
          */}
          <div className="border-t border-line px-4 py-2 text-[11px] leading-relaxed text-muted">
            这是打印机自己的设置。每个任务下发了哪些参数，在任务详情页里看。
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="风扇" />
        <div className="divide-y divide-line">
          {Object.entries(printer.fans).map(([k, v]) => (
            <Row key={k} label={k} value={v === null ? "—" : `${v}%`} />
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader title="固件版本" sub="在验证通过的版本上锁定，升级前先查 changelog" />
        <div className="divide-y divide-line">
          {Object.entries(printer.versions).map(([k, v]) => (
            <Row key={k} label={k} value={<span className="font-mono text-xs">{v}</span>} />
          ))}
        </div>
      </Card>

      {printer.raw_keys_seen.length ? (
        <Card>
          <CardHeader
            title="尚未建模的上报字段"
            sub="打印机报了这些，但界面还没用上——列在这里是为了不静默丢弃"
          />
          <div className="p-4 font-mono text-xs text-muted">
            {printer.raw_keys_seen.join("  ")}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
