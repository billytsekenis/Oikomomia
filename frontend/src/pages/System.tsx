import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Card, ErrorBox, Loading, Note } from "../components/ui";
import { api } from "../lib/api";
import { ago } from "../lib/format";

const JOBS = [["train", "Retrain & re-validate model"], ["discovery", "Rebuild discovery scan"], ["log_predictions", "Log today's forecasts"],
  ["evaluate", "Evaluate due predictions"], ["alerts", "Check alerts"], ["paper_orders", "Process paper orders"], ["equity_snapshot", "Snapshot paper equity"],
  ["system_strategy", "Run system paper strategy"]];

export default function System() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["sys-status"], queryFn: () => api("/system/status"), refetchInterval: 10_000 });
  const ds = useQuery({ queryKey: ["sys-ds"], queryFn: () => api("/system/data-sources") });
  const run = useMutation({ mutationFn: (j: string) => api(`/system/jobs/${j}`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["sys-status"] }) });
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Data sources & system</h1>
        <p className="text-xs text-muted">Where every number comes from, how fresh it is, and what runs in the background.</p>
      </div>
      {st.isLoading ? <Loading rows={4} /> : st.error ? <ErrorBox error={st.error} /> : (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card title="Configuration">
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between"><span className="text-muted">Mode</span>{st.data.demo_mode ? <Badge tone="warn">DEMO (synthetic data)</Badge> : <Badge tone="up">Live data</Badge>}</div>
              {Object.entries(st.data.keys).map(([k, v]: any) => <div key={k} className="flex justify-between"><span className="text-muted">{k}</span>{v ? <Badge tone="up">configured</Badge> : <Badge>not set</Badge>}</div>)}
              {st.data.model && <div className="flex justify-between"><span className="text-muted">Model</span><span className="text-xs">{st.data.model.version} · trained {ago(st.data.model.trained_at)} · {st.data.model.n_tickers} stocks</span></div>}
            </div>
            <div className="mt-3"><Note>Secrets are read from environment variables on the server and are never sent to the browser - only whether each one is set.</Note></div>
          </Card>
          <Card title="Provider health">
            {Object.keys(st.data.provider_health).length === 0 && <div className="text-sm text-muted">No requests yet.</div>}
            {Object.entries(st.data.provider_health).map(([h, v]: any) => (
              <div key={h} className="flex justify-between text-sm py-0.5"><span className="text-xs">{h}</span>
                {v.blocked_for_s > 0 ? <Badge tone="down">cooling down {v.blocked_for_s}s (rate-limited)</Badge> : v.recent_failures > 0 ? <Badge tone="warn">{v.recent_failures} recent failures</Badge> : <Badge tone="up">ok</Badge>}</div>
            ))}
          </Card>
          <Card title="Background jobs">
            <div className="grid grid-cols-2 gap-1.5">
              {JOBS.map(([j, l]) => <button key={j} className="btn text-xs justify-center" onClick={() => run.mutate(j)} disabled={!!st.data.jobs.running[j]}>{st.data.jobs.running[j] ? `${Math.round((st.data.jobs.running[j].progress || 0) * 100)}%…` : l}</button>)}
            </div>
            <div className="mt-3 max-h-60 overflow-auto scrollbar-thin divide-y divide-line">
              {st.data.jobs.recent.map((r: any, i: number) => (
                <div key={i} className="py-1 text-xs"><b>{r.name}</b> <Badge tone={r.status === "ok" ? "up" : r.status === "error" ? "down" : "warn"}>{r.status}</Badge> <span className="text-muted">{ago(r.started_at)}</span>
                  <div className="text-[10px] text-muted truncate" title={r.message}>{r.message}</div></div>
              ))}
            </div>
          </Card>
        </div>
      )}
      {ds.data && (
        <>
          <Card title="Data sources in use" pad={false}>
            <div className="overflow-x-auto scrollbar-thin"><table className="w-full">
              <thead><tr>{["Source", "Used for", "Cost", "Rate limits", "Freshness", "History", "Real-time?", "Licensing"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{ds.data.sources.map((s: any) => (
                <tr key={s.name} className="border-t border-line align-top">{["name", "used_for", "cost", "rate_limits", "freshness", "history", "realtime", "licensing"].map((k) => <td key={k} className="td text-xs whitespace-normal min-w-[120px]">{s[k]}</td>)}</tr>
              ))}</tbody></table></div>
          </Card>
          <Card title="Evaluated but not used">
            <ul className="text-xs space-y-1">{ds.data.evaluated_not_used.map((s: any) => <li key={s.name}><b>{s.name}</b>: <span className="text-muted">{s.why}</span></li>)}</ul>
          </Card>
          <Card title={`Cached price series (${ds.data.series.length})`} pad={false}>
            <div className="overflow-x-auto scrollbar-thin max-h-96"><table className="w-full">
              <thead className="sticky top-0 bg-panel"><tr>{["Symbol", "Source", "Adjustment", "Last bar", "Fetched", "Validation notes"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{ds.data.series.sort((a: any, b: any) => a.ticker.localeCompare(b.ticker)).map((s: any) => (
                <tr key={s.ticker} className="border-t border-line"><td className="td font-medium">{s.ticker}</td><td className="td text-xs">{s.source}</td><td className="td text-xs">{s.adjusted}</td><td className="td text-xs">{s.last_date}</td><td className="td text-xs">{ago(s.fetched_at)}</td><td className="td text-[11px] text-muted whitespace-normal">{s.error}</td></tr>
              ))}</tbody></table></div>
          </Card>
        </>
      )}
    </div>
  );
}
