import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Delta, ErrorBox, Loading, Note, ScoreBar, Segmented, Tip } from "../components/ui";
import { api } from "../lib/api";
import { ago, compact, isNum, num, pct } from "../lib/format";

const CATS = [
  ["composite", "Top composite"], ["undervalued", "Undervalued"], ["growth", "Growth"], ["momentum", "Momentum"],
  ["breakout", "Breakout"], ["oversold", "Oversold"], ["reversal", "Reversal"], ["earnings", "Earnings"],
  ["high_risk_high_reward", "High risk / reward"], ["overvalued_risk", "Overvalued / risk"],
] as const;

export default function Discover() {
  const qc = useQueryClient();
  const [cat, setCat] = useState("composite");
  const [horizon, setHorizon] = useState("medium");
  const [risk, setRisk] = useState<string[]>([]);
  const [sector, setSector] = useState("");
  const [cap, setCap] = useState("");
  const [minRet, setMinRet] = useState("");
  const qs = new URLSearchParams({ category: cat, horizon, risk: risk.join(","), sector, cap, limit: "80" });
  if (minRet !== "") qs.set("min_return", String(Number(minRet) / 100));
  const { data, isLoading, error, isFetching } = useQuery({ queryKey: ["discovery", qs.toString()], queryFn: () => api(`/discovery?${qs}`), staleTime: 120_000 });
  const toggleRisk = (r: string) => setRisk((x) => (x.includes(r) ? x.filter((y) => y !== r) : [...x, r]));
  const fh = data?.forecast_horizon;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Stock discovery engine</h1>
          <p className="text-xs text-muted">Multi-factor scores (0-100 = percentile within the universe). Scores rank; they do not promise returns.</p>
        </div>
        <button className="btn" onClick={async () => { await api("/discovery/rebuild", { method: "POST" }); setTimeout(() => qc.invalidateQueries({ queryKey: ["discovery"] }), 4000); }}>
          <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> Rebuild scan
        </button>
      </div>
      <div className="flex gap-1.5 overflow-x-auto scrollbar-thin pb-1">
        {CATS.map(([id, label]) => (
          <button key={id} onClick={() => setCat(id)} className={`chip whitespace-nowrap py-1 ${cat === id ? "bg-accent text-white border-transparent" : "hover:bg-panel2"}`}>{label}</button>
        ))}
      </div>
      <Card pad>
        <div className="flex flex-wrap gap-4 items-end">
          <div><span className="label">Time horizon</span><Segmented value={horizon} onChange={setHorizon} options={[{ id: "short", label: "Short (30D)" }, { id: "medium", label: "Medium (90D)" }, { id: "long", label: "Long (12M)" }]} /></div>
          <div><span className="label">Risk level (63d vol)</span>
            <div className="flex gap-1">{["low", "medium", "high"].map((r) => <button key={r} onClick={() => toggleRisk(r)} className={`chip py-1 ${risk.includes(r) ? "bg-panel2 text-fg border-accent" : ""}`}>{r}</button>)}</div>
          </div>
          <div><label className="label" htmlFor="sec">Sector</label>
            <select id="sec" className="input py-1.5" value={sector} onChange={(e) => setSector(e.target.value)}>
              <option value="">All sectors</option>{(data?.sectors || []).map((s: string) => <option key={s}>{s}</option>)}
            </select>
          </div>
          <div><label className="label" htmlFor="cap">Market cap</label>
            <select id="cap" className="input py-1.5" value={cap} onChange={(e) => setCap(e.target.value)}>
              <option value="">All</option><option value="mega">Mega (&gt;$200B)</option><option value="large">Large ($10-200B)</option><option value="mid">Mid ($2-10B)</option><option value="small">Small</option>
            </select>
          </div>
          <div className="w-36"><label className="label" htmlFor="mr">Min. model exp. return ({fh || "—"}) %</label>
            <input id="mr" className="input py-1.5" type="number" step="0.5" value={minRet} onChange={(e) => setMinRet(e.target.value)} placeholder="any" />
          </div>
        </div>
      </Card>
      {data?.forecast_skill && fh && (
        <Note tone="warn">Forecast inputs ({fh}): {data.forecast_skill[fh]} The composite only uses the forecast factor where walk-forward validation shows skill; otherwise its weight is set to zero.</Note>
      )}
      {isLoading && <Card><Loading rows={10} /><div className="text-xs text-muted mt-2">First scan of ~105 stocks can take a minute…</div></Card>}
      {error && <ErrorBox error={error} />}
      {data && (
        <Card title={`${data.n} matches`} sub={<>Scan generated {ago(data.generated_at)} · universe {data.n_universe} stocks</>} pad={false}>
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full">
              <thead className="sticky top-0 bg-panel"><tr>
                <th className="th">Ticker</th><th className="th">Score</th><th className="th text-right">Price</th><th className="th text-right">1D</th><th className="th text-right">3M</th>
                <th className="th">Value</th><th className="th">Growth</th><th className="th">Quality</th><th className="th">Momentum</th>
                <th className="th text-right">Model E[r] {fh}<Tip text="Ensemble expected return for the forecast horizon. See Backtesting for its (lack of) validated skill." /></th>
                <th className="th text-right">P(up)</th><th className="th">Risk</th><th className="th">Why</th>
              </tr></thead>
              <tbody>
                {data.rows.map((r: any) => (
                  <tr key={r.ticker} className="border-t border-line hover:bg-panel2 align-top">
                    <td className="td"><Link to={`/stock/${r.ticker}`} className="font-semibold hover:text-accent">{r.ticker}</Link><div className="text-[11px] text-muted truncate max-w-[130px]">{r.name}</div></td>
                    <td className="td"><ScoreBar value={r.score} /></td>
                    <td className="td text-right">{num(r.price)}</td>
                    <td className="td text-right"><Delta v={r.ret_1d} /></td>
                    <td className="td text-right"><Delta v={r.ret_3m} d={1} /></td>
                    {["value", "growth", "quality", "momentum"].map((f) => <td key={f} className="td"><ScoreBar value={r.factors[f]} /></td>)}
                    <td className="td text-right"><Delta v={r.forecast?.exp_ret} d={1} /></td>
                    <td className="td text-right">{isNum(r.forecast?.p_up) ? pct(r.forecast.p_up, 0, false) : "—"}</td>
                    <td className="td"><Badge tone={r.risk_level === "high" ? "down" : r.risk_level === "low" ? "up" : "warn"}>{r.risk_level}</Badge><div className="text-[11px] text-muted">{compact(r.market_cap, "$")}</div></td>
                    <td className="td text-xs text-muted whitespace-normal min-w-[220px]">{r.why.join(" · ")}{r.earnings_next && <div className="text-accent">Earnings {r.earnings_next.date} ({r.earnings_next.time})</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {data?.methodology && (
        <Card title="Methodology (fixed, documented weights)">
          <div className="grid md:grid-cols-2 gap-4 text-xs">
            <div><div className="font-medium mb-1">Factor scores</div>{Object.entries(data.methodology.factor_scores).map(([k, v]) => <p key={k} className="mb-1"><b>{k}</b>: <span className="text-muted">{v as string}</span></p>)}</div>
            <div><div className="font-medium mb-1">Categories</div>{Object.entries(data.methodology.categories).map(([k, v]) => <p key={k} className="mb-1"><b>{k}</b>: <span className="text-muted">{v as string}</span></p>)}
              <div className="font-medium mt-2 mb-1">Composite by horizon</div>{Object.entries(data.methodology.composite).map(([k, v]) => <p key={k} className="mb-1"><b>{k}</b>: <span className="text-muted">{v as string}</span></p>)}</div>
          </div>
          <ul className="list-disc pl-5 text-xs text-muted mt-2">{data.methodology.caveats.map((c: string) => <li key={c}>{c}</li>)}</ul>
        </Card>
      )}
    </div>
  );
}
