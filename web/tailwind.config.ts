import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        line: "var(--border)",
        fg: "var(--fg)",
        muted: "var(--fg-muted)",
        brand: "var(--brand)",
        "brand-fg": "var(--brand-fg)",
        warn: "var(--warn)",
        danger: "var(--danger)",
        idle: "var(--idle)",
        "switch-off": "var(--switch-off)",
        scrollbar: "var(--scrollbar)",
        "scrollbar-hover": "var(--scrollbar-hover)",
        // T8 浅色主题 demo 新增：ColorDot 边框 / Switch 开态描边用的通用细描边色，
        // 深/浅两套主题各给一个值（见 styles.css）。
        hairline: "var(--hairline)",
      },
      fontFamily: {
        sans: ['"Segoe UI"', "system-ui", '"Microsoft YaHei"', "sans-serif"],
        mono: ['"Cascadia Mono"', "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
