"""Stock analysis page endpoints."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from ..analytics import fundamentals as fa
from ..analytics import valuation
from ..analytics.context import get_market_context, latest_rf
from ..analytics.forecast import forecast as run_forecast
from ..analytics.indicators import compute_all, max_drawdown, support_resistance
from ..data import fundamentals_store as fs
from ..data import market
from ..data.http import ProviderError
from ..data.news import aggregate_sentiment
from ..security import get_current_user
from ..universe import SECTOR_ETFS, UNIVERSE
from .common import clean, memo

router = APIRouter(prefix="/api/stocks", tags=["stocks"], dependencies=[Depends(get_current_user)])
RANGES = {"1m": 22, "3m": 64, "6m": 127, "1y": 253, "2y": 505, "5y": 1260, "max": 100000}


def _t(t: str) -> str:
    t = t.upper().strip()
    if not t or len(t) > 12 or not all(c.isalnum() or c in "-^." for c in t):
        raise HTTPException(400, "invalid ticker")
    return t


def _bars(t: str) -> pd.DataFrame:
    try:
        return market.bars(t)
    except ProviderError as exc:
        raise HTTPException(404, f"No price data for {t}: {exc}") from exc


@router.get("/search")
def search(q: str = ""):
    q = q.strip()
    if not q:
        return {"results": [{"ticker": t, "name": v["name"], "sector": v["sector"], "in_universe": True}
                            for t, v in list(UNIVERSE.items())[:15]]}
    ql = q.lower()
    res = []
    for t, v in UNIVERSE.items():
        if t.lower().startswith(ql) or ql in v["name"].lower():
            res.append({"ticker": t, "name": v["name"], "sector": v["sector"], "in_universe": True})
    try:
        tm = fs.ticker_map()
        for t, v in tm.items():
            if len(res) >= 25:
                break
            if t in UNIVERSE:
                continue
            if t.lower() == ql or (len(ql) >= 2 and t.lower().startswith(ql)) or (len(ql) >= 3 and ql in v["name"].lower()):
                res.append({"ticker": t, "name": v["name"].title(), "sector": None, "in_universe": False})
    except ProviderError:
        pass
    res.sort(key=lambda r: (r["ticker"].lower() != ql, not r["ticker"].lower().startswith(ql), not r["in_universe"], len(r["ticker"])))
    return {"results": res[:25]}


def stock_summary(t: str) -> dict:
    t = t.upper()

    def build():
        df = market.bars(t)
        info = UNIVERSE.get(t, {"name": t, "sector": "Unknown", "industry": "Unknown", "type": "stock"})
        ind = compute_all(df)
        last = ind.iloc[-1]
        c = df["adj_close"]
        ctx = get_market_context()
        lr = np.log(c).diff()
        j = pd.concat([lr, ctx["mf"]["mkt_ret"].reindex(df.index)], axis=1).dropna().tail(252)
        beta = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var()) if len(j) > 60 else None
        j3 = pd.concat([lr, ctx["mf"]["mkt_ret"].reindex(df.index)], axis=1).dropna().tail(756)
        beta3 = float(j3.iloc[:, 0].cov(j3.iloc[:, 1]) / j3.iloc[:, 1].var()) if len(j3) > 250 else beta
        beta_blume = 0.67 * beta3 + 0.33 if beta3 is not None else None
        sr = support_resistance(df)
        ret = lambda n: float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None  # noqa: E731
        try:
            q = market.quote(t)
        except ProviderError:
            q = {}
        price = q.get("price") or float(df["close"].iloc[-1])
        spy = ctx["spy"]["adj_close"]
        sec_etf = SECTOR_ETFS.get(info.get("sector"))
        sector_1d = None
        if sec_etf:
            try:
                s = market.bars(sec_etf)["adj_close"]
                sector_1d = float(s.iloc[-1] / s.iloc[-2] - 1)
            except ProviderError:
                pass
        out = {
            "ticker": t, "name": info["name"], "sector": info.get("sector"), "industry": info.get("industry"),
            "in_universe": t in UNIVERSE, "price": price, "as_of": q.get("quote_time") or str(df.index[-1]),
            "source": q.get("source") or df.attrs.get("source"), "quote": q,
            "change_1d": (q.get("change_pct") / 100) if q.get("change_pct") is not None else ret(1),
            "market_1d": float(spy.iloc[-1] / spy.iloc[-2] - 1), "sector_1d": sector_1d, "sector_etf": sec_etf,
            "beta": beta, "beta_3y": beta3, "beta_adjusted": beta_blume,
            "beta_note": "beta = 1y daily OLS vs SPY; beta_adjusted = Blume (0.67 x 3y beta + 0.33), used for WACC",
            "provenance": market.provenance(df),
            "technicals": {
                "rsi14": float(last["rsi14"]), "macd": float(last["macd"]), "macd_signal": float(last["signal"]),
                "macd_hist": float(last["hist"]), "sma50": float(last["sma50"]), "sma200": float(last["sma200"]) if last["sma200"] == last["sma200"] else None,
                "dist_sma50": float(c.iloc[-1] / last["sma50"] - 1), "dist_sma200": float(c.iloc[-1] / last["sma200"] - 1) if last["sma200"] == last["sma200"] else None,
                "atr14": float(last["atr14"]), "atr_pct": float(last["atr_pct"]), "bb_pctb": float(last["bb_pctb"]),
                "rvol20": float(last["rvol20"]) if last["rvol20"] == last["rvol20"] else None,
                "vol63": float(lr.tail(63).std() * np.sqrt(252)), "max_dd_1y": max_drawdown(c.tail(252)),
                "hi52": float(df["high"].tail(252).max()), "lo52": float(df["low"].tail(252).min()),
                "ret_1w": ret(5), "ret_1m": ret(21), "ret_3m": ret(63), "ret_6m": ret(126), "ret_1y": ret(252),
                "support": [lv["price"] for lv in sr["support"]], "resistance": [lv["price"] for lv in sr["resistance"]],
                "levels": sr,
            },
        }
        cf_env = fs.company_facts_env(t) if not t.startswith("^") else None
        if cf_env:
            fc = fs.nasdaq_env("forecast", t, ttl=3 * 86400)
            summ = fs.nasdaq_env("summary", t, ttl=7 * 86400)
            snap = fa.snapshot(cf_env["value"], price, forecast=fc["value"] if fc else None, sector=info.get("sector"),
                               market_cap_hint=(summ or {}).get("value", {}).get("market_cap") if summ else None)
            if summ and summ["value"].get("dividend_yield_pct") is not None:
                snap["dividend_yield"] = summ["value"]["dividend_yield_pct"] / 100
            out["fundamentals"] = snap
            out["fundamentals_source"] = f"{cf_env['source']} (fetched {cf_env['fetched_at']})"
            out["valuation"] = valuation.reverse_dcf(snap, latest_rf(ctx), beta_blume)
        subs = fs.submissions_env(t) if not t.startswith("^") else None
        if subs:
            out["recent_filings"] = subs["value"]["recent_filings"][:8]
        return out

    return clean(memo(f"summary:{t}", 120, build))


@router.get("/{ticker}/summary")
def summary(ticker: str):
    t = _t(ticker)
    try:
        return stock_summary(t)
    except ProviderError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{ticker}/history")
def history(ticker: str, range: str = "1y"):  # noqa: A002
    t = _t(ticker)
    df = _bars(t)
    n = RANGES.get(range, 253)
    # chart in split-adjusted traded prices; indicators on the same basis for visual consistency
    raw = df.copy()
    raw["adj_close"] = raw["close"]
    ind = compute_all(raw.tail(n + 260)).tail(n)
    d = df.tail(n)
    cols = ["sma20", "sma50", "sma200", "ema21", "rsi14", "macd", "signal", "hist", "bb_upper", "bb_mid", "bb_lower", "atr14", "obv", "rvol20"]
    rows = []
    for (dt, r), (_, i) in zip(d.iterrows(), ind.iterrows()):
        rows.append({"date": str(dt), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "adj_close": r.adj_close,
                     "volume": r.volume, **{c: i[c] for c in cols}})
    return clean({"ticker": t, "range": range, "bars": rows, "provenance": market.provenance(df),
                  "levels": support_resistance(df),
                  "note": "Candles: split-adjusted traded prices. Returns/forecasts elsewhere use dividend-adjusted closes."})


@router.get("/{ticker}/fundamentals")
def fundamentals(ticker: str):
    t = _t(ticker)
    env = fs.company_facts_env(t)
    if not env:
        raise HTTPException(404, f"No SEC XBRL financial statements found for {t} (ETF, foreign filer or unknown ticker).")
    cf = env["value"]
    s = stock_summary(t)
    fc = fs.nasdaq_env("forecast", t, ttl=3 * 86400)
    surprise = fs.nasdaq_env("surprise", t, ttl=3 * 86400)
    return clean({"ticker": t, "entity": cf.get("entity"), "snapshot": s.get("fundamentals"),
                  "quarterly": fa.quarterly_table(cf), "annual": fa.annual_table(cf),
                  "analyst_estimates": fc["value"] if fc else None, "earnings_surprises": surprise["value"] if surprise else None,
                  "definitions": fa.DEFINITIONS,
                  "sources": {"statements": env["source"], "statements_fetched_at": env["fetched_at"], "stale": env["stale"],
                              "estimates": "Nasdaq analyst consensus" if fc else None, "price": s.get("source")}})


@router.get("/{ticker}/valuation")
def valuation_ep(ticker: str):
    s = stock_summary(_t(ticker))
    summ = fs.nasdaq_env("summary", ticker.upper(), ttl=7 * 86400)
    target = (summ or {}).get("value", {}).get("one_year_target") if summ else None
    fu = s.get("fundamentals") or {}
    exp = {"reverse_dcf": s.get("valuation"), "analyst_1y_target": target,
           "upside_to_target": (target / s["price"] - 1) if target and s.get("price") else None,
           "forward_eps_growth_implied_by_consensus": fu.get("eps_growth_forward"),
           "pe_ttm": fu.get("pe_ttm"), "forward_pe": fu.get("forward_pe")}
    if fu.get("pe_ttm") and fu.get("eps_growth_forward") is not None:
        exp["note"] = ("A forward P/E below the trailing P/E means consensus expects earnings growth; the reverse DCF "
                       "shows how much long-run cash-flow growth the current price requires.")
    return clean(exp | {"source_target": "Nasdaq (1-year analyst target)" if target else None})


@router.get("/{ticker}/news")
def news(ticker: str):
    t = _t(ticker)
    try:
        env = fs.news_env(t)
    except ProviderError as exc:
        return {"items": [], "error": str(exc), "aggregate": {"score": None, "n": 0, "label": "unavailable"}}
    items = env["value"]
    return clean({"items": items, "aggregate": aggregate_sentiment(items), "source": env["source"],
                  "fetched_at": env["fetched_at"], "stale": env["stale"],
                  "method": "Finance-lexicon headline scoring with negation handling, recency half-life 3 days. "
                            "Items with instruction-like text are flagged and excluded from the aggregate and from the AI analyst."})


@router.get("/{ticker}/forecast")
def forecast(ticker: str):
    t = _t(ticker)
    df = _bars(t)
    try:
        return clean(memo(f"forecast:{t}:{df.index[-1]}", 1800, lambda: run_forecast(t)))
    except ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{ticker}/peers")
def peers(ticker: str):
    t = _t(ticker)
    from .discovery_api import get_snapshot
    snap = get_snapshot()
    rows = snap.get("rows", [])
    me = next((r for r in rows if r["ticker"] == t), None)
    sector = (me or {}).get("sector") or UNIVERSE.get(t, {}).get("sector")
    industry = UNIVERSE.get(t, {}).get("industry")
    peers_ = [r for r in rows if r["sector"] == sector]
    peers_.sort(key=lambda r: (r.get("industry") != industry, -(r.get("market_cap") or 0)))
    keys = ["ticker", "name", "industry", "price", "market_cap", "pe_ttm", "forward_pe", "ev_ebitda", "revenue_growth",
            "operating_margin", "roe", "fcf_yield", "ret_6m", "vol63", "factors", "composite"]
    table = [{k: r.get(k) for k in keys} for r in peers_[:12]]
    med = {}
    for k in ("pe_ttm", "forward_pe", "ev_ebitda", "revenue_growth", "operating_margin", "roe", "fcf_yield", "ret_6m", "vol63"):
        vals = [r.get(k) for r in peers_ if r.get(k) is not None]
        med[k] = float(np.median(vals)) if vals else None
    return clean({"ticker": t, "sector": sector, "peers": table, "sector_median": med, "self": me,
                  "note": "Peers = same-sector stocks in the analysis universe; metrics from SEC filings and Nasdaq prices."})


@router.get("/{ticker}/ownership")
def ownership(ticker: str):
    t = _t(ticker)
    inst = fs.nasdaq_env("institutional", t, ttl=3 * 86400)
    subs = fs.submissions_env(t)
    sv = subs["value"] if subs else {}
    return clean({"institutional": inst["value"] if inst else None,
                  "institutional_source": "Nasdaq (aggregated 13F filings; holder dates vary by filer)" if inst else None,
                  "insider_form4_count_90d": sv.get("insider_form4_count_90d"),
                  "insider_filings": sv.get("insider_form4_recent"),
                  "major_holder_filings": [f for f in sv.get("recent_filings", []) if f["form"].startswith("SC 13")],
                  "insider_source": "SEC EDGAR submissions (Form 4 filings; transaction details in each filing)" if subs else None,
                  "note": "Form 4 counts show filing activity, not net buying/selling; open each filing for details."})


@router.get("/{ticker}/catalysts")
def catalysts(ticker: str):
    t = _t(ticker)
    out = {"ticker": t}
    try:
        cal = fs.earnings_calendar(30)["value"]
        out["next_earnings"] = next((e for e in cal if e["ticker"] == t), None)
    except ProviderError:
        out["next_earnings"] = None
    subs = fs.submissions_env(t)
    sv = subs["value"] if subs else {}
    df = _bars(t)
    c = df["adj_close"]
    reactions = []
    for d in sv.get("earnings_release_dates", [])[-8:]:
        dd = date.fromisoformat(d)
        pos = c.index.searchsorted(dd)
        if 1 <= pos < len(c) - 1:
            r0 = float(c.iloc[pos] / c.iloc[pos - 1] - 1)
            r1 = float(c.iloc[pos + 1] / c.iloc[pos] - 1)
            reactions.append({"date": d, "day_return": r0, "next_day_return": r1, "largest": r0 if abs(r0) > abs(r1) else r1})
    out["past_earnings_reactions"] = reactions
    if reactions:
        out["avg_abs_earnings_move"] = float(np.mean([abs(r["largest"]) for r in reactions]))
    out["recent_8k"] = [f for f in sv.get("recent_filings", []) if f["form"].startswith("8-K")][:8]
    fc = fs.nasdaq_env("forecast", t, ttl=3 * 86400)
    if fc and fc["value"].get("quarterly"):
        q0 = fc["value"]["quarterly"][0]
        out["estimate_revisions_4w"] = {"up": q0.get("revisions_up"), "down": q0.get("revisions_down"), "period": q0.get("fiscal_end")}
    out["sources"] = ["SEC EDGAR 8-K item 2.02 dates", "Nasdaq earnings calendar", "Nasdaq analyst estimates", df.attrs.get("source")]
    return clean(out)
