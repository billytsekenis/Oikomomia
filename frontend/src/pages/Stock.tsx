import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CandleChart, DistChart, FanChart, MultiLine, SimpleBars } from "../components/charts";
import { Badge, Card, Delta, ErrorBox, Loading, Note, Segmented, SkillBadge, Source, Stat, Tabs, Tip } from "../components/ui";
import { api } from "../lib/api";
import { ago, compact, isNum, money, num, pct, signClass } from "../lib/format";

type Tab = "overview" | "chart" | "fundamentals" | "forecast" | "news" | "peers" | "ownership";

export default function Stock() {
  const { ticker = "" } = useParams();
  const t = ticker.toUpperCase();
  const [tab, setTab] = useState<Tab>("overview");
  const s = useQuery({ queryKey: ["summary", t], queryFn: () => api(`/stocks/${t}/summary`), refetchInterval: 60_000 });
  const wl = useMutation({
    mutationFn: async () => {
      const lists = await api("/watchlists?quotes=false");
      return api(`/watchlists/${lists[0].id}/items`, { body: { ticker: t } });
    },
  });
  if (s.isLoading) return <Loading rows={10} />;
  if (s.error) return <ErrorBox error={s.error} title={`No data for ${t}`} />;
  const d = s.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold tracking-tight">{t}</h1>
            <span className="text-sm text-muted truncate">{d.name}</span>
            {d.sector && <Badge>{d.sector}</Badge>}
            {!d.in_universe && <Badge tone="warn">outside scored universe</Badge>}
          </div>
          <div className="flex items-baseline gap-3 mt-1">
            <span className="text-3xl font-semibold num">{num(d.price)}</span>
            <span className={clsx("text-lg num", signClass(d.change_1d))}>{pct(d.change_1d, 2)}</span>
            {d.quote?.market_status && <span className="text-xs text-muted">{d.quote.market_status}</span>}
          </div>
          <Source source={d.source} at={d.as_of} extra={d.provenance?.provisional_last_bar ? <span className="text-warn">· last daily bar provisional</span> : null} />
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => wl.mutate()} disabled={wl.isPending}><Plus size={14} />{wl.isSuccess ? "Added" : "Watchlist"}</button>
          <Link className="btn" to={`/paper?ticker=${t}`}>Paper trade</Link>
          <Link className="btn-primary" to={`/analyst?ticker=${t}`}><Bot size={14} />Ask analyst</Link>
        </div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "overview", label: "Overview" }, { id: "chart", label: "Chart & technicals" }, { id: "fundamentals", label: "Fundamentals" },
        { id: "forecast", label: "Forecast" }, { id: "news", label: "News & sentiment" }, { id: "peers", label: "Competitors" },
        { id: "ownership", label: "Ownership & catalysts" },
      ]} />
      {tab === "overview" && <Overview d={d} t={t} go={setTab} />}
      {tab === "chart" && <ChartTab t={t} d={d} />}
      {tab === "fundamentals" && <Fundamentals t={t} />}
      {tab === "forecast" && <ForecastTab t={t} />}
      {tab === "news" && <News t={t} />}
      {tab === "peers" && <Peers t={t} />}
      {tab === "ownership" && <Ownership t={t} />}
    </div>
  );
}

function Overview({ d, t, go }: { d: any; t: string; go: (t: Tab) => void }) {
  const tech = d.technicals, fu = d.fundamentals || {}, val = d.valuation;
  const fc = useQuery({ queryKey: ["forecast", t], queryFn: () => api(`/stocks/${t}/forecast`), staleTime: 600_000 });
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card title="Price & risk" className="lg:col-span-2">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="1 week" value={pct(tech.ret_1w)} />
          <Stat label="1 month" value={pct(tech.ret_1m)} />
          <Stat label="3 months" value={pct(tech.ret_3m)} />
          <Stat label="1 year" value={pct(tech.ret_1y)} />
          <Stat label="RSI(14)" value={num(tech.rsi14, 1)} hint="Wilder RSI. >70 overbought, <30 oversold." />
          <Stat label="vs 200-day SMA" value={pct(tech.dist_sma200)} />
          <Stat label="Volatility (63d, ann.)" value={pct(tech.vol63, 0, false)} />
          <Stat label="Max drawdown 1y" value={pct(tech.max_dd_1y, 1)} />
          <Stat label="Beta (1y)" value={num(d.beta, 2)} hint={d.beta_note} />
          <Stat label="Beta adj. (3y Blume)" value={num(d.beta_adjusted, 2)} hint={d.beta_note} />
          <Stat label="52w range" value={`${num(tech.lo52, 0)}–${num(tech.hi52, 0)}`} />
          <Stat label="ATR(14)" value={`${num(tech.atr14)} (${pct(tech.atr_pct, 1, false)})`} />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {tech.support.map((p: number) => <Badge key={p} tone="up">Support {money(p)}</Badge>)}
          {tech.resistance.map((p: number) => <Badge key={p} tone="down">Resistance {money(p)}</Badge>)}
        </div>
        <div className="mt-2"><Source source={d.provenance?.source} at={d.provenance?.fetched_at} extra={<span>· {d.provenance?.adjusted}</span>} /></div>
      </Card>
      <Card title="Valuation snapshot" right={<button className="text-xs text-accent" onClick={() => go("fundamentals")}>Details</button>}>
        {d.fundamentals ? (
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Market cap" value={compact(fu.market_cap, "$")} />
            <Stat label="P/E (TTM)" value={num(fu.pe_ttm, 1)} />
            <Stat label="Forward P/E" value={num(fu.forward_pe, 1)} hint="Price / next-FY consensus EPS (Nasdaq)." />
            <Stat label="PEG (fwd)" value={num(fu.peg_forward, 2)} />
            <Stat label="EV/EBITDA" value={num(fu.ev_ebitda, 1)} />
            <Stat label="FCF yield" value={pct(fu.fcf_yield, 1, false)} />
            <Stat label="Revenue growth" value={pct(fu.revenue_growth_yoy)} />
            <Stat label="Net margin" value={pct(fu.net_margin, 1, false)} />
          </div>
        ) : <Note>No SEC XBRL financials for this symbol (ETF, index or foreign filer).</Note>}
      </Card>
      <Card title="What is priced in?" sub="Reverse DCF on trailing free cash flow">
        {val?.available ? (
          <>
            <div className="text-2xl font-semibold num">{pct(val.implied_fcf_growth_10y, 1, false)}<span className="text-sm text-muted font-normal"> / yr FCF growth for 10y</span></div>
            <div className="text-xs text-muted mt-1">WACC {pct(val.wacc, 1, false)} (rf {pct(val.risk_free, 2, false)}, beta {num(val.beta, 2)}, ERP {pct(val.erp, 0, false)}), terminal growth {pct(val.terminal_growth, 1, false)}</div>
            <div className="text-xs mt-2">3-year revenue CAGR: <b>{pct(val.historical_revenue_cagr_3y, 1, false)}</b></div>
            {val.interpretation && <p className="text-xs mt-2">{val.interpretation}</p>}
          </>
        ) : <Note>{val?.reason || "Not available."}</Note>}
      </Card>
      <Card title="Model outlook" className="lg:col-span-2" right={<button className="text-xs text-accent" onClick={() => go("forecast")}>Full forecast</button>}>
        {fc.isLoading ? <Loading rows={3} /> : fc.error ? <ErrorBox error={fc.error} /> : fc.data?.available ? (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full"><thead><tr><th className="th">Horizon</th><th className="th text-right">Median</th><th className="th text-right">P(up)</th><th className="th text-right">80% range</th><th className="th">Validated skill</th></tr></thead>
              <tbody>{fc.data.horizons.map((h: any) => (
                <tr key={h.horizon} className="border-t border-line"><td className="td">{h.label}</td><td className="td text-right"><Delta v={h.median_return} d={1} /></td>
                  <td className="td text-right">{pct(h.p_up, 0, false)}</td><td className="td text-right">{money(h.price_range_80[0])} – {money(h.price_range_80[1])}</td><td className="td"><SkillBadge skill={h.skill} /></td></tr>
              ))}</tbody></table>
          </div>
        ) : <Note>{fc.data?.reason}</Note>}
      </Card>
    </div>
  );
}

function ChartTab({ t, d }: { t: string; d: any }) {
  const [range, setRange] = useState("1y");
  const [ov, setOv] = useState<Record<string, boolean>>({ sma50: true, sma200: true, sma20: false, ema21: false, bb: false, levels: true });
  const [panes, setPanes] = useState<Record<string, boolean>>({ rsi: true, macd: true });
  const h = useQuery({ queryKey: ["history", t, range], queryFn: () => api(`/stocks/${t}/history?range=${range}`) });
  const tech = d.technicals;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
      <Card className="xl:col-span-3" title="Interactive chart" right={<Segmented value={range} onChange={setRange} options={["1m", "3m", "6m", "1y", "2y", "5y", "max"].map((r) => ({ id: r, label: r.toUpperCase() }))} />}>
        <div className="flex flex-wrap gap-1.5 mb-2">
          {([["sma20", "SMA 20"], ["sma50", "SMA 50"], ["sma200", "SMA 200"], ["ema21", "EMA 21"], ["bb", "Bollinger 20,2"], ["levels", "Support/resistance"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setOv({ ...ov, [k]: !ov[k] })} className={clsx("chip py-1", ov[k] && "border-accent text-fg bg-panel2")}>{l}</button>
          ))}
          {(["rsi", "macd"] as const).map((k) => <button key={k} onClick={() => setPanes({ ...panes, [k]: !panes[k] })} className={clsx("chip py-1", panes[k] && "border-accent text-fg bg-panel2")}>{k.toUpperCase()}</button>)}
        </div>
        {h.isLoading ? <Loading rows={12} /> : h.error ? <ErrorBox error={h.error} /> : <CandleChart bars={h.data.bars} overlays={ov} panes={panes} levels={h.data.levels} />}
        {h.data && <div className="mt-2 text-[11px] text-muted">{h.data.note} · {h.data.levels?.method}</div>}
      </Card>
      <div className="space-y-4">
        <Card title="Technical read">
          <div className="space-y-2 text-sm">
            <Row k="RSI(14)" v={num(tech.rsi14, 1)} note={tech.rsi14 > 70 ? "overbought" : tech.rsi14 < 30 ? "oversold" : "neutral"} />
            <Row k="MACD hist" v={num(tech.macd_hist, 3)} note={tech.macd_hist > 0 ? "above signal" : "below signal"} />
            <Row k="vs SMA 50" v={pct(tech.dist_sma50)} />
            <Row k="vs SMA 200" v={pct(tech.dist_sma200)} note={tech.dist_sma200 > 0 ? "uptrend" : "downtrend"} />
            <Row k="Bollinger %B" v={num(tech.bb_pctb, 2)} />
            <Row k="Relative volume" v={isNum(tech.rvol20) ? `${tech.rvol20.toFixed(2)}x` : "—"} note={tech.rvol20 >= 2 ? "unusual" : ""} />
            <Row k="ATR(14)" v={money(tech.atr14)} />
          </div>
        </Card>
        <Card title="Volume analysis">
          {h.data && <SimpleBars height={160} data={h.data.bars.slice(-30).map((b: any) => ({ date: b.date.slice(5), v: b.volume / 1e6 }))} x="date" y="v" fmt={(v) => `${v.toFixed(0)}M`} signed={false} />}
          <div className="text-[11px] text-muted">Last 30 sessions, millions of shares.</div>
        </Card>
      </div>
    </div>
  );
}

function Row({ k, v, note }: { k: string; v: string; note?: string }) {
  return <div className="flex justify-between"><span className="text-muted">{k}</span><span className="num">{v}{note && <span className="text-xs text-muted"> · {note}</span>}</span></div>;
}

function Fundamentals({ t }: { t: string }) {
  const f = useQuery({ queryKey: ["fund", t], queryFn: () => api(`/stocks/${t}/fundamentals`) });
  const v = useQuery({ queryKey: ["val", t], queryFn: () => api(`/stocks/${t}/valuation`) });
  if (f.isLoading) return <Loading rows={10} />;
  if (f.error) return <ErrorBox error={f.error} />;
  const s = f.data.snapshot || {};
  const q = f.data.quarterly;
  const M = (x: any) => compact(x, "$");
  const metrics: [string, string, string?][] = [
    ["Revenue (TTM)", M(s.revenue_ttm)], ["Gross profit (TTM)", M(s.gross_profit_ttm)], ["Operating income (TTM)", M(s.operating_income_ttm)],
    ["Net income (TTM)", M(s.net_income_ttm)], ["EBITDA (TTM)", M(s.ebitda_ttm)], ["Free cash flow (TTM)", M(s.fcf_ttm), f.data.definitions.fcf_ttm],
    ["Total debt", M(s.total_debt)], ["Cash + ST investments", M((s.cash || 0) + (s.short_term_investments || 0))], ["Net debt", M(s.net_debt)],
    ["EPS (TTM, diluted)", num(s.eps_ttm)], ["Market cap", M(s.market_cap), s.market_cap_method], ["Enterprise value", M(s.enterprise_value)],
    ["P/E (TTM)", num(s.pe_ttm, 1), f.data.definitions.pe_ttm], ["Forward P/E", num(s.forward_pe, 1), f.data.definitions.forward_pe],
    ["PEG (forward)", num(s.peg_forward, 2), f.data.definitions.peg_forward], ["PEG (trailing)", num(s.peg_trailing, 2), f.data.definitions.peg_trailing],
    ["EV/EBITDA", num(s.ev_ebitda, 1), f.data.definitions.ev_ebitda], ["EV/Sales", num(s.ev_sales, 2)], ["P/S", num(s.ps_ttm, 2)], ["P/B", num(s.pb, 2)],
    ["P/FCF", num(s.p_fcf, 1)], ["FCF yield", pct(s.fcf_yield, 1, false)], ["Earnings yield", pct(s.earnings_yield, 1, false)],
    ["Gross margin", pct(s.gross_margin, 1, false)], ["Operating margin", pct(s.operating_margin, 1, false)], ["Net margin", pct(s.net_margin, 1, false)],
    ["ROE", pct(s.roe, 1, false), f.data.definitions.roe], ["ROA", pct(s.roa, 1, false)], ["Debt / equity", num(s.debt_to_equity, 2)],
    ["Net debt / EBITDA", num(s.net_debt_to_ebitda, 2)], ["Interest coverage", num(s.interest_coverage, 1)],
    ["Revenue growth YoY", pct(s.revenue_growth_yoy)], ["Net income growth YoY", pct(s.net_income_growth_yoy)], ["3y revenue CAGR", pct(s.revenue_cagr_3y)],
    ["Consensus EPS growth (next FY)", pct(s.eps_growth_forward)],
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="Key metrics" className="lg:col-span-2" sub={`Latest period ${s.period_end || "—"} · balance sheet ${s.balance_sheet_date || "—"}`}>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2">
            {metrics.map(([k, val, tip]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-line/60 py-1 text-sm"><span className="text-muted text-xs flex items-center gap-1">{k}{tip && <Tip text={tip} />}</span><span className="num">{val}</span></div>
            ))}
          </div>
          {(s.notes || []).map((n: string) => <div key={n} className="mt-2"><Note>{n}</Note></div>)}
          <div className="mt-2"><Source source={f.data.sources.statements} at={f.data.sources.statements_fetched_at} stale={f.data.sources.stale} /></div>
        </Card>
        <Card title="Market expectations">
          {v.data ? (
            <div className="space-y-2 text-sm">
              <Row k="Analyst 1y target" v={money(v.data.analyst_1y_target)} />
              <Row k="Implied upside" v={pct(v.data.upside_to_target)} />
              <Row k="Implied FCF growth (rev. DCF)" v={pct(v.data.reverse_dcf?.implied_fcf_growth_10y, 1, false)} />
              <Row k="Consensus EPS growth" v={pct(v.data.forward_eps_growth_implied_by_consensus)} />
              {v.data.reverse_dcf?.sensitivity && (
                <div className="mt-2">
                  <div className="text-xs text-muted mb-1">Implied growth sensitivity (rows WACC, cols terminal g)</div>
                  <table className="w-full text-xs num"><thead><tr><th />{v.data.reverse_dcf.sensitivity.terminal_growth.map((g: number) => <th key={g} className="text-muted font-normal">{pct(g, 1, false)}</th>)}</tr></thead>
                    <tbody>{v.data.reverse_dcf.sensitivity.rows.map((r: any) => <tr key={r.wacc}><td className="text-muted">{pct(r.wacc, 1, false)}</td>{r.values.map((x: number, i: number) => <td key={i} className="text-center">{pct(x, 1, false)}</td>)}</tr>)}</tbody></table>
                </div>
              )}
              {v.data.note && <p className="text-xs text-muted">{v.data.note}</p>}
              <Source source={v.data.source_target || "SEC + model"} />
            </div>
          ) : <Loading rows={4} />}
        </Card>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Revenue & net income (quarterly)"><MultiLine data={q.map((r: any) => ({ ...r, revenue: r.revenue / 1e9, net_income: r.net_income / 1e9, fcf: isNum(r.fcf) ? r.fcf / 1e9 : null }))} x="period_end" yFmt={(v) => `$${v.toFixed(1)}B`} series={[{ key: "revenue", name: "Revenue" }, { key: "net_income", name: "Net income" }, { key: "fcf", name: "Free cash flow" }]} /></Card>
        <Card title="Quarterly statements (SEC XBRL)" pad={false}>
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full"><thead><tr>{["Quarter", "Revenue", "Op. inc.", "Net inc.", "EBITDA", "FCF", "EPS", "LT debt"].map((h) => <th key={h} className="th text-right first:text-left">{h}</th>)}</tr></thead>
              <tbody>{[...q].reverse().map((r: any) => (
                <tr key={r.period_end} className="border-t border-line"><td className="td">{r.period_end}</td>{["revenue", "operating_income", "net_income", "ebitda", "fcf"].map((k) => <td key={k} className="td text-right">{compact(r[k])}</td>)}<td className="td text-right">{num(r.eps_diluted)}</td><td className="td text-right">{compact(r.debt)}</td></tr>
              ))}</tbody></table>
          </div>
        </Card>
      </div>
      {f.data.earnings_surprises?.length > 0 && (
        <Card title="Earnings surprises" sub="Reported vs consensus EPS (Nasdaq)">
          <SimpleBars data={f.data.earnings_surprises.slice().reverse().map((e: any) => ({ q: e.fiscal_quarter, s: (e.surprise_pct || 0) / 100 }))} x="q" y="s" height={160} />
        </Card>
      )}
    </div>
  );
}

function ForecastTab({ t }: { t: string }) {
  const fc = useQuery({ queryKey: ["forecast", t], queryFn: () => api(`/stocks/${t}/forecast`), staleTime: 600_000 });
  const hist = useQuery({ queryKey: ["history", t, "6m"], queryFn: () => api(`/stocks/${t}/history?range=6m`) });
  const [sel, setSel] = useState("30D");
  const [dist, setDist] = useState("30D");
  if (fc.isLoading) return <Card><Loading rows={8} /><div className="text-xs text-muted mt-2">Running Monte Carlo simulation…</div></Card>;
  if (fc.error) return <ErrorBox error={fc.error} />;
  const f = fc.data;
  if (!f.available) return <Note tone="warn">{f.reason}</Note>;
  const h = f.horizons.find((x: any) => x.horizon === sel) || f.horizons[2];
  return (
    <div className="space-y-4">
      {f.notes.map((n: string) => <Note key={n} tone={n.startsWith("DEMO") || n.includes("unavailable") ? "warn" : "info"}>{n}</Note>)}
      <Card title="Forecasts by horizon" sub={`As of close ${f.as_of} at ${money(f.price)} · model ${f.model_version} (trained ${ago(f.model_trained_at)}) · ${f.volatility_model.kind}`} pad={false}>
        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full">
            <thead><tr>{["Horizon", "Target date", "Expected", "Median", "P(up)", "P(down)", "80% price range", "90% range", "VaR 95%", "CVaR 95%", "P(-10% DD)", "Omega", "Skill"].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
            <tbody>{f.horizons.map((x: any) => (
              <tr key={x.horizon} onClick={() => setSel(x.horizon)} className={clsx("border-t border-line cursor-pointer hover:bg-panel2", sel === x.horizon && "bg-panel2")}>
                <td className="td font-medium">{x.label}</td><td className="td text-xs text-muted">{x.target_date}</td>
                <td className="td"><Delta v={x.expected_return} d={1} /></td><td className="td"><Delta v={x.median_return} d={1} /></td>
                <td className="td">{pct(x.p_up, 0, false)}</td><td className="td">{pct(x.p_down, 0, false)}</td>
                <td className="td">{money(x.price_range_80[0])} – {money(x.price_range_80[1])}</td>
                <td className="td text-muted">{money(x.price_range_90[0])} – {money(x.price_range_90[1])}</td>
                <td className="td text-down">{pct(x.potential_downside.var95, 1)}</td><td className="td text-down">{pct(x.potential_downside.cvar95, 1)}</td>
                <td className="td">{pct(x.potential_downside.p_drawdown_10pct, 0, false)}</td>
                <td className="td">{num(x.risk_reward.omega_ratio, 2)}</td><td className="td"><SkillBadge skill={x.skill} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card title="Forecast fan chart" sub="Median path with 50% and 90% uncertainty bands (Monte Carlo)" className="xl:col-span-2">
          <FanChart fan={f.fan} price={f.price} history={hist.data?.bars} />
        </Card>
        <Card title="Probability distribution" right={<Segmented value={dist} onChange={setDist} options={Object.keys(f.distributions).map((k) => ({ id: k, label: k }))} />}>
          <DistChart data={f.distributions[dist]} />
          <div className="text-[11px] text-muted">Simulated return distribution; green bars = gains.</div>
        </Card>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title={`Scenarios · ${h.label}`}>
          {(["bull", "base", "bear"] as const).map((k) => (
            <div key={k} className="flex justify-between py-1.5 border-b border-line last:border-0 text-sm">
              <span className="capitalize">{k} <span className="text-[11px] text-muted">({h.scenarios[k].definition})</span></span>
              <span className="num">{money(h.scenarios[k].price)} <span className={signClass(h.scenarios[k].return)}>{pct(h.scenarios[k].return, 1)}</span></span>
            </div>
          ))}
          <div className="text-xs text-muted mt-2">Risk/reward (Omega at 0%): expected upside {pct(h.risk_reward.expected_upside, 1, false)} vs downside {pct(h.risk_reward.expected_downside, 1, false)}</div>
        </Card>
        <Card title="Model components & uncertainty">
          {Object.entries(h.components).map(([k, v]: any) => <Row key={k} k={k.replace("_", " ")} v={pct(Math.exp(v) - 1, 2)} />)}
          <Row k="ML weight (OOS encompassing)" v={num(h.ml_weight, 2)} />
          <Row k="Model disagreement (σ of components)" v={pct(h.uncertainty.model_disagreement, 2, false)} />
          <Row k="90% interval width" v={pct(h.uncertainty.interval_90_width, 1, false)} />
          <Row k="Horizon σ (GARCH)" v={pct(h.sigma, 1, false)} />
          {h.validation && <div className="text-xs text-muted mt-2">Walk-forward ({h.validation.start} → {h.validation.end}, n={h.validation.n}): Brier skill {num(h.validation.brier_skill_score, 3)}, 80%/90% band coverage {pct(h.validation.coverage_80, 0, false)}/{pct(h.validation.coverage_90, 0, false)}.</div>}
          <div className="text-xs mt-1">{h.skill_detail}</div>
        </Card>
        <Card title="Invalidation conditions">
          <ul className="list-disc pl-4 space-y-1.5 text-xs">{h.invalidation.map((x: string) => <li key={x}>{x}</li>)}</ul>
        </Card>
      </div>
    </div>
  );
}

function News({ t }: { t: string }) {
  const n = useQuery({ queryKey: ["news", t], queryFn: () => api(`/stocks/${t}/news`), staleTime: 300_000 });
  if (n.isLoading) return <Loading rows={8} />;
  if (n.error) return <ErrorBox error={n.error} />;
  const a = n.data.aggregate;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card title="Sentiment">
        <div className="text-3xl font-semibold num">{isNum(a.score) ? a.score.toFixed(2) : "—"}</div>
        <div className="text-sm capitalize">{a.label} · {a.n} headlines (30d)</div>
        <p className="text-xs text-muted mt-2">{n.data.method}</p>
        <Note tone="warn">Sentiment is not part of the forecast numbers: without a point-in-time news archive its predictive value cannot be validated.</Note>
        <div className="mt-2"><Source source={n.data.source} at={n.data.fetched_at} stale={n.data.stale} /></div>
      </Card>
      <Card title="Headlines" className="lg:col-span-2">
        <div className="divide-y divide-line">
          {n.data.items.map((it: any, i: number) => (
            <div key={i} className="py-2">
              <div className="flex items-start justify-between gap-3">
                <a href={it.url} target="_blank" rel="noopener noreferrer" className="text-sm hover:text-accent">{it.title}</a>
                <span className={clsx("text-xs num shrink-0", signClass(it.sentiment))}>{it.sentiment > 0 ? "+" : ""}{it.sentiment.toFixed(2)}</span>
              </div>
              <div className="text-[11px] text-muted">{it.publisher} · {ago(it.published_at)} {it.suspicious && <Badge tone="warn">instruction-like text: excluded from analysis</Badge>}</div>
            </div>
          ))}
          {n.data.items.length === 0 && <div className="text-sm text-muted">No recent headlines.</div>}
        </div>
      </Card>
    </div>
  );
}

function Peers({ t }: { t: string }) {
  const p = useQuery({ queryKey: ["peers", t], queryFn: () => api(`/stocks/${t}/peers`), staleTime: 600_000 });
  if (p.isLoading) return <Loading rows={8} />;
  if (p.error) return <ErrorBox error={p.error} />;
  const med = p.data.sector_median;
  const cols: [string, string, (v: any) => string][] = [["pe_ttm", "P/E", (v) => num(v, 1)], ["forward_pe", "Fwd P/E", (v) => num(v, 1)], ["ev_ebitda", "EV/EBITDA", (v) => num(v, 1)],
    ["revenue_growth", "Rev growth", (v) => pct(v, 1)], ["operating_margin", "Op margin", (v) => pct(v, 1, false)], ["roe", "ROE", (v) => pct(v, 1, false)],
    ["fcf_yield", "FCF yield", (v) => pct(v, 1, false)], ["ret_6m", "6M return", (v) => pct(v, 1)], ["vol63", "Vol", (v) => pct(v, 0, false)]];
  return (
    <Card title={`Competitor analysis · ${p.data.sector}`} sub={p.data.note} pad={false}>
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full">
          <thead><tr><th className="th">Company</th><th className="th text-right">Mkt cap</th>{cols.map(([, l]) => <th key={l} className="th text-right">{l}</th>)}<th className="th text-right">Score (med.)</th></tr></thead>
          <tbody>
            {p.data.peers.map((r: any) => (
              <tr key={r.ticker} className={clsx("border-t border-line", r.ticker === t && "bg-accent/10")}>
                <td className="td"><Link to={`/stock/${r.ticker}`} className="font-semibold hover:text-accent">{r.ticker}</Link> <span className="text-[11px] text-muted">{r.industry}</span></td>
                <td className="td text-right">{compact(r.market_cap, "$")}</td>
                {cols.map(([k, , f]) => <td key={k} className="td text-right">{f(r[k])}</td>)}
                <td className="td text-right">{num(r.composite?.medium?.score, 0)}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-line bg-panel2"><td className="td font-medium">Sector median</td><td className="td" />{cols.map(([k, , f]) => <td key={k} className="td text-right">{f(med[k])}</td>)}<td className="td" /></tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Ownership({ t }: { t: string }) {
  const o = useQuery({ queryKey: ["own", t], queryFn: () => api(`/stocks/${t}/ownership`), staleTime: 3600_000 });
  const c = useQuery({ queryKey: ["cat", t], queryFn: () => api(`/stocks/${t}/catalysts`), staleTime: 3600_000 });
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card title="Institutional & insider activity">
        {o.isLoading ? <Loading rows={6} /> : o.error ? <ErrorBox error={o.error} /> : (
          <>
            {o.data.institutional ? (
              <>
                <div className="grid grid-cols-3 gap-3 mb-3">
                  <Stat label="Institutional ownership" value={`${num(o.data.institutional.institutional_ownership_pct, 1)}%`} />
                  <Stat label="Increased positions" value={compact(o.data.institutional.positions?.["Increased Positions"]?.holders)} />
                  <Stat label="Decreased positions" value={compact(o.data.institutional.positions?.["Decreased Positions"]?.holders)} />
                </div>
                <table className="w-full"><thead><tr><th className="th">Holder</th><th className="th">As of</th><th className="th text-right">Shares</th><th className="th text-right">Change</th></tr></thead>
                  <tbody>{o.data.institutional.top_holders.slice(0, 10).map((h: any) => <tr key={h.owner} className="border-t border-line"><td className="td text-xs truncate max-w-[180px]">{h.owner}</td><td className="td text-xs text-muted">{h.date}</td><td className="td text-right">{compact(h.shares)}</td><td className={clsx("td text-right", signClass(h.change))}>{compact(h.change)}</td></tr>)}</tbody></table>
                <Source source={o.data.institutional_source} />
              </>
            ) : <Note>Institutional ownership data unavailable for this symbol.</Note>}
            <div className="mt-3 text-sm">SEC Form 4 insider filings (90d): <b>{o.data.insider_form4_count_90d ?? "—"}</b></div>
            <div className="text-xs text-muted">{o.data.note}</div>
            <ul className="mt-1 text-xs space-y-0.5">{(o.data.insider_filings || []).slice(0, 6).map((f: any, i: number) => <li key={i}><a className="hover:text-accent" href={f.url} target="_blank" rel="noopener noreferrer">Form {f.form} · {f.filing_date}</a></li>)}</ul>
          </>
        )}
      </Card>
      <Card title="Catalysts">
        {c.isLoading ? <Loading rows={6} /> : c.error ? <ErrorBox error={c.error} /> : (
          <div className="space-y-3 text-sm">
            <div>Next earnings: {c.data.next_earnings ? <b>{c.data.next_earnings.date} ({c.data.next_earnings.time}), consensus EPS {num(c.data.next_earnings.eps_forecast)}</b> : <span className="text-muted">not scheduled within 30 days</span>}</div>
            {isNum(c.data.avg_abs_earnings_move) && <div>Average absolute post-earnings move (last {c.data.past_earnings_reactions.length}): <b className="num">{pct(c.data.avg_abs_earnings_move, 1, false)}</b></div>}
            {c.data.past_earnings_reactions.length > 0 && <SimpleBars height={140} data={c.data.past_earnings_reactions.map((r: any) => ({ d: r.date.slice(0, 7), r: r.largest }))} x="d" y="r" />}
            {c.data.estimate_revisions_4w && <div className="text-xs">EPS estimate revisions (4w, {c.data.estimate_revisions_4w.period}): <span className="text-up">{c.data.estimate_revisions_4w.up} up</span> / <span className="text-down">{c.data.estimate_revisions_4w.down} down</span></div>}
            <div><div className="text-xs text-muted mb-1">Recent 8-K filings</div>
              <ul className="text-xs space-y-0.5">{c.data.recent_8k.map((f: any, i: number) => <li key={i}><a className="hover:text-accent" href={f.url} target="_blank" rel="noopener noreferrer">{f.filing_date} · 8-K items {f.items || "—"}</a></li>)}</ul></div>
            <Source source={c.data.sources.filter(Boolean).join("; ")} />
          </div>
        )}
      </Card>
    </div>
  );
}
