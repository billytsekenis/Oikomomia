import clsx from "clsx";
import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { ReactNode, useState } from "react";
import { ago, isNum, pct, signClass } from "../lib/format";

export function Card({ title, right, children, className, pad = true, sub }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; pad?: boolean; sub?: ReactNode;
}) {
  return (
    <section className={clsx("card min-w-0", className)}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-2 px-4 pt-3 pb-2">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
          </div>
          {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
        </header>
      )}
      <div className={clsx(pad && "px-4 pb-4", !title && !right && pad && "pt-4")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, delta, hint, className }: {
  label: ReactNode; value: ReactNode; delta?: number | null; hint?: ReactNode; className?: string;
}) {
  return (
    <div className={clsx("min-w-0", className)}>
      <div className="text-[11px] uppercase tracking-wide text-muted flex items-center gap-1">{label}{hint && <Tip text={hint} />}</div>
      <div className="text-lg font-semibold num leading-tight mt-0.5 truncate">{value}</div>
      {delta !== undefined && <div className={clsx("text-xs num", signClass(delta))}>{pct(delta, 2)}</div>}
    </div>
  );
}

export function Tip({ text }: { text: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
          onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>
      <Info size={12} className="text-muted cursor-help" aria-label="info" />
      {open && (
        <span role="tooltip" className="absolute z-50 left-1/2 -translate-x-1/2 top-5 w-64 rounded-lg border border-line bg-panel p-2.5 text-xs font-normal normal-case tracking-normal text-fg shadow-xl">
          {text}
        </span>
      )}
    </span>
  );
}

export function Delta({ v, d = 2 }: { v: unknown; d?: number }) {
  return <span className={clsx("num", signClass(v))}>{pct(v, d)}</span>;
}

export function Badge({ children, tone = "neutral", className }: { children: ReactNode; tone?: "neutral" | "up" | "down" | "warn" | "accent"; className?: string }) {
  const t = { neutral: "text-muted", up: "text-up border-up/40", down: "text-down border-down/40", warn: "text-warn border-warn/40", accent: "text-accent border-accent/40" }[tone];
  return <span className={clsx("chip", t, className)}>{children}</span>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return <div className="space-y-2" aria-busy="true">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-5 w-full" />)}</div>;
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <Loader2 size={size} className="animate-spin" />;
}

export function ErrorBox({ error, title = "Could not load" }: { error: unknown; title?: string }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex gap-2 rounded-lg border border-down/40 bg-down/5 p-3 text-sm">
      <AlertTriangle size={16} className="text-down shrink-0 mt-0.5" />
      <div><div className="font-medium">{title}</div><div className="text-muted text-xs mt-0.5 break-words">{msg}</div></div>
    </div>
  );
}

export function Note({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return (
    <div className={clsx("flex gap-2 rounded-lg border p-2.5 text-xs", tone === "warn" ? "border-warn/40 bg-warn/5" : "border-line bg-panel2/60")}>
      {tone === "warn" ? <AlertTriangle size={14} className="text-warn shrink-0 mt-0.5" /> : <Info size={14} className="text-muted shrink-0 mt-0.5" />}
      <div className="text-muted leading-relaxed">{children}</div>
    </div>
  );
}

export function Source({ source, at, stale, extra }: { source?: string | null; at?: string | null; stale?: boolean; extra?: ReactNode }) {
  if (!source && !at) return null;
  return (
    <div className="text-[11px] text-muted flex flex-wrap items-center gap-x-2">
      <span>Source: {source || "—"}</span>
      {at && <span title={at}>· {at.length > 12 && at.includes("T") ? ago(at) : at}</span>}
      {stale && <span className="text-warn">· stale (refresh failed)</span>}
      {extra}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, className }: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; className?: string;
}) {
  return (
    <div role="tablist" className={clsx("flex gap-1 overflow-x-auto scrollbar-thin border-b border-line", className)}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
                className={clsx("px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition",
                  value === t.id ? "border-accent text-fg font-medium" : "border-transparent text-muted hover:text-fg")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({ options, value, onChange }: { options: { id: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
      {options.map((o) => (
        <button key={o.id} onClick={() => onChange(o.id)}
                className={clsx("px-2.5 py-1 text-xs rounded-md transition", value === o.id ? "bg-panel text-fg shadow-sm font-medium" : "text-muted hover:text-fg")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-sm text-muted py-6 text-center">{children}</div>;
}

export function ScoreBar({ value }: { value: number | null | undefined }) {
  if (!isNum(value)) return <span className="text-muted">—</span>;
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="h-1.5 flex-1 rounded-full bg-line overflow-hidden">
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
      </div>
      <span className="num text-xs w-7 text-right">{value.toFixed(0)}</span>
    </div>
  );
}

export function SkillBadge({ skill }: { skill?: string }) {
  const map: Record<string, [string, "up" | "warn" | "down" | "neutral"]> = {
    "small-but-detectable": ["small validated skill", "up"],
    weak: ["weak skill", "warn"],
    "none-demonstrated": ["no demonstrated skill", "down"],
    unvalidated: ["not validated", "neutral"],
  };
  const [label, tone] = map[skill || "unvalidated"] || ["unknown", "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}
