"""FastAPI application: REST API, WebSocket push channel and the built SPA."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import jobs
from .api import (analyst_api, auth_api, backtest_api, discovery_api, market_api, paper_api, portfolio_api,
                  stock_api, system_api, watch_api)
from .config import BACKEND_DIR, get_settings
from .data.http import ProviderError
from .db import SessionLocal
from .migrate import upgrade
from .security import COOKIE_NAME, user_from_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oik")
STATIC = Path(BACKEND_DIR / "static")


class Hub:
    """Per-user WebSocket fan-out; thread-safe entry point for background jobs."""

    def __init__(self):
        self.conns: dict[int, set[WebSocket]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    async def add(self, uid: int, ws: WebSocket):
        self.conns.setdefault(uid, set()).add(ws)

    def remove(self, uid: int, ws: WebSocket):
        self.conns.get(uid, set()).discard(ws)

    async def send(self, uid: int, payload: dict):
        for ws in list(self.conns.get(uid, set())):
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:  # noqa: BLE001
                self.remove(uid, ws)

    def send_threadsafe(self, uid: int, payload: dict):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.send(uid, payload), self.loop)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade()
    hub.loop = asyncio.get_running_loop()
    jobs.notifier = hub.send_threadsafe
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(title="Oikonomia Terminal", version="1.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(ProviderError)
async def provider_error(_: Request, exc: ProviderError):
    return JSONResponse({"detail": f"Data provider unavailable: {exc}"}, status_code=503)


for r in (auth_api, market_api, stock_api, discovery_api, analyst_api, portfolio_api, paper_api, backtest_api, watch_api, system_api):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "demo_mode": s.demo_mode, "version": app.version}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.query_params.get("token") or ws.cookies.get(COOKIE_NAME)
    with SessionLocal() as db:
        user = user_from_token(db, token)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.add(user.id, ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", "user": user.email}))
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(user.id, ws)


# ------------------------------------------------------------------- SPA
@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    f = (STATIC / path).resolve()
    if path and f.is_file() and STATIC.resolve() in f.parents:
        return FileResponse(f)
    index = STATIC / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
