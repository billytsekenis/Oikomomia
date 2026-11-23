from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analytics import backtest


def _universe(n_t=12, n_d=400, seed=3, flat=False):
    rng = np.random.default_rng(seed)
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(n_d)]
    bars = {}
    for k in range(n_t):
        r = np.zeros(n_d) if flat else rng.normal(0.0003, 0.02, n_d)
        p = 100 * np.cumprod(1 + r)
        bars[f"T{k}"] = pd.DataFrame({"adj_close": p, "close": p}, index=days)
    bars["SPY"] = pd.DataFrame({"adj_close": 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n_d)), "close": 1.0}, index=days)
    return bars, days


def _oos(bars, days, h=21, perfect=True):
    rows = []
    for i in range(0, len(days) - h - 1, 5):
        d = days[i]
        for t, df in bars.items():
            if t == "SPY":
                continue
            # forward return measured from the EXECUTION day (t+1) to t+1+h
            fwd = float(np.log(df["adj_close"].iloc[i + 1 + h] / df["adj_close"].iloc[i + 1]))
            pred = fwd if perfect else np.random.default_rng(i).normal()
            rows.append({"date": d, "ticker": t, "horizon": "30D", "ensemble": pred, "sigma": 0.05, "p_up": 0.5, "y": fwd})
    return pd.DataFrame(rows)


def test_perfect_foresight_beats_equal_weight():
    bars, days = _universe()
    res = backtest.run(_oos(bars, days), bars, {"horizon": "30D", "strategy": "top_n", "top_n": 3, "rebalance_sessions": 21,
                                                "cost_bps": 0, "slippage_bps": 0})
    assert res["stats"]["total_return"] > res["benchmarks"]["equal_weight_universe"]["total_return"]


def test_transaction_costs_exact_on_flat_prices():
    bars, days = _universe(flat=True)
    cfg = {"horizon": "30D", "strategy": "top_n", "top_n": 3, "rebalance_sessions": 21, "cost_bps": 7, "slippage_bps": 3}
    res = backtest.run(_oos(bars, days, perfect=False), bars, cfg)
    s = res["stats"]
    # with zero price moves, the only P&L is cost: prod(1 - turnover_i * 10bps) - 1
    assert s["total_return"] == pytest.approx(-s["total_cost_drag"], rel=0.01)
    assert s["total_return"] < 0


def test_execution_lag_one_session():
    bars, days = _universe()
    res = backtest.run(_oos(bars, days), bars, {"horizon": "30D", "strategy": "top_n", "top_n": 3, "rebalance_sessions": 21,
                                                "cost_bps": 0, "slippage_bps": 0})
    first_trade = res["rebalances"][0]["date"]
    first_signal = str(days[0])
    assert first_trade > first_signal  # trades happen the session after the signal
