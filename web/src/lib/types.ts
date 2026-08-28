/** 与后端 to_dict() 一一对应。改这里之前先看 src/bpq/snapshot.py。 */

export interface Tray {
  global_id: number;
  unit_id: number;
  slot: number;
  is_external: boolean;
  label: string;
  tray_type: string;
  tray_sub_brands: string;
  color: string;
  rgb: string;
  info_idx: string;
  remain: number;
  k: number;
  empty: boolean;
  nozzle_temp_min: number | null;
  nozzle_temp_max: number | null;
}

export interface AmsUnit {
  unit_id: number;
  humidity: number | null;
  temp: number | null;
  trays: Tray[];
}

export interface Hms {
  attr: number;
  code: number;
  key: string;
  severity: string;
  url: string;
}

export interface Printer {
  connected: boolean;
  stale: boolean;
  updated_at: string | null;
  job: {
    gcode_state: string;
    print_type: string;
    subtask_name: string;
    gcode_file: string;
    percent: number | null;
    remaining_min: number | null;
    layer_num: number | null;
    total_layers: number | null;
    stage_code: number | null;
    stage: string;
    print_error: number | null;
    prepare_percent: number | null;
    hms: Hms[];
  };
  temps: {
    nozzle: number | null;
    nozzle_target: number | null;
    bed: number | null;
    bed_target: number | null;
    chamber: number | null;
  };
  ams: {
    units: AmsUnit[];
    external: Tray | null;
    tray_now: number | null;
    tray_tar: number | null;
    exist_bits: string;
  };
  fans: Record<string, number | null>;
  lights: Record<string, string>;
  speed: { level: number | null; name: string; mag: number | null };
  nozzle: { diameter: string; type: string };
  wifi_signal: string;
  sdcard: boolean | null;
  home_flag: number | null;
  ipcam: { record: boolean | null; timelapse: boolean | null; resolution: string };
  xcam: Record<string, unknown>;
  versions: Record<string, string>;
  raw_keys_seen: string[];
}

export interface LinkHealth {
  connected: boolean;
  yielded: boolean;
  stale: boolean;
  last_report_at: string | null;
  opened_at: string | null;
  reconnects: number;
  last_error: string;
}

export interface Filament {
  id: number;
  type: string;
  color: string;
  rgb: string;
  info_idx: string;
  used_g: number;
}

/** 五个开关。null = 跟随全局默认，和 false 是两回事，界面上必须区分。 */
export interface PrintOptions {
  bed_leveling: boolean | null;
  vibration_cali: boolean | null;
  flow_cali: boolean | null;
  layer_inspect: boolean | null;
  timelapse: boolean | null;
}

export type TaskState =
  | "pending" | "uploaded" | "started" | "cancelled" | "aborted" | "failed";

export interface Task {
  id: string;
  title: string;
  source_path: string;
  remote_name: string;
  plate: string;
  plate_index: number;
  md5: string;
  bed_type: string;
  use_ams: boolean;
  ams_mapping: number[];
  mapping_source: "auto" | "manual";
  mapping_notes: string[];
  filaments: Filament[];
  options: PrintOptions;
  state: TaskState;
  origin: string;
  scheduled_at: string;
  created_at: string;
  triggered_at: string | null;
  uploaded_at: string | null;
  error: string | null;
  sent_payload: string | null;
}

export interface Plate {
  index: number;
  gcode_path: string;
  md5: string;
  bed_type: string;
  prediction_sec: number;
  weight_g: number;
  needs_ams: boolean;
  filaments: Filament[];
}

export interface FileInfo {
  file_id: string;
  name: string;
  size: number;
  slimmed_from: number;
  upload_seconds: number;
  plates: Plate[];
}

export interface MappingPreview {
  mapping: number[];
  notes: string[];
  filaments: Filament[];
}

/** 打印机连接参数。access_code 只回打码值，不回明文。 */
export interface PrinterConfig {
  ip: string;
  serial: string;
  model: string;
  access_code_set: boolean;
  access_code_masked: string;
}

export interface PrinterConfigInput {
  ip?: string;
  serial?: string;
  access_code?: string;
  model?: string;
}

export interface ProbeResult {
  ok: boolean;
  detail: string;
  state?: string;
}

/** 「自动获取序列号」的结果。失败时只给 detail，不像 ProbeResult 那样带 state。 */
export type DiscoverSerialResult =
  | { ok: true; serial: string }
  | { ok: false; detail: string };

export interface AppConfig {
  printer: { model: string; ip: string; serial_masked: string };
  print_defaults: Record<keyof PrintOptions, boolean>;
  scheduler: {
    upload_timing: string;
    start_after_failure: boolean;
    misfire_grace_time: number;
  };
  config_path: string;
}

export interface JournalRecord {
  ts: string;
  event: string;
  [k: string]: unknown;
}

/** 日志查询条件。全部可选——什么都不给就是「最近一页」。 */
export interface JournalQuery {
  /** 事件名白名单。空数组和不传是一个意思：不按类型筛。 */
  events?: string[];
  /** YYYY-MM-DD，含当天。 */
  since?: string;
  /** YYYY-MM-DD，含当天（后端会补到 23:59:59）。 */
  until?: string;
  offset?: number;
  limit?: number;
}

/**
 * 一页日志。
 *
 * 后端返回的 `items` 已经是**倒序**（最新在前），前端不要再 reverse 一次。
 * `events` 是日志文件里实际出现过的全部事件名，用来铺筛选下拉——
 * 硬编码一张事件名表迟早会和 src/bpq/journal.py 漂移。
 */
export interface JournalPage {
  items: JournalRecord[];
  total: number;
  offset: number;
  limit: number;
  events: string[];
}
