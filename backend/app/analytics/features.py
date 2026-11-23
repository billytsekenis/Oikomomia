"""Point-in-time feature engineering for the prediction engine.

Every feature at date t uses only information available at the close of t:
rolling windows end at t, fundamentals are used only after their SEC *filing*
date, macro series are as-of t. Targets are forward log returns from t to t+h.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import fundamentals as fa
from .indicators import bollinger, rsi

HORIZONS: dict[str, int] = {"1D": 1, "7D": 5, "30D": 21, "90D": 63, "6M": 126, "12M": 252}
HORIZON_LABELS = {"1D": "Next session", "7D": "7 days (5 sessions)", "30D": "30 days (21 sessions)",
                  "90D": "90 days (63 sessions)", "6M": "6 months (126 sessions)", "12M": "12 months (252 sessions)"}

STOCK_FEATURES = ["mom5", "mom21", "mom63", "mom126", "mom12_1", "vol21", "vol63", "vol_ratio", "rsi14",
                  "dist_sma50", "dist_sma200", "bb_pctb", "rvol20", "hi52_dist", "lo52_dist", "beta126",
                  "idio_vol63", "rev_growth", "net_margin", "roe"]
MARKET_FEATURES = ["mkt_mom21", "mkt_mom63", "mkt_vol21", "vix", "vix_z", "term_spread", "rate_chg63"]
ALL_FEATURES = STOCK_FEATURES + MARKET_FEATURES


def market_frame(mkt: pd.DataFrame, vix: pd.Series | None, rates: pd.DataFrame | None) -> pd.DataFrame:
    c = mkt["adj_close"]
    lr = np.log(c).diff()
    out = pd.DataFrame(index=mkt.index)
    out["mkt_ret"] = lr
    out["mkt_mom21"] = np.log(c / c.shift(21))
    out["mkt_mom63"] = np.log(c / c.shift(63))
    out["mkt_vol21"] = lr.rolling(21).std() * np.sqrt(252)
    if vix is not None and len(vix):
        v = vix.reindex(out.index).ffill()
        out["vix"] = v
        out["vix_z"] = (v - v.rolling(252, min_periods=60).mean()) / v.rolling(252, min_periods=60).std()
    else:
        out["vix"] = np.nan
        out["vix_z"] = np.nan
    if rates is not None and len(rates):
        r = rates.reindex(out.index).ffill()
        out["rf"] = r["3M"] / 100 if "3M" in r else np.nan
        out["term_spread"] = (r["10Y"] - r["3M"]) / 100 if "10Y" in r and "3M" in r else np.nan
        out["rate_chg63"] = (r["10Y"] - r["10Y"].shift(63)) / 100 if "10Y" in r else np.nan
    else:
        out["rf"] = np.nan
        out["term_spread"] = np.nan
        out["rate_chg63"] = np.nan
    return out


def _pit_fundamentals(cf: dict | None, index: pd.Index) -> pd.DataFrame:
    """Price-free (split-invariant) fundamentals, recomputed at each month end
    using only facts filed by then, forward-filled daily."""
    out = pd.DataFrame(index=index, columns=["rev_growth", "net_margin", "roe"], dtype=float)
    if not cf:
        return out
    months = pd.Series(index, index=index).groupby([d.year * 100 + d.month for d in index]).last()
    vals = {}
    for d in months.values:
        g = fa.growth(cf, "revenue", d)
        ni, _ = fa.ttm(cf, "net_income", d)
        rev, _ = fa.ttm(cf, "revenue", d)
        eq, _ = fa.latest_instant(cf, "equity", d)
        vals[d] = (g, (ni / rev) if ni is not None and rev else None,
                   (ni / eq) if ni is not None and eq and eq > 0 else None)
    m = pd.DataFrame.from_dict(vals, orient="index", columns=["rev_growth", "net_margin", "roe"], dtype=float)
    return m.reindex(index).ffill()


def stock_features(df: pd.DataFrame, mf: pd.DataFrame, cf: dict | None = None) -> pd.DataFrame:
    c = df["adj_close"]
    lr = np.log(c).diff()
    f = df["adj_close"] / df["close"]
    high, low = df["high"] * f, df["low"] * f
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["ret1"] = lr
    for n in (5, 21, 63, 126):
        out[f"mom{n}"] = np.log(c / c.shift(n))
    out["mom12_1"] = np.log(c.shift(21) / c.shift(252))
    out["vol21"] = lr.rolling(21).std() * np.sqrt(252)
    out["vol63"] = lr.rolling(63).std() * np.sqrt(252)
    out["vol_ratio"] = out["vol21"] / out["vol63"]
    out["rsi14"] = rsi(c, 14)
    out["dist_sma50"] = c / c.rolling(50).mean() - 1
    out["dist_sma200"] = c / c.rolling(200).mean() - 1
    out["bb_pctb"] = bollinger(c)["pct_b"]
    vol = df["volume"].replace(0, np.nan)
    out["rvol20"] = np.log(vol / vol.shift(1).rolling(20).mean())
    out["hi52_dist"] = c / high.rolling(252, min_periods=60).max() - 1
    out["lo52_dist"] = c / low.rolling(252, min_periods=60).min() - 1
    m = mf.reindex(df.index)
    mret = m["mkt_ret"]
    cov = lr.rolling(126, min_periods=60).cov(mret)
    var = mret.rolling(126, min_periods=60).var()
    out["beta126"] = cov / var
    resid = lr - out["beta126"] * mret
    out["idio_vol63"] = resid.rolling(63).std() * np.sqrt(252)
    out = out.join(_pit_fundamentals(cf, df.index))
    for col in MARKET_FEATURES + ["rf", "mkt_ret"]:
        out[col] = m[col] if col in m else np.nan
    return out


def add_targets(feat: pd.DataFrame) -> pd.DataFrame:
    lc = np.log(feat["close"])
    for h, n in HORIZONS.items():
        feat[f"y_{h}"] = lc.shift(-n) - lc
    return feat


def build_panel(bars: dict[str, pd.DataFrame], mf: pd.DataFrame, facts: dict[str, dict | None],
                step: int = 5, min_history: int = 260) -> pd.DataFrame:
    """Long panel (date, ticker) sampled every `step` sessions, with targets."""
    frames = []
    grid = set(mf.index[::step])  # one common sampling calendar -> full cross-sections per date
    for t, df in bars.items():
        if len(df) < min_history:
            continue
        feat = add_targets(stock_features(df, mf, facts.get(t)))
        feat = feat.iloc[252:]
        feat = feat[[d in grid for d in feat.index]]
        feat["ticker"] = t
        frames.append(feat)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames)
    panel.index.name = "date"
    panel = panel.reset_index()
    return panel


def cross_sectional_rank(panel: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Rank-transform stock features within each date to [-0.5, 0.5]; robust to
    outliers and to regime-level shifts. Market features are left as is."""
    out = panel.copy()
    g = out.groupby("date")
    for c in cols:
        out[c] = g[c].rank(pct=True) - 0.5
    return out


def latest_feature_row(df: pd.DataFrame, mf: pd.DataFrame, cf: dict | None, reference: pd.DataFrame | None) -> pd.DataFrame:
    """Feature row for the last bar, ranked against the latest cross-section in
    `reference` (the universe's last-date features) to match training transforms."""
    feat = stock_features(df, mf, cf).iloc[[-1]].copy()
    if reference is not None and len(reference):
        for c in STOCK_FEATURES:
            ref = reference[c].dropna().values
            v = feat[c].iloc[0]
            if len(ref) >= 10 and np.isfinite(v):
                feat[c] = (np.sum(ref < v) + 0.5 * np.sum(ref == v)) / len(ref) - 0.5
            else:
                feat[c] = np.nan
    return feat


def asof_date(df: pd.DataFrame) -> date:
    return df.index[-1]
