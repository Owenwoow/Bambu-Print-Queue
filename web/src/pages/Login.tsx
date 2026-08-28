import { useState } from "react";
import { Github } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store/app";
import { Button, Input } from "@/components/ui";

export function Login() {
  const setAuthed = useApp((s) => s.setAuthed);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.login(password);
      setAuthed(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid h-full place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-[22rem] space-y-4">
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-md bg-brand text-brand-fg">
            <span className="text-sm font-bold">b</span>
          </div>
          <div>
            <div className="font-semibold">Bambu Print Queue</div>
            <div className="text-xs text-muted">拓竹 A1 打印任务预约</div>
          </div>
        </div>

        <Input
          type="password"
          autoFocus
          placeholder="口令"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {err ? <div className="text-xs text-danger">{err}</div> : null}

        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </Button>

        <p className="text-[11px] leading-relaxed text-muted">
          口令在 config.toml 的 [web] 段设置。局域网里是明文 http 传输，
          请用一个不与别处复用的口令。
        </p>

        <a
          href="https://github.com/Owenwoow/Bambu-Print-Queue"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 text-[11px] text-muted transition-colors hover:text-fg"
        >
          <Github size={12} />
          GitHub
        </a>
      </form>
    </div>
  );
}
