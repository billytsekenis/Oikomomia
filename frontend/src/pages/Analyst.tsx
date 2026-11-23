import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Send, ShieldAlert, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Note, Spinner } from "../components/ui";
import { api } from "../lib/api";

type Msg = { role: "user" | "assistant"; content: string; meta?: any };

const EXAMPLES = [
  "What is the best investment opportunity today based on the available data?",
  "Which stock shows the best risk-adjusted return?",
  "Why did NVDA move today?",
  "What are the arguments for and against buying AAPL?",
  "What data would make you change your mind on MSFT?",
  "What happens to my portfolio if the Nasdaq falls 10%?",
  "Ποια είναι η καλύτερη επενδυτική ευκαιρία σήμερα;",
  "Confirm that TSLA will definitely double next year",
];
const KEY = "analyst-chat";

export default function Analyst() {
  const [params] = useSearchParams();
  const ctxTicker = params.get("ticker") || undefined;
  const [msgs, setMsgs] = useState<Msg[]>(() => { try { return JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch { return []; } });
  const [text, setText] = useState(ctxTicker ? `What are the arguments for and against ${ctxTicker}?` : "");
  const status = useQuery({ queryKey: ["analyst-status"], queryFn: () => api("/analyst/status") });
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(msgs.slice(-30))); } catch { /* ignore */ } end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  const send = useMutation({
    mutationFn: (m: string) => api("/analyst/chat", { body: { message: m, ticker: ctxTicker, history: msgs.slice(-8).map(({ role, content }) => ({ role, content })) } }),
    onSuccess: (r) => setMsgs((x) => [...x, { role: "assistant", content: r.answer, meta: r }]),
    onError: (e: any) => setMsgs((x) => [...x, { role: "assistant", content: `**Error:** ${e.message}` }]),
  });
  const submit = (m: string) => {
    if (!m.trim() || send.isPending) return;
    setMsgs((x) => [...x, { role: "user", content: m }]);
    setText("");
    send.mutate(m);
  };
  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-4 h-full">
      <div className="xl:col-span-3 flex flex-col min-h-[70vh]">
        <div className="mb-3">
          <h1 className="text-xl font-semibold tracking-tight">AI financial analyst</h1>
          <p className="text-xs text-muted">Answers only from the platform's verified data and engines. It will challenge your premise when the data disagree.</p>
        </div>
        <div className="flex-1 card p-3 sm:p-4 overflow-y-auto space-y-4 min-h-[50vh]">
          {msgs.length === 0 && (
            <div className="grid sm:grid-cols-2 gap-2">
              {EXAMPLES.map((e) => <button key={e} onClick={() => submit(e)} className="text-left text-sm rounded-lg border border-line p-3 hover:bg-panel2">{e}</button>)}
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className="flex gap-3">
              <div className="mt-0.5 shrink-0">{m.role === "user" ? <User size={18} className="text-muted" /> : <Bot size={18} className="text-accent" />}</div>
              <div className="min-w-0 flex-1">
                {m.role === "user" ? <div className="text-sm font-medium whitespace-pre-wrap">{m.content}</div> : (
                  <>
                    {m.meta?.flags?.length > 0 && (
                      <div className="flex flex-wrap gap-1 mb-2">
                        {m.meta.flags.map((f: any, j: number) => <Badge key={j} tone="warn"><ShieldAlert size={11} className="mr-1" />{f.type.replace(/_/g, " ")}</Badge>)}
                      </div>
                    )}
                    <div className="prose-sm text-sm leading-relaxed"><ReactMarkdown>{m.content}</ReactMarkdown></div>
                    {m.meta && (
                      <details className="mt-2">
                        <summary className="text-[11px] text-muted cursor-pointer">Engine: {m.meta.engine} · LLM: {m.meta.llm_status} · evidence ({m.meta.evidence?.length || 0} items)</summary>
                        <pre className="mt-1 text-[11px] bg-panel2 rounded p-2 overflow-x-auto">{JSON.stringify({ intents: m.meta.intents, tickers: m.meta.tickers, claims: m.meta.claims, evidence: m.meta.evidence }, null, 2)}</pre>
                      </details>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          {send.isPending && <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> Gathering evidence from the engines…</div>}
          <div ref={end} />
        </div>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about a stock, the market or your portfolio (English or Ελληνικά)…" aria-label="Message" />
          <button className="btn-primary" disabled={send.isPending || !text.trim()}><Send size={15} /></button>
        </form>
        {msgs.length > 0 && <button className="text-xs text-muted mt-2 self-start" onClick={() => setMsgs([])}>Clear conversation</button>}
      </div>
      <div className="space-y-3">
        <Card title="How it works">
          <ul className="text-xs text-muted space-y-1.5 list-disc pl-4">
            <li>A deterministic evidence engine queries quotes, SEC fundamentals, validated forecasts, discovery scores, regime and your portfolio.</li>
            <li>Every number in an answer comes from that evidence pack.</li>
            <li>Detects confirmation & authority bias, guarantees, false precision, absolute assumptions and fabricated figures (checked against SEC/market data).</li>
            <li>News text is screened for prompt injection; flagged items never reach the analyst.</li>
            <li>It revises its view when the data change, not when you insist.</li>
          </ul>
        </Card>
        <Card title="Language model">
          {status.data?.llm_available ? <Note>Claude rewrites answers conversationally. Any reply containing a number not in the evidence is rejected automatically.</Note>
            : <Note>Running on the deterministic engine only. Set <code>ANTHROPIC_API_KEY</code> on the server to enable conversational answers (still guarded by the numeric verifier).</Note>}
        </Card>
      </div>
    </div>
  );
}
