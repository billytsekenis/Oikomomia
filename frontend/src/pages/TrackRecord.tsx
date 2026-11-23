import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorBox, Loading, Note, Segmented, Stat } from "../components/ui";
import { api } from "../lib/api";
import { ago, money, num, pct, signClass } from "../lib/format";

const H = ["1D", "7D", "30D", "90D", "6M", "12M"];

export default function TrackRecord() {
  const qc = useQueryClient();
  const [h, setH] = useState("");
  const [status, setStatus] = useState("");
  const [ticker, setTicker] = useState("");
  const q = useQuery({ queryKey: ["track", h, status, ticker], queryFn: () => api(`/track-record?horizon=${h}&status=${status}&ticker=${ticker}&limit=500`) });
  const ev = useMutation({ mutationFn: () => api("/track-record/evaluate", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["track"] }) });
  const log = useMutation({ mutationFn: () => api("/system/jobs/log_predictions", { method: "POST" }) });
  if (q.isLoading) return <Loading rows={10} />;
  if (q.error) return <ErrorBox error={q.error} />;
  const sum = q.data.summary;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Prediction track record</h1>
          <p className="text-xs text-muted">Live, forward-only record: each forecast is time-stamped when made and scored when its horizon ends.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => ev.mutate()} disabled={ev.isPending}>{ev.isPending ? "Evaluating…" : "Evaluate due predictions"}</button>
          <button className="btn" onClick={() => log.mutate()} disabled={log.isPending}>{log.isSuccess ? "Logging started" : "Log today's forecasts"}</button>
        </div>
      </div>
      {ev.data && <Note>Evaluated {ev.data.evaluated} · awaiting data {ev.data.awaiting_data} · checked {ev.data.checked}</Note>}
      <Note>{q.data.policy} This record is free of look-ahead and survivorship bias by construction, which makes it the most honest test of the system - but it needs time: meaningful statistics require 100+ evaluated predictions per horizon. Until then, rely on the walk-forward validation in Backtesting.</Note>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {H.map((x) => {
          const s = sum.by_horizon[x];
          return (
            <div key={x} className="card p-3">
              <div className="flex justify-between items-center"><span className="font-semibold">{x}</span>{s && !s.sufficient && s.evaluated > 0 && <Badge tone="warn">small sample</Badge>}</div>
              {s ? (
                <div className="mt-2 space-y-1 text-xs">
                  <div className="flex justify-between"><span className="text-muted">Logged / evaluated</span><span className="num">{s.total} / {s.evaluated}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Direction hit</span><span className="num">{pct(s.directional_accuracy, 1, false)}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Realised up-rate</span><span className="num">{pct(s.realized_up_rate, 1, false)}</span></div>
                  <div className="flex justify-between"><span className="text-muted">In 80% / 90% band</span><span className="num">{pct(s.coverage_80, 0, false)} / {pct(s.coverage_90, 0, false)}</span></div>
                  <div className="flex justify-between"><span className="text-muted">Brier (vs base)</span><span className="num">{num(s.brier, 3)} ({num(s.brier_vs_hindsight_base_rate, 3)})</span></div>
                </div>
              ) : <div className="text-xs text-muted mt-2">No predictions yet</div>}
            </div>
          );
        })}
      </div>
      <Card title="All predictions (including failures)" sub={`${sum.n_records} records${sum.first_logged ? ` since ${ago(sum.first_logged)}` : ""}`} pad={false}
            right={<div className="flex gap-2 items-center flex-wrap">
              <Segmented value={h} onChange={setH} options={[{ id: "", label: "All" }, ...H.map((x) => ({ id: x, label: x }))]} />
              <Segmented value={status} onChange={setStatus} options={[{ id: "", label: "All" }, { id: "pending", label: "Pending" }, { id: "evaluated", label: "Scored" }]} />
              <input className="input py-1 w-24" placeholder="Ticker" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} />
            </div>}>
        {q.data.items.length === 0 ? <Empty>No predictions logged yet. The scheduler logs forecasts for the universe after every session close; you can also trigger it above.</Empty> : (
          <div className="overflow-x-auto scrollbar-thin max-h-[640px]">
            <table className="w-full">
              <thead className="sticky top-0 bg-panel"><tr>{["Logged", "Ticker", "Horizon", "As of", "Target", "Price", "Median", "P(up)", "80% range", "Status", "Actual", "Hit", "In 80%", "Brier"].map((x) => <th key={x} className="th">{x}</th>)}</tr></thead>
              <tbody>{q.data.items.map((r: any) => (
                <tr key={r.id} className="border-t border-line">
                  <td className="td text-xs text-muted" title={r.created_at}>{ago(r.created_at)}</td>
                  <td className="td"><Link to={`/stock/${r.ticker}`} className="font-semibold hover:text-accent">{r.ticker}</Link></td>
                  <td className="td">{r.horizon}</td><td className="td text-xs">{r.as_of}</td><td className="td text-xs">{r.target_date}</td>
                  <td className="td">{money(r.price)}</td><td className={clsx("td", signClass(r.median_return))}>{pct(r.median_return, 1)}</td><td className="td">{pct(r.p_up, 0, false)}</td>
                  <td className="td text-xs">{pct(r.range_80[0], 1)} / {pct(r.range_80[1], 1)}</td>
                  <td className="td"><Badge tone={r.status === "evaluated" ? "accent" : "neutral"}>{r.status}</Badge></td>
                  <td className={clsx("td", signClass(r.actual_return))}>{r.actual_return == null ? "—" : pct(r.actual_return, 1)}</td>
                  <td className="td">{r.direction_hit == null ? "—" : r.direction_hit ? <span className="text-up">✓</span> : <span className="text-down">✗</span>}</td>
                  <td className="td">{r.in_80 == null ? "—" : r.in_80 ? "yes" : "no"}</td><td className="td">{num(r.brier, 3)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="grid grid-cols-2 gap-3"><Stat label="Today" value={q.data.today} /></div>
    </div>
  );
}
