"""Nasdaq public JSON API (api.nasdaq.com).

Free, keyless, unofficial. Used for: daily OHLCV (split-adjusted, 10y), last
sale + bid/ask quotes (real-time Nasdaq Last Sale), dividends, analyst EPS
consensus, earnings surprises, institutional ownership summary, earnings and
economic calendars. Licensing: Nasdaq.com terms - personal, non-commercial use.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from ..universe import UNIVERSE
from .http import ProviderError, get_json

BASE = "https://api.nasdaq.com/api"
SOURCE = "Nasdaq"
_INDEX = {"^IXIC": "COMP", "^NDX": "NDX"}


def num(v) -> float | None:
    """Parse '$1,234.5', '1.2%', '--', 'N/A' -> float/None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "").replace("%", "")
    if s in ("", "--", "N/A", "NA", "-"):
        return None
    m = re.match(r"^[+-]?\d*\.?\d+(e[+-]?\d+)?", s, re.I)
    return float(m.group(0)) if m else None


def asset_class(ticker: str) -> tuple[str, str]:
    if ticker in _INDEX:
        return _INDEX[ticker], "index"
    info = UNIVERSE.get(ticker)
    return ticker.replace("-", "."), ("etf" if info and info["type"] == "etf" else "stocks")


def _data(payload: dict) -> dict:
    status = (payload or {}).get("status") or {}
    if status.get("rCode") not in (None, 200) or payload.get("data") is None:
        msgs = status.get("bCodeMessage") or []
        raise ProviderError("Nasdaq: " + "; ".join(m.get("errorMessage", "") for m in msgs if isinstance(m, dict)) or "no data")
    return payload["data"]


def historical(ticker: str, start: date, end: date, klass: str | None = None) -> pd.DataFrame:
    sym, cls = asset_class(ticker)
    cls = klass or cls
    payload = get_json(
        f"{BASE}/quote/{sym}/historical",
        params={"assetclass": cls, "fromdate": start.isoformat(), "todate": end.isoformat(), "limit": 9999},
    )
    try:
        data = _data(payload)
    except ProviderError:
        if cls == "stocks" and klass is None:  # maybe an ETF not in our universe
            return historical(ticker, start, end, klass="etf")
        raise
    rows = ((data.get("tradesTable") or {}).get("rows")) or []
    recs = []
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        recs.append({
            "date": d, "open": num(r.get("open")), "high": num(r.get("high")), "low": num(r.get("low")),
            "close": num(r.get("close")), "volume": num(r.get("volume")) or 0.0,
        })
    if not recs:
        raise ProviderError(f"Nasdaq: empty history for {ticker}")
    df = pd.DataFrame(recs).drop_duplicates("date").set_index("date").sort_index()
    return df


def dividends(ticker: str) -> list[tuple[date, float]]:
    sym, cls = asset_class(ticker)
    if cls == "index":
        return []
    payload = get_json(f"{BASE}/quote/{sym}/dividends", params={"assetclass": cls})
    try:
        data = _data(payload)
    except ProviderError:
        return []
    rows = ((data.get("dividends") or {}).get("rows")) or []
    out = []
    for r in rows:
        if str(r.get("type", "Cash")).lower() not in ("cash", ""):
            continue
        amt = num(r.get("amount"))
        try:
            d = datetime.strptime(r.get("exOrEffDate", ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        if amt and amt > 0:
            out.append((d, amt))
    return sorted(out)


def quote(ticker: str) -> dict:
    sym, cls = asset_class(ticker)
    payload = get_json(f"{BASE}/quote/{sym}/info", params={"assetclass": cls})
    data = _data(payload)
    p = data.get("primaryData") or {}
    s = data.get("secondaryData") or {}
    ks = data.get("keyStats") or {}
    last = num(p.get("lastSalePrice"))
    if last is None or last <= 0:
        raise ProviderError(f"Nasdaq: no valid last price for {ticker}")
    prev_close = None
    if "previousclose" in ks:
        prev_close = num(ks["previousclose"].get("value"))
    out = {
        "ticker": ticker,
        "name": data.get("companyName"),
        "price": last,
        "change": num(p.get("netChange")),
        "change_pct": num(p.get("percentageChange")),
        "bid": num(p.get("bidPrice")),
        "ask": num(p.get("askPrice")),
        "bid_size": num(p.get("bidSize")),
        "ask_size": num(p.get("askSize")),
        "volume": num(p.get("volume")),
        "is_real_time": bool(p.get("isRealTime")),
        "quote_time": p.get("lastTradeTimestamp"),
        "market_status": data.get("marketStatus"),
        "regular_close": num(s.get("lastSalePrice")) if s else None,
        "regular_close_change_pct": num(s.get("percentageChange")) if s else None,
        "previous_close": prev_close,
        "exchange": data.get("exchange"),
        "source": SOURCE + (" (real-time last sale)" if p.get("isRealTime") else " (delayed)"),
    }
    if "dayrange" in ks:
        parts = str(ks["dayrange"].get("value", "")).split("-")
        if len(parts) == 2:
            out["day_low"], out["day_high"] = num(parts[0]), num(parts[1])
    if "fiftyTwoWeekHighLow" in ks:
        parts = str(ks["fiftyTwoWeekHighLow"].get("value", "")).split("-")
        if len(parts) == 2:
            out["low_52w"], out["high_52w"] = num(parts[0]), num(parts[1])
    return out


def summary(ticker: str) -> dict:
    sym, cls = asset_class(ticker)
    data = _data(get_json(f"{BASE}/quote/{sym}/summary", params={"assetclass": cls}))
    sd = data.get("summaryData") or {}
    g = lambda k: (sd.get(k) or {}).get("value")  # noqa: E731
    return {
        "sector": g("Sector"), "industry": g("Industry"), "one_year_target": num(g("OneYrTarget")),
        "market_cap": num(g("MarketCap")), "annual_dividend": num(g("AnnualizedDividend")),
        "dividend_yield_pct": num(g("Yield")), "exchange": g("Exchange"),
    }


def earnings_forecast(ticker: str) -> dict:
    sym, _ = asset_class(ticker)
    data = _data(get_json(f"{BASE}/analyst/{sym}/earnings-forecast"))

    def rows(block):
        out = []
        for r in ((data.get(block) or {}).get("rows")) or []:
            out.append({
                "fiscal_end": r.get("fiscalEnd"), "consensus_eps": num(r.get("consensusEPSForecast")),
                "high_eps": num(r.get("highEPSForecast")), "low_eps": num(r.get("lowEPSForecast")),
                "n_estimates": num(r.get("noOfEstimates")), "revisions_up": num(r.get("up")),
                "revisions_down": num(r.get("down")),
            })
        return out

    return {"quarterly": rows("quarterlyForecast"), "yearly": rows("yearlyForecast")}


def earnings_surprise(ticker: str) -> list[dict]:
    sym, _ = asset_class(ticker)
    data = _data(get_json(f"{BASE}/company/{sym}/earnings-surprise"))
    out = []
    for r in ((data.get("earningsSurpriseTable") or {}).get("rows")) or []:
        try:
            d = datetime.strptime(r.get("dateReported", ""), "%m/%d/%Y").date().isoformat()
        except ValueError:
            d = None
        out.append({
            "fiscal_quarter": r.get("fiscalQtrEnd"), "date_reported": d, "eps": num(r.get("eps")),
            "consensus": num(r.get("consensusForecast")), "surprise_pct": num(r.get("percentageSurprise")),
        })
    return out


def institutional(ticker: str) -> dict:
    sym, _ = asset_class(ticker)
    data = _data(get_json(f"{BASE}/company/{sym}/institutional-holdings", params={"limit": 15, "type": "TOTAL"}))
    summ = data.get("ownershipSummary") or {}
    active = {r.get("positions"): {"holders": num(r.get("holders")), "shares": num(r.get("shares"))}
              for r in ((data.get("activePositions") or {}).get("rows")) or []}
    newsold = {r.get("positions"): {"holders": num(r.get("holders")), "shares": num(r.get("shares"))}
               for r in ((data.get("newSoldOutPositions") or {}).get("rows")) or []}
    holders = []
    for r in (((data.get("holdingsTransactions") or {}).get("table") or {}).get("rows")) or []:
        holders.append({
            "owner": r.get("ownerName"), "date": r.get("date"), "shares": num(r.get("sharesHeld")),
            "change": num(r.get("sharesChange")), "change_pct": num(r.get("sharesChangePCT")),
            "value_thousands": num(r.get("marketValue")),
        })
    return {
        "institutional_ownership_pct": num((summ.get("SharesOutstandingPCT") or {}).get("value")),
        "total_holdings_value_m": num((summ.get("TotalHoldingsValue") or {}).get("value")),
        "positions": active, "new_sold_out": newsold, "top_holders": holders,
    }


def earnings_calendar(d: date) -> list[dict]:
    data = _data(get_json(f"{BASE}/calendar/earnings", params={"date": d.isoformat()}))
    out = []
    for r in data.get("rows") or []:
        out.append({
            "date": d.isoformat(), "ticker": r.get("symbol"), "name": r.get("name"),
            "time": {"time-pre-market": "BMO", "time-after-hours": "AMC"}.get(r.get("time"), "TBD"),
            "market_cap": num(r.get("marketCap")), "fiscal_quarter": r.get("fiscalQuarterEnding"),
            "eps_forecast": num(r.get("epsForecast")), "n_estimates": num(r.get("noOfEsts")),
            "last_year_eps": num(r.get("lastYearEPS")), "last_year_date": r.get("lastYearRptDt"),
        })
    return out


def economic_calendar(d: date) -> list[dict]:
    data = _data(get_json(f"{BASE}/calendar/economicevents", params={"date": d.isoformat()}))
    out = []
    for r in data.get("rows") or []:
        out.append({
            "date": d.isoformat(), "time_gmt": r.get("gmt"), "country": r.get("country"),
            "event": r.get("eventName"), "actual": r.get("actual"), "consensus": r.get("consensus"),
            "previous": r.get("previous"),
        })
    return out


def session_bar(ticker: str) -> dict:
    """Provisional daily bar for the most recent *completed* regular session, built from
    the quote (regular close, day range, volume) and the intraday chart (09:30 open).
    Used only when the historical endpoint has not yet published that session."""
    sym, cls = asset_class(ticker)
    info = _data(get_json(f"{BASE}/quote/{sym}/info", params={"assetclass": cls}))
    sec = info.get("secondaryData") or {}
    prim = info.get("primaryData") or {}
    ts = str(sec.get("lastTradeTimestamp") or "")
    m = re.search(r"Closed at ([A-Za-z]{3} \d{1,2}, \d{4})", ts)
    if m:
        close = num(sec.get("lastSalePrice"))
        d = datetime.strptime(m.group(1), "%b %d, %Y").date()
    else:
        m2 = re.search(r"([A-Za-z]{3} \d{1,2}, \d{4})", str(prim.get("lastTradeTimestamp") or ""))
        if not m2 or "closed" not in str(info.get("marketStatus", "")).lower():
            raise ProviderError("no completed session in quote")
        close = num(prim.get("lastSalePrice"))
        d = datetime.strptime(m2.group(1), "%b %d, %Y").date()
    ks = info.get("keyStats") or {}
    rng = str((ks.get("dayrange") or {}).get("value", "")).split("-")
    hi = num(rng[1]) if len(rng) == 2 else None
    lo = num(rng[0]) if len(rng) == 2 else None
    chart = _data(get_json(f"{BASE}/quote/{sym}/chart", params={"assetclass": cls})).get("chart") or []
    open_ = next((float(p["y"]) for p in chart if str((p.get("z") or {}).get("dateTime", "")).startswith("9:30 AM")), None)
    if hi is None or lo is None:
        # ETFs have no day range in the quote: use regular-session minute prices (approximate high/low)
        def _mins(p):
            m = re.match(r"(\d+):(\d+) (AM|PM)", str((p.get("z") or {}).get("dateTime", "")))
            if not m:
                return -1
            h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
            return h * 60 + int(m.group(2))
        reg = [float(p["y"]) for p in chart if 570 <= _mins(p) <= 960 and p.get("y") is not None]
        if reg:
            hi, lo = max(reg), min(reg)
    if close is None or open_ is None or hi is None or lo is None:
        raise ProviderError("incomplete session data")
    hi, lo = max(hi, open_, close), min(lo, open_, close)
    return {"date": d, "open": open_, "high": hi, "low": lo, "close": close, "volume": num(prim.get("volume")) or 0.0}
