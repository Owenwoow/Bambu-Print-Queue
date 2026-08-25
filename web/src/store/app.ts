import { create } from "zustand";
import { api, Unauthorized } from "@/lib/api";
import type { AppConfig, JournalRecord, LinkHealth, Printer, Task } from "@/lib/types";

/** 深合并一个 RFC 7386 风格的 merge patch。数组整段替换，不做元素级 diff。 */
function applyPatch<T>(base: T, patch: unknown): T {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) {
    return patch as T;
  }
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [k, v] of Object.entries(patch as Record<string, unknown>)) {
    const cur = out[k];
    out[k] =
      v !== null && typeof v === "object" && !Array.isArray(v) &&
      cur !== null && typeof cur === "object" && !Array.isArray(cur)
        ? applyPatch(cur, v)
        : v;
  }
  return out as T;
}

interface State {
  /** 浏览器 ↔ daemon 的连接。和下面那个 link 是两回事，界面上要分开显示。 */
  streamConnected: boolean;
  /** daemon ↔ 打印机 的连接。 */
  link: LinkHealth | null;
  printer: Printer | null;
  tasks: Task[];
  journal: JournalRecord[];
  config: AppConfig | null;
  authed: boolean | null;
  lastUpdate: number;

  connect: () => void;
  disconnect: () => void;
  setAuthed: (v: boolean) => void;
  refreshTasks: () => Promise<void>;
  refreshJournal: () => Promise<void>;
  loadConfig: () => Promise<void>;
}

let source: EventSource | null = null;
let retry = 0;
let retryTimer: number | undefined;

export const useApp = create<State>((set, get) => ({
  streamConnected: false,
  link: null,
  printer: null,
  tasks: [],
  journal: [],
  config: null,
  authed: null,
  lastUpdate: 0,

  setAuthed: (v) => set({ authed: v }),

  connect: () => {
    if (source) return;

    const open = () => {
      source = new EventSource("/api/events");

      source.addEventListener("open", () => {
        retry = 0;
        set({ streamConnected: true, authed: true });
      });

      // 首帧永远是完整快照，直接整体替换 state
      source.addEventListener("snapshot", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        set({
          printer: d.printer,
          link: d.link,
          tasks: d.tasks ?? [],
          lastUpdate: Date.now(),
        });
      });

      source.addEventListener("patch", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        const cur = get().printer;
        set({
          printer: cur ? applyPatch(cur, d.printer) : cur,
          link: d.link ?? get().link,
          lastUpdate: Date.now(),
        });
      });

      source.addEventListener("tasks", (e) => {
        set({ tasks: JSON.parse((e as MessageEvent).data), lastUpdate: Date.now() });
      });

      // 我们跟不上了，服务端让重新对齐——拉一份完整的回来
      source.addEventListener("resync", () => {
        void get().refreshTasks();
        api.printer().then(
          (p) => set({ printer: p, link: p.link }),
          () => undefined,
        );
      });

      // 配置在别的标签页或 CLI 里被改掉时，后端会广播这个事件——不监听的话
      // 这边界面会一直显示陈旧值，得手动刷新才能看到别处改动生效。
      source.addEventListener("config", () => {
        void get().loadConfig();
      });

      // daemon 每写一条日志就会推一条，用来让概览页的「最近事件」不必手动刷新。
      // journal 数组是正序（最新在末尾），新记录 append 到末尾，并裁掉超出 200 条的头部。
      source.addEventListener("journal", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        const record = d?.record as JournalRecord | undefined;
        if (!record) return;
        set((s) => ({ journal: [...s.journal, record].slice(-200) }));
      });

      source.addEventListener("error", () => {
        set({ streamConnected: false });
        source?.close();
        source = null;
        // 指数退避重连，封顶 15 秒。daemon 重启、笔记本合盖唤醒都会走到这里。
        retry = Math.min(retry + 1, 6);
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(open, Math.min(1000 * 2 ** retry, 15000));
      });
    };

    open();
  },

  disconnect: () => {
    window.clearTimeout(retryTimer);
    source?.close();
    source = null;
    set({ streamConnected: false });
  },

  refreshTasks: async () => {
    try {
      set({ tasks: await api.tasks() });
    } catch (e) {
      if (e instanceof Unauthorized) set({ authed: false });
    }
  },

  refreshJournal: async () => {
    try {
      set({ journal: await api.journal() });
    } catch (e) {
      if (e instanceof Unauthorized) set({ authed: false });
    }
  },

  loadConfig: async () => {
    try {
      set({ config: await api.config(), authed: true });
    } catch (e) {
      if (e instanceof Unauthorized) set({ authed: false });
    }
  },
}));
