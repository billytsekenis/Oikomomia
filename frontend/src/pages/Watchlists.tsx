import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Delta, Empty, ErrorBox, Loading, Note } from "../components/ui";
import { api } from "../lib/api";
import { ago, money } from "../lib/format";

export default function Watchlists() {
  const qc = useQueryClient();
  const wl = useQuery({ queryKey: ["watchlists"], queryFn: () => api("/watchlists"), refetchInterval: 60_000 });
  const [sel, setSel] = useState<number | null>(null);
  useEffect(() => { if (!sel && wl.data?.length) setSel(wl.data[0].id); }, [wl.data, sel]);
  const cur = wl.data?.find((w: any) => w.id === sel);
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["watchlists"] });
  const add = useMutation({ mutationFn: () => api(`/watchlists/${sel}/items`, { body: { ticker: t } }), onSuccess: () => { setT(""); setErr(""); refresh(); }, onError: (e: any) => setErr(e.message) });
  const create = useMutation({ mutationFn: (name: string) => api("/watchlists", { body: { name } }), onSuccess: (r) => { refresh(); setSel(r.id); } });
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Watchlists & alerts</h1>
        <p className="text-xs text-muted">Alerts are checked every 5 minutes by the background scheduler; triggered alerts appear in the bell menu in real time.</p>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Watchlists" right={<button className="btn" onClick={() => { const n = prompt("Watchlist name"); if (n) create.mutate(n); }}>New list</button>}>
          {wl.isLoading ? <Loading rows={5} /> : wl.error ? <ErrorBox error={wl.error} /> : (
            <>
              <div className="flex gap-1.5 flex-wrap mb-3">
                {wl.data.map((w: any) => <button key={w.id} onClick={() => setSel(w.id)} className={`chip py-1 ${w.id === sel ? "border-accent bg-panel2 text-fg" : ""}`}>{w.name} ({w.items.length})</button>)}
              </div>
              {cur && (
                <>
                  <form className="flex gap-2 mb-2" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
                    <input className="input" placeholder="Add ticker (e.g. AMZN)" value={t} onChange={(e) => setT(e.target.value.toUpperCase())} />
                    <button className="btn-primary" disabled={!t || add.isPending}>Add</button>
                    <button type="button" className="btn" onClick={async () => { if (confirm(`Delete ${cur.name}?`)) { await api(`/watchlists/${cur.id}`, { method: "DELETE" }); setSel(null); refresh(); } }}>Delete list</button>
                  </form>
                  {err && <div className="text-sm text-down mb-2">{err}</div>}
                  <table className="w-full"><thead><tr><th className="th">Ticker</th><th className="th text-right">Price</th><th className="th text-right">Change</th><th className="th">Source</th><th /></tr></thead>
                    <tbody>{cur.items.map((i: any) => (
                      <tr key={i.ticker} className="border-t border-line">
                        <td className="td"><Link to={`/stock/${i.ticker}`} className="font-semibold hover:text-accent">{i.ticker}</Link> <span className="text-[11px] text-muted">{i.quote?.name}</span></td>
                        <td className="td text-right">{money(i.quote?.price)}</td><td className="td text-right"><Delta v={i.quote?.change_pct != null ? i.quote.change_pct / 100 : null} /></td>
                        <td className="td text-[11px] text-muted max-w-[160px] truncate" title={i.quote?.source}>{i.quote?.error ? <span className="text-down">{i.quote.error}</span> : `${i.quote?.source} · ${ago(i.quote?.fetched_at)}`}</td>
                        <td className="td"><button aria-label={`Remove ${i.ticker}`} onClick={async () => { await api(`/watchlists/${cur.id}/items/${i.ticker}`, { method: "DELETE" }); refresh(); }}><Trash2 size={14} className="text-muted hover:text-down" /></button></td>
                      </tr>
                    ))}</tbody></table>
                  {cur.items.length === 0 && <Empty>Empty watchlist.</Empty>}
                </>
              )}
            </>
          )}
        </Card>
        <Alerts />
      </div>
    </div>
  );
}

function Alerts() {
  const qc = useQueryClient();
  const kinds = useQuery({ queryKey: ["alert-kinds"], queryFn: () => api("/alerts/kinds") });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api("/alerts") });
  const pfs = useQuery({ queryKey: ["portfolios"], queryFn: () => api("/portfolios") });
  const [kind, setKind] = useState("price_above");
  const [ticker, setTicker] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const [repeat, setRepeat] = useState(false);
  const [msg, setMsg] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["notifications"] }); };
  const create = useMutation({
    mutationFn: () => {
      const spec = kinds.data[kind].params;
      const p: Record<string, any> = {};
      Object.entries(spec).forEach(([k, typ]: any) => { p[k] = typ === "number" ? Number(params[k] ?? "") : params[k] ?? typ.split("|")[0]; });
      return api("/alerts", { body: { kind, ticker: kind === "portfolio_risk" ? null : ticker, params: p, repeat } });
    },
    onSuccess: (r) => { setMsg(r.triggered_now ? `Created - triggered immediately: ${r.triggered_now}` : "Alert created."); refresh(); },
    onError: (e: any) => setMsg(e.message),
  });
  const check = useMutation({ mutationFn: () => api("/alerts/check", { method: "POST" }), onSuccess: (r) => { setMsg(r.triggered.length ? r.triggered.join(" | ") : "No alerts triggered."); refresh(); } });
  if (kinds.isLoading) return <Card title="Alerts"><Loading rows={5} /></Card>;
  const spec = kinds.data[kind]?.params || {};
  const HINT: Record<string, string> = { var_pct: "e.g. 0.03 = 3% daily VaR", drawdown: "e.g. 0.15", max_weight: "e.g. 0.25" };
  return (
    <Card title="Alerts" right={<button className="btn" onClick={() => check.mutate()} disabled={check.isPending}>Check now</button>}>
      <form className="grid grid-cols-2 gap-2 items-end" onSubmit={(e) => { e.preventDefault(); setMsg(""); create.mutate(); }}>
        <div className="col-span-2"><label className="label">Alert type</label>
          <select className="input" value={kind} onChange={(e) => { setKind(e.target.value); setParams({}); }}>
            {Object.entries(kinds.data).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        {kind !== "portfolio_risk" && <div><label className="label">Ticker</label><input className="input" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} required /></div>}
        {Object.entries(spec).map(([k, typ]: any) => (
          <div key={k}><label className="label">{k}{k === "threshold" && params.metric ? ` (${HINT[params.metric] || ""})` : ""}</label>
            {k === "portfolio_id" ? (
              <select className="input" value={params[k] || ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} required>
                <option value="">Select…</option>{pfs.data?.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            ) : typ === "number" ? <input className="input" type="number" step="any" value={params[k] || ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} required />
              : <select className="input" value={params[k] || typ.split("|")[0]} onChange={(e) => setParams({ ...params, [k]: e.target.value })}>{typ.split("|").map((o: string) => <option key={o}>{o}</option>)}</select>}
          </div>
        ))}
        <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} /> Repeat (max once a day)</label>
        <button className="btn-primary justify-center" disabled={create.isPending}>Create alert</button>
      </form>
      {msg && <div className="mt-2"><Note>{msg}</Note></div>}
      <div className="mt-4 divide-y divide-line">
        {alerts.data?.length === 0 && <Empty>No alerts yet.</Empty>}
        {alerts.data?.map((a: any) => (
          <div key={a.id} className="py-2 flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm"><b>{a.ticker || "Portfolio"}</b> · {a.label} <span className="text-muted text-xs">{JSON.stringify(a.params)}</span></div>
              <div className="text-[11px] text-muted">
                {a.active ? <Badge tone="up">active</Badge> : <Badge>inactive</Badge>} {a.repeat && <Badge>repeat</Badge>} checked {ago(a.last_checked_at)}
                {a.last_triggered_at && <> · last triggered {ago(a.last_triggered_at)}</>}
                {a.state?.last_error && <span className="text-warn"> · {a.state.last_error}</span>}
              </div>
            </div>
            <div className="flex gap-2 shrink-0">
              <button className="text-xs text-muted hover:text-fg" onClick={async () => { await api(`/alerts/${a.id}?active=${!a.active}`, { method: "PATCH" }); refresh(); }}>{a.active ? "Pause" : "Resume"}</button>
              <button aria-label="Delete alert" onClick={async () => { await api(`/alerts/${a.id}`, { method: "DELETE" }); refresh(); }}><Trash2 size={14} className="text-muted hover:text-down" /></button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
