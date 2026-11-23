import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Heatmap, MultiLine, Sparkline } from "../components/charts";
import { Badge, Card, Delta, ErrorBox, Loading, Note, Segmented, Source, Stat, Tip } from "../components/ui";
import { api } from "../lib/api";
import { compact, divColor, isNum, num, pct } from "../lib/format";

function Instrument({ it }: { it: any }) {
  const q = it.quote || {};
  const price = q.price ?? it.last;
  const ch = q.change_pct != null ? q.change_pct / 100 : it["1D"];
  const proxy = it.provenance?.proxy || q.proxy;
  return (
    <div className="card p-3 min-w-0">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs text-muted truncate">{it.name}</div>
          <div className="text-lg font-semibold num">{num(price)}</div>
        </div>
        <Sparkline data={it.spark || []} width={84} height={30} />
      </div>
      <div className="flex items-center justify-between text-xs mt-1">
        <Delta v={ch} />
        <span className="text-muted num">1M {pct(it["1M"], 1)}</span>
      </div>
      {proxy && <div className="text-[10px] text-warn mt-1">{q.proxy ? `price & history via ${proxy} ETF proxy` : `index level live; chart & 1M via ${proxy} proxy`}</div>}
      {(q.quote_time || it.date) && <div className="text-[10px] text-muted mt-0.5 truncate">as of {q.quote_time || it.date}</div>}
      {it.error && <div className="text-[10px] text-down mt-1 truncate">{it.error}</div>}
      <div className="text-[10px] text-muted mt-1 truncate" title={q.source || it.provenance?.source}>{q.source || it.provenance?.source}</div>
    </div>
  );
}

function RegimeCard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["regime"], queryFn: () => api("/market/regime"), staleTime: 300_000 });
  if (isLoading) return <Card title="Market regime"><Loading rows={5} /></Card>;
  if (error) return <Card title="Market regime"><ErrorBox error={error} /></Card>;
  const r = data.regime, s = data.sentiment;
  const tone = r.state === "bullish" ? "up" : r.state === "bearish" ? "down" : "warn";
  return (
    <Card title="Market state & regime" sub={`As of ${r.as_of}`} right={<Badge tone={tone as any} className="uppercase font-semibold">{r.state}</Badge>}>
      <div className="text-sm font-medium mb-3">{r.regime}</div>
      <div className="grid grid-cols-3 gap-3 mb-3">
        <Stat label="Signal score" value={num(r.score, 2)} hint={r.method} />
        <Stat label="Risk appetite" value={isNum(s.index) ? `${s.index.toFixed(0)}/100` : "—"} hint={s.method} />
        <Stat label="P(high-vol state)" value={r.markov?.available ? pct(r.markov.p_high_vol_state, 0, false) : "—"} hint={r.markov?.method || r.markov?.reason} />
      </div>
      {s.label && <div className="text-xs text-muted mb-2">Sentiment: <span className="text-fg font-medium">{s.label}</span></div>}
      <div className="space-y-1.5">
        {r.signals.map((sg: any) => (
          <div key={sg.name} className="flex items-center gap-2 text-xs">
            <div className="w-40 sm:w-48 shrink-0 text-muted truncate" title={sg.name}>{sg.name}</div>
            <div className="relative h-1.5 flex-1 rounded-full bg-line">
              <div className="absolute top-0 h-1.5 rounded-full" style={{
                left: sg.score >= 0 ? "50%" : `${50 + sg.score * 50}%`, width: `${Math.abs(sg.score) * 50}%`,
                background: sg.score >= 0 ? "rgb(var(--up))" : "rgb(var(--down))" }} />
              <div className="absolute left-1/2 -top-0.5 h-2.5 w-px bg-muted" />
            </div>
            <div className="w-44 hidden md:block truncate text-muted" title={sg.detail}>{sg.detail}</div>
          </div>
        ))}
      </div>
      <details className="mt-3">
        <summary className="text-xs text-muted cursor-pointer">Risk-appetite components</summary>
        <div className="mt-2 space-y-1">
          {s.components.map((c: any) => (
            <div key={c.name} className="flex justify-between text-xs"><span className="text-muted">{c.name}</span><span className="num">{isNum(c.score) ? c.score.toFixed(0) : "—"}</span></div>
          ))}
        </div>
      </details>
      <div className="mt-2"><Source source={`${r.sources.spy}; VIX: ${r.sources.vix}; rates: ${r.sources.rates}`} /></div>
    </Card>
  );
}

function MacroCard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["macro"], queryFn: () => api("/market/macro"), staleTime: 1800_000 });
  if (isLoading) return <Card title="Rates & macro"><Loading rows={6} /></Card>;
  if (error) return <Card title="Rates & macro"><ErrorBox error={error} /></Card>;
  const r = data.rates;
  return (
    <Card title="Rates, inflation & economy" sub={r.available ? `Treasury curve as of ${r.as_of}` : "Treasury data unavailable"}>
      {r.available && (
        <>
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 mb-3">
            {r.curve.filter((c: any) => ["3M", "2Y", "5Y", "10Y", "30Y"].includes(c.tenor)).map((c: any) => (
              <div key={c.tenor} className="rounded-lg bg-panel2 p-2">
                <div className="text-[11px] text-muted">{c.tenor}</div>
                <div className="text-sm font-semibold num">{num(c.yield, 2)}%</div>
                <div className={clsx("text-[11px] num", c.change_1m_bp > 0 ? "text-down" : "text-up")}>{c.change_1m_bp > 0 ? "+" : ""}{num(c.change_1m_bp, 0)}bp 1M</div>
              </div>
            ))}
          </div>
          <div className="flex gap-4 text-xs mb-2">
            <span>10Y-2Y <b className="num">{num(r.spread_10y_2y, 2)}</b> pp</span>
            <span>10Y-3M <b className="num">{num(r.spread_10y_3m, 2)}</b> pp {r.spread_10y_3m < 0 && <Badge tone="warn">inverted</Badge>}</span>
          </div>
          <MultiLine data={r.history} height={150} yFmt={(v) => `${v.toFixed(1)}%`}
                     series={[{ key: "m3", name: "3M" }, { key: "y2", name: "2Y" }, { key: "y10", name: "10Y" }]} />
          <Source source={r.source} at={r.fetched_at} />
        </>
      )}
      <div className="mt-3 divide-y divide-line">
        {data.series.map((s: any) => (
          <div key={s.id} className="flex items-center justify-between py-1.5 text-sm">
            <span className="text-muted text-xs">{s.label}</span>
            {s.available ? (
              <span className="num text-xs">
                {isNum(s.yoy) ? `${pct(s.yoy, 1)} YoY` : ["UNRATE", "FEDFUNDS", "BAMLH0A0HYM2"].includes(s.id) ? `${num(s.last, 2)}%` : compact(s.last)}
                <span className="text-muted"> · {s.date}</span>
              </span>
            ) : <span className="text-[11px] text-warn" title={s.error}>unavailable</span>}
          </div>
        ))}
      </div>
      {data.series.some((s: any) => !s.available) && <div className="mt-2"><Note tone="warn">Some FRED series could not be fetched (FRED rate-limits/blocks some hosts). Set FRED_API_KEY to use the official API. Missing values are shown as unavailable, never estimated.</Note></div>}
      <div className="mt-2 text-xs">EUR/USD (ECB): <b className="num">{num(data.fx?.rate, 4)}</b> <span className="text-muted">· {data.fx?.date}</span></div>
      <Source source="FRED (St. Louis Fed); ECB via Frankfurter" />
    </Card>
  );
}

function SectorsCard() {
  const nav = useNavigate();
  const [per, setPer] = useState("1D");
  const { data, isLoading, error } = useQuery({ queryKey: ["sectors"], queryFn: () => api("/market/sectors"), staleTime: 300_000 });
  if (isLoading) return <Card title="Sector performance"><Loading rows={6} /></Card>;
  if (error) return <Card title="Sector performance"><ErrorBox error={error} /></Card>;
  const lim = { "1D": 0.03, "1W": 0.06, "1M": 0.1, "3M": 0.15, YTD: 0.25, "1Y": 0.3 }[per] || 0.03;
  return (
    <Card title="Sector performance heatmap" sub="SPDR sector ETFs; stock tiles below, grouped by sector"
          right={<Segmented value={per} onChange={setPer} options={["1D", "1W", "1M", "3M", "YTD", "1Y"].map((p) => ({ id: p, label: p }))} />}>
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-1.5 mb-4">
        {data.sectors.filter((s: any) => !s.error).sort((a: any, b: any) => (b[per] ?? -9) - (a[per] ?? -9)).map((s: any) => (
          <div key={s.etf} className="rounded-lg p-2" style={{ background: divColor(s[per], lim) }} title={`${s.etf} · ${s.source}`}>
            <div className="text-xs font-medium truncate">{s.sector}</div>
            <div className="flex justify-between text-xs num"><span className="opacity-70">{s.etf}</span><span className="font-semibold">{pct(s[per], 1)}</span></div>
          </div>
        ))}
      </div>
      <Heatmap items={data.stocks} valueKey={["1D", "1W", "1M"].includes(per) ? per : "1M"} groupKey="sector" onClick={(it) => nav(`/stock/${it.ticker}`)} />
      {!["1D", "1W", "1M"].includes(per) && <div className="text-[11px] text-muted mt-1">Stock tiles show 1M for longer periods.</div>}
    </Card>
  );
}

function MoversCard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["movers"], queryFn: () => api("/market/movers"), staleTime: 300_000 });
  const [tab, setTab] = useState<"gainers" | "losers" | "unusual">("gainers");
  if (isLoading) return <Card title="Movers"><Loading rows={8} /></Card>;
  if (error) return <Card title="Movers"><ErrorBox error={error} /></Card>;
  const rows = data[tab];
  return (
    <Card title="Biggest moves & unusual activity" sub={`Last completed session: ${data.as_of}`}
          right={<Segmented value={tab} onChange={setTab as any} options={[{ id: "gainers", label: "Gainers" }, { id: "losers", label: "Losers" }, { id: "unusual", label: "Unusual" }]} />}>
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full">
          <thead><tr><th className="th">Ticker</th><th className="th text-right">Price</th><th className="th text-right">1D</th><th className="th text-right">Rel. vol</th><th className="th">{tab === "unusual" ? "Why" : "Sector"}</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={5} className="td text-muted">Nothing unusual in the last session.</td></tr>}
            {rows.map((r: any) => (
              <tr key={r.ticker} className="border-t border-line hover:bg-panel2">
                <td className="td"><Link to={`/stock/${r.ticker}`} className="font-semibold hover:text-accent">{r.ticker}</Link></td>
                <td className="td text-right">{num(r.price)}</td>
                <td className="td text-right"><Delta v={r.change} /></td>
                <td className="td text-right">{isNum(r.rvol) ? `${r.rvol.toFixed(1)}x` : "—"}</td>
                <td className="td text-xs text-muted">{tab === "unusual" ? r.reasons.join("; ") : r.sector}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[11px] text-muted mt-2">{data.note}</div>
    </Card>
  );
}

function Calendars() {
  const [kind, setKind] = useState<"earnings" | "economic">("earnings");
  const [uni, setUni] = useState(true);
  const e = useQuery({ queryKey: ["earncal", uni], queryFn: () => api(`/market/calendar/earnings?days=14&universe_only=${uni}`), enabled: kind === "earnings", staleTime: 3600_000 });
  const ec = useQuery({ queryKey: ["econcal"], queryFn: () => api("/market/calendar/economic?days=10"), enabled: kind === "economic", staleTime: 3600_000 });
  const q = kind === "earnings" ? e : ec;
  return (
    <Card title="Calendar" right={<Segmented value={kind} onChange={setKind as any} options={[{ id: "earnings", label: "Earnings" }, { id: "economic", label: "Economic (US)" }]} />}>
      {kind === "earnings" && (
        <label className="flex items-center gap-2 text-xs text-muted mb-2"><input type="checkbox" checked={uni} onChange={(ev) => setUni(ev.target.checked)} /> Universe stocks only</label>
      )}
      {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorBox error={q.error} /> : (
        <div className="max-h-[420px] overflow-auto scrollbar-thin">
          <table className="w-full">
            <tbody>
              {q.data.rows.length === 0 && <tr><td className="td text-muted">No events.</td></tr>}
              {kind === "earnings" ? q.data.rows.map((r: any, i: number) => (
                <tr key={i} className="border-t border-line">
                  <td className="td text-xs text-muted">{r.date}</td>
                  <td className="td">{r.in_universe ? <Link className="font-semibold hover:text-accent" to={`/stock/${r.ticker}`}>{r.ticker}</Link> : <span className="font-medium">{r.ticker}</span>}</td>
                  <td className="td text-xs truncate max-w-[160px]">{r.name}</td>
                  <td className="td text-xs">{r.time}</td>
                  <td className="td text-xs text-right" title="Consensus EPS">{isNum(r.eps_forecast) ? `EPS est ${num(r.eps_forecast)}` : ""}</td>
                </tr>
              )) : q.data.rows.map((r: any, i: number) => (
                <tr key={i} className="border-t border-line">
                  <td className="td text-xs text-muted">{r.date} {r.time_gmt}</td>
                  <td className="td text-xs whitespace-normal">{r.event}</td>
                  <td className="td text-xs text-right">{r.actual || "—"} <span className="text-muted">/ cons {r.consensus || "—"} / prev {r.previous || "—"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.data && <Source source={q.data.source} at={q.data.fetched_at} stale={q.data.stale} />}
    </Card>
  );
}

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["overview"], queryFn: () => api("/market/overview"), refetchInterval: 60_000 });
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Market intelligence</h1>
          <p className="text-xs text-muted">Indices, volatility, rates, sentiment and activity. Every value shows its source and time.</p>
        </div>
      </div>
      {isLoading && <div className="grid grid-cols-2 md:grid-cols-5 gap-3">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="card p-3"><Loading rows={3} /></div>)}</div>}
      {error && <ErrorBox error={error} />}
      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">{data.indices.map((it: any) => <Instrument key={it.ticker} it={it} />)}</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[...data.volatility, ...data.cross_asset].map((it: any) => <Instrument key={it.ticker} it={it} />)}
          </div>
        </>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 space-y-4"><SectorsCard /><MoversCard /></div>
        <div className="space-y-4"><RegimeCard /><MacroCard /><Calendars /></div>
      </div>
    </div>
  );
}
