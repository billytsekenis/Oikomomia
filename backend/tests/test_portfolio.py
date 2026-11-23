from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.analytics import portfolio as pf


def _fx():
    # USD per EUR (ECB convention)
    return pd.Series({date(2024, 1, 10): 1.10, date(2025, 2, 3): 1.00, date(2026, 11, 20): 1.25})


def test_average_cost_realized_and_fx_decomposition():
    tx = [
        {"ticker": "X", "side": "BUY", "quantity": 10, "price": 100, "currency": "USD", "fee": 10, "fee_currency": "EUR",
         "fx_rate": None, "trade_date": date(2024, 1, 10)},
        {"ticker": "X", "side": "SELL", "quantity": 4, "price": 150, "currency": "USD", "fee": 5, "fee_currency": "EUR",
         "fx_rate": None, "trade_date": date(2025, 2, 3)},
    ]
    lots, warn = pf.build_lots(tx, "EUR", _fx())
    lot = lots["X"]
    X0, X1 = 1 / 1.10, 1 / 1.00
    assert lot.realized_price == pytest.approx(4 * (150 - 100) * X0)
    assert lot.realized_fx == pytest.approx(4 * 150 * (X1 - X0))
    assert lot.realized_fees == pytest.approx(5 + 10 * 0.4)
    # identity: realized total = proceeds - allocated cost
    proceeds = 4 * 150 * X1 - 5
    cost = 4 * 100 * X0 + 4
    assert lot.realized_price + lot.realized_fx - lot.realized_fees == pytest.approx(proceeds - cost)
    v = pf.positions_view(lots, {"X": 200.0}, "EUR", eurusd_now=1.25)
    p = v["positions"][0]
    X2 = 1 / 1.25
    assert p["quantity"] == 6
    assert p["market_value"] == pytest.approx(6 * 200 * X2)
    assert p["unrealized_price_effect"] == pytest.approx(6 * (200 - 100) * X0)
    assert p["unrealized_fx_effect"] == pytest.approx(6 * 200 * (X2 - X0))
    assert p["unrealized_pnl"] == pytest.approx(6 * 200 * X2 - (6 * 100 * X0 + 6))
    assert not warn


def test_oversell_is_clipped_with_warning():
    tx = [{"ticker": "X", "side": "BUY", "quantity": 1, "price": 10, "currency": "EUR", "trade_date": date(2024, 1, 10)},
          {"ticker": "X", "side": "SELL", "quantity": 3, "price": 10, "currency": "EUR", "trade_date": date(2024, 2, 1)}]
    lots, warn = pf.build_lots(tx, "EUR", None)
    assert lots["X"].qty == 0 and warn


def test_twr_ignores_cash_flows():
    days = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    px = pd.DataFrame({"close": [100.0, 110.0, 110.0, 121.0], "adj_close": [100.0, 110.0, 110.0, 121.0]}, index=days)
    tx = [{"ticker": "X", "side": "BUY", "quantity": 1, "price": 100, "currency": "USD", "fx_rate": 1.0, "trade_date": days[0]},
          {"ticker": "X", "side": "BUY", "quantity": 9, "price": 110, "currency": "USD", "fx_rate": 1.0, "trade_date": days[2]}]
    tw = pf.twr_series(tx, {"X": px}, "USD", None)
    # TWR = 1.10 * 1.00 * 1.10 regardless of the large mid-period deposit
    assert tw["twr_index"].iloc[-1] == pytest.approx(1.21)
    assert tw["value"].iloc[-1] == pytest.approx(10 * 121)


def test_perf_stats_known_series():
    r = pd.Series([0.01, -0.02, 0.03, 0.0] * 30)
    s = pf.perf_stats(r)
    idx = (1 + r).cumprod()
    assert s["max_drawdown"] == pytest.approx(float((idx / idx.cummax() - 1).min()))
    assert s["sharpe"] == pytest.approx(r.mean() / r.std() * np.sqrt(252))


def test_risk_metrics_var_and_concentration():
    rng = np.random.default_rng(0)
    idx = pd.RangeIndex(400)
    rets = pd.DataFrame({"A": rng.normal(0, 0.01, 400), "B": rng.normal(0, 0.02, 400)}, index=idx)
    bench = pd.Series(rng.normal(0, 0.01, 400), index=idx)
    out = pf.risk_metrics({"A": 0.9, "B": 0.1}, rets, bench, 1000.0, {"A": "Tech", "B": "Tech"})
    rp = rets.tail(252).values @ np.array([0.9, 0.1])
    assert out["var_1d_95_hist"] == pytest.approx(-np.quantile(rp, 0.05) * 1000)
    assert out["effective_n"] == pytest.approx(1 / (0.81 + 0.01))
    assert any("Largest position" in f for f in out["flags"])
