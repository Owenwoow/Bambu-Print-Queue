import type {
  AppConfig,
  DiscoverSerialResult,
  PrinterConfig,
  PrinterConfigInput,
  ProbeResult,
  FileInfo,
  JournalPage,
  JournalQuery,
  JournalRecord,
  LinkHealth,
  MappingPreview,
  Printer,
  PrintOptions,
  Task,
} from "./types";

/** 未登录。上层捕获它跳登录页——后端刻意返回 401 而不是 422 就是为了这个。 */
export class Unauthorized extends Error {}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (res.status === 401) throw new Unauthorized("请先登录");
  if (!res.ok) {
    // 后端的 detail 是写给人看的中文，直接透出去比「请求失败(400)」有用得多
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* 响应体不是 JSON，用状态码兜底 */
    }
    throw new ApiError(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),

  me: () => request<{ authed: boolean; password_required: boolean }>("/api/auth/me"),
  login: (password: string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  printer: () => request<Printer & { link: LinkHealth }>("/api/printer"),
  refresh: () => request<{ ok: boolean }>("/api/printer/refresh", { method: "POST" }),
  yieldLink: () =>
    request<{ link: LinkHealth }>("/api/printer/yield", { method: "POST" }),
  resumeLink: () =>
    request<{ link: LinkHealth }>("/api/printer/resume", { method: "POST" }),

  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<FileInfo>("/api/files", { method: "POST", body: fd });
  },
  mapping: (fileId: string, plate: number) =>
    request<MappingPreview>(`/api/files/${fileId}/mapping?plate=${plate}`),
  thumbnailUrl: (fileId: string, plate: number) =>
    `/api/files/${fileId}/thumbnail?plate=${plate}`,

  tasks: () => request<Task[]>("/api/tasks"),
  task: (id: string) => request<Task>(`/api/tasks/${id}`),
  createTask: (body: {
    file_id: string;
    scheduled_at: string;
    plate_index?: number;
    use_ams?: boolean;
    ams_mapping?: number[];
    options?: Partial<PrintOptions>;
    title?: string;
  }) =>
    request<{ task: Task; notes: string[] }>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchTask: (
    id: string,
    body: { scheduled_at?: string; options?: Partial<PrintOptions>; ams_mapping?: number[] },
  ) =>
    request<Task>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** 触发前反悔：软取消，任务变成 cancelled，记录还在。 */
  cancelTask: (id: string) =>
    request<{ ok: boolean }>(`/api/tasks/${id}`, { method: "DELETE" }),
  /**
   * 删记录：真的把这一行从库里抹掉，不可恢复。
   *
   * 只对**已结束**的任务有效。还没结束的任务后端会返回 409——
   * 那种情况必须先 cancelTask 再删，否则 jobstore 里会留下一个孤儿 job，
   * 到点还会去触发一个已经不存在的任务。
   */
  deleteTask: (id: string) =>
    request<{ ok: boolean }>(`/api/tasks/${id}?purge=true`, { method: "DELETE" }),

  /**
   * 「最近 N 条」的简易读法，给概览这类不需要筛选的地方用。
   *
   * 后端返回的是倒序，这里翻回正序，保持老调用方的行为不变。
   */
  journal: async (limit = 200): Promise<JournalRecord[]> => {
    const page = await request<JournalPage>(`/api/journal?limit=${limit}`);
    return [...page.items].reverse();
  },
  /** 带筛选和分页的读法，日志页用它。返回的 items 是倒序（最新在前）。 */
  journalPage: (q: JournalQuery = {}) => {
    const p = new URLSearchParams();
    if (q.events?.length) p.set("event", q.events.join(","));
    if (q.since) p.set("since", q.since);
    if (q.until) p.set("until", q.until);
    if (q.offset) p.set("offset", String(q.offset));
    if (q.limit) p.set("limit", String(q.limit));
    return request<JournalPage>(`/api/journal?${p.toString()}`);
  },
  /** 清日志。给了 before（YYYY-MM-DD）就只删那天之前的，不给就全清。 */
  clearJournal: (before?: string) =>
    request<{ ok: boolean; deleted: number }>(
      `/api/journal${before ? `?before=${before}` : ""}`,
      { method: "DELETE" },
    ),
  config: () => request<AppConfig>("/api/config"),

  printerConfig: () => request<PrinterConfig>("/api/config/printer"),
  testPrinter: (body: PrinterConfigInput) =>
    request<ProbeResult>("/api/config/printer/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  savePrinter: (body: PrinterConfigInput & { force?: boolean }) =>
    request<PrinterConfig & { probe: ProbeResult }>("/api/config/printer", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** 只连 FTPS 读 SERIAL，不碰 MQTT、不保存。序列号字段依旧可以手动改。 */
  discoverSerial: (body: { ip: string; access_code?: string }) =>
    request<DiscoverSerialResult>("/api/config/printer/discover-serial", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  saveConfig: (body: {
    print_defaults?: Partial<Record<keyof PrintOptions, boolean>>;
    scheduler?: { start_after_failure?: boolean; upload_timing?: string };
  }) =>
    request<AppConfig>("/api/config", { method: "PATCH", body: JSON.stringify(body) }),
};
