import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MultiLine } from "../components/charts";
import { Badge, Card, Empty, ErrorBox, Loading, Note, Segmented, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ago, money, num, pct, signClass } from "../lib/format";

export default function Paper() {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const accts = useQuery({ queryKey: ["paper-accts"], queryFn: () => api("/paper/accounts") });
  const [aid, setAid] = useState<number | null>(null);
  useEffect(() => { if (!aid && accts.data?.length) setAid(accts.data.find((a: any) => !a.is_system)?.id ?? accts.data[0].id); }, [accts.data, aid]);
  const acct = accts.data?.find((a: any) => a.id === aid);
  const view = useQuery({ queryKey: ["paper", aid], queryFn: () => api(`/paper/accounts/${aid}`), enabled: !!aid, refetchInterval: 30_000 });
  const orders = useQuery({ queryKey: ["paper-orders", aid], queryFn: () => api(`/paper/accounts/${aid}/orders`), enabled: !!aid, refetchInterval: 30_000 });
  const trades = useQuery({ queryKey: ["paper-trades", aid], queryFn: () => api(`/paper/accounts/${aid}/trades`), enabled: !!aid });
  const [tab, setTab] = useState<"positions" | "orders" | "history">("positions");
  const [showNew, setShowNew] = useState(false);
  const refresh = () => ["paper", "paper-orders", "paper-trades", "paper-accts"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const newAcct = useMutation({ mutationFn: (b: any) => api("/paper/accounts", { body: b }), onSuccess: (r) => { refresh(); setAid(r.id); } });
  const cancel = useMutation({ mutationFn: (oid: number) => api(`/paper/accounts/${aid}/orders/${oid}/cancel`, { method: "POST" }), onSuccess: refresh });
  if (accts.isLoading) return <Loading rows={8} />;
  const v = view.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Paper trading</h1>
          <p className="text-xs text-muted">Virtual capital only - no real orders are ever sent. Fills use live bid/ask (or a modelled spread), slippage and commissions.</p>
        </div>
        <div className="flex gap-2">
          <select className="input py-1.5 w-auto" value={aid ?? ""} onChange={(e) => setAid(Number(e.target.value))} aria-label="Account">
            {accts.data?.map((a: any) => <option key={a.id} value={a.id}>{a.is_system ? "🤖 " : ""}{a.name}</option>)}
          </select>
          <button className="btn" onClick={() => setShowNew(!showNew)}>New account</button>
        </div>
      </div>
      {showNew && <NewAccount onCreate={(b) => newAcct.mutate(b, { onSuccess: () => setShowNew(false) })} pending={newAcct.isPending} error={newAcct.error} />}
      {v && !acct?.is_system && (
        <div className="text-xs text-muted">
          Fees: <b className="text-fg">{v.account.fee_profile || (v.account.fee_model === "pct" ? "custom %" : "per share")}</b>
          {v.account.fee_model === "pct"
            ? <> · {pct(v.account.commission_pct, 2, false)} (min {num(v.account.min_commission)}) after {v.account.free_trades_per_month} free order(s)/month · used this month: {v.account.trades_this_month} · FX fee {pct(v.account.fx_fee_pct, 2, false)}</>
            : <> · ${num(v.account.commission_per_share, 3)}/share, min ${num(v.account.min_commission)}</>}
        </div>
      )}
      {acct?.is_system && <Note>System strategy account (read-only): the platform paper-trades its own rule ({acct.strategy?.rule}) to evaluate itself forward in time.</Note>}
      {v && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
          {[["Equity", money(v.equity)], ["Cash", money(v.account.cash)], ["Invested", money(v.market_value)],
            ["Total return", <span className={signClass(v.total_return)}>{pct(v.total_return, 2)}</span>],
            ["Realized P&L", money(v.realized_pnl)], ["Win rate", v.win_rate == null ? "—" : pct(v.win_rate, 0, false)],
            ["Profit factor", num(v.profit_factor, 2)], ["Fees paid", money(v.fees_paid)]].map(([l, x]: any) => <div key={l} className="card p-3"><Stat label={l} value={x} /></div>)}
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 space-y-4">
          <Card pad={false}>
            <div className="px-4 pt-2"><Tabs value={tab} onChange={setTab} tabs={[{ id: "positions", label: "Positions" }, { id: "orders", label: "Orders" }, { id: "history", label: "Transaction history" }]} /></div>
            <div className="overflow-x-auto scrollbar-thin p-2">
              {tab === "positions" && (v?.positions?.length ? (
                <table className="w-full"><thead><tr>{["Ticker", "Qty", "Avg price", "Last", "Value", "Unrealized", "Realized"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                  <tbody>{v.positions.map((p: any) => (
                    <tr key={p.ticker} className="border-t border-line"><td className="td"><Link to={`/stock/${p.ticker}`} className="font-semibold hover:text-accent">{p.ticker}</Link></td><td className="td">{p.quantity}</td><td className="td">{money(p.avg_price)}</td><td className="td">{money(p.price)}</td><td className="td">{money(p.market_value)}</td>
                      <td className={clsx("td", signClass(p.unrealized_pnl))}>{money(p.unrealized_pnl)} ({pct(p.unrealized_pct, 1)})</td><td className="td">{money(p.realized_pnl)}</td></tr>
                  ))}</tbody></table>
              ) : <Empty>No open positions.</Empty>)}
              {tab === "orders" && (orders.data?.length ? (
                <table className="w-full"><thead><tr>{["#", "Created", "Ticker", "Side", "Type", "Qty", "Limit", "Stop", "TIF", "Role", "Status", "Fill", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                  <tbody>{orders.data.map((o: any) => (
                    <tr key={o.id} className="border-t border-line"><td className="td text-muted">{o.id}</td><td className="td text-xs">{ago(o.created_at)}</td><td className="td font-medium">{o.ticker}</td>
                      <td className="td"><Badge tone={o.side === "BUY" ? "up" : "down"}>{o.side}</Badge></td><td className="td">{o.order_type}</td><td className="td">{o.quantity}</td>
                      <td className="td">{money(o.limit_price)}</td><td className="td">{money(o.stop_price)}</td><td className="td">{o.time_in_force}</td><td className="td text-xs">{o.role}</td>
                      <td className="td"><Badge tone={o.status === "filled" ? "up" : o.status === "pending" ? "warn" : "neutral"}>{o.status}</Badge>{o.reason && <div className="text-[10px] text-muted max-w-[160px] truncate" title={o.reason}>{o.reason}</div>}</td>
                      <td className="td">{money(o.fill_price)}</td>
                      <td className="td">{o.status === "pending" && !acct?.is_system && <button className="text-xs text-down" onClick={() => cancel.mutate(o.id)}>Cancel</button>}</td></tr>
                  ))}</tbody></table>
              ) : <Empty>No orders.</Empty>)}
              {tab === "history" && (trades.data?.length ? (
                <table className="w-full"><thead><tr>{["Time", "Ticker", "Side", "Qty", "Fill", "Ref. price", "Spread bps", "Slippage bps", "Commission", "Realized", "Quote source"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                  <tbody>{trades.data.map((t: any) => (
                    <tr key={t.id} className="border-t border-line"><td className="td text-xs">{new Date(t.executed_at).toLocaleString()}</td><td className="td font-medium">{t.ticker}</td><td className="td">{t.side}</td><td className="td">{t.quantity}</td>
                      <td className="td">{money(t.price, 3)}</td><td className="td">{money(t.reference_price, 3)}</td><td className="td">{num(t.spread_bps, 1)}</td><td className="td">{num(t.slippage_bps, 1)}</td><td className="td">{money(t.commission)}</td>
                      <td className={clsx("td", signClass(t.realized_pnl))}>{t.side === "SELL" ? money(t.realized_pnl) : ""}</td><td className="td text-[11px] text-muted">{t.quote_source}</td></tr>
                  ))}</tbody></table>
              ) : <Empty>No executions yet.</Empty>)}
            </div>
          </Card>
          <Card title="Equity curve & performance">
            {v?.equity_curve?.length > 1 ? <MultiLine data={v.equity_curve} yFmt={(x) => money(x, 0)} series={[{ key: "equity", name: "Equity" }]} /> : <Empty>Equity snapshots are recorded after each session close.</Empty>}
            {v?.stats && !v.stats.insufficient && Object.keys(v.stats).length > 0 && (
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mt-2">
                <Stat label="CAGR" value={pct(v.stats.cagr, 1)} /><Stat label="Volatility" value={pct(v.stats.volatility, 1, false)} /><Stat label="Sharpe" value={num(v.stats.sharpe, 2)} />
                <Stat label="Sortino" value={num(v.stats.sortino, 2)} /><Stat label="Max DD" value={pct(v.stats.max_drawdown, 1)} /><Stat label="Days" value={v.stats.n_days} />
              </div>
            )}
          </Card>
        </div>
        {!acct?.is_system && aid && <OrderTicket aid={aid} initial={params.get("ticker") || ""} onDone={refresh} market={v?.market} />}
      </div>
    </div>
  );
}

function OrderTicket({ aid, initial, onDone, market }: { aid: number; initial: string; onDone: () => void; market: any }) {
  const [f, setF] = useState({ ticker: initial, side: "BUY", order_type: "market", quantity: "", limit_price: "", stop_price: "", time_in_force: "GTC", stop_loss: "", take_profit: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const q = useQuery({ queryKey: ["xq", f.ticker], queryFn: () => api(`/paper/quote/${f.ticker}`), enabled: f.ticker.length >= 1, retry: false, refetchInterval: 20_000 });
  const n = (s: string) => (s === "" ? null : Number(s));
  const m = useMutation({
    mutationFn: () => api(`/paper/accounts/${aid}/orders`, { body: { ...f, ticker: f.ticker.toUpperCase(), quantity: Number(f.quantity), limit_price: n(f.limit_price), stop_price: n(f.stop_price), stop_loss: n(f.stop_loss), take_profit: n(f.take_profit) } }),
    onSuccess: (o) => { setMsg({ ok: true, text: `Order #${o.id} ${o.status}${o.fill_price ? ` at ${money(o.fill_price, 3)}` : o.status === "pending" ? " - queued until conditions/market hours are met" : ""}` }); onDone(); },
    onError: (e: any) => setMsg({ ok: false, text: e.message }),
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: k === "ticker" ? e.target.value.toUpperCase() : e.target.value });
  const submit = (e: FormEvent) => { e.preventDefault(); setMsg(null); m.mutate(); };
  const ref = q.data ? (f.side === "BUY" ? q.data.ask : q.data.bid) : null;
  return (
    <Card title="Order ticket" sub={market ? `NYSE ${market.state}${!market.is_open ? ` · next open ${new Date(market.next_open).toLocaleString()}` : ""}` : undefined}>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label">Ticker</label><input className="input" value={f.ticker} onChange={set("ticker")} required /></div>
          <div><label className="label">Side</label><Segmented value={f.side} onChange={(s) => setF({ ...f, side: s })} options={[{ id: "BUY", label: "Buy" }, { id: "SELL", label: "Sell" }]} /></div>
        </div>
        {q.data && (
          <div className="rounded-lg bg-panel2 p-2 text-xs num grid grid-cols-3 gap-1">
            <span>Bid <b>{money(q.data.bid)}</b></span><span>Ask <b>{money(q.data.ask)}</b></span><span>Spread {num(q.data.spread_bps, 1)}bp</span>
            <span className="col-span-3 text-[10px] text-muted">{q.data.source}</span>
          </div>
        )}
        {q.error && f.ticker && <div className="text-xs text-down">{(q.error as Error).message}</div>}
        <div className="grid grid-cols-2 gap-2">
          <div><label className="label">Order type</label><select className="input" value={f.order_type} onChange={set("order_type")}><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option><option value="stop_limit">Stop-limit</option></select></div>
          <div><label className="label">Quantity</label><input className="input" type="number" min="0" step="any" value={f.quantity} onChange={set("quantity")} required /></div>
          {(f.order_type === "limit" || f.order_type === "stop_limit") && <div><label className="label">Limit price</label><input className="input" type="number" step="any" value={f.limit_price} onChange={set("limit_price")} required /></div>}
          {(f.order_type === "stop" || f.order_type === "stop_limit") && <div><label className="label">Stop price</label><input className="input" type="number" step="any" value={f.stop_price} onChange={set("stop_price")} required /></div>}
          <div><label className="label">Time in force</label><select className="input" value={f.time_in_force} onChange={set("time_in_force")}><option value="GTC">GTC</option><option value="DAY">DAY</option></select></div>
        </div>
        {f.side === "BUY" && (
          <div className="grid grid-cols-2 gap-2">
            <div><label className="label">Stop-loss (bracket)</label><input className="input" type="number" step="any" value={f.stop_loss} onChange={set("stop_loss")} placeholder="optional" /></div>
            <div><label className="label">Take-profit (bracket)</label><input className="input" type="number" step="any" value={f.take_profit} onChange={set("take_profit")} placeholder="optional" /></div>
          </div>
        )}
        {ref && f.quantity && <div className="text-xs text-muted">Est. notional {money(ref * Number(f.quantity))} before slippage & commission</div>}
        <button className={clsx("w-full justify-center py-2", f.side === "BUY" ? "btn-primary" : "btn bg-down text-white border-transparent")} disabled={m.isPending}>{m.isPending ? "Submitting…" : `Place ${f.side.toLowerCase()} order`}</button>
        {msg && <div className={clsx("text-sm", msg.ok ? "text-up" : "text-down")}>{msg.text}</div>}
        <p className="text-[11px] text-muted">Long-only, no margin. Market orders outside regular hours queue for the next open. Stops are not guaranteed: gaps fill at the worse price.</p>
      </form>
    </Card>
  );
}

function NewAccount({ onCreate, pending, error }: { onCreate: (b: any) => void; pending: boolean; error: unknown }) {
  const pre = useQuery({ queryKey: ["fee-presets"], queryFn: () => api("/paper/fee-presets") });
  const [f, setF] = useState({ name: "Revolut practice", initial_cash: "10000", preset: "revolut-standard", fx_fee_pct: "0" });
  const p = pre.data?.presets.find((x: any) => x.id === f.preset);
  return (
    <Card title="New paper account">
      <form className="grid grid-cols-2 sm:grid-cols-5 gap-2 items-end" onSubmit={(e) => { e.preventDefault(); onCreate({ name: f.name, initial_cash: Number(f.initial_cash), preset: f.preset, fx_fee_pct: Number(f.fx_fee_pct) / 100 }); }}>
        <div className="col-span-2 sm:col-span-1"><label className="label">Name</label><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
        <div><label className="label">Starting cash</label><input className="input" type="number" min="1" value={f.initial_cash} onChange={(e) => setF({ ...f, initial_cash: e.target.value })} /></div>
        <div><label className="label">Fee profile</label>
          <select className="input" value={f.preset} onChange={(e) => setF({ ...f, preset: e.target.value })}>{pre.data?.presets.map((x: any) => <option key={x.id} value={x.id}>{x.label}</option>)}</select></div>
        <div><label className="label">FX fee % (EUR→USD)</label><input className="input" type="number" step="0.01" min="0" value={f.fx_fee_pct} onChange={(e) => setF({ ...f, fx_fee_pct: e.target.value })} /></div>
        <button className="btn-primary justify-center py-2" disabled={pending}>Create</button>
      </form>
      {p && <div className="text-xs text-muted mt-2">{p.fee_model === "pct" ? `${pct(p.commission_pct, 2, false)} per order (min ${p.min_commission}) after ${p.free_trades_per_month} free order(s) per month.` : "Per-share commission."}</div>}
      {pre.data && <div className="mt-2"><Note>{pre.data.note}</Note></div>}
      {error ? <div className="mt-2"><ErrorBox error={error} /></div> : null}
    </Card>
  );
}
