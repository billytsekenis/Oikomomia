export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

let token: string | null = null;
try { token = localStorage.getItem("token"); } catch { /* private mode */ }
export const setToken = (t: string | null) => {
  token = t;
  try { t ? localStorage.setItem("token", t) : localStorage.removeItem("token"); } catch { /* ignore */ }
};
export const getToken = () => token;

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers, credentials: "same-origin", signal: opts.signal,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("oik:unauthorized"));
    throw new ApiError(res.status, msg);
  }
  return res.json();
}
