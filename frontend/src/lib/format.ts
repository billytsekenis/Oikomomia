export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function pct(v: unknown, d = 1, sign = true): string {
  if (!isNum(v)) return "—";
  const s = (v * 100).toFixed(d);
  return `${sign && v > 0 ? "+" : ""}${s}%`;
}
export function num(v: unknown, d = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
export function money(v: unknown, d = 2, ccy = "$"): string {
  if (!isNum(v)) return "—";
  const neg = v < 0;
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return `${neg ? "-" : ""}${ccy}${s}`;
}
export function ccy(v: unknown, code: string, d = 2): string {
  return money(v, d, code === "EUR" ? "€" : code === "USD" ? "$" : code + " ");
}
export function compact(v: unknown, prefix = ""): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const f = (x: number, s: string) => `${v < 0 ? "-" : ""}${prefix}${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)}${s}`;
  if (a >= 1e12) return f(a / 1e12, "T");
  if (a >= 1e9) return f(a / 1e9, "B");
  if (a >= 1e6) return f(a / 1e6, "M");
  if (a >= 1e3) return f(a / 1e3, "K");
  return `${v < 0 ? "-" : ""}${prefix}${a.toFixed(2)}`;
}
export const signClass = (v: unknown) => (!isNum(v) || v === 0 ? "text-muted" : v > 0 ? "text-up" : "text-down");

export function ago(iso?: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];

/** Diverging color for returns: down (red) <- gray -> up (green-aqua), clipped at +/-lim. */
export function divColor(v: unknown, lim = 0.03): string {
  if (!isNum(v)) return "rgb(var(--panel2))";
  const t = Math.max(-1, Math.min(1, v / lim));
  const a = Math.abs(t);
  const c = t >= 0 ? "var(--up)" : "var(--down)";
  return `color-mix(in oklab, rgb(${c}) ${Math.round(15 + a * 75)}%, var(--mid))`;
}
