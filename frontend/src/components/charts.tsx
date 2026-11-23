import { ColorType, createChart, CrosshairMode, IChartApi, LineStyle, Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { divColor, isNum, money, num, pct, SERIES } from "../lib/format";

const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const rgbVar = (name: string, a = 1) => `rgba(${cssVar(name).split(" ").join(",")},${a})`;

function useThemeKey() {
  const [k, setK] = useState(document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const o = new MutationObserver(() => setK(document.documentElement.classList.contains("dark")));
    o.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => o.disconnect();
  }, []);
  return k;
}

export function Sparkline({ data, width = 110, height = 32 }: { data: number[]; width?: number; height?: number }) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const lo = Math.min(...data), hi = Math.max(...data);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4)}`);
  const up = data[data.length - 1] >= data[0];
  return (
    <svg width={width} height={height} aria-hidden className="overflow-visible">
      <polyline points={pts.join(" ")} fill="none" stroke={up ? "rgb(var(--up))" : "rgb(var(--down))"} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

type Bar_ = { date: string; open: number; high: number; low: number; close: number; volume: number; [k: string]: any };

function baseOpts(el: HTMLElement, height: number) {
  return {
    width: el.clientWidth, height,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: rgbVar("--muted"), fontSize: 11,
              fontFamily: "Inter, system-ui, sans-serif" },
    grid: { vertLines: { color: cssVar("--grid") }, horzLines: { color: cssVar("--grid") } },
    rightPriceScale: { borderColor: rgbVar("--line") },
    timeScale: { borderColor: rgbVar("--line"), timeVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
  };
}

export function CandleChart({ bars, overlays, levels, panes }: {
  bars: Bar_[]; overlays: Record<string, boolean>; levels?: { support: any[]; resistance: any[] }; panes: Record<string, boolean>;
}) {
  const main = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const dark = useThemeKey();
  const [hover, setHover] = useState<Bar_ | null>(null);

  useEffect(() => {
    if (!main.current || !bars.length) return;
    const charts: IChartApi[] = [];
    const t = (b: Bar_) => b.date as Time;
    const c = createChart(main.current, baseOpts(main.current, 380));
    charts.push(c);
    const candles = c.addCandlestickSeries({
      upColor: rgbVar("--up"), downColor: rgbVar("--down"), borderVisible: false,
      wickUpColor: rgbVar("--up"), wickDownColor: rgbVar("--down"),
    });
    candles.setData(bars.map((b) => ({ time: t(b), open: b.open, high: b.high, low: b.low, close: b.close })));
    const vol = c.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: t(b), value: b.volume, color: b.close >= b.open ? rgbVar("--up", 0.35) : rgbVar("--down", 0.35) })));
    const line = (key: string, color: string, width = 1.5, style = LineStyle.Solid) => {
      const s = c.addLineSeries({ color, lineWidth: width as any, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(bars.filter((b) => isNum(b[key])).map((b) => ({ time: t(b), value: b[key] })));
    };
    if (overlays.sma20) line("sma20", cssVar("--s4"));
    if (overlays.sma50) line("sma50", cssVar("--s1"));
    if (overlays.sma200) line("sma200", cssVar("--s7"), 2);
    if (overlays.ema21) line("ema21", cssVar("--s2"));
    if (overlays.bb) { line("bb_upper", rgbVar("--muted", 0.8), 1, LineStyle.Dashed); line("bb_lower", rgbVar("--muted", 0.8), 1, LineStyle.Dashed); }
    if (overlays.levels && levels) {
      levels.support.forEach((l) => candles.createPriceLine({ price: l.price, color: rgbVar("--up", 0.7), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: `S ${l.touches}x` }));
      levels.resistance.forEach((l) => candles.createPriceLine({ price: l.price, color: rgbVar("--down", 0.7), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: `R ${l.touches}x` }));
    }
    const byTime = new Map(bars.map((b) => [b.date, b]));
    c.subscribeCrosshairMove((p) => setHover(p.time ? byTime.get(String(p.time)) || null : null));
    c.timeScale().fitContent();

    const sub = (el: HTMLDivElement | null, build: (ch: IChartApi) => void) => {
      if (!el) return;
      const ch = createChart(el, { ...baseOpts(el, 120), timeScale: { visible: false, borderColor: rgbVar("--line") } });
      build(ch);
      charts.push(ch);
      ch.timeScale().fitContent();
    };
    if (panes.rsi) sub(rsiRef.current, (ch) => {
      const s = ch.addLineSeries({ color: cssVar("--s1"), lineWidth: 2 as any, priceLineVisible: false });
      s.setData(bars.filter((b) => isNum(b.rsi14)).map((b) => ({ time: t(b), value: b.rsi14 })));
      [30, 70].forEach((v) => s.createPriceLine({ price: v, color: rgbVar("--muted", 0.6), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" }));
    });
    if (panes.macd) sub(macdRef.current, (ch) => {
      const h = ch.addHistogramSeries({ priceLineVisible: false });
      h.setData(bars.filter((b) => isNum(b.hist)).map((b) => ({ time: t(b), value: b.hist, color: b.hist >= 0 ? rgbVar("--up", 0.6) : rgbVar("--down", 0.6) })));
      const m = ch.addLineSeries({ color: cssVar("--s1"), lineWidth: 2 as any, priceLineVisible: false, lastValueVisible: false });
      m.setData(bars.filter((b) => isNum(b.macd)).map((b) => ({ time: t(b), value: b.macd })));
      const sg = ch.addLineSeries({ color: cssVar("--s2"), lineWidth: 2 as any, priceLineVisible: false, lastValueVisible: false });
      sg.setData(bars.filter((b) => isNum(b.signal)).map((b) => ({ time: t(b), value: b.signal })));
    });
    // keep panes in sync with the main time scale
    c.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r) charts.slice(1).forEach((ch) => ch.timeScale().setVisibleLogicalRange(r)); });
    const ro = new ResizeObserver(() => charts.forEach((ch, i) => {
      const el = [main.current, rsiRef.current, macdRef.current].filter(Boolean)[i];
      if (el) ch.applyOptions({ width: el.clientWidth });
    }));
    if (main.current) ro.observe(main.current);
    return () => { ro.disconnect(); charts.forEach((ch) => ch.remove()); };
  }, [bars, overlays, levels, panes, dark]);

  const last = hover || bars[bars.length - 1];
  return (
    <div>
      {last && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs num text-muted mb-1">
          <span className="text-fg">{last.date}</span>
          <span>O {num(last.open)}</span><span>H {num(last.high)}</span><span>L {num(last.low)}</span>
          <span className="text-fg">C {num(last.close)}</span><span>Vol {(last.volume / 1e6).toFixed(2)}M</span>
          {isNum(last.rsi14) && <span>RSI {last.rsi14.toFixed(1)}</span>}
        </div>
      )}
      <div ref={main} className="w-full" />
      {panes.rsi && <div className="mt-1"><div className="text-[11px] text-muted">RSI(14) · 30/70</div><div ref={rsiRef} /></div>}
      {panes.macd && <div className="mt-1"><div className="text-[11px] text-muted">MACD(12,26,9)</div><div ref={macdRef} /></div>}
    </div>
  );
}

const axisProps = { stroke: "rgb(var(--muted))", fontSize: 11, tickLine: false, axisLine: false } as const;
const tipStyle = { background: "rgb(var(--panel))", border: "1px solid rgb(var(--line))", borderRadius: 8, fontSize: 12, color: "rgb(var(--fg))" };

export function FanChart({ fan, price, history }: { fan: any[]; price: number; history?: { date: string; close: number }[] }) {
  const data = useMemo(() => {
    const h = (history || []).slice(-120).map((b) => ({ date: b.date, close: b.close }));
    const f = fan.map((p) => ({ date: p.date, band90: [p.p05, p.p95], band50: [p.p25, p.p75], p50: p.p50 }));
    return [...h, { date: h.length ? h[h.length - 1].date : "", band90: [price, price], band50: [price, price], p50: price, close: price }, ...f];
  }, [fan, price, history]);
  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="date" {...axisProps} minTickGap={50} />
        <YAxis {...axisProps} domain={["auto", "auto"]} width={56} tickFormatter={(v) => money(v, 0)} />
        <Tooltip contentStyle={tipStyle} formatter={(v: any, n: string) => [Array.isArray(v) ? `${money(v[0])} – ${money(v[1])}` : money(v), n]} />
        <Area dataKey="band90" name="90% range" stroke="none" fill="var(--s1)" fillOpacity={0.15} isAnimationActive={false} />
        <Area dataKey="band50" name="50% range" stroke="none" fill="var(--s1)" fillOpacity={0.3} isAnimationActive={false} />
        <Line dataKey="p50" name="Median path" stroke="var(--s1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line dataKey="close" name="Price (history)" stroke="rgb(var(--fg))" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <ReferenceLine y={price} stroke="rgb(var(--muted))" strokeDasharray="3 3" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function DistChart({ data }: { data: { ret: number; p: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="ret" {...axisProps} tickFormatter={(v) => pct(v, 0)} minTickGap={30} />
        <YAxis {...axisProps} tickFormatter={(v) => pct(v, 0, false)} width={40} />
        <Tooltip contentStyle={tipStyle} formatter={(v: any) => [pct(v, 1, false), "probability"]} labelFormatter={(v) => `return ≈ ${pct(v, 1)}`} />
        <ReferenceLine x={0} stroke="rgb(var(--muted))" />
        <Bar dataKey="p" isAnimationActive={false} radius={[3, 3, 0, 0]}>
          {data.map((d, i) => <Cell key={i} fill={d.ret >= 0 ? "rgb(var(--up))" : "rgb(var(--down))"} fillOpacity={0.75} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function MultiLine({ data, series, height = 260, yFmt = (v: number) => num(v, 2), x = "date" }: {
  data: any[]; series: { key: string; name: string; color?: string }[]; height?: number; yFmt?: (v: number) => string; x?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} {...axisProps} minTickGap={50} />
        <YAxis {...axisProps} tickFormatter={yFmt} width={56} domain={["auto", "auto"]} />
        <Tooltip contentStyle={tipStyle} formatter={(v: any) => yFmt(v)} />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
        {series.map((s, i) => <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color || SERIES[i]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function SimpleBars({ data, x, y, height = 220, fmt = (v: number) => pct(v, 1), signed = true }: {
  data: any[]; x: string; y: string; height?: number; fmt?: (v: number) => string; signed?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} {...axisProps} />
        <YAxis {...axisProps} tickFormatter={fmt} width={52} />
        <Tooltip contentStyle={tipStyle} formatter={(v: any) => fmt(v)} />
        <ReferenceLine y={0} stroke="rgb(var(--muted))" />
        <Bar dataKey={y} radius={[4, 4, 0, 0]} isAnimationActive={false}>
          {data.map((d, i) => <Cell key={i} fill={!signed ? "var(--s1)" : d[y] >= 0 ? "rgb(var(--up))" : "rgb(var(--down))"} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function CalibrationChart({ bins }: { bins: { mean_p: number; realized: number; n: number }[] }) {
  const data = bins.map((b) => ({ x: b.mean_p, y: b.realized, n: b.n }));
  const lo = Math.min(0.3, ...data.map((d) => Math.min(d.x, d.y))) - 0.02;
  const hi = Math.max(0.8, ...data.map((d) => Math.max(d.x, d.y))) + 0.02;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" />
        <XAxis type="number" dataKey="x" name="predicted P(up)" domain={[lo, hi]} {...axisProps} tickFormatter={(v) => pct(v, 0, false)} />
        <YAxis type="number" dataKey="y" name="realised up-rate" domain={[lo, hi]} {...axisProps} tickFormatter={(v) => pct(v, 0, false)} width={44} />
        <ZAxis dataKey="n" range={[60, 260]} name="n" />
        <Tooltip contentStyle={tipStyle} formatter={(v: any, n: string) => (n === "n" ? v : pct(v, 1, false))} />
        <ReferenceLine segment={[{ x: lo, y: lo }, { x: hi, y: hi }]} stroke="rgb(var(--muted))" strokeDasharray="4 4" />
        <Scatter data={data} fill="var(--s1)" />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function Heatmap({ items, valueKey, labelKey = "ticker", groupKey, onClick }: {
  items: any[]; valueKey: string; labelKey?: string; groupKey?: string; onClick?: (it: any) => void;
}) {
  const groups = useMemo(() => {
    if (!groupKey) return [["", items]] as [string, any[]][];
    const m = new Map<string, any[]>();
    items.forEach((it) => m.set(it[groupKey], [...(m.get(it[groupKey]) || []), it]));
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [items, groupKey]);
  return (
    <div className="space-y-2">
      {groups.map(([g, list]) => (
        <div key={g}>
          {g && <div className="text-[11px] text-muted mb-1">{g}</div>}
          <div className="grid gap-[2px]" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(64px, 1fr))" }}>
            {list.map((it) => (
              <button key={it[labelKey]} onClick={() => onClick?.(it)} title={`${it.name || it[labelKey]}: ${pct(it[valueKey], 2)}`}
                      className="rounded-md px-1.5 py-1.5 text-left hover:ring-2 hover:ring-accent/60 transition"
                      style={{ background: divColor(it[valueKey]) }}>
                <div className="text-[11px] font-semibold truncate">{it[labelKey]}</div>
                <div className="text-[11px] num">{pct(it[valueKey], 1)}</div>
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span>-3%</span>
        <div className="h-2 w-32 rounded" style={{ background: `linear-gradient(90deg, ${divColor(-0.03)}, ${divColor(0)}, ${divColor(0.03)})` }} />
        <span>+3%</span>
      </div>
    </div>
  );
}

export function CorrMatrix({ tickers, matrix }: { tickers: string[]; matrix: number[][] }) {
  const color = (v: number) => (v >= 0 ? `color-mix(in oklab, var(--s1) ${Math.round(Math.abs(v) * 85)}%, var(--mid))`
                                        : `color-mix(in oklab, var(--s8) ${Math.round(Math.abs(v) * 85)}%, var(--mid))`);
  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="text-[11px] num border-separate" style={{ borderSpacing: 2 }}>
        <thead><tr><th />{tickers.map((t) => <th key={t} className="px-1 font-medium text-muted">{t}</th>)}</tr></thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={tickers[i]}>
              <th className="pr-1 text-right font-medium text-muted">{tickers[i]}</th>
              {row.map((v, j) => <td key={j} title={`${tickers[i]} / ${tickers[j]}: ${v.toFixed(2)}`} className="w-10 h-8 text-center rounded" style={{ background: color(v) }}>{v.toFixed(2)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
