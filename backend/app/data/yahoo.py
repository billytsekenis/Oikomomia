"""Yahoo Finance chart API (unofficial, keyless).

Secondary price source and the only free source here for the S&P 500 / Dow
index levels. Yahoo aggressively rate-limits datacenter IPs (HTTP 429); the
HTTP layer puts the host on a cool-down and callers fall back to Nasdaq or to
clearly labelled ETF proxies. Licensing: Yahoo terms - personal use only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from .http import ProviderError, get_json

SOURCE = "Yahoo Finance"
HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def _chart(ticker: str, params: dict) -> dict:
    last: Exception | None = None
    for h in HOSTS:
        try:
            payload = get_json(f"{h}/v8/finance/chart/{ticker}", params=params, retries=1, cooldown_on_429=900)
            res = (payload.get("chart") or {}).get("result")
            if not res:
                err = (payload.get("chart") or {}).get("error")
                raise ProviderError(f"Yahoo: {err}")
            return res[0]
        except ProviderError as exc:
            last = exc
    raise ProviderError(str(last))


def history(ticker: str, start: date, end: date | None = None) -> tuple[pd.DataFrame, dict]:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) if end is None else int(
        datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp())
    r = _chart(ticker, {"period1": p1, "period2": p2, "interval": "1d", "events": "div,split", "includeAdjustedClose": "true"})
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((r.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")
    if not ts:
        raise ProviderError(f"Yahoo: empty history for {ticker}")
    tz_off = (r.get("meta") or {}).get("gmtoffset", 0)
    idx = [datetime.fromtimestamp(t + tz_off, tz=timezone.utc).date() for t in ts]
    df = pd.DataFrame({
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"), "close": q.get("close"),
        "volume": q.get("volume"), "adj_close": adj if adj else q.get("close"),
    }, index=idx)
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["close"]).sort_index()
    df["volume"] = df["volume"].fillna(0.0)
    return df, r.get("meta") or {}


def quote(ticker: str) -> dict:
    r = _chart(ticker, {"range": "5d", "interval": "1d"})
    m = r.get("meta") or {}
    price = m.get("regularMarketPrice")
    if price is None:
        raise ProviderError(f"Yahoo: no price for {ticker}")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    closes = (((r.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    closes = [c for c in closes if c is not None]
    if len(closes) >= 2:
        prev = closes[-2]
    t = m.get("regularMarketTime")
    return {
        "ticker": ticker, "name": m.get("longName") or m.get("shortName"), "price": float(price),
        "previous_close": prev, "change": (price - prev) if prev else None,
        "change_pct": ((price / prev - 1) * 100) if prev else None,
        "day_high": m.get("regularMarketDayHigh"), "day_low": m.get("regularMarketDayLow"),
        "volume": m.get("regularMarketVolume"), "high_52w": m.get("fiftyTwoWeekHigh"),
        "low_52w": m.get("fiftyTwoWeekLow"), "currency": m.get("currency"),
        "quote_time": datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t else None,
        "source": SOURCE + " (delayed)",
    }


def intraday(ticker: str, interval: str = "5m", rng: str = "5d") -> pd.DataFrame:
    r = _chart(ticker, {"range": rng, "interval": interval})
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    df = pd.DataFrame({"open": q.get("open"), "high": q.get("high"), "low": q.get("low"), "close": q.get("close"),
                       "volume": q.get("volume")}, index=pd.to_datetime(ts, unit="s", utc=True))
    return df.dropna(subset=["close"])
