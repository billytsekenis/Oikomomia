"""Market data service: routes each symbol to the best available source, caches
daily bars in the `price_bars` table, validates them, and attaches provenance
(source, fetched_at, adjustment method, proxy) to every series and quote.

Routing (first that works):
  stocks / ETFs  : Nasdaq (split-adjusted; dividend-adjusted here)  -> Yahoo (adjusted)
  ^IXIC, ^NDX    : Nasdaq index                                      -> Yahoo
  ^GSPC ^DJI ^RUT: Yahoo                                             -> ETF proxy (SPY/DIA/IWM), labelled
  ^VIX ^VIX3M ^VIX9D: Cboe official CSV                              -> Yahoo
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from ..analytics.market_hours import last_completed_session
from ..config import get_settings
from ..db import SessionLocal
from ..models import PriceBar, SeriesMeta, utcnow
from ..universe import UNIVERSE
from . import demo, macro, nasdaq, yahoo
from .cache import cached
from .http import ProviderError

log = logging.getLogger("oik.market")

INDEX_PROXIES = {"^GSPC": "SPY", "^DJI": "DIA", "^RUT": "IWM", "^IXIC": "QQQ", "^NDX": "QQQ"}
CBOE = {"^VIX": "VIX", "^VIX3M": "VIX3M", "^VIX9D": "VIX9D"}
INDEX_NAMES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite", "^NDX": "Nasdaq-100", "^DJI": "Dow Jones Industrial Average",
               "^RUT": "Russell 2000", "^VIX": "Cboe VIX", "^VIX3M": "Cboe 3-Month VIX", "^VIX9D": "Cboe 9-Day VIX"}
_SPLIT_RATIOS = (2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(t: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(t, threading.Lock())


# ------------------------------------------------------------------ validation
def validate_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop impossible rows and repair minor OHLC inconsistencies."""
    issues: list[str] = []
    df = df.copy()
    for c in ("open", "high", "low"):
        df[c] = df[c].fillna(df["close"])
    bad = (df["close"] <= 0) | df["close"].isna()
    if bad.any():
        issues.append(f"dropped {int(bad.sum())} rows with non-positive/missing close")
        df = df[~bad]
    hi = df[["open", "high", "low", "close"]].max(axis=1)
    lo = df[["open", "high", "low", "close"]].min(axis=1)
    if ((df["high"] < hi - 1e-9) | (df["low"] > lo + 1e-9)).any():
        issues.append("repaired OHLC bounds")
        df["high"], df["low"] = hi, lo
    r = np.log(df["close"]).diff().abs()
    if (r > 0.6).any():
        issues.append(f"{int((r > 0.6).sum())} daily moves > 60% (possible unadjusted split) - kept, flagged")
    df["volume"] = df["volume"].fillna(0).clip(lower=0)
    return df, issues


def infer_splits(divs: list[tuple[date, float]], shares: list[tuple[date, float]]) -> dict[date, int]:
    """Infer stock splits from the raw dividend series, confirmed by SEC share counts.

    Nasdaq prices are split-adjusted but its dividend amounts are not. A drop in
    the per-share dividend by an exact integer ratio k is a split *candidate*;
    it is accepted only if SEC cover-page share counts also jumped by ~k between
    the two dividend dates (a dividend cut does not change the share count).
    Returns {first_ex_date_after_split: k}.
    """
    out: dict[date, int] = {}
    if not shares:
        return out
    sh = pd.Series({d: v for d, v in shares}).sort_index()
    for (d0, a0), (d1, a1) in zip(divs, divs[1:]):
        if a1 <= 0:
            continue
        ratio = a0 / a1
        k = next((k for k in _SPLIT_RATIOS if abs(ratio / k - 1) < 0.05), None)
        if not k:
            continue
        before = sh[sh.index <= d0 + timedelta(days=30)]
        after = sh[sh.index >= d1 - timedelta(days=60)]
        if before.empty or after.empty:
            continue
        sratio = after.iloc[0] / before.iloc[-1]
        if 0.8 * k <= sratio <= 1.25 * k:
            out[d1] = k
    return out


def dividend_adjust(df: pd.DataFrame, divs: list[tuple[date, float]], splits: dict[date, int]) -> tuple[pd.Series, int]:
    """Total-return adjusted close (CRSP-style multiplicative factors)."""
    close = df["close"]
    if not divs:
        return close.copy(), 0
    # convert raw dividends to the split-adjusted share basis used by the price series
    adj_divs = []
    for d, amt in divs:
        later = [k for sd, k in splits.items() if sd > d]
        factor = float(np.prod(later)) if later else 1.0
        adj_divs.append((d, amt / factor))
    factors = pd.Series(1.0, index=close.index)
    used = 0
    idx = close.index
    for d, amt in adj_divs:
        pos = idx.searchsorted(d)  # first bar on/after ex-date
        if pos <= 0 or pos >= len(idx):
            continue
        prev_close = close.iloc[pos - 1]
        f = 1.0 - amt / prev_close
        if not (0.85 < f < 1.0):
            continue  # implausible (>15%) -> skip rather than distort history
        factors.iloc[:pos] *= f
        used += 1
    return close * factors, used


# ------------------------------------------------------------------- fetching
def _sec_shares(ticker: str) -> list[tuple[date, float]]:
    try:
        from .fundamentals_store import company_facts_for
        cf = company_facts_for(ticker)
        return [(date.fromisoformat(e), v) for e, v, _ in (cf or {}).get("instant", {}).get("shares_outstanding", [])]
    except Exception:  # noqa: BLE001 - shares are only used to confirm splits
        return []


NASDAQ_LISTED_ETFS = {"QQQ"}


def _sec_dividends(ticker: str) -> list[tuple[date, float]]:
    """Quarterly dividends per share from 10-Q/10-K XBRL. Exact ex-dates are not in
    XBRL, so each is placed 30 days before the fiscal quarter end (approximation)."""
    try:
        from .fundamentals_store import company_facts_for
        cf = company_facts_for(ticker) or {}
        q = [(date.fromisoformat(e), v) for e, v, _ in cf.get("quarterly", {}).get("dps", []) if v and v > 0]
        out = {e - timedelta(days=30): v for e, v in q}
        # Fill fiscal years where quarterly tags are incomplete from the annual figure.
        for e, v, _ in cf.get("annual", {}).get("dps", []):
            fy_end = date.fromisoformat(e)
            inside = [qe for qe, _ in q if fy_end - timedelta(days=366) < qe <= fy_end]
            if v and v > 0 and len(inside) < 4:
                for k in range(4):
                    out.pop(next((d for d in list(out) if abs((d - (fy_end - timedelta(days=30 + 91 * k))).days) < 40), None), None)
                for k in range(4):
                    out[fy_end - timedelta(days=30 + 91 * k)] = v / 4
        return sorted(out.items())
    except Exception:  # noqa: BLE001
        return []


def _fetch_remote(ticker: str, years: int) -> tuple[pd.DataFrame, dict]:
    start = date.today() - timedelta(days=int(365.25 * years) + 5)
    errors: list[str] = []
    if get_settings().demo_mode:
        df = demo.history(ticker, years)
        return df, {"source": demo.SOURCE, "adjusted": "synthetic"}

    if ticker in CBOE:
        try:
            df = macro.cboe_index(CBOE[ticker])
            df = df[df.index >= start]
            df["adj_close"] = df["close"]
            return df, {"source": macro.CBOE_SOURCE, "adjusted": "index level"}
        except ProviderError as exc:
            errors.append(str(exc))
    if ticker.startswith("^") and ticker not in ("^IXIC", "^NDX"):
        try:
            df, _ = yahoo.history(ticker, start)
            return df, {"source": yahoo.SOURCE, "adjusted": "index level"}
        except ProviderError as exc:
            errors.append(str(exc))
        if ticker in INDEX_PROXIES:
            df, meta = _fetch_remote(INDEX_PROXIES[ticker], years)
            meta["proxy_for"] = ticker
            meta["proxy"] = INDEX_PROXIES[ticker]
            meta["source"] = f"{meta['source']} - {INDEX_PROXIES[ticker]} ETF used as proxy"
            return df, meta
        raise ProviderError("; ".join(errors))

    info = UNIVERSE.get(ticker, {})
    if info.get("type") == "etf" and ticker not in NASDAQ_LISTED_ETFS:
        # Nasdaq has no dividend history for NYSE-listed ETFs: prefer Yahoo's total-return series.
        try:
            df, _ = yahoo.history(ticker, start)
            return df, {"source": yahoo.SOURCE, "adjusted": "split & dividend adjusted (Yahoo)"}
        except ProviderError as exc:
            errors.append(str(exc))
    try:
        df = nasdaq.historical(ticker, start, date.today())
        if ticker.startswith("^"):
            df["adj_close"] = df["close"]
            return df, {"source": nasdaq.SOURCE, "adjusted": "index level"}
        divs = nasdaq.dividends(ticker)
        div_src = "Nasdaq dividends"
        if not divs and info.get("type", "stock") == "stock":
            divs = _sec_dividends(ticker)
            div_src = "SEC XBRL dividends/share, ex-dates estimated"
        splits = infer_splits(divs, _sec_shares(ticker)) if divs else {}
        adj, n_used = dividend_adjust(df, divs, splits)
        df["adj_close"] = adj
        if n_used:
            how = f"split-adj. (Nasdaq) + {n_used} dividends ({div_src})" + (f"; {len(splits)} split(s) inferred" if splits else "")
        else:
            how = "split-adjusted price return; dividends NOT included"
        return df, {"source": nasdaq.SOURCE, "adjusted": how}
    except ProviderError as exc:
        errors.append(str(exc))
    try:
        df, _ = yahoo.history(ticker if ticker != "^IXIC" else "^IXIC", start)
        return df, {"source": yahoo.SOURCE, "adjusted": "split & dividend adjusted (Yahoo)"}
    except ProviderError as exc:
        errors.append(str(exc))
    raise ProviderError(f"{ticker}: all price sources failed: " + " | ".join(errors))


def _load_db(ticker: str) -> tuple[pd.DataFrame | None, SeriesMeta | None]:
    with SessionLocal() as db:
        meta = db.get(SeriesMeta, ticker)
        rows = db.execute(select(PriceBar.date, PriceBar.open, PriceBar.high, PriceBar.low, PriceBar.close,
                                 PriceBar.adj_close, PriceBar.volume).where(PriceBar.ticker == ticker)
                          .order_by(PriceBar.date)).all()
    if not rows:
        return None, meta
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "adj_close", "volume"]).set_index("date")
    return df, meta


def _save_db(ticker: str, df: pd.DataFrame, meta: dict, error: str = "") -> None:
    now = utcnow()
    with SessionLocal() as db:
        keep = db.execute(select(PriceBar).where(PriceBar.ticker == ticker, PriceBar.date > df.index[-1],
                                                 PriceBar.source.like("%provisional%"))).scalars().all()
        keep = [{c: getattr(r, c) for c in ("ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "source", "fetched_at")}
                for r in keep]
        db.execute(delete(PriceBar).where(PriceBar.ticker == ticker))
        if keep:
            db.bulk_insert_mappings(PriceBar, keep)
        db.bulk_insert_mappings(PriceBar, [
            {"ticker": ticker, "date": d, "open": float(r.open), "high": float(r.high), "low": float(r.low),
             "close": float(r.close), "adj_close": float(r.adj_close), "volume": float(r.volume or 0),
             "source": meta["source"][:40], "fetched_at": now}
            for d, r in df.iterrows()
        ])
        sm = db.get(SeriesMeta, ticker) or SeriesMeta(ticker=ticker)
        sm.source = meta["source"][:120]
        sm.adjusted = (meta.get("adjusted", "") + (f" | proxy:{meta['proxy']}" if meta.get("proxy") else ""))[:160]
        sm.first_date = df.index[0]
        sm.last_date = max([df.index[-1]] + [k["date"] for k in keep])
        sm.fetched_at = now
        sm.error = error
        db.merge(sm)
        db.commit()
    _meta_full[ticker] = meta


_meta_full: dict[str, dict] = {}


def _needs_refresh(meta: SeriesMeta | None) -> bool:
    if meta is None or meta.last_date is None:
        return True
    age = utcnow() - meta.fetched_at
    if age > timedelta(hours=12):
        return True
    # Nasdaq history publishes the latest session late; provisional bars cover the gap, so
    # retry the official history at most every 3 hours instead of on every request.
    return meta.last_date < last_completed_session() and age > timedelta(hours=3)


def bars(ticker: str, years: int | None = None, force: bool = False, provisional: bool = True) -> pd.DataFrame:
    """Daily bars with provenance in df.attrs. Raises ProviderError if no data at all."""
    ticker = ticker.upper()
    years = years or get_settings().history_years
    with _lock(ticker):
        df, meta = _load_db(ticker)
        if force or _needs_refresh(meta):
            try:
                new, m = _fetch_remote(ticker, years)
                new, issues = validate_bars(new)
                if len(new) < 2:
                    raise ProviderError(f"{ticker}: too little data")
                _save_db(ticker, new, m, "; ".join(issues))
                df, meta = _load_db(ticker)
            except ProviderError as exc:
                if df is None:
                    raise
                log.warning("refresh failed for %s, serving cached: %s", ticker, exc)
                df.attrs["stale"] = True
                df.attrs["error"] = str(exc)
        if df is None:
            raise ProviderError(f"No data for {ticker}")
        if provisional:
            df = _maybe_append_session(ticker, df, meta)
        full = _meta_full.get(ticker, {})
        df.attrs.update({
            "ticker": ticker,
            "source": full.get("source") or (meta.source if meta else ""),
            "adjusted": full.get("adjusted") or (meta.adjusted if meta else ""),
            "proxy": full.get("proxy") or ((meta.adjusted.split("proxy:")[1]) if meta and "proxy:" in (meta.adjusted or "") else None),
            "fetched_at": (meta.fetched_at.isoformat() + "Z") if meta else None,
            "last_date": str(df.index[-1]),
            "provisional_last_bar": bool(df.attrs.get("provisional_last_bar")) or _is_provisional(ticker, df.index[-1]),
            "demo": get_settings().demo_mode,
        })
        return df


_session_tried: dict[str, float] = {}


def _maybe_append_session(ticker: str, df: pd.DataFrame, meta: SeriesMeta | None) -> pd.DataFrame:
    """If Nasdaq's historical feed lags the last completed session, append a provisional
    bar built from the Nasdaq quote + intraday chart (flagged in attrs and in the DB source)."""
    import time as _t
    if get_settings().demo_mode or meta is None or not (meta.source or "").startswith("Nasdaq") or ticker in CBOE:
        return df
    target = last_completed_session()
    if df.index[-1] >= target or _session_tried.get(ticker, 0) > _t.time() - 900:
        return df
    _session_tried[ticker] = _t.time()
    try:
        b = nasdaq.session_bar(ticker)
    except ProviderError as exc:
        log.info("no provisional session bar for %s: %s", ticker, exc)
        return df
    if b["date"] != target or b["date"] <= df.index[-1]:
        return df
    ratio = float(df["adj_close"].iloc[-1] / df["close"].iloc[-1])
    with SessionLocal() as db:
        db.merge(PriceBar(ticker=ticker, date=b["date"], open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                          adj_close=b["close"] * ratio, volume=b["volume"], source="Nasdaq quote (provisional)", fetched_at=utcnow()))
        sm = db.get(SeriesMeta, ticker)
        if sm is not None and (sm.last_date is None or sm.last_date < b["date"]):
            sm.last_date = b["date"]
        db.commit()
    row = pd.DataFrame([{**{k: b[k] for k in ("open", "high", "low", "close", "volume")}, "adj_close": b["close"] * ratio}],
                       index=pd.Index([b["date"]], name="date"))
    out = pd.concat([df, row[df.columns]])
    out.attrs.update(df.attrs)
    out.attrs["provisional_last_bar"] = True
    return out


def bars_many(tickers: list[str], years: int | None = None, workers: int = 4) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}

    def one(t):
        try:
            return t, bars(t, years, provisional=False)
        except ProviderError as exc:
            log.warning("bars failed for %s: %s", t, exc)
            return t, None

    # Fresh series are pure local DB reads, which are much faster serially (threads contend
    # heavily in SQLite/SQLAlchemy row materialisation); only network refreshes use the pool.
    with SessionLocal() as db:
        metas = {m.ticker: m for m in db.execute(select(SeriesMeta).where(SeriesMeta.ticker.in_(tickers))).scalars()}
    local = [t for t in tickers if not _needs_refresh(metas.get(t))]
    remote = [t for t in tickers if t not in set(local)]
    for t in local:
        _, df = one(t)
        if df is not None and len(df) > 0:
            out[t] = df
    if remote:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for t, df in ex.map(one, remote):
                if df is not None and len(df) > 0:
                    out[t] = df
    return {t: out[t] for t in tickers if t in out}


def _is_provisional(ticker: str, d) -> bool:
    with SessionLocal() as db:
        row = db.get(PriceBar, (ticker, d))
        return bool(row and "provisional" in (row.source or ""))


def provenance(df: pd.DataFrame) -> dict:
    return {k: df.attrs.get(k) for k in ("source", "adjusted", "proxy", "fetched_at", "last_date", "provisional_last_bar",
                                         "stale", "error", "demo")}


# --------------------------------------------------------------------- quotes
def _quote_remote(ticker: str) -> dict:
    if get_settings().demo_mode:
        return demo.quote(ticker)
    errors = []
    if ticker in CBOE:
        df = bars(ticker)
        last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
        return {"ticker": ticker, "name": INDEX_NAMES[ticker], "price": last, "previous_close": prev,
                "change": last - prev, "change_pct": (last / prev - 1) * 100, "quote_time": str(df.index[-1]),
                "source": df.attrs.get("source", macro.CBOE_SOURCE) + " (end of day)"}
    if ticker.startswith("^") and ticker not in ("^IXIC", "^NDX"):
        try:
            q = yahoo.quote(ticker)
            q["name"] = INDEX_NAMES.get(ticker, q.get("name"))
            return q
        except ProviderError as exc:
            errors.append(str(exc))
        if ticker in INDEX_PROXIES:
            q = dict(_quote_remote(INDEX_PROXIES[ticker]))
            q.update({"ticker": ticker, "name": INDEX_NAMES.get(ticker), "proxy": INDEX_PROXIES[ticker],
                      "source": q["source"] + f" - {INDEX_PROXIES[ticker]} ETF proxy (index level unavailable)"})
            return q
        raise ProviderError("; ".join(errors))
    try:
        q = nasdaq.quote(ticker)
        if ticker in INDEX_NAMES:
            q["name"] = INDEX_NAMES[ticker]
        if q.get("previous_close") is None:
            status = (q.get("market_status") or "").lower()
            rc, rcp = q.get("regular_close"), q.get("regular_close_change_pct")
            if "pre" in status and rc:
                q["previous_close"] = rc  # pre-market: reference is the last regular close
            elif rc is not None and rcp is not None:
                q["previous_close"] = rc / (1 + rcp / 100)  # after-hours/closed: session's prior close
            elif q.get("change") is not None:
                q["previous_close"] = q["price"] - q["change"]
        if q.get("previous_close"):
            q["extended_change_pct"] = q.get("change_pct") if q.get("regular_close") else None
            q["change"] = q["price"] - q["previous_close"]
            q["change_pct"] = (q["price"] / q["previous_close"] - 1) * 100
        return q
    except ProviderError as exc:
        errors.append(str(exc))
    try:
        return yahoo.quote(ticker)
    except ProviderError as exc:
        errors.append(str(exc))
    raise ProviderError(f"{ticker}: quote unavailable: " + " | ".join(errors))


def quote(ticker: str, ttl: float = 20.0) -> dict:
    ticker = ticker.upper()
    env = cached(f"quote:{ticker}", ttl, "quote", lambda: _quote_remote(ticker))
    q = dict(env["value"])
    q["fetched_at"] = env["fetched_at"]
    q["stale"] = env["stale"]
    if not q.get("name"):
        q["name"] = UNIVERSE.get(ticker, {}).get("name", ticker)
    return q


def quotes(tickers: list[str], workers: int = 4) -> dict[str, dict]:
    def one(t):
        try:
            return t, quote(t)
        except ProviderError as exc:
            return t, {"ticker": t, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, tickers))


def daily_change_from_bars(df: pd.DataFrame) -> dict:
    c = df["adj_close"]
    out = {"last": float(df["close"].iloc[-1]), "date": str(df.index[-1])}
    for label, n in (("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252)):
        out[label] = float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
    ytd = c[[d.year == df.index[-1].year for d in c.index]]
    prev = c[[d.year < df.index[-1].year for d in c.index]]
    out["YTD"] = float(c.iloc[-1] / prev.iloc[-1] - 1) if len(prev) and len(ytd) else None
    return out


def last_refresh_times() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(select(SeriesMeta)).scalars().all()
        return [{"ticker": r.ticker, "source": r.source, "adjusted": r.adjusted, "last_date": str(r.last_date),
                 "fetched_at": r.fetched_at.isoformat() + "Z", "error": r.error} for r in rows]


def as_of_now() -> str:
    return datetime.utcnow().isoformat() + "Z"
