"""FX rates from the European Central Bank reference rates via Frankfurter
(api.frankfurter.app). Official ECB data, daily ~16:00 CET, free, keyless."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .http import ProviderError, get_json

SOURCE = "ECB reference rates (via Frankfurter)"


def latest(base: str = "EUR", quote: str = "USD") -> dict:
    d = get_json("https://api.frankfurter.app/latest", params={"from": base, "to": quote})
    rate = (d.get("rates") or {}).get(quote)
    if not rate:
        raise ProviderError("Frankfurter: no rate")
    return {"base": base, "quote": quote, "rate": float(rate), "date": d.get("date"), "source": SOURCE}


def history(start: date, end: date | None = None, base: str = "EUR", quote: str = "USD") -> pd.Series:
    end = end or date.today()
    d = get_json(f"https://api.frankfurter.app/{start.isoformat()}..{end.isoformat()}", params={"from": base, "to": quote})
    rates = d.get("rates") or {}
    if not rates:
        raise ProviderError("Frankfurter: empty history")
    s = pd.Series({date.fromisoformat(k): float(v[quote]) for k, v in rates.items() if quote in v}).sort_index()
    return s


def rate_on(series: pd.Series, d: date) -> float | None:
    """Last available fixing on or before date d (no look-ahead)."""
    s = series[series.index <= d]
    if s.empty:
        return None
    if (d - s.index[-1]) > timedelta(days=7):
        return None
    return float(s.iloc[-1])
