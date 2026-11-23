"""Cached access to company-level datasets (SEC + Nasdaq extras + news + calendars)."""
from __future__ import annotations

from datetime import date, timedelta

from ..config import get_settings
from ..universe import UNIVERSE
from . import demo, macro, nasdaq, news, sec
from .cache import cached
from .http import ProviderError

DAY = 86400.0


def _demo() -> bool:
    return get_settings().demo_mode


def ticker_map() -> dict:
    if _demo():
        return {t: {"cik": 0, "name": v["name"]} for t, v in UNIVERSE.items()}
    return cached("sec:tickers", 7 * DAY, sec.SOURCE, sec.ticker_map)["value"]


def cik_for(ticker: str) -> int | None:
    try:
        m = ticker_map()
    except ProviderError:
        return None
    rec = m.get(ticker.upper())
    return int(rec["cik"]) if rec else None


def company_facts_env(ticker: str) -> dict | None:
    if _demo():
        return {"value": demo.fundamentals(ticker), "source": demo.SOURCE, "fetched_at": None, "stale": False}
    cik = cik_for(ticker)
    if not cik:
        return None
    return cached(f"sec:facts:{cik}", 2 * DAY, sec.SOURCE + " XBRL companyfacts", lambda: sec.company_facts(cik))


def company_facts_for(ticker: str) -> dict | None:
    env = company_facts_env(ticker)
    return env["value"] if env else None


def submissions_env(ticker: str) -> dict | None:
    if _demo():
        return None
    cik = cik_for(ticker)
    if not cik:
        return None
    return cached(f"sec:subs:{cik}", 0.5 * DAY, sec.SOURCE + " submissions", lambda: sec.submissions(cik))


def nasdaq_env(kind: str, ticker: str, ttl: float = DAY) -> dict | None:
    if _demo():
        return None
    fn = {"forecast": nasdaq.earnings_forecast, "surprise": nasdaq.earnings_surprise,
          "institutional": nasdaq.institutional, "summary": nasdaq.summary}[kind]
    try:
        return cached(f"nasdaq:{kind}:{ticker}", ttl, f"Nasdaq {kind}", lambda: fn(ticker))
    except ProviderError:
        return None


def news_env(ticker: str) -> dict:
    if _demo():
        items = demo.news(ticker)
        from ..analytics.text_sentiment import score_text
        for it in items:
            it.update({"sentiment": score_text(it["title"])["score"], "sentiment_hits": [], "injection_flags": [],
                       "suspicious": False})
        return {"value": items, "source": demo.SOURCE, "fetched_at": None, "stale": False}
    name = UNIVERSE.get(ticker, {}).get("name")
    return cached(f"news:{ticker}", 900, "Yahoo Finance RSS + Google News RSS", lambda: news.fetch_news(ticker, name))


def earnings_calendar(days: int = 14) -> dict:
    if _demo():
        return {"value": [], "source": demo.SOURCE, "fetched_at": None, "stale": False}
    rows, errors, fetched = [], [], None
    for i in range(days):
        d = date.today() + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            env = cached(f"nasdaq:earncal:{d}", 6 * 3600, "Nasdaq earnings calendar", lambda d=d: nasdaq.earnings_calendar(d))
            rows += env["value"]
            fetched = env["fetched_at"]
        except ProviderError as exc:
            errors.append(f"{d}: {exc}")
    return {"value": rows, "source": "Nasdaq earnings calendar", "fetched_at": fetched, "stale": bool(errors), "error": "; ".join(errors[:3])}


def economic_calendar(days: int = 10, country: str = "United States") -> dict:
    if _demo():
        return {"value": [], "source": demo.SOURCE, "fetched_at": None, "stale": False}
    rows, errors, fetched = [], [], None
    for i in range(-1, days):
        d = date.today() + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            env = cached(f"nasdaq:econcal:{d}", 3 * 3600, "Nasdaq economic calendar", lambda d=d: nasdaq.economic_calendar(d))
            rows += [r for r in env["value"] if not country or r.get("country") == country]
            fetched = env["fetched_at"]
        except ProviderError as exc:
            errors.append(f"{d}: {exc}")
    return {"value": rows, "source": "Nasdaq economic calendar", "fetched_at": fetched, "stale": bool(errors), "error": "; ".join(errors[:3])}


def treasury_env() -> dict:
    if _demo():
        raise ProviderError("Treasury yields unavailable in demo mode")

    this = date.today().year
    dates, values, cols, fetched, stale = [], [], None, None, False
    for y in range(this - get_settings().history_years, this + 1):
        def fetch(y=y):
            df = macro.treasury_year(y)
            return {"dates": [d.isoformat() for d in df.index], "columns": list(df.columns),
                    "values": [[None if v != v else float(v) for v in row] for row in df.values.tolist()]}
        try:
            env = cached(f"treasury:{y}", (6 * 3600) if y == this else 30 * DAY, macro.TREASURY_SOURCE, fetch)
        except ProviderError:
            if y == this:
                raise
            continue
        v = env["value"]
        if cols is None:
            cols = v["columns"]
        idx = [v["columns"].index(c) if c in v["columns"] else None for c in cols]
        dates += v["dates"]
        values += [[row[i] if i is not None else None for i in idx] for row in v["values"]]
        fetched = env["fetched_at"]
        stale = stale or env["stale"]
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    return {"value": {"dates": [dates[i] for i in order], "columns": cols, "values": [values[i] for i in order]},
            "source": macro.TREASURY_SOURCE, "fetched_at": fetched, "stale": stale}


def fred_env(series_id: str) -> dict:
    if _demo():
        raise ProviderError("FRED unavailable in demo mode")

    def fetch():
        s = macro.fred_series(series_id)
        return {"dates": [d.isoformat() for d in s.index], "values": [float(v) for v in s.values]}

    return cached(f"fred:{series_id}", DAY, macro.FRED_SOURCE, fetch)
