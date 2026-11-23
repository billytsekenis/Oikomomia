"""Market Intelligence Dashboard endpoints."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from fastapi import APIRouter, Depends

from ..analytics import market_hours, regime
from ..analytics.context import get_market_context, rates_frame, universe_bars
from ..analytics.indicators import compute_all
from ..data import fundamentals_store as fs
from ..data import fx, market
from ..data.http import ProviderError
from ..data.macro import FRED_SERIES
from ..security import get_current_user
from ..universe import SECTOR_ETFS, STOCK_TICKERS, UNIVERSE
from .common import clean, memo

router = APIRouter(prefix="/api/market", tags=["market"], dependencies=[Depends(get_current_user)])

INDICES = ["^GSPC", "^IXIC", "^NDX", "^DJI", "^RUT"]
VOL = ["^VIX", "^VIX9D", "^VIX3M"]
CROSS_ASSET = {"TLT": "20+Y Treasuries (ETF)", "HYG": "High yield credit (ETF)", "GLD": "Gold (ETF proxy)",
               "USO": "Crude oil (ETF proxy)", "UUP": "US dollar index (ETF proxy)"}


def _instrument(t: str, name: str | None = None) -> dict:
    out = {"ticker": t, "name": name or market.INDEX_NAMES.get(t) or UNIVERSE.get(t, {}).get("name", t)}
    try:
        df = market.bars(t)
        out.update(market.daily_change_from_bars(df))
        out["spark"] = [float(x) for x in df["close"].tail(90).values]
        out["provenance"] = market.provenance(df)
    except ProviderError as exc:
        out["error"] = str(exc)
    try:
        q = market.quote(t)
        out["quote"] = {k: q.get(k) for k in ("price", "change", "change_pct", "quote_time", "source", "market_status",
                                              "fetched_at", "proxy", "is_real_time")}
    except ProviderError as exc:
        out["quote_error"] = str(exc)
    return out


def _overview():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        idx = list(ex.map(_instrument, INDICES))
        vol = list(ex.map(_instrument, VOL))
        cross = list(ex.map(lambda kv: _instrument(kv[0], kv[1]), CROSS_ASSET.items()))
    return {"indices": idx, "volatility": vol, "cross_asset": cross, "market_hours": market_hours.status()}


@router.get("/overview")
def overview():
    return clean(memo("overview", 60, _overview))


def _rates():
    df, meta = rates_frame()
    if df is None:
        return {"available": False, "error": meta.get("error")}
    last = df.dropna(how="all").iloc[-1]
    month = df[df.index <= df.index[-1] - timedelta(days=30)]
    prev = month.iloc[-1] if len(month) else None
    curve = [{"tenor": c, "yield": float(last[c]) if last[c] == last[c] else None,
              "change_1m_bp": float((last[c] - prev[c]) * 100) if prev is not None and last[c] == last[c] and prev[c] == prev[c] else None}
             for c in df.columns]
    hist = df[["2Y", "10Y", "3M"]].dropna().tail(260) if all(c in df for c in ("2Y", "10Y", "3M")) else None
    return {"available": True, "as_of": str(df.dropna(how="all").index[-1]), "curve": curve,
            "spread_10y_2y": float(last["10Y"] - last["2Y"]) if "2Y" in last else None,
            "spread_10y_3m": float(last["10Y"] - last["3M"]) if "3M" in last else None,
            "history": [{"date": str(d), "y2": float(r["2Y"]), "y10": float(r["10Y"]), "m3": float(r["3M"])}
                        for d, r in hist.iloc[::5].iterrows()] if hist is not None else [],
            "source": meta.get("source"), "fetched_at": meta.get("fetched_at")}


def _macro():
    out = []
    for sid, label in FRED_SERIES.items():
        try:
            env = fs.fred_env(sid)
        except ProviderError as exc:
            out.append({"id": sid, "label": label, "available": False, "error": str(exc)[:160]})
            continue
        v = env["value"]
        vals, dates = v["values"], v["dates"]
        item = {"id": sid, "label": label, "available": True, "last": vals[-1], "date": dates[-1],
                "source": env["source"], "fetched_at": env["fetched_at"], "stale": env["stale"]}
        if sid in ("CPIAUCSL", "CPILFESL", "PCEPILFE", "PAYEMS", "INDPRO", "GDPC1"):
            # year-over-year change for index-level series (12 months / 4 quarters back)
            lag = 4 if sid == "GDPC1" else 12
            if len(vals) > lag:
                item["yoy"] = vals[-1] / vals[-1 - lag] - 1
        if sid == "PAYEMS" and len(vals) > 1:
            item["mom_change_k"] = vals[-1] - vals[-2]
        item["series"] = [{"date": d, "value": x} for d, x in list(zip(dates, vals))[-60:]]
        out.append(item)
    try:
        f = fx.latest("EUR", "USD")
    except ProviderError as exc:
        f = {"error": str(exc)}
    return {"series": out, "fx": f}


@router.get("/macro")
def macro():
    return clean(memo("macro", 1800, lambda: {"rates": _rates(), **_macro()}))


def _sectors():
    rows = []
    for sec, etf in SECTOR_ETFS.items():
        try:
            df = market.bars(etf)
            ch = market.daily_change_from_bars(df)
            rows.append({"sector": sec, "etf": etf, **{k: ch.get(k) for k in ("1D", "1W", "1M", "3M", "YTD", "1Y")},
                         "last": ch["last"], "date": ch["date"], "source": df.attrs.get("source")})
        except ProviderError as exc:
            rows.append({"sector": sec, "etf": etf, "error": str(exc)})
    # stock-level heatmap
    ub = universe_bars()
    stocks = []
    for t in STOCK_TICKERS:
        df = ub.get(t)
        if df is None or len(df) < 30:
            continue
        c = df["adj_close"]
        stocks.append({"ticker": t, "sector": UNIVERSE[t]["sector"], "name": UNIVERSE[t]["name"],
                       "1D": float(c.iloc[-1] / c.iloc[-2] - 1), "1W": float(c.iloc[-1] / c.iloc[-6] - 1),
                       "1M": float(c.iloc[-1] / c.iloc[-22] - 1),
                       "dollar_volume": float((df["close"] * df["volume"]).tail(20).mean()), "date": str(df.index[-1])})
    return {"sectors": rows, "stocks": stocks}


@router.get("/sectors")
def sectors():
    return clean(memo("sectors", 300, _sectors))


def _movers():
    ub = universe_bars()
    rows = []
    for t, df in ub.items():
        if len(df) < 70:
            continue
        ind = compute_all(df.tail(320))
        c = df["adj_close"]
        r1 = float(c.iloc[-1] / c.iloc[-2] - 1)
        vol = float(np.log(c).diff().tail(63).std())
        rv = ind["rvol20"].iloc[-1]
        rows.append({"ticker": t, "name": UNIVERSE.get(t, {}).get("name", t), "sector": UNIVERSE.get(t, {}).get("sector"),
                     "price": float(df["close"].iloc[-1]), "change": r1, "date": str(df.index[-1]),
                     "rvol": float(rv) if rv == rv else None, "move_sigma": r1 / vol if vol > 0 else None,
                     "rsi14": float(ind["rsi14"].iloc[-1])})
    rows.sort(key=lambda r: r["change"])
    unusual = [r | {"reasons": [x for x in [
        f"volume {r['rvol']:.1f}x 20-day average" if r["rvol"] and r["rvol"] >= 2 else None,
        f"{r['move_sigma']:+.1f} sigma move" if r["move_sigma"] and abs(r["move_sigma"]) >= 3 else None] if x]}
        for r in rows if (r["rvol"] and r["rvol"] >= 2) or (r["move_sigma"] and abs(r["move_sigma"]) >= 3)]
    unusual.sort(key=lambda r: -max(r["rvol"] or 0, abs(r["move_sigma"] or 0)))
    return {"gainers": rows[::-1][:10], "losers": rows[:10], "unusual": unusual[:15],
            "as_of": max((r["date"] for r in rows), default=None),
            "note": "Computed from the last completed daily bar of each universe stock (Nasdaq / Yahoo data)."}


@router.get("/movers")
def movers():
    return clean(memo("movers", 300, _movers))


def regime_payload():
    def build():
        ub = universe_bars()
        return {"regime": regime.analyse(ub), "sentiment": regime.risk_appetite(ub)}
    return clean(memo("regime", 900, build))


@router.get("/regime")
def regime_ep():
    return regime_payload()


@router.get("/calendar/earnings")
def earnings_calendar(days: int = 14, universe_only: bool = False):
    env = fs.earnings_calendar(min(days, 30))
    rows = env["value"]
    for r in rows:
        r["in_universe"] = r["ticker"] in UNIVERSE
    if universe_only:
        rows = [r for r in rows if r["in_universe"]]
    rows.sort(key=lambda r: (r["date"], -(r.get("market_cap") or 0)))
    return clean({"rows": rows[:400], "source": env["source"], "fetched_at": env.get("fetched_at"), "stale": env.get("stale"),
                  "error": env.get("error")})


@router.get("/calendar/economic")
def economic_calendar(days: int = 10, country: str = "United States"):
    env = fs.economic_calendar(min(days, 21), country)
    return clean({"rows": env["value"], "source": env["source"], "fetched_at": env.get("fetched_at"),
                  "stale": env.get("stale"), "error": env.get("error"),
                  "note": "Times are GMT as published by Nasdaq."})


@router.get("/hours")
def hours():
    return market_hours.status()


_ = (date, get_market_context)
