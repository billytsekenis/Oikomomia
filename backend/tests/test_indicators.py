import numpy as np
import pandas as pd
import pytest

from app.analytics import indicators as ind


def _ohlc(n=300, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.005, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.005, n)))
    v = rng.integers(1e5, 1e6, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "adj_close": c, "volume": v},
                        index=pd.RangeIndex(n))


def rsi_reference(close, n=14):
    """Textbook Wilder RSI written independently (explicit loops)."""
    d = np.diff(close)
    gains, losses = np.maximum(d, 0), np.maximum(-d, 0)
    ag, al = gains[:n].mean(), losses[:n].mean()
    out = [np.nan] * n
    out.append(100 - 100 / (1 + ag / al))
    for g, l_ in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l_) / n
        out.append(100 - 100 / (1 + ag / al))
    return np.array(out)


def test_rsi_matches_reference_wilder():
    df = _ohlc()
    got = ind.rsi(df["close"]).values
    ref = rsi_reference(df["close"].values)
    mask = ~np.isnan(ref)
    assert np.allclose(got[mask], ref[mask], atol=1e-9)
    assert np.isnan(got[:14]).all()


def test_rsi_bounds_and_monotone_series():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100.0
    assert ind.rsi(pd.Series(np.full(40, 5.0))).iloc[-1] == 50.0


def test_sma_ema():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert ind.sma(s, 3).iloc[-1] == 4.0
    e = ind.ema(s, 3)
    # EMA(3) alpha = 0.5, seeded at first value (adjust=False)
    ref, a = 1.0, 0.5
    for x in [2, 3, 4, 5]:
        ref = a * x + (1 - a) * ref
    assert e.iloc[-1] == pytest.approx(ref)


def test_macd_definition():
    df = _ohlc()
    m = ind.macd(df["close"])
    c = df["close"]
    line = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    assert m["macd"].iloc[-1] == pytest.approx(line.iloc[-1])
    assert m["hist"].iloc[-1] == pytest.approx(m["macd"].iloc[-1] - m["signal"].iloc[-1])


def test_bollinger_definition():
    df = _ohlc()
    b = ind.bollinger(df["close"])
    w = df["close"].iloc[-20:]
    assert b["mid"].iloc[-1] == pytest.approx(w.mean())
    assert b["upper"].iloc[-1] == pytest.approx(w.mean() + 2 * w.std(ddof=0))


def test_atr_reference():
    df = _ohlc()
    tr = [df.high[0] - df.low[0]]
    for i in range(1, len(df)):
        tr.append(max(df.high[i] - df.low[i], abs(df.high[i] - df.close[i - 1]), abs(df.low[i] - df.close[i - 1])))
    a = np.mean(tr[:14])
    for x in tr[14:]:
        a = (a * 13 + x) / 14
    assert ind.atr(df).iloc[-1] == pytest.approx(a)


def test_relative_volume_excludes_today():
    v = pd.Series([100.0] * 20 + [300.0])
    assert ind.relative_volume(v).iloc[-1] == pytest.approx(3.0)


def test_max_drawdown():
    assert ind.max_drawdown(pd.Series([100, 120, 90, 130, 65.0])) == pytest.approx(65 / 130 - 1)


def test_support_resistance_sides():
    df = _ohlc(400)
    sr = ind.support_resistance(df)
    last = df["close"].iloc[-1]
    assert all(s["price"] < last for s in sr["support"])
    assert all(r["price"] > last for r in sr["resistance"])
