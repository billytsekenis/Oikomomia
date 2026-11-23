import { useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { api, setToken } from "../lib/api";

export default function Login({ onAuth }: { onAuth: (u: any) => void }) {
  const cfg = useQuery({ queryKey: ["authcfg"], queryFn: () => api("/auth/config") });
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const effective = cfg.data && !cfg.data.has_users ? "register" : mode;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await api(`/auth/${effective}`, { body: { email, password } });
      setToken(r.token);
      onAuth(r.user);
    } catch (ex: any) { setErr(ex.message); } finally { setBusy(false); }
  };
  return (
    <div className="min-h-full flex items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6 space-y-4">
        <div>
          <div className="text-xl font-semibold tracking-tight">Oikonomia<span className="text-accent">.</span></div>
          <div className="text-sm text-muted">{effective === "register" ? "Create your account" : "Sign in to your terminal"}</div>
        </div>
        <div><label className="label" htmlFor="email">Email</label><input id="email" className="input" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
        <div><label className="label" htmlFor="pw">Password (min 8 characters)</label><input id="pw" className="input" type="password" autoComplete={effective === "register" ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} /></div>
        {err && <div className="text-sm text-down">{err}</div>}
        <button className="btn-primary w-full justify-center py-2" disabled={busy}>{busy ? "…" : effective === "register" ? "Create account" : "Sign in"}</button>
        {cfg.data?.has_users && cfg.data?.registration_open && (
          <button type="button" className="text-xs text-muted hover:text-fg w-full" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "No account? Register" : "Have an account? Sign in"}
          </button>
        )}
        <p className="text-[11px] text-muted leading-snug">Research tool. Forecasts are probabilistic and validated out-of-sample; nothing here is investment advice.</p>
      </form>
    </div>
  );
}
