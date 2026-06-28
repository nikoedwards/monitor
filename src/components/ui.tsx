import React from "react";

type Div = React.HTMLAttributes<HTMLDivElement>;

export function cx(...parts: (string | false | undefined | null)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ className, ...rest }: Div) {
  return <div className={cx("panel p-5", className)} style={{ boxShadow: "var(--shadow)" }} {...rest} />;
}

export function InfoHint({ text, className }: { text: React.ReactNode; className?: string }) {
  return (
    <span className={cx("relative inline-flex items-center group align-middle", className)}>
      <span
        tabIndex={0}
        aria-label="说明"
        className="inline-flex items-center justify-center w-[15px] h-[15px] rounded-full text-[10px] font-semibold leading-none cursor-help select-none"
        style={{ background: "var(--bg-soft-2)", color: "var(--mute)", border: "1px solid var(--hairline-strong)" }}
      >
        i
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-[150%] z-50 w-[300px] p-3 rounded-md text-[12px] font-normal leading-relaxed opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity"
        style={{ background: "var(--panel)", color: "var(--body)", border: "1px solid var(--hairline-strong)", boxShadow: "var(--shadow)" }}
      >
        {text}
      </span>
    </span>
  );
}

export function SectionTitle({ title, subtitle, action, hint }: { title: string; subtitle?: string; action?: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 mb-4">
      <div>
        <h2 className="text-[20px] font-semibold tracking-tight flex items-center gap-2" style={{ color: "var(--ink)" }}>
          {title}
          {hint && <InfoHint text={hint} />}
        </h2>
        {subtitle && <p className="text-[13px] mt-0.5" style={{ color: "var(--mute)" }}>{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md" }) {
  const base = "inline-flex items-center justify-center gap-1.5 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer";
  const sizes = size === "sm" ? "h-7 px-2.5 text-[13px] rounded-md" : "h-9 px-3.5 text-[14px] rounded-md";
  const variants: Record<string, React.CSSProperties> = {
    primary: { background: "var(--ink)", color: "var(--bg)" },
    secondary: { background: "var(--panel)", color: "var(--ink)", border: "1px solid var(--hairline-strong)" },
    ghost: { background: "transparent", color: "var(--body)" },
    danger: { background: "transparent", color: "var(--danger)", border: "1px solid var(--hairline-strong)" },
  };
  return <button className={cx(base, sizes, className)} style={variants[variant]} {...rest} />;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "positive" | "negative" | "warning" | "accent"; children: React.ReactNode }) {
  const tones: Record<string, React.CSSProperties> = {
    neutral: { background: "var(--bg-soft-2)", color: "var(--body)" },
    positive: { background: "rgba(0,112,243,0.12)", color: "var(--accent)" },
    negative: { background: "var(--danger-soft)", color: "var(--danger)" },
    warning: { background: "var(--warning-soft)", color: "var(--warning)" },
    accent: { background: "rgba(121,40,202,0.12)", color: "var(--violet)" },
  };
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[12px] font-medium" style={tones[tone]}>
      {children}
    </span>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx("h-9 px-3 text-[14px] rounded-md outline-none w-full", props.className)}
      style={{ background: "var(--bg-soft)", color: "var(--ink)", border: "1px solid var(--hairline-strong)" }}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx("h-9 px-2.5 text-[14px] rounded-md outline-none", props.className)}
      style={{ background: "var(--bg-soft)", color: "var(--ink)", border: "1px solid var(--hairline-strong)" }}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx("px-3 py-2 text-[14px] rounded-md outline-none w-full", props.className)}
      style={{ background: "var(--bg-soft)", color: "var(--ink)", border: "1px solid var(--hairline-strong)" }}
    />
  );
}

export function StatCard({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: string; tone?: "negative" | "accent" }) {
  const color = tone === "negative" ? "var(--danger)" : tone === "accent" ? "var(--accent)" : "var(--ink)";
  return (
    <Card className="p-4">
      <div className="text-[12px] uppercase tracking-wide" style={{ color: "var(--mute)" }}>{label}</div>
      <div className="text-[28px] font-semibold mt-1 tabular-nums tracking-tight" style={{ color }}>{value}</div>
      {hint && <div className="text-[12px] mt-1" style={{ color: "var(--mute)" }}>{hint}</div>}
    </Card>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4 rounded-lg" style={{ border: "1px dashed var(--hairline-strong)" }}>
      <div className="text-[14px] font-medium" style={{ color: "var(--body)" }}>{title}</div>
      {hint && <div className="text-[13px] mt-1 max-w-md" style={{ color: "var(--mute)" }}>{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function SegmentGroup<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex p-0.5 rounded-md" style={{ background: "var(--bg-soft-2)", border: "1px solid var(--hairline)" }}>
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className="px-2.5 h-7 text-[13px] rounded-[5px] font-medium transition-colors cursor-pointer"
          style={value === opt.value ? { background: "var(--panel)", color: "var(--ink)", boxShadow: "var(--shadow)" } : { color: "var(--mute)" }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, onClose, title, children, width = 560 }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; width?: number }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 overflow-y-auto" style={{ background: "rgba(0,0,0,0.5)" }} onClick={onClose}>
      <div className="panel mt-[8vh] w-full" style={{ maxWidth: width, boxShadow: "var(--shadow)" }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--hairline)" }}>
          <h3 className="text-[16px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>{title}</h3>
          <button onClick={onClose} className="text-[18px] leading-none cursor-pointer" style={{ color: "var(--mute)" }}>×</button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="h-6 w-6 rounded-full animate-spin" style={{ border: "2px solid var(--hairline)", borderTopColor: "var(--ink)" }} />
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[13px] font-medium mb-1.5 flex items-center gap-1.5" style={{ color: "var(--body)" }}>
        {label}
        {hint && <InfoHint text={hint} />}
      </span>
      {children}
    </label>
  );
}
