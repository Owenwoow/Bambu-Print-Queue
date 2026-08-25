import { useCallback, useEffect, useState } from "react";

/**
 * T8：浅色主题 demo 的主题切换逻辑。
 *
 * 三态语义：
 * - "system"：不在 <html> 上打 data-theme，交给 styles.css 里的
 *   `@media (prefers-color-scheme: light)` 根据系统偏好决定，默认态。
 * - "light" / "dark"：明确打 `data-theme="light"` / `data-theme="dark"`，
 *   优先级高于媒体查询（选择器特异性更高，见 styles.css 里的说明）。
 *
 * 存储用 localStorage，key 是 "bpq.theme"，和现有 Sidebar 的 "bpq.nav.open"
 * 一个风格：读不到 / 值不合法就当默认值，不抛错。
 */

export type ThemeChoice = "system" | "light" | "dark";

const STORAGE_KEY = "bpq.theme";

function isThemeChoice(v: unknown): v is ThemeChoice {
  return v === "system" || v === "light" || v === "dark";
}

function readStored(): ThemeChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (isThemeChoice(v)) return v;
  } catch {
    // localStorage 不可用（隐私模式等），当成跟随系统
  }
  return "system";
}

/**
 * 把选择应用到 <html> 上。
 *
 * 这段逻辑必须和 web/index.html 里那段内联 <script> 保持一致——那段在 React
 * 挂载前、CSS 生效前就跑一遍同样的判断，提前把 data-theme 打上去，避免深色偏好
 * 的用户在页面刚加载时先闪一下浅色。两处逻辑没法合并成同一份代码：内联脚本要在
 * bundle 加载前跑，等不到这个模块被 import。改这个函数时记得同步改
 * index.html 里的那段内联脚本。
 */
function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/** 三态主题切换的小 hook：读取/应用/持久化一次性包好。 */
export function useTheme(): [ThemeChoice, (v: ThemeChoice) => void] {
  const [theme, setThemeState] = useState<ThemeChoice>(readStored);

  // 首次挂载时也应用一次：index.html 的内联脚本只处理了「刚打开页面」这一刻，
  // 这里保证 React 状态和 DOM 属性始终同步（包括后续切换）。
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((v: ThemeChoice) => {
    setThemeState(v);
    try {
      localStorage.setItem(STORAGE_KEY, v);
    } catch {
      // 存不进去就算了，本次会话里 state 仍然生效，下次刷新会退回默认值
    }
  }, []);

  return [theme, setTheme];
}
