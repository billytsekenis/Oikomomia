import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { CalibrationChart, MultiLine, SimpleBars } from "../components/charts";
import { Badge, Card, ErrorBox, Loading, Note, Segmented, Stat, Tabs, Tip } from "../components/ui";
import { api } from "../lib/api";
import { ago, num, pct } from "../lib/format";

const H = ["1D", "7D", "30D", "90D", "6M", "12M"];

export default function Backtest() {
  const [tab, setTab] = useState<"eval" | "strategy" | "runs">("eval");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Backtesting & validation</h1>
        <p className="text-xs text-muted">Does the prediction engine actually work? Walk-forward, out-of-sample evidence - including the failures.</p>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "eval", label: "Model validation" }, { id: "strategy", label: "Strategy backtest" }, { id: "runs", label: "All runs" }]} />
      {tab === "eval" && <ModelEval />}
      {tab === "strategy" && <StrategyBacktest />}
      {tab === "runs" && <Runs />}
    </div>
  );
}

function ModelEval() {
  const q = useQuery({ queryKey: ["model-eval"], queryFn: () => api("/backtest/model-evaluation") });
  const [h, setH] = useState("30D");
  if (q.isLoading) return <Loading rows={10} />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data.available) return <Note tone="warn">{q.data.reason}</Note>;
  const m = q.data.metrics[h];
  return (
    <div className="space-y-4">
      <Card title="Verdict by horizon" sub={`Model ${q.data.version} · trained ${ago(q.data.trained_at)} on ${q.data.n_tickers} stocks · data to ${q.data.data_end}`} pad={false}>
        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full">
            <thead><tr>{["Horizon", "OOS predictions", "Period", "Base rate up", "Dir. accuracy", "Always-majority", "IC (t-stat)", "Brier skill", "80% / 90% coverage", "ML weight: avg in test / live", "Verdict"].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
            <tbody>{H.filter((x) => q.data.metrics[x]).map((x) => {
              const r = q.data.metrics[x], e = r.models.ensemble;
              return (
                <tr key={x} onClick={() => setH(x)} className={clsx("border-t border-line cursor-pointer hover:bg-panel2", x === h && "bg-panel2")}>
                  <td className="td font-medium">{x}</td><td className="td">{r.n.toLocaleString()}</td><td className="td text-xs text-muted">{r.start} → {r.end}</td>
                  <td className="td">{pct(r.base_rate_up, 1, false)}</td><td className="td">{pct(e.directional_accuracy, 1, false)}</td><td className="td text-muted">{pct(r.always_majority_accuracy, 1, false)}</td>
                  <td className="td">{num(e.ic_mean, 3)} ({num(e.ic_tstat_overlap_adj, 1)})</td>
                  <td className={clsx("td", r.brier_skill_score > 0 ? "text-up" : "text-down")}>{num(r.brier_skill_score, 4)}</td>
                  <td className="td">{pct(r.coverage_80, 1, false)} / {pct(r.coverage_90, 1, false)}</td><td className="td">{num(r.ml_weight_mean, 2)} / <b>{num(q.data.weights?.[x] ?? 0, 2)}</b></td>
                  <td className="td text-xs whitespace-normal min-w-[220px]">{r.verdict}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      </Card>
      <Note>
        How to read this: <b>directional accuracy</b> must beat <b>always-majority</b> (just predicting the more frequent direction) to mean anything.
        <b> Brier skill</b> &gt; 0 means probabilities beat the historical base rate. <b>Coverage</b> close to 80%/90% means the uncertainty bands are honest.
        IC is the average cross-sectional rank correlation between forecasts and outcomes; t-stats are deflated for overlapping horizons.
      </Note>
      {m && (
        <>
          <div className="flex items-center gap-2"><span className="text-sm text-muted">Detail for</span><Segmented value={h} onChange={setH} options={H.filter((x) => q.data.metrics[x]).map((x) => ({ id: x, label: x }))} /></div>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card title="Model comparison (out-of-sample)" pad={false} className="xl:col-span-2">
              <div className="overflow-x-auto scrollbar-thin">
                <table className="w-full"><thead><tr>{["Model", "Dir. accuracy", "MAE", "RMSE", "R² vs zero", "R² vs prior", "IC", "IC t"].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
                  <tbody>{Object.entries(m.models).map(([k, v]: any) => (
                    <tr key={k} className="border-t border-line"><td className="td font-medium">{k}</td><td className="td">{pct(v.directional_accuracy, 1, false)}</td><td className="td">{pct(v.mae, 2, false)}</td><td className="td">{pct(v.rmse, 2, false)}</td>
                      <td className="td">{num(v.r2_vs_zero, 4)}</td><td className="td">{num(v.r2_vs_prior, 4)}</td><td className="td">{num(v.ic_mean, 3)}</td><td className="td">{num(v.ic_tstat_overlap_adj, 2)}</td></tr>
                  ))}</tbody></table>
              </div>
              <div className="p-3 text-xs text-muted">prior = CAPM drift + GARCH; ridge / gbm = ML residual models; ml = their average; ensemble = prior + encompassing weight × (ml − prior), weight estimated only on earlier OOS data.</div>
            </Card>
            <Card title="Probability calibration" sub={`Brier ${num(m.brier, 4)} vs climatology ${num(m.brier_climatology, 4)}`}>
              <CalibrationChart bins={m.calibration} />
              <div className="text-[11px] text-muted">Points on the dashed diagonal = perfectly calibrated. Size = number of predictions. Uncalibrated Brier skill: {num(m.brier_skill_uncalibrated, 4)}.</div>
            </Card>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Card title="Stability by year (every year shown)">
              <SimpleBars data={m.by_year.map((y: any) => ({ year: String(y.year), edge: y.accuracy - Math.max(y.base_rate_up, 1 - y.base_rate_up) }))} x="year" y="edge" height={200} fmt={(v) => pct(v, 1)} />
              <div className="text-[11px] text-muted">Directional accuracy minus always-majority accuracy per year. Negative = worse than a naive rule.</div>
            </Card>
            <Card title="Cross-sectional usefulness">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Top-minus-bottom quintile (per period)" value={pct(m.quintile_spread_mean, 2)} hint="Average h-day return of the top 20% forecasts minus the bottom 20%, per prediction date." />
                <Stat label="Periods with positive spread" value={pct(m.quintile_spread_hit_rate, 0, false)} />
                <Stat label="Mean |return| at horizon" value={pct(m.mean_abs_return, 1, false)} />
                <Stat label="Avg. ML weight" value={num(m.ml_weight_mean, 2)} />
              </div>
            </Card>
          </div>
        </>
      )}
      <Card title="Validation protocol & anti-bias controls">
        <ul className="list-disc pl-5 text-xs space-y-1">{q.data.protocol.map((p: string) => <li key={p}>{p}</li>)}</ul>
        <details className="mt-2"><summary className="text-xs text-muted cursor-pointer">Fixed hyper-parameters</summary><pre className="text-[11px] bg-panel2 rounded p-2 mt-1 overflow-x-auto">{JSON.stringify(q.data.hyperparams, null, 2)}</pre></details>
      </Card>
    </div>
  );
}

function StrategyBacktest() {
  const qc = useQueryClient();
  const [cfg, setCfg] = useState({ horizon: "30D", strategy: "top_n", top_n: 10, threshold: 0.55, rebalance_sessions: 21, cost_bps: 5, slippage_bps: 5, start: "", end: "" });
  const m = useMutation({ mutationFn: () => api("/backtest/run", { body: { ...cfg, start: cfg.start || null, end: cfg.end || null } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["bt-runs"] }) });
  const set = (k: string, num_ = true) => (e: any) => setCfg({ ...cfg, [k]: num_ ? Number(e.target.value) : e.target.value });
  return (
    <div className="space-y-4">
      <Card title="Configure" sub="Signals come only from walk-forward out-of-sample predictions; trades execute at the next session's close.">
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-9 gap-2 items-end">
          <div><label className="label">Signal horizon</label><select className="input" value={cfg.horizon} onChange={set("horizon", false)}>{H.map((x) => <option key={x}>{x}</option>)}</select></div>
          <div className="col-span-2 sm:col-span-1"><label className="label">Strategy</label><select className="input" value={cfg.strategy} onChange={set("strategy", false)}><option value="top_n">Top-N long</option><option value="long_short">Long/short quintiles</option><option value="prob_threshold">P(up) threshold</option></select></div>
          <div><label className="label">Top N</label><input className="input" type="number" value={cfg.top_n} onChange={set("top_n")} /></div>
          <div><label className="label">P(up) threshold</label><input className="input" type="number" step="0.01" value={cfg.threshold} onChange={set("threshold")} /></div>
          <div><label className="label">Rebalance (sessions)</label><input className="input" type="number" value={cfg.rebalance_sessions} onChange={set("rebalance_sessions")} /></div>
          <div><label className="label">Costs (bps)</label><input className="input" type="number" value={cfg.cost_bps} onChange={set("cost_bps")} /></div>
          <div><label className="label">Slippage (bps)</label><input className="input" type="number" value={cfg.slippage_bps} onChange={set("slippage_bps")} /></div>
          <div><label className="label">Start</label><input className="input" type="date" value={cfg.start} onChange={set("start", false)} /></div>
          <div><label className="label">End</label><input className="input" type="date" value={cfg.end} onChange={set("end", false)} /></div>
        </div>
        <button className="btn-primary mt-3" onClick={() => m.mutate()} disabled={m.isPending}>{m.isPending ? "Running backtest…" : "Run backtest"}</button>
        <span className="text-[11px] text-muted ml-3">Every run is stored permanently and listed under "All runs".</span>
      </Card>
      {m.error && <ErrorBox error={m.error} />}
      {m.data && <Result r={m.data} />}
    </div>
  );
}

export function Result({ r }: { r: any }) {
  const s = r.stats, ew = r.benchmarks.equal_weight_universe, spy = r.benchmarks.SPY;
  const rows: [string, string, (v: any) => string][] = [["Total return", "total_return", (v) => pct(v, 1)], ["CAGR", "cagr", (v) => pct(v, 1)], ["Volatility", "volatility", (v) => pct(v, 1, false)],
    ["Sharpe", "sharpe", (v) => num(v, 2)], ["Sortino", "sortino", (v) => num(v, 2)], ["Max drawdown", "max_drawdown", (v) => pct(v, 1)]];
  const beat = s.cagr > ew.cagr;
  return (
    <div className="space-y-4">
      <Note tone={beat ? "info" : "warn"}>
        {beat ? "The strategy beat the equal-weight universe over this period." : "The strategy did NOT beat simply holding the equal-weight universe over this period."} Period {r.period.join(" → ")}.
      </Note>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card title="Cumulative returns" className="xl:col-span-2">
          <MultiLine data={r.equity_curve} yFmt={(v) => v.toFixed(2)} series={[{ key: "strategy", name: "Strategy" }, { key: "equal_weight", name: "Equal-weight universe" }, ...(spy ? [{ key: "SPY", name: "SPY" }] : [])]} />
          <MultiLine data={r.drawdown} height={120} yFmt={(v) => pct(v, 0)} series={[{ key: "dd", name: "Strategy drawdown", color: "rgb(var(--down))" }]} />
        </Card>
        <Card title="Statistics" pad={false}>
          <table className="w-full"><thead><tr><th className="th">Metric</th><th className="th text-right">Strategy</th><th className="th text-right">EW univ.</th>{spy && <th className="th text-right">SPY</th>}</tr></thead>
            <tbody>{rows.map(([l, k, f]) => <tr key={k} className="border-t border-line"><td className="td text-xs">{l}</td><td className="td text-right font-medium">{f(s[k])}</td><td className="td text-right">{f(ew[k])}</td>{spy && <td className="td text-right">{f(spy[k])}</td>}</tr>)}</tbody></table>
          <div className="grid grid-cols-2 gap-3 p-3">
            <Stat label="Profit factor" value={num(s.profit_factor, 2)} hint="Sum of positive rebalance-period returns / |sum of negative ones|." />
            <Stat label="Hit rate (periods)" value={pct(s.period_hit_rate, 0, false)} />
            <Stat label="Info. ratio vs EW" value={num(s.information_ratio_vs_ew, 2)} />
            <Stat label="Beta vs SPY" value={num(s.beta_vs_spy, 2)} />
            <Stat label="Avg turnover / rebalance" value={pct(s.avg_turnover, 0, false)} />
            <Stat label="Total cost drag" value={pct(s.total_cost_drag, 1, false)} />
          </div>
        </Card>
      </div>
      <Card title="Year by year">
        <MultiLine data={r.by_year.map((y: any) => ({ ...y, year: String(y.year) }))} x="year" yFmt={(v) => pct(v, 0)} series={[{ key: "strategy", name: "Strategy" }, { key: "equal_weight", name: "Equal-weight" }, { key: "SPY", name: "SPY" }]} height={200} />
      </Card>
      <ul className="list-disc pl-5 text-xs text-muted">{r.caveats.map((c: string) => <li key={c}>{c}</li>)}</ul>
    </div>
  );
}

function Runs() {
  const q = useQuery({ queryKey: ["bt-runs"], queryFn: () => api("/backtest/runs") });
  const [sel, setSel] = useState<number | null>(null);
  const d = useQuery({ queryKey: ["bt-run", sel], queryFn: () => api(`/backtest/runs/${sel}`), enabled: !!sel });
  if (q.isLoading) return <Loading rows={6} />;
  return (
    <div className="space-y-4">
      <Card title="All backtest runs" sub="Immutable log - runs cannot be deleted, so results cannot be cherry-picked." pad={false}>
        <div className="overflow-x-auto scrollbar-thin"><table className="w-full">
          <thead><tr>{["#", "When", "Strategy", "Horizon", "Costs", "CAGR", "Sharpe", "Max DD", "EW CAGR", ""].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
          <tbody>{q.data.map((r: any) => (
            <tr key={r.id} className="border-t border-line"><td className="td text-muted">{r.id}</td><td className="td text-xs">{ago(r.created_at)}{r.mine && <Badge className="ml-1">you</Badge>}</td>
              <td className="td">{r.config.strategy}{r.config.strategy === "top_n" ? ` (${r.config.top_n})` : ""}</td><td className="td">{r.config.horizon}</td><td className="td">{r.config.cost_bps + r.config.slippage_bps}bp</td>
              <td className="td">{pct(r.summary.stats?.cagr, 1)}</td><td className="td">{num(r.summary.stats?.sharpe, 2)}</td><td className="td">{pct(r.summary.stats?.max_drawdown, 1)}</td>
              <td className="td text-muted">{pct(r.summary.benchmarks?.equal_weight_universe?.cagr, 1)}</td><td className="td"><button className="text-xs text-accent" onClick={() => setSel(r.id)}>View</button></td></tr>
          ))}</tbody></table></div>
      </Card>
      {d.data && <Result r={d.data} />}
    </div>
  );
}

const _t = Tip;
