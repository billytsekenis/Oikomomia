"""Look-ahead / leakage tests for the feature pipeline and the walk-forward engine."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analytics import model_engine
from app.analytics.features import HORIZONS, build_panel, market_frame, stock_features


def _bars(n=1500, seed=0, momentum=0.0):
    rng = np.random.default_rng(seed)
    days = [d for d in (date(2016, 1, 4) + timedelta(days=i) for i in range(int(n * 1.5))) if d.weekday() < 5][:n]
    r = rng.normal(0.0003, 0.015, n)
    if momentum:
        for i in range(21, n):
            r[i] += momentum * r[i - 21:i].mean()
    p = 100 * np.exp(np.cumsum(r))
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "adj_close": p,
                         "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=days)


def test_features_do_not_use_future_data():
    df = _bars()
    mkt = _bars(seed=99)
    mf = market_frame(mkt, None, None)
    f1 = stock_features(df, mf)
    df2 = df.copy()
    df2.iloc[-60:, :] *= 3.0  # rewrite the future
    f2 = stock_features(df2, mf)
    cut = df.index[-61]
    a, b = f1.loc[:cut].drop(columns=[c for c in f1 if c.startswith("y_")]), f2.loc[:cut]
    pd.testing.assert_frame_equal(a, b[a.columns])


def test_targets_are_forward_returns():
    df = _bars()
    mf = market_frame(_bars(seed=5), None, None)
    panel = build_panel({"A": df}, mf, {}, step=5)
    row = panel.iloc[10]
    i = df.index.get_loc(row["date"])
    assert row["y_30D"] == pytest.approx(np.log(df["adj_close"].iloc[i + 21] / df["adj_close"].iloc[i]), abs=1e-12)


def test_walkforward_purging_and_no_skill_on_noise():
    bars = {f"S{k}": _bars(seed=k) for k in range(25)}
    mkt = _bars(seed=1000)
    mf = market_frame(mkt, None, None)
    res = model_engine.run_walkforward(bars, mf, {k: None for k in bars})
    assert res.oos is not None and len(res.oos)
    for h, n in HORIZONS.items():
        o = res.oos[res.oos["horizon"] == h]
        if o.empty:
            continue
        # every OOS prediction date is at least MIN_TRAIN_YEARS after the data start
        assert o["date"].min() >= mf.index[0].replace(year=mf.index[0].year + model_engine.MIN_TRAIN_YEARS)
    m = res.metrics.get("30D")
    assert m is not None
    ic = m["models"]["ensemble"]["ic_mean"]
    assert abs(ic) < 0.06, f"spurious skill on pure noise: IC={ic}"
