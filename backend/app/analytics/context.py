"""Shared, in-memory cached analytical context: market frame (SPY, VIX, rates),
trained model artefacts, and universe snapshots."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import DATA_DIR
from ..data import fundamentals_store as fs
from ..data import market
from ..data.cache import get_entry, put
from ..data.http import ProviderError
from . import model_engine
from .features import market_frame

log = logging.getLogger("oik.ctx")
_lock = threading.Lock()
_ctx: dict = {}
MODEL_DIR = DATA_DIR / "models"


def rates_frame() -> tuple[pd.DataFrame | None, dict]:
    try:
        env = fs.treasury_env()
    except ProviderError as exc:
        return None, {"source": "U.S. Treasury", "error": str(exc)}
    v = env["value"]
    df = pd.DataFrame(v["values"], index=[date.fromisoformat(d) for d in v["dates"]], columns=v["columns"])
    df = df[~df.index.duplicated(keep="last")].sort_index().astype(float)
    return df, {"source": env["source"], "fetched_at": env["fetched_at"], "stale": env["stale"]}


def get_market_context(max_age: float = 600) -> dict:
    with _lock:
        if _ctx.get("mf_time", 0) + max_age > time.time():
            return _ctx["mf"]
    spy = market.bars("SPY")
    try:
        vix_df = market.bars("^VIX")
        vix = vix_df["close"]
    except ProviderError:
        vix_df, vix = None, None
    rates, rmeta = rates_frame()
    mf = market_frame(spy, vix, rates)
    ctx = {"mf": mf, "spy": spy, "vix": vix, "vix_df": vix_df, "rates": rates, "rates_meta": rmeta}
    with _lock:
        _ctx["mf"] = ctx
        _ctx["mf_time"] = time.time()
    return ctx


def latest_rf(ctx: dict) -> float | None:
    r = ctx.get("rates")
    if r is None or "10Y" not in r:
        return None
    s = r["10Y"].dropna()
    return float(s.iloc[-1]) / 100 if len(s) else None


# ----------------------------------------------------------- model artefacts
def save_model(res: model_engine.TrainResult) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    oos = res.oos
    if oos is not None:
        keep = ["date", "ticker", "horizon", "y", "prior", "ridge", "gbm", "ml", "ensemble", "sigma", "p_up", "p_raw", "w"]
        oos = oos[keep].copy()
        for c in ("y", "prior", "ridge", "gbm", "ml", "ensemble", "sigma", "p_up", "p_raw", "w"):
            oos[c] = oos[c].astype("float32")
    art = {"version": res.version, "trained_at": res.trained_at, "data_end": res.data_end, "tickers": res.tickers,
           "hyperparams": res.hyperparams, "weights": res.weights, "final_models": res.final_models,
           "calibrators": res.calibrators, "dead_features": res.dead_features,
           "reference": res.reference, "oos": oos, "seconds": res.seconds}
    blob = model_engine.pack(art)
    (MODEL_DIR / "latest.b64").write_text(blob)
    put("model:artifact", {"blob": blob}, "model trainer", 3650 * 86400)
    put("model:metrics", {"version": res.version, "trained_at": res.trained_at, "data_end": res.data_end,
                          "tickers": res.tickers, "n_tickers": len(res.tickers), "hyperparams": res.hyperparams,
                          "weights": res.weights, "calibrators": res.calibrators, "metrics": res.metrics,
                          "seconds": res.seconds},
        "model trainer", 3650 * 86400)
    with _lock:
        _ctx["model"] = art
        _ctx["model_time"] = time.time()


def load_model() -> dict | None:
    with _lock:
        if "model" in _ctx:
            return _ctx["model"]
    blob = None
    f = MODEL_DIR / "latest.b64"
    if f.exists():
        blob = f.read_text()
    else:
        row = get_entry("model:artifact")
        if row is not None:
            blob = row.value.get("blob")
    if not blob:
        return None
    try:
        art = model_engine.unpack(blob)
    except Exception as exc:  # noqa: BLE001
        log.error("failed to load model artefact: %s", exc)
        return None
    with _lock:
        _ctx["model"] = art
    return art


def model_metrics() -> dict | None:
    row = get_entry("model:metrics")
    return row.value if row else None


def reset_cache() -> None:
    with _lock:
        _ctx.clear()


def finite(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


_ub: dict = {}


def universe_bars(max_age: float = 900) -> dict:
    """All universe stock bars (from the DB cache), memoised in-process."""
    from ..universe import STOCK_TICKERS
    with _lock:
        if _ub.get("t", 0) + max_age > time.time():
            return _ub["bars"]
    bars = market.bars_many(STOCK_TICKERS, workers=4)
    with _lock:
        _ub["bars"], _ub["t"] = bars, time.time()
    return bars


def stable_seed(*parts) -> int:
    import hashlib
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
