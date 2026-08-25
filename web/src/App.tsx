import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell, NotFound } from "@/components/layout/AppShell";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { TaskList } from "@/pages/TaskList";
import { TaskNew } from "@/pages/TaskNew";
import { TaskDetail } from "@/pages/TaskDetail";
import { PrinterPage } from "@/pages/Printer";
import { AmsPage } from "@/pages/Ams";
import { JournalPage } from "@/pages/Journal";
import { SettingsPage } from "@/pages/Settings";
import { useApp } from "@/store/app";
import { api } from "@/lib/api";

export function App() {
  const { authed, setAuthed } = useApp();

  useEffect(() => {
    api.me().then(
      (m) => setAuthed(m.authed),
      () => setAuthed(false),
    );
  }, [setAuthed]);

  if (authed === null) {
    return (
      <div className="grid h-full place-items-center text-sm text-muted">载入中…</div>
    );
  }

  return (
    <BrowserRouter>
      {authed ? (
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tasks" element={<TaskList />} />
            <Route path="/tasks/new" element={<TaskNew />} />
            <Route path="/tasks/:id" element={<TaskDetail />} />
            <Route path="/printer" element={<PrinterPage />} />
            <Route path="/printer/ams" element={<AmsPage />} />
            <Route path="/journal" element={<JournalPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            {/* 登录完 URL 还停在 /login，已登录的路由表里没有这条，
                不重定向就会掉进 NotFound。 */}
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      ) : (
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      )}
    </BrowserRouter>
  );
}
