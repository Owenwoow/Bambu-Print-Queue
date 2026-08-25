import type { TaskState } from "./types";

export function duration(seconds: number): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h) return `${h}h${String(m).padStart(2, "0")}m`;
  return `${m}m${String(s).padStart(2, "0")}s`;
}

export function minutes(min: number | null): string {
  if (min === null || min <= 0) return "—";
  const h = Math.floor(min / 60);
  return h ? `${h}小时${min % 60}分` : `${min} 分钟`;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(2)} MB`;
}

export function temp(v: number | null): string {
  return v === null ? "—" : `${v.toFixed(1)}°`;
}

export function clock(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function timeOnly(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 距离触发还有多久。这是任务列表里最该一眼看到的信息。 */
export function untilText(iso: string): string {
  const diff = new Date(iso).getTime() - Date.now();
  if (diff <= 0) return "已到时刻";
  const mins = Math.round(diff / 60000);
  if (mins < 60) return `${mins} 分钟后`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时 ${mins % 60} 分后`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时后`;
}

/**
 * 任务状态的中文名。
 *
 * 特别说明 `aborted`：它表示「到点检查时打印机不空闲，按设计放弃本次触发，
 * 不重试、不排队」，发生在触发之前，不是打印过程中途被打断。选词要避免让人
 * 误以为是打印中断——它其实是「压根没打」。
 */
export const STATE_LABEL: Record<TaskState, string> = {
  pending: "待上传",
  uploaded: "已就位",
  started: "已启动",
  cancelled: "已取消",
  aborted: "已中止",
  failed: "执行失败",
};

export const STATE_TONE: Record<TaskState, string> = {
  pending: "text-muted border-line",
  uploaded: "text-brand border-brand/40 bg-brand/10",
  started: "text-brand border-brand/40 bg-brand/10",
  cancelled: "text-muted border-line",
  aborted: "text-warn border-warn/40 bg-warn/10",
  failed: "text-danger border-danger/40 bg-danger/10",
};

/** 打印机上报状态码（生英文）→ 规范设备状态术语。 */
export const PRINTER_STATE_LABEL: Record<string, string> = {
  IDLE: "空闲",
  RUNNING: "打印中",
  PAUSE: "已暂停",
  FINISH: "打印完成",
  FAILED: "打印失败",
  UNKNOWN: "状态未知",
};

/** 只要中文名：映射表里没有的生码原样返回。 */
export function printerStateLabel(state: string | null | undefined): string {
  if (!state) return "状态未知";
  return PRINTER_STATE_LABEL[state] ?? state;
}

/**
 * 「中文（英文原码）」的形式，例如 `打印完成（FINISH）`。
 * 排障时经常要对照打印机上报的原始值，只显示中文会丢这层信息。
 */
export function printerStateText(state: string | null | undefined): string {
  if (!state) return "状态未知";
  const label = PRINTER_STATE_LABEL[state];
  return label ? `${label}（${state}）` : state;
}

/** 五个开关在界面上的中文名，和 Studio 的发送对话框对齐。 */
export const OPTION_LABEL = {
  timelapse: "延时摄影",
  bed_leveling: "自动热床调平",
  flow_cali: "动态流量校准",
  vibration_cali: "振动补偿",
  layer_inspect: "层间检查",
} as const;

/** AMS 湿度是 1–5 档，**数值越小越干**——反过来理解会得出完全相反的结论。 */
export function humidityText(level: number | null): string {
  if (level === null) return "—";
  const names = ["", "很干燥", "干燥", "一般", "偏潮", "潮湿"];
  return `${names[level] ?? "?"}（${level} 档）`;
}
