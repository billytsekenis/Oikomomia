"""Official macro & volatility sources.

* US Treasury daily par yield curve (home.treasury.gov) - official, free, daily.
* Cboe index history CSVs (VIX, VIX9D, VIX3M) - official, free, end-of-day.
* FRED (fred.stlouisfed.org fredgraph CSV, keyless; api.stlouisfed.org with
  FRED_API_KEY) - CPI, unemployment, Fed funds, credit spreads, etc.
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from ..config import get_settings
from .http import ProviderError, get, get_json

TREASURY_SOURCE = "U.S. Department of the Treasury"
CBOE_SOURCE = "Cboe Global Markets"
FRED_SOURCE = "FRED, Federal Reserve Bank of St. Louis"

TREASURY_COLS = {"1 Mo": "1M", "3 Mo": "3M", "6 Mo": "6M", "1 Yr": "1Y", "2 Yr": "2Y", "5 Yr": "5Y",
                 "10 Yr": "10Y", "20 Yr": "20Y", "30 Yr": "30Y"}


def treasury_year(year: int) -> pd.DataFrame:
    url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
    resp = get(url, timeout=60)
    df = pd.read_csv(io.StringIO(resp.text))
    if "Date" not in df.columns:
        raise ProviderError("Treasury: unexpected CSV format")
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y").dt.date
    keep = {k: v for k, v in TREASURY_COLS.items() if k in df.columns}
    out = df.set_index("Date")[list(keep)].rename(columns=keep).sort_index()
    return out.apply(pd.to_numeric, errors="coerce")


def treasury_curve(years: int = 3) -> pd.DataFrame:
    this = date.today().year
    frames = []
    for y in range(this - years + 1, this + 1):
        try:
            frames.append(treasury_year(y))
        except ProviderError:
            if y == this:
                raise
    if not frames:
        raise ProviderError("Treasury: no data")
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def cboe_index(symbol: str) -> pd.DataFrame:
    url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
    resp = get(url, timeout=60)
    df = pd.read_csv(io.StringIO(resp.text))
    cols = {c.upper(): c for c in df.columns}
    if "DATE" not in cols or "CLOSE" not in cols:
        raise ProviderError(f"Cboe: unexpected CSV for {symbol}")
    df["date"] = pd.to_datetime(df[cols["DATE"]], format="mixed").dt.date
    out = pd.DataFrame({
        "open": pd.to_numeric(df[cols.get("OPEN", cols["CLOSE"])], errors="coerce"),
        "high": pd.to_numeric(df[cols.get("HIGH", cols["CLOSE"])], errors="coerce"),
        "low": pd.to_numeric(df[cols.get("LOW", cols["CLOSE"])], errors="coerce"),
        "close": pd.to_numeric(df[cols["CLOSE"]], errors="coerce"),
    })
    out.index = df["date"]
    out.index.name = "date"
    out = out.dropna(subset=["close"]).sort_index()
    out["volume"] = 0.0
    return out[~out.index.duplicated(keep="last")]


FRED_SERIES = {
    "CPIAUCSL": "CPI (all items)",
    "CPILFESL": "Core CPI",
    "PCEPILFE": "Core PCE",
    "UNRATE": "Unemployment rate",
    "FEDFUNDS": "Effective Fed funds rate",
    "PAYEMS": "Nonfarm payrolls",
    "ICSA": "Initial jobless claims",
    "BAMLH0A0HYM2": "High-yield OAS",
    "UMCSENT": "UMich consumer sentiment",
    "GDPC1": "Real GDP",
    "INDPRO": "Industrial production",
}


def fred_series(series_id: str, start: str = "2010-01-01") -> pd.Series:
    key = get_settings().fred_api_key
    if key:
        payload = get_json("https://api.stlouisfed.org/fred/series/observations",
                           params={"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start})
        obs = payload.get("observations") or []
        s = pd.Series({pd.Timestamp(o["date"]).date(): pd.to_numeric(o["value"], errors="coerce") for o in obs})
    else:
        resp = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id, "cosd": start},
                   timeout=45, retries=3)
        df = pd.read_csv(io.StringIO(resp.text))
        if df.shape[1] < 2:
            raise ProviderError(f"FRED: unexpected CSV for {series_id}")
        dcol = df.columns[0]
        s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                      index=pd.to_datetime(df[dcol]).dt.date)
    s = s.dropna().sort_index()
    if s.empty:
        raise ProviderError(f"FRED: empty series {series_id}")
    return s
