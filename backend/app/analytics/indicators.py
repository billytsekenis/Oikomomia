"""Technical indicators (pure functions over pandas Series/DataFrames).

Conventions follow the original definitions: RSI and ATR use Wilder's
smoothing (alpha = 1/n); MACD uses EMA(12) - EMA(26) with a 9-period signal;
Bollinger Bands use a 20-period SMA +/- 2 population standard deviations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder: seed with simple average of first n, then recursive smoothing
    avg_gain = pd.Series(np.nan, index=close.index)
    avg_loss = pd.Series(np.nan, index=close.index)
    if len(close) <= n:
        return avg_gain
    g, l_ = gain.values, loss.values
    ag, al = np.empty(len(close)), np.empty(len(close))
    ag[:] = np.nan
    al[:] = np.nan
    ag[n] = g[1:n + 1].mean()
    al[n] = l_[1:n + 1].mean()
    for i in range(n + 1, len(close)):
        ag[i] = (ag[i - 1] * (n - 1) + g[i]) / n
        al[i] = (al[i - 1] * (n - 1) + l_[i]) / n
    avg_gain[:] = ag
    avg_loss[:] = al
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    out = pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})
    out.iloc[: slow - 1] = np.nan
    return out


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower,
                         "pct_b": (close - lower) / (upper - lower), "bandwidth": (upper - lower) / mid})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = true_range(df).values
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        out[n - 1] = tr[:n].mean()
        for i in range(n, len(tr)):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return pd.Series(out, index=df.index)


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> pd.DataFrame:
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = 100 * (df["close"] - lo) / (hi - lo)
    return pd.DataFrame({"k": k, "d": k.rolling(d).mean()})


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Volume vs the average of the *previous* n sessions (no self-inclusion)."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def volume_zscore(volume: pd.Series, n: int = 60) -> pd.Series:
    lv = np.log(volume.replace(0, np.nan))
    mu = lv.shift(1).rolling(n, min_periods=20).mean()
    sd = lv.shift(1).rolling(n, min_periods=20).std()
    return (lv - mu) / sd


def realized_vol(close: pd.Series, n: int = 21) -> pd.Series:
    return np.log(close).diff().rolling(n, min_periods=n).std() * np.sqrt(252)


def max_drawdown(series: pd.Series) -> float:
    peak = series.cummax()
    return float((series / peak - 1).min())


def support_resistance(df: pd.DataFrame, lookback: int = 250, window: int = 5, max_levels: int = 4) -> dict:
    """Swing-pivot clustering. Pivots are local extrema over +/-`window` bars;
    pivots within 0.6 ATR are merged; levels are ranked by touches and recency."""
    d = df.tail(lookback)
    if len(d) < 2 * window + 5:
        return {"support": [], "resistance": []}
    a = atr(d).iloc[-1]
    if not np.isfinite(a) or a <= 0:
        a = float((d["high"] - d["low"]).mean())
    highs, lows = d["high"].values, d["low"].values
    piv = []
    for i in range(window, len(d) - window):
        if highs[i] == highs[i - window:i + window + 1].max():
            piv.append((highs[i], i))
        if lows[i] == lows[i - window:i + window + 1].min():
            piv.append((lows[i], i))
    piv.sort()
    clusters: list[list[tuple[float, int]]] = []
    for p in piv:
        if clusters and abs(p[0] - np.mean([x[0] for x in clusters[-1]])) <= 0.6 * a:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(d["close"].iloc[-1])
    levels = []
    for c in clusters:
        price = float(np.mean([x[0] for x in c]))
        recency = max(x[1] for x in c) / len(d)
        levels.append({"price": round(price, 2), "touches": len(c), "strength": round(len(c) + 2 * recency, 2),
                       "distance_pct": round((price / last - 1) * 100, 2)})
    sup = sorted([lv for lv in levels if lv["price"] < last], key=lambda x: -x["strength"])[:max_levels]
    res = sorted([lv for lv in levels if lv["price"] > last], key=lambda x: -x["strength"])[:max_levels]
    return {"support": sorted(sup, key=lambda x: -x["price"]), "resistance": sorted(res, key=lambda x: x["price"]),
            "method": "swing pivots (+/-5 bars, 250 sessions) clustered within 0.6x ATR(14)"}


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Indicator frame on adjusted prices (adjusted OHLC = OHLC * adj_close/close)."""
    f = df["adj_close"] / df["close"]
    a = pd.DataFrame({"open": df["open"] * f, "high": df["high"] * f, "low": df["low"] * f,
                      "close": df["adj_close"], "volume": df["volume"]}, index=df.index)
    out = pd.DataFrame(index=df.index)
    c = a["close"]
    for n in (20, 50, 100, 200):
        out[f"sma{n}"] = sma(c, n)
    for n in (9, 12, 21, 26, 50):
        out[f"ema{n}"] = ema(c, n)
    out["rsi14"] = rsi(c, 14)
    out = out.join(macd(c))
    bb = bollinger(c)
    out["bb_upper"], out["bb_mid"], out["bb_lower"], out["bb_pctb"], out["bb_width"] = bb["upper"], bb["mid"], bb["lower"], bb["pct_b"], bb["bandwidth"]
    out["atr14"] = atr(a)
    out["atr_pct"] = out["atr14"] / c
    out["obv"] = obv(a)
    st = stochastic(a)
    out["stoch_k"], out["stoch_d"] = st["k"], st["d"]
    out["rvol20"] = relative_volume(a["volume"])
    out["vol_z"] = volume_zscore(a["volume"])
    out["rv21"] = realized_vol(c, 21)
    out["hi52"] = a["high"].rolling(252, min_periods=60).max()
    out["lo52"] = a["low"].rolling(252, min_periods=60).min()
    return out
