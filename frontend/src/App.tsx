import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, lazy, Suspense, useContext, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { api, setToken } from "./lib/api";
import Login from "./pages/Login";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Discover = lazy(() => import("./pages/Discover"));
const Stock = lazy(() => import("./pages/Stock"));
const Analyst = lazy(() => import("./pages/Analyst"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const Paper = lazy(() => import("./pages/Paper"));
const Backtest = lazy(() => import("./pages/Backtest"));
const TrackRecord = lazy(() => import("./pages/TrackRecord"));
const Watchlists = lazy(() => import("./pages/Watchlists"));
const System = lazy(() => import("./pages/System"));

type User = { id: number; email: string; base_currency: string };
const AuthCtx = createContext<{ user: User | null; setUser: (u: User | null) => void; logout: () => void }>({
  user: null, setUser: () => {}, logout: () => {},
});
export const useAuth = () => useContext(AuthCtx);

export default function App() {
  const qc = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api("/auth/me"), retry: false });
  useEffect(() => { if (me.data?.user) setUser(me.data.user); }, [me.data]);
  useEffect(() => {
    const h = () => { setUser(null); setToken(null); };
    window.addEventListener("oik:unauthorized", h);
    return () => window.removeEventListener("oik:unauthorized", h);
  }, []);
  const logout = async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    setToken(null); setUser(null); qc.clear();
  };
  if (me.isLoading) return <div className="p-8"><Loading /></div>;
  if (!user) return <Login onAuth={(u) => { setUser(u); qc.invalidateQueries(); }} />;
  return (
    <AuthCtx.Provider value={{ user, setUser, logout }}>
      <Layout>
        <Suspense fallback={<Loading rows={8} />}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/stock/:ticker" element={<Stock />} />
            <Route path="/analyst" element={<Analyst />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/paper" element={<Paper />} />
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/track-record" element={<TrackRecord />} />
            <Route path="/watchlists" element={<Watchlists />} />
            <Route path="/system" element={<System />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </Suspense>
      </Layout>
    </AuthCtx.Provider>
  );
}
