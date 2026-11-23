"""DEMO MODE - synthetic data generator.

Used only when DEMO_MODE=true (or in offline tests). Every value it produces is
labelled with source 'DEMO (synthetic)' and the UI shows a persistent banner.
Prices follow a GARCH(1,1)-like random walk seeded by the ticker symbol, so
they are reproducible but carry NO information about real markets.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..analytics.market_hours import is_trading_day
from ..universe import UNIVERSE

SOURCE = "DEMO (synthetic)"


def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))


def _sessions(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _market_shocks(days: list[date]) -> np.ndarray:
    rng = _rng("MARKET")
    n = len(days)
    var = np.empty(n)
    r = np.empty(n)
    omega, alpha, beta = 2e-6, 0.09, 0.89
    var[0] = 1e-4
    for i in range(n):
        if i:
            var[i] = omega + alpha * r[i - 1] ** 2 + beta * var[i - 1]
        r[i] = np.sqrt(var[i]) * rng.standard_t(5) / np.sqrt(5 / 3)
    return r + 0.0003


def history(ticker: str, years: int = 10, end: date | None = None) -> pd.DataFrame:
    end = end or date.today()
    days = _sessions(end - timedelta(days=int(365.25 * years)), end)
    mkt = _market_shocks(days)
    rng = _rng(ticker)
    n = len(days)
    if ticker.startswith("^VIX"):
        lvl = 14 + 60 * pd.Series(mkt).abs().ewm(span=10).mean().values + rng.normal(0, 0.6, n)
        close = np.clip(lvl, 9, 80)
        return pd.DataFrame({"open": close, "high": close * 1.03, "low": close * 0.97, "close": close,
                             "adj_close": close, "volume": 0.0}, index=pd.Index(days, name="date"))
    beta = 0.6 + rng.random() * 0.9 if ticker not in ("SPY", "^GSPC") else 1.0
    idio = rng.normal(0, 0.008 + rng.random() * 0.012, n) if beta != 1.0 else np.zeros(n)
    ret = beta * mkt + idio + rng.normal(0.0001, 0.00005)
    start_px = 20 + rng.random() * 280
    close = start_px * np.exp(np.cumsum(ret))
    rng_hl = np.abs(rng.normal(0, 0.008, n)) + 0.003
    open_ = close * np.exp(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + rng_hl)
    low = np.minimum(open_, close) * (1 - rng_hl)
    vol = (1e6 + rng.random() * 2e7) * np.exp(rng.normal(0, 0.35, n)) * (1 + 8 * np.abs(ret))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "adj_close": close,
                         "volume": vol}, index=pd.Index(days, name="date"))


def quote(ticker: str) -> dict:
    h = history(ticker, years=1)
    last, prev = float(h["close"].iloc[-1]), float(h["close"].iloc[-2])
    spread = last * 0.0004
    return {"ticker": ticker, "name": UNIVERSE.get(ticker, {}).get("name", ticker), "price": last,
            "previous_close": prev, "change": last - prev, "change_pct": (last / prev - 1) * 100,
            "bid": last - spread / 2, "ask": last + spread / 2, "volume": float(h["volume"].iloc[-1]),
            "quote_time": str(h.index[-1]), "source": SOURCE, "is_real_time": False}


def fundamentals(ticker: str) -> dict:
    """Synthetic compact SEC-like extract."""
    rng = _rng("F" + ticker)
    q_ends = [date(2021, 3, 31) + timedelta(days=91 * i) for i in range(23)]
    rev0 = 2e9 + rng.random() * 3e10
    g = rng.normal(0.02, 0.02)
    margin = 0.05 + rng.random() * 0.25
    rows = {k: [] for k in ("revenue", "net_income", "operating_income", "dna", "ocf", "capex", "eps_diluted",
                            "diluted_shares", "gross_profit")}
    shares = 2e8 + rng.random() * 3e9
    for i, e in enumerate(q_ends):
        rev = rev0 * (1 + g) ** i * (1 + rng.normal(0, 0.03))
        ni = rev * (margin + rng.normal(0, 0.02))
        filed = (e + timedelta(days=35)).isoformat()
        for k, v in (("revenue", rev), ("net_income", ni), ("operating_income", ni * 1.25), ("dna", rev * 0.04),
                     ("ocf", ni * 1.2), ("capex", rev * 0.05), ("eps_diluted", ni / shares),
                     ("diluted_shares", shares), ("gross_profit", rev * (0.3 + margin))):
            rows[k].append([e.isoformat(), float(v), filed])
    inst = {}
    for k, v in (("cash", rev0 * 0.5), ("long_term_debt", rev0 * 0.8), ("equity", rev0 * 2.0),
                 ("assets", rev0 * 5), ("liabilities", rev0 * 3), ("shares_outstanding", shares)):
        inst[k] = [[q_ends[-1].isoformat(), float(v), (q_ends[-1] + timedelta(days=35)).isoformat()]]
    return {"entity": UNIVERSE.get(ticker, {}).get("name", ticker) + " (DEMO)", "quarterly": rows,
            "annual": {}, "instant": inst}


def news(ticker: str) -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    heads = [f"[DEMO] {ticker} synthetic headline: quarterly results beat estimates",
             f"[DEMO] {ticker} synthetic headline: analyst downgrade on valuation concerns",
             f"[DEMO] {ticker} synthetic headline: company launches new product line"]
    return [{"title": h, "summary": "Synthetic demo item - not real news.", "url": "", "publisher": SOURCE,
             "feed": SOURCE, "published_at": (now - timedelta(hours=6 * i)).isoformat()} for i, h in enumerate(heads)]
