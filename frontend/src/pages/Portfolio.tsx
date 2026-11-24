import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CorrMatrix, MultiLine } from "../components/charts";
import { Badge, Card, Delta, Empty, ErrorBox, Loading, Note, Segmented, Stat, Tip } from "../components/ui";
import { api } from "../lib/api";
import { ccy, isNum, num, pct, signClass } from "../lib/format";

export default function Portfolio() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => api("/portfolios") });
  const [pid, setPid] = useState<number | null>(null);
  useEffect(() => { if (!pid && list.data?.length) setPid(list.data[0].id); }, [list.data, pid]);
  const pf = list.data?.find((p: any) => p.id === pid);
  const an = useQuery({ queryKey: ["pf-analytics", pid], queryFn: () => api(`/portfolios/${pid}/analytics`), enabled: !!pid });
  const txns = useQuery({ queryKey: ["pf-txns", pid], queryFn: () => api(`/portfolios/${pid}/transactions`), enabled: !!pid });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["pf-analytics", pid] }); qc.invalidateQueries({ queryKey: ["pf-txns", pid] }); qc.invalidateQueries({ queryKey: ["portfolios"] }); };
  const create = useMutation({ mutationFn: (b: any) => api("/portfolios", { body: b }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["portfolios"] }); setPid(r.id); } });
  const setBase = useMutation({ mutationFn: (base: string) => api(`/portfolios/${pid}`, { method: "PATCH", body: { name: pf.name, base_currency: base, benchmark: pf.benchmark } }), onSuccess: refresh });
  if (list.isLoading) return <Loading rows={8} />;
  const B = an.data?.base_currency || pf?.base_currency || "EUR";
  const s = an.data?.summary;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Portfolio intelligence</h1>
          <p className="text-xs text-muted">Average-cost accounting, P&L split into price and FX effects, risk, stress tests and what-if simulation.</p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <select className="input py-1.5 w-auto" value={pid ?? ""} onChange={(e) => setPid(Number(e.target.value))} aria-label="Portfolio">
            {list.data?.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {pf && <Segmented value={pf.base_currency} onChange={(v) => setBase.mutate(v)} options={[{ id: "EUR", label: "EUR" }, { id: "USD", label: "USD" }]} />}
          <button className="btn" onClick={() => { const n = prompt("New portfolio name"); if (n) create.mutate({ name: n, base_currency: "EUR" }); }}>New</button>
        </div>
      </div>
      {pid && <RevolutImport pid={pid} base={B} onDone={refresh} />}
      {pid && <TxnForm pid={pid} base={B} onDone={refresh} />}
      {an.isLoading && <Card><Loading rows={8} /></Card>}
      {an.error && <ErrorBox error={an.error} />}
      {an.data?.empty && <Note>No transactions yet. Add your first buy above.</Note>}
      {an.data?.error && <ErrorBox error={an.data.error} />}
      {s && !an.data.empty && (
        <>
          {an.data.warnings?.map((w: string) => <Note key={w} tone="warn">{w}</Note>)}
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
            {[
              ["Market value", ccy(s.market_value, B)], ["Cost basis", ccy(s.cost_basis, B)],
              ["Unrealized P&L", <span className={signClass(s.unrealized_pnl)}>{ccy(s.unrealized_pnl, B)}</span>],
              ["of which price", ccy(s.unrealized_price_effect, B)], ["of which FX", ccy(s.unrealized_fx_effect, B)],
              ["Realized P&L", <span className={signClass(s.realized_pnl)}>{ccy(s.realized_pnl, B)}</span>], ["Trading fees", ccy(s.fees_paid, B)],
              ["Dividends", ccy(an.data.income?.dividends, B)], ["Other fees", ccy(an.data.income?.other_fees, B)],
              ["Total P&L", <span className={signClass(an.data.income?.total_pnl)}>{ccy(an.data.income?.total_pnl, B)}</span>],
              ["EUR/USD (ECB)", `${num(an.data.fx?.eurusd, 4)}`],
            ].map(([l, v]: any) => <div key={l} className="card p-3"><Stat label={l} value={v} /></div>)}
          </div>
          <Card title="Holdings" pad={false}>
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full">
                <thead><tr>{["Ticker", "Qty", "Avg cost", "Last", "Value", "Weight", "Unrealized", "Price eff.", "FX eff.", "Day", "Realized"].map((h) => <th key={h} className="th text-right first:text-left">{h}</th>)}</tr></thead>
                <tbody>{s.positions.map((p: any) => (
                  <tr key={p.ticker} className="border-t border-line">
                    <td className="td"><Link to={`/stock/${p.ticker}`} className="font-semibold hover:text-accent">{p.ticker}</Link> <span className="text-[11px] text-muted">{p.currency}</span></td>
                    <td className="td text-right">{num(p.quantity, 4).replace(/\.?0+$/, "")}</td><td className="td text-right">{num(p.avg_price)}</td><td className="td text-right">{num(p.last_price)}</td>
                    <td className="td text-right">{ccy(p.market_value, B)}</td><td className="td text-right">{pct(p.weight, 1, false)}</td>
                    <td className={clsx("td text-right", signClass(p.unrealized_pnl))}>{ccy(p.unrealized_pnl, B)} <span className="text-[11px]">({pct(p.unrealized_pct, 1)})</span></td>
                    <td className="td text-right">{ccy(p.unrealized_price_effect, B)}</td><td className="td text-right">{ccy(p.unrealized_fx_effect, B)}</td>
                    <td className={clsx("td text-right", signClass(p.day_change_value))}>{ccy(p.day_change_value, B)}</td>
                    <td className="td text-right">{ccy(p.realized_pnl, B)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card title={`Performance vs ${an.data.benchmark}`} sub="Time-weighted return index (base currency)" className="xl:col-span-2">
              {an.data.series.length > 1 ? <MultiLine data={an.data.series} yFmt={(v) => v.toFixed(2)} series={[{ key: "portfolio", name: "Portfolio (TWR)" }, { key: "benchmark", name: an.data.benchmark }]} /> : <Empty>Not enough history yet.</Empty>}
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-3">
                {[["Total return", "total_return"], ["CAGR", "cagr"], ["Volatility", "volatility"], ["Sharpe", "sharpe"], ["Sortino", "sortino"], ["Max drawdown", "max_drawdown"]].map(([l, k]) => (
                  <Stat key={k} label={l} value={["sharpe", "sortino"].includes(k) ? num(an.data.performance?.[k], 2) : pct(an.data.performance?.[k], 1)}
                        hint={an.data.benchmark_performance ? `${an.data.benchmark}: ${["sharpe", "sortino"].includes(k) ? num(an.data.benchmark_performance[k], 2) : pct(an.data.benchmark_performance[k], 1)}` : undefined} />
                ))}
              </div>
            </Card>
            <Card title="Risk">
              {an.data.risk?.insufficient ? <Empty>Need 60+ sessions of history.</Empty> : (
                <div className="space-y-1.5 text-sm">
                  {[["Beta vs SPY", num(an.data.risk.beta, 2)], ["Volatility (1y)", pct(an.data.risk.volatility, 1, false)],
                    ["1-day VaR 95% (hist.)", ccy(an.data.risk.var_1d_95_hist, B)], ["1-day VaR 99% (hist.)", ccy(an.data.risk.var_1d_99_hist, B)],
                    ["1-day CVaR 95%", ccy(an.data.risk.cvar_1d_95_hist, B)], ["10-day VaR 95%", ccy(an.data.risk.var_10d_95_hist, B)],
                    ["1-day VaR 95% (normal)", ccy(an.data.risk.var_1d_95_param, B)], ["Effective # positions", num(an.data.risk.effective_n, 1)],
                    ["Top-5 weight", pct(an.data.risk.top5_weight, 0, false)], ["Avg. correlation", num(an.data.risk.avg_pairwise_correlation, 2)],
                    ["Diversification ratio", num(an.data.risk.diversification_ratio, 2)]].map(([k, v]) => (
                    <div key={k} className="flex justify-between"><span className="text-muted text-xs">{k}</span><span className="num">{v}</span></div>
                  ))}
                  <div className="text-[11px] text-muted">{an.data.risk.method}</div>
                  {an.data.risk.flags?.map((f: string) => <Note key={f} tone="warn">{f}</Note>)}
                </div>
              )}
            </Card>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card title="Allocation & currency exposure">
              {Object.entries(an.data.risk?.sector_weights || {}).sort((a: any, b: any) => b[1] - a[1]).map(([k, v]: any) => (
                <div key={k} className="mb-1.5"><div className="flex justify-between text-xs"><span>{k}</span><span className="num">{pct(v, 1, false)}</span></div>
                  <div className="h-1.5 rounded-full bg-line"><div className="h-1.5 rounded-full bg-accent" style={{ width: `${v * 100}%` }} /></div></div>
              ))}
              <div className="mt-3 text-xs">{Object.entries(an.data.currency_exposure || {}).map(([k, v]: any) => <Badge key={k} className="mr-1">{k} {pct(v, 0, false)}</Badge>)}</div>
              {an.data.fx_sensitivity && <div className="text-xs text-muted mt-2">{an.data.fx_sensitivity.description}: <b className={signClass(an.data.fx_sensitivity.pnl)}>{ccy(an.data.fx_sensitivity.pnl, B)}</b> ({pct(an.data.fx_sensitivity.pct, 1)})</div>}
            </Card>
            <Card title="Correlation matrix (1y daily)" className="xl:col-span-2">
              {an.data.risk?.correlation ? <CorrMatrix tickers={an.data.risk.correlation.tickers} matrix={an.data.risk.correlation.matrix} /> : <Empty>—</Empty>}
            </Card>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Stress pid={pid!} base={B} data={an.data.stress} />
            <WhatIf pid={pid!} base={B} counterfactual={an.data.counterfactual} />
          </div>
        </>
      )}
      <Card title="Transactions" pad={false}>
        {txns.data?.length ? (
          <div className="overflow-x-auto scrollbar-thin"><table className="w-full">
            <thead><tr>{["Date", "Side", "Ticker", "Qty", "Price", "Ccy", "Fee", "FX (base/ccy)", "FX source", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{[...txns.data].reverse().map((t: any) => (
              <tr key={t.id} className="border-t border-line"><td className="td">{t.trade_date}</td><td className="td"><Badge tone={t.side === "BUY" ? "up" : t.side === "SELL" ? "down" : "accent"}>{t.side}</Badge>{t.source === "revolut-csv" && <span className="ml-1 text-[10px] text-muted">Revolut</span>}</td><td className="td font-medium">{t.ticker}</td>
                <td className="td">{t.quantity}</td><td className="td">{num(t.price)}</td><td className="td">{t.currency}</td><td className="td">{num(t.fee)} {t.fee_currency}</td><td className="td">{num(t.fx_rate, 5)}</td><td className="td text-[11px] text-muted max-w-[200px] truncate">{t.fx_source}</td>
                <td className="td"><button aria-label="Delete transaction" onClick={async () => { if (confirm("Delete transaction?")) { await api(`/portfolios/${pid}/transactions/${t.id}`, { method: "DELETE" }); refresh(); } }}><Trash2 size={14} className="text-muted hover:text-down" /></button></td></tr>
            ))}</tbody></table></div>
        ) : <Empty>No transactions.</Empty>}
      </Card>
    </div>
  );
}

function TxnForm({ pid, base, onDone }: { pid: number; base: string; onDone: () => void }) {
  const [f, setF] = useState({ ticker: "", side: "BUY", quantity: "", price: "", currency: "USD", fee: "0", fx_rate: "", trade_date: new Date().toISOString().slice(0, 10) });
  const [err, setErr] = useState("");
  const m = useMutation({
    mutationFn: () => api(`/portfolios/${pid}/transactions`, { body: { ...f, ticker: f.ticker.toUpperCase(), quantity: Number(f.quantity), price: Number(f.price), fee: Number(f.fee || 0), fx_rate: f.fx_rate ? Number(f.fx_rate) : null, fee_currency: base } }),
    onSuccess: () => { setErr(""); setF({ ...f, ticker: "", quantity: "", price: "", fx_rate: "" }); onDone(); },
    onError: (e: any) => setErr(e.message),
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <Card title="Record a transaction" sub={`Fees in ${base}. FX rate defaults to the ECB fixing on the trade date.`}>
      <form onSubmit={submit} className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-9 gap-2 items-end">
        <div><label className="label">Ticker</label><input className="input" value={f.ticker} onChange={set("ticker")} required placeholder="AAPL" /></div>
        <div><label className="label">Side</label><select className="input" value={f.side} onChange={set("side")}><option>BUY</option><option>SELL</option></select></div>
        <div><label className="label">Quantity</label><input className="input" type="number" step="any" min="0" value={f.quantity} onChange={set("quantity")} required /></div>
        <div><label className="label">Price</label><input className="input" type="number" step="any" min="0" value={f.price} onChange={set("price")} required /></div>
        <div><label className="label">Currency</label><select className="input" value={f.currency} onChange={set("currency")}><option>USD</option><option>EUR</option></select></div>
        <div><label className="label">Fee ({base})</label><input className="input" type="number" step="any" min="0" value={f.fee} onChange={set("fee")} /></div>
        <div><label className="label flex items-center gap-1">FX override <Tip text={`${base} per 1 ${f.currency}. Leave empty to use the ECB reference rate on the trade date.`} /></label><input className="input" type="number" step="any" value={f.fx_rate} onChange={set("fx_rate")} placeholder="auto" /></div>
        <div><label className="label">Trade date</label><input className="input" type="date" value={f.trade_date} onChange={set("trade_date")} required /></div>
        <button className="btn-primary justify-center py-2" disabled={m.isPending}>{m.isPending ? "…" : "Add"}</button>
      </form>
      {err && <div className="text-sm text-down mt-2">{err}</div>}
    </Card>
  );
}

function Stress({ pid, base, data }: { pid: number; base: string; data: any }) {
  const [factor, setFactor] = useState("QQQ");
  const [shock, setShock] = useState("-10");
  const m = useMutation({ mutationFn: () => api(`/portfolios/${pid}/stress?factor=${factor}&shock=${Number(shock) / 100}`, { method: "POST" }) });
  return (
    <Card title="Stress testing">
      <div className="text-xs text-muted mb-1">Historical scenarios (actual returns of your holdings)</div>
      <table className="w-full mb-3"><tbody>{data?.historical?.map((h: any) => (
        <tr key={h.scenario} className="border-t border-line"><td className="td text-xs">{h.scenario}<div className="text-[10px] text-muted">{h.window.join(" → ")}{h.beta_proxied.length ? ` · beta-proxied: ${h.beta_proxied.join(", ")}` : ""}</div></td>
          <td className="td text-right"><Delta v={h.portfolio_return} d={1} /></td><td className={clsx("td text-right", signClass(h.pnl))}>{ccy(h.pnl, base, 0)}</td></tr>
      ))}</tbody></table>
      <div className="text-xs text-muted mb-1">Factor shocks (regression betas)</div>
      <table className="w-full mb-3"><tbody>{data?.hypothetical?.map((h: any) => (
        <tr key={h.scenario} className="border-t border-line"><td className="td text-xs">{h.scenario}</td><td className="td text-right"><Delta v={h.expected_return} d={1} /></td>
          <td className="td text-right text-xs">{ccy(h.expected_pnl, base, 0)} <span className="text-muted">[{ccy(h.range_90[0], base, 0)}, {ccy(h.range_90[1], base, 0)}]</span></td></tr>
      ))}</tbody></table>
      <div className="flex flex-wrap gap-2 items-end">
        <div><label className="label">Factor</label><select className="input py-1.5" value={factor} onChange={(e) => setFactor(e.target.value)}><option value="QQQ">Nasdaq-100 (QQQ)</option><option value="SPY">S&P 500 (SPY)</option><option value="TLT">Long Treasuries (TLT)</option></select></div>
        <div className="w-24"><label className="label">Move %</label><input className="input py-1.5" type="number" value={shock} onChange={(e) => setShock(e.target.value)} /></div>
        <button className="btn" onClick={() => m.mutate()} disabled={m.isPending}>Run custom shock</button>
      </div>
      {m.data?.custom_shock && (
        <div className="mt-2 text-sm">Expected: <Delta v={m.data.custom_shock.expected_return} d={1} /> = <b>{ccy(m.data.custom_shock.expected_pnl, base, 0)}</b> <span className="text-xs text-muted">(90%: {ccy(m.data.custom_shock.range_90[0], base, 0)} to {ccy(m.data.custom_shock.range_90[1], base, 0)})</span></div>
      )}
      {m.error && <ErrorBox error={m.error} />}
      <div className="text-[11px] text-muted mt-2">{data?.hypothetical?.[0]?.method}</div>
    </Card>
  );
}

function WhatIf({ pid, base, counterfactual }: { pid: number; base: string; counterfactual: any }) {
  const [rows, setRows] = useState([{ ticker: "", side: "BUY", quantity: "" }]);
  const m = useMutation({ mutationFn: () => api(`/portfolios/${pid}/whatif`, { body: { trades: rows.filter((r) => r.ticker && r.quantity).map((r) => ({ ...r, quantity: Number(r.quantity) })) } }) });
  const keys: [string, string, (v: any) => string][] = [["market_value", "Value", (v) => ccy(v, base, 0)], ["beta", "Beta", (v) => num(v, 2)], ["volatility", "Volatility", (v) => pct(v, 1, false)],
    ["var_1d_95_hist", "1d VaR 95%", (v) => ccy(v, base, 0)], ["effective_n", "Effective N", (v) => num(v, 1)], ["avg_pairwise_correlation", "Avg corr.", (v) => num(v, 2)]];
  return (
    <Card title="Decision simulator (what-if)">
      {counterfactual && (
        <div className="text-sm mb-3 rounded-lg bg-panel2 p-2.5">
          If every trade had instead bought/sold <b>{counterfactual.benchmark}</b>: {ccy(counterfactual.value_if_benchmark, base, 0)} vs your {ccy(counterfactual.actual_value_plus_realized, base, 0)} →
          <b className={clsx("ml-1", signClass(counterfactual.difference))}>{ccy(counterfactual.difference, base, 0)}</b>
          <div className="text-[11px] text-muted">{counterfactual.method}</div>
        </div>
      )}
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2 mb-2">
          <input className="input py-1.5" placeholder="Ticker" value={r.ticker} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, ticker: e.target.value.toUpperCase() } : x)))} />
          <select className="input py-1.5 w-24" value={r.side} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, side: e.target.value } : x)))}><option>BUY</option><option>SELL</option></select>
          <input className="input py-1.5 w-28" type="number" placeholder="Qty" value={r.quantity} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
        </div>
      ))}
      <div className="flex gap-2"><button className="btn" onClick={() => setRows([...rows, { ticker: "", side: "BUY", quantity: "" }])}>+ Trade</button>
        <button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>{m.isPending ? "Simulating…" : "Simulate"}</button></div>
      {m.error && <div className="mt-2"><ErrorBox error={m.error} /></div>}
      {m.data && (
        <table className="w-full mt-3"><thead><tr><th className="th">Metric</th><th className="th text-right">Before</th><th className="th text-right">After</th></tr></thead>
          <tbody>{keys.map(([k, l, f]) => <tr key={k} className="border-t border-line"><td className="td text-xs">{l}</td><td className="td text-right">{f(m.data.before[k])}</td><td className="td text-right">{f(m.data.after[k])}</td></tr>)}</tbody></table>
      )}
      {m.data?.flags_after?.map((f: string) => <div key={f} className="mt-1"><Note tone="warn">{f}</Note></div>)}
      {m.data && <div className="text-[11px] text-muted mt-1">{m.data.note}</div>}
    </Card>
  );
}

const _unused = isNum;

function RevolutImport({ pid, base, onDone }: { pid: number; base: string; onDone: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [open, setOpen] = useState(false);
  const preview = useMutation({ mutationFn: (csv: string) => api(`/portfolios/${pid}/import/revolut`, { body: { csv, commit: false } }) });
  const commit = useMutation({ mutationFn: () => api(`/portfolios/${pid}/import/revolut`, { body: { csv: text, commit: true } }), onSuccess: () => { onDone(); preview.reset(); setText(null); setName(""); } });
  const onFile = (f?: File | null) => {
    if (!f) return;
    if (f.size > 5_000_000) { alert("File too large (max 5 MB)"); return; }
    setName(f.name);
    const r = new FileReader();
    r.onload = () => { const t = String(r.result || ""); setText(t); commit.reset(); preview.mutate(t); };
    r.readAsText(f);
  };
  const tone: Record<string, "up" | "neutral" | "down" | "warn"> = { new: "up", duplicate: "neutral", skipped: "warn", error: "down" };
  return (
    <Card title="Import from Revolut" sub="Upload the stocks account statement (CSV). Nothing is saved until you confirm the preview."
          right={<button className="text-xs text-accent" onClick={() => setOpen(!open)}>{open ? "Hide help" : "How?"}</button>}>
      {open && (
        <ol className="list-decimal pl-5 text-xs text-muted space-y-1 mb-3">
          <li>In the Revolut app open <b>Invest</b> (stocks), then <b>More / ⋯ → Documents → Stocks → Account statement</b> (menu names can differ slightly between app versions).</li>
          <li>Choose the full period and the <b>CSV</b> (Excel) format, then save the file to the iPhone <b>Files</b> app.</li>
          <li>Tap <b>Choose file</b> below and pick it. Review the preview, then tap <b>Import</b>.</li>
          <li>Re-importing a newer statement later is safe: rows already imported are recognised as duplicates.</li>
        </ol>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <label className="btn cursor-pointer">Choose file<input type="file" accept=".csv,text/csv,text/plain" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} /></label>
        <span className="text-xs text-muted truncate max-w-[220px]">{name}</span>
        {preview.isPending && <span className="text-xs text-muted">Reading…</span>}
      </div>
      {preview.error && <div className="mt-2"><ErrorBox error={preview.error} title="Could not read this file" /></div>}
      {preview.data && !commit.isSuccess && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2 text-xs">
            {Object.entries(preview.data.counts).map(([k, v]: any) => <Badge key={k} tone={tone[k]}>{v} {k}</Badge>)}
          </div>
          <div className="max-h-80 overflow-auto scrollbar-thin border border-line rounded-lg divide-y divide-line">
            {preview.data.rows.map((r: any) => (
              <div key={r.line} className="px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate">
                    <span className="text-muted text-xs mr-2">{r.date}</span>
                    <b>{r.ticker || ""}</b> <span className="text-xs text-muted">{r.type}</span>
                  </div>
                  <Badge tone={tone[r.status]}>{r.status}</Badge>
                </div>
                {r.status !== "skipped" && (
                  <div className="text-xs num text-muted mt-0.5">
                    {r.kind === "DIV" || r.kind === "FEE" ? `${r.kind === "DIV" ? "Dividend" : "Fee"} ${num(r.total)} ${r.currency || ""}`
                      : r.kind === "SPLIT" ? `Split: ${r.quantity > 0 ? "+" : ""}${r.quantity} shares`
                      : `${r.quantity} × ${num(r.price)} ${r.currency || ""}${r.fee ? ` · fee ${num(r.fee)}` : ""}`}
                    {r.fx_rate && r.currency !== base ? ` · FX ${num(r.fx_rate, 5)} ${base}/${r.currency}` : ""}
                  </div>
                )}
                {r.issues?.length > 0 && <div className="text-[11px] text-muted mt-0.5">{r.issues.join("; ")}</div>}
              </div>
            ))}
          </div>
          <div className="text-[11px] text-muted">{preview.data.note} Fees are read from the gap between the cash amount and shares × price. FX: the statement rate is used when it matches the ECB fixing (either direction), otherwise the ECB fixing.</div>
          <button className="btn-primary" disabled={commit.isPending || !preview.data.counts.new} onClick={() => commit.mutate()}>
            {commit.isPending ? "Importing…" : `Import ${preview.data.counts.new} transaction(s)`}
          </button>
        </div>
      )}
      {commit.isSuccess && <div className="mt-2 text-sm text-up">Imported {commit.data.imported} transaction(s).</div>}
      {commit.error && <div className="mt-2"><ErrorBox error={commit.error} /></div>}
    </Card>
  );
}
