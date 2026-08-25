import * as React from "react";
import { cn } from "@/lib/cn";

/* ------------------------------------------------------------------ Button */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "outline" | "danger";
  size?: "sm" | "md";
};

export function Button({
  className, variant = "outline", size = "md", ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium",
        "transition-colors disabled:pointer-events-none disabled:opacity-45",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-4 text-sm",
        variant === "primary" && "bg-brand text-brand-fg hover:bg-brand/90 font-semibold",
        variant === "outline" && "border border-line bg-surface-2 hover:bg-line/60",
        variant === "ghost" && "hover:bg-surface-2",
        variant === "danger" &&
          "border border-danger/40 text-danger hover:bg-danger/10",
        className,
      )}
      {...props}
    />
  );
}

/* -------------------------------------------------------------------- Card */

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-line bg-surface", className)}
      {...props}
    />
  );
}

export function CardHeader({
  title, sub, right,
}: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-semibold">{title}</div>
        {sub ? <div className="mt-0.5 text-xs text-muted">{sub}</div> : null}
      </div>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------------- Badge */

export function Badge({
  className, children,
}: { className?: string; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ Switch */

/**
 * 开关的视觉约定：**开 = 绿底 + 滑块靠右；关 = 灰底（--switch-off）+ 滑块收回左边**。
 *
 * 关态特意不用 --border 上色：--border 是给卡片轮廓、分隔线这类"线"用的克制色，
 * 本来就该淡；但开关的关态是一整块要被一眼认出"这是个可点控件"的填充面，必须比
 * 边框更亮、更接近可交互元素的视觉重量，所以单独有一个 --switch-off（见
 * styles.css 里的说明），轨道再叠一圈 border-line 描边，双重强调它不是装饰色块。
 *
 * 轨道 w-9=36px、滑块 w-4=16px、滑块 left-0.5=2px：
 *   关态 translate-x-0 → 滑块左边距 2px；
 *   开态 translate-x-4（16px）→ 滑块左边距变成 2+16=18px，
 *   轨道宽 36 减滑块宽 16 再减左边距 18 = 右边距同样 2px，左右对称。
 * 这个 2px 的余量很薄，所以描边只能用 inset ring（box-shadow 不占布局），
 * 一旦改成 border，padding box 缩到 34×18，开态滑块就会贴死右下角。
 *
 * 轨道和滑块统一用 transition-all + duration-200 + ease-out：
 * 之前轨道只写 transition-colors，但 disabled 态改的是 opacity、颜色切换改的是
 * background-color，两条属性各转各的，节奏对不上；改成 transition-all 后两者
 * 用同一条时间曲线，不会出现"透明度瞬变、颜色渐变"这种割裂感。
 */
export function Switch({
  checked, onChange, disabled, label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** 无障碍用：透传成 aria-label，让屏幕阅读器能读出这是哪个开关。不传时行为不变。 */
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-all duration-200 ease-out",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/60",
        // 禁用态和「关」态要能区分开：禁用的「开」仍然要看得出是绿的，
        // 所以只压 opacity 到 60%，不整体改色，也不能压得比关态的灰更暗。
        "disabled:cursor-not-allowed disabled:opacity-60",
        // 描边用 inset ring（box-shadow）而不是 border：border 会吃掉 1px 的
        // padding box，滑块的 left/translate 是相对 padding 边算的，加了 border
        // 之后开态滑块会正好贴死右边和下边，看起来就像"滑出去了"。
        // 描边颜色改用 --hairline 变量（T8）：原来两套主题共用硬编码的半透明白，
        // 浅色主题下白色系描边在白底/浅色控件上会隐形，--hairline 在深/浅两套里
        // 各给一个值，深色下和原硬编码同一档位，组件这里不用关心具体是哪个主题。
        checked
          ? "bg-brand shadow-[inset_0_0_0_1px_var(--hairline)]"
          : "bg-switch-off shadow-[inset_0_0_0_1px_var(--border)]",
      )}
    >
      <span
        className={cn(
          "absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-all duration-200 ease-out",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
  );
}

/* -------------------------------------------------------------------- 三态 */

/**
 * 三态开关：开 / 关 / 跟随全局。
 *
 * 「跟随全局」不是装饰——null 和 false 在后端是不同语义：提交时不把开关固化成
 * 布尔值，之后改了全局默认，还没触发的任务才能跟着变。所以界面上必须能选到它，
 * 也必须能看出当前处在哪一态。
 */
export function TriSwitch({
  value, fallback, onChange,
}: {
  value: boolean | null;
  fallback: boolean;
  onChange: (v: boolean | null) => void;
}) {
  const opts: Array<{ v: boolean | null; label: string }> = [
    { v: null, label: `跟随全局（${fallback ? "开" : "关"}）` },
    { v: true, label: "开" },
    { v: false, label: "关" },
  ];
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {opts.map((o) => (
        <button
          key={String(o.v)}
          type="button"
          onClick={() => onChange(o.v)}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs transition-colors",
            value === o.v
              ? "bg-brand text-brand-fg font-semibold"
              : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------- Input */

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-lg border border-line bg-surface-2 px-3 text-sm",
        "placeholder:text-muted focus:border-brand/60 focus:outline-none",
        className,
      )}
      {...props}
    />
  );
});

/* ------------------------------------------------------------------- 其他 */

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-14 text-center text-sm text-muted">
      {children}
    </div>
  );
}

export function Field({
  label, hint, children,
}: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium text-muted">{label}</div>
      {children}
      {hint ? <div className="text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

/**
 * 一个小色块，用来显示耗材颜色。AMS lite 无 RFID，这些颜色是人手填的。
 * 边框颜色用 --hairline（T8 之前是硬编码的半透明白）：色块本身颜色不可控，
 * 深色耗材在深色主题的卡片底色上会糊在一起，需要一圈够亮的描边兜底；
 * 但浅色主题下反过来——白色/浅色耗材摆在白卡片上，半透明白描边直接隐形，
 * 所以改成随主题切换的 --hairline（深色下是半透明白，浅色下是半透明黑），
 * 而不是固定颜色。
 */
export function ColorDot({ rgb, size = 16 }: { rgb: string; size?: number }) {
  return (
    <span
      className="inline-block shrink-0 rounded border border-hairline"
      style={{ width: size, height: size, background: `#${rgb || "666666"}` }}
    />
  );
}
