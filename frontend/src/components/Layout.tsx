import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Command } from "cmdk";
import {
  Bell, Bot, BriefcaseBusiness, CandlestickChart, Compass, Database, FlaskConical, History, LayoutDashboard, LogOut,
  Menu, Moon, Search, Star, Sun, X,
} from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, getToken } from "../lib/api";
import { ago } from "../lib/format";
import { useAuth } from "../App";

export const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/analyst", label: "AI Analyst", icon: Bot },
  { to: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness },
  { to: "/paper", label: "Paper Trading", icon: CandlestickChart },
  { to: "/backtest", label: "Backtesting", icon: FlaskConical },
  { to: "/track-record", label: "Track Record", icon: History },
  { to: "/watchlists", label: "Watchlists & Alerts", icon: Star },
  { to: "/system", label: "Data & System", icon: Database },
];

function useTheme() {
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));
  const toggle = () => {
    const d = !dark;
    document.documentElement.classList.toggle("dark", d);
    try { localStorage.setItem("theme", d ? "dark" : "light"); } catch { /* ignore */ }
    setDark(d);
  };
  return { dark, toggle };
}

function MarketStatus() {
  const { data } = useQuery({ queryKey: ["hours"], queryFn: () => api("/market/hours"), refetchInterval: 60_000 });
  if (!data) return null;
  const st: string = data.state;
  return (
    <span className="hidden sm:inline-flex items-center gap-1.5 text-xs text-muted" title={`Next open ${data.next_open}`}>
      <span className={clsx("h-2 w-2 rounded-full", st === "open" ? "bg-up" : st === "closed" ? "bg-muted" : "bg-warn")} />
      NYSE {st}
    </span>
  );
}

function Notifications() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data = [] } = useQuery<any[]>({ queryKey: ["notifications"], queryFn: () => api("/notifications"), refetchInterval: 120_000 });
  const unread = data.filter((n) => !n.read).length;
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    let stop = false;
    const connect = () => {
      ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(getToken() || "")}`);
      ws.onmessage = (e) => { try { if (JSON.parse(e.data).type === "alert") qc.invalidateQueries({ queryKey: ["notifications"] }); } catch { /* ignore */ } };
      ws.onclose = () => { if (!stop) setTimeout(connect, 15_000); };
    };
    connect();
    const ping = setInterval(() => ws?.readyState === 1 && ws.send("ping"), 30_000);
    return () => { stop = true; clearInterval(ping); ws?.close(); };
  }, [qc]);
  return (
    <div className="relative">
      <button className="btn px-2" aria-label="Notifications" onClick={async () => { setOpen(!open); if (unread) { await api("/notifications/read", { method: "POST" }); qc.invalidateQueries({ queryKey: ["notifications"] }); } }}>
        <Bell size={16} />{unread > 0 && <span className="text-[10px] rounded-full bg-down text-white px-1.5">{unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50 w-80 max-h-96 overflow-auto card p-2 shadow-2xl">
          {data.length === 0 && <div className="text-sm text-muted p-3">No alerts triggered yet.</div>}
          {data.map((n) => (
            <div key={n.id} className="px-2 py-2 border-b border-line last:border-0">
              <div className="text-sm">{n.message}</div>
              <div className="text-[11px] text-muted">{n.kind} · {ago(n.created_at)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CommandPalette({ open, setOpen }: { open: boolean; setOpen: (b: boolean) => void }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["search", q], queryFn: () => api(`/stocks/search?q=${encodeURIComponent(q)}`), enabled: open, staleTime: 60_000 });
  useEffect(() => { if (!open) setQ(""); }, [open]);
  if (!open) return null;
  const go = (to: string) => { setOpen(false); nav(to); };
  return (
    <div className="fixed inset-0 z-[100] bg-black/50 flex items-start justify-center p-4 pt-[12vh]" onClick={() => setOpen(false)}>
      <Command label="Command palette" className="w-full max-w-xl card shadow-2xl overflow-hidden" onClick={(e) => e.stopPropagation()} shouldFilter={false}>
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search size={16} className="text-muted" />
          <Command.Input autoFocus value={q} onValueChange={setQ} placeholder="Search a ticker or company, or jump to a page…" className="w-full bg-transparent py-3 text-sm outline-none" />
          <kbd className="text-[10px] text-muted border border-line rounded px-1">esc</kbd>
        </div>
        <Command.List className="max-h-[60vh] overflow-auto p-2">
          <Command.Empty className="p-3 text-sm text-muted">No results.</Command.Empty>
          {q.trim() && (data?.results?.length ?? 0) > 0 && (
            <Command.Group heading="Securities" className="text-[11px] text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
              {data.results.map((r: any) => (
                <Command.Item key={r.ticker} value={r.ticker} onSelect={() => go(`/stock/${r.ticker}`)}
                              className="flex items-center justify-between rounded-md px-2 py-2 text-sm cursor-pointer data-[selected=true]:bg-panel2">
                  <span><span className="font-semibold">{r.ticker}</span> <span className="text-muted">{r.name}</span></span>
                  <span className="text-[11px] text-muted">{r.in_universe ? r.sector : "SEC-listed"}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
          <Command.Group heading="Pages" className="text-[11px] text-muted [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1">
            {NAV.filter((n) => !q || n.label.toLowerCase().includes(q.toLowerCase())).map((n) => (
              <Command.Item key={n.to} value={n.label} onSelect={() => go(n.to)} className="flex items-center gap-2 rounded-md px-2 py-2 text-sm cursor-pointer data-[selected=true]:bg-panel2">
                <n.icon size={15} className="text-muted" />{n.label}
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { dark, toggle } = useTheme();
  const [palette, setPalette] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const loc = useLocation();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api("/health"), staleTime: 300_000 });
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
      if (e.key === "Escape") setPalette(false);
      if (e.key === "/" && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) { e.preventDefault(); setPalette(true); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  useEffect(() => { setDrawer(false); mainRef.current?.scrollTo(0, 0); }, [loc.pathname]);

  const navList = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map((n) => (
        <NavLink key={n.to} to={n.to} end={n.to === "/"}
                 className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                   isActive ? "bg-accent/12 text-fg font-medium bg-panel2" : "text-muted hover:text-fg hover:bg-panel2")}>
          <n.icon size={16} />{n.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden lg:flex w-60 shrink-0 flex-col border-r border-line bg-panel px-3 py-4">
        <div className="px-2 mb-5">
          <div className="text-base font-semibold tracking-tight">Oikonomia<span className="text-accent">.</span></div>
          <div className="text-[11px] text-muted">Market intelligence terminal</div>
        </div>
        {navList}
        <div className="mt-auto px-2 text-[11px] text-muted space-y-1">
          <div className="truncate">{user?.email}</div>
          <button onClick={logout} className="inline-flex items-center gap-1 hover:text-fg"><LogOut size={12} /> Sign out</button>
          <p className="leading-snug pt-2">Analysis only - not investment advice. No real orders are ever placed.</p>
        </div>
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-50 lg:hidden bg-black/50" onClick={() => setDrawer(false)}>
          <aside className="absolute left-0 top-0 h-full w-72 bg-panel border-r border-line p-4 flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <div className="font-semibold">Oikonomia<span className="text-accent">.</span></div>
              <button onClick={() => setDrawer(false)} aria-label="Close menu"><X size={18} /></button>
            </div>
            {navList}
            <div className="mt-auto text-xs text-muted">
              <div className="truncate mb-1">{user?.email}</div>
              <button onClick={logout} className="inline-flex items-center gap-1"><LogOut size={12} /> Sign out</button>
            </div>
          </aside>
        </div>
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        {health?.demo_mode && (
          <div className="bg-warn text-black text-xs font-semibold text-center py-1.5 px-2">
            DEMO MODE - all prices, fundamentals and news are SYNTHETIC and not real market data
          </div>
        )}
        <header className="flex items-center gap-2 border-b border-line bg-panel/80 backdrop-blur px-3 sm:px-4 py-2 sticky top-0 z-40">
          <button className="lg:hidden btn px-2" onClick={() => setDrawer(true)} aria-label="Open menu"><Menu size={16} /></button>
          <button onClick={() => setPalette(true)} className="flex-1 min-w-0 max-w-md flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-1.5 text-sm text-muted hover:text-fg">
            <Search size={15} /><span className="truncate">Search ticker or command…</span>
            <kbd className="ml-auto hidden sm:inline text-[10px] border border-line rounded px-1">⌘K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-2">
            <MarketStatus />
            <Notifications />
            <button className="btn px-2" onClick={toggle} aria-label="Toggle theme">{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
          </div>
        </header>
        <main ref={mainRef} className="flex-1 overflow-y-auto overflow-x-hidden px-3 sm:px-5 py-4 pb-24 lg:pb-6">
          <div className="mx-auto max-w-[1500px]">{children}</div>
        </main>
        <nav className="lg:hidden fixed bottom-0 inset-x-0 z-40 border-t border-line bg-panel/95 backdrop-blur grid grid-cols-5 safe-bottom">
          {NAV.slice(0, 4).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => clsx("flex flex-col items-center gap-0.5 py-2 text-[10px]", isActive ? "text-accent" : "text-muted")}>
              <n.icon size={19} />{n.label.replace("AI ", "")}
            </NavLink>
          ))}
          <button onClick={() => setDrawer(true)} className="flex flex-col items-center gap-0.5 py-2 text-[10px] text-muted"><Menu size={19} />More</button>
        </nav>
      </div>
      <CommandPalette open={palette} setOpen={setPalette} />
    </div>
  );
}
