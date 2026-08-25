import { useCallback, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useApp } from "@/store/app";
import { Button, Card, Empty } from "@/components/ui";
import { AmsPanel } from "@/components/printer";
import { api, ApiError } from "@/lib/api";

/** 等 SSE 把刷新后的数据推回来，最多等 3 秒；等不到也不算失败，只是不确认。 */
function waitForUpdate(after: number, timeoutMs = 3000): Promise<void> {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
      if (useApp.getState().lastUpdate > after || Date.now() - start > timeoutMs) {
        resolve();
        return;
      }
      window.setTimeout(check, 150);
    };
    check();
  });
}

export function AmsPage() {
  const printer = useApp((s) => s.printer);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const handleRefresh = useCallback(async () => {
    setBusy(true);
    setFeedback(null);
    const before = useApp.getState().lastUpdate;
    try {
      await api.refresh();
      await waitForUpdate(before);
      const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      setFeedback(`已向打印机重新拉取 · ${ts}`);
    } catch (e) {
      setFeedback(e instanceof ApiError ? e.message : "刷新失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">AMS 与耗材</h1>
        <div className="flex items-center gap-2">
          {feedback ? <span className="text-xs text-muted">{feedback}</span> : null}
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void handleRefresh()}
            title="重新读取打印机里保存的耗材信息（只读查询，不会改动打印机）"
          >
            {busy ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            手动刷新
          </Button>
        </div>
      </div>
      {printer ? (
        <AmsPanel printer={printer} />
      ) : (
        <Card>
          <Empty>还没读到 AMS 信息。</Empty>
        </Card>
      )}
    </div>
  );
}
