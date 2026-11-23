from app.analytics import regime
from app.data import market


def test_markov_regime_fits_and_reports_probability():
    r = regime.markov_regime(market.bars("SPY"))  # demo data
    assert r["available"], r.get("reason")
    assert 0.0 <= r["p_high_vol_state"] <= 1.0
    assert r["states"]["high_vol"]["weekly_vol_pct"] > r["states"]["low_vol"]["weekly_vol_pct"]


def test_regime_and_risk_appetite_shapes():
    ub = {t: market.bars(t) for t in ("AAPL", "MSFT", "JPM", "XOM", "KO")}
    a = regime.analyse(ub)
    assert a["state"] in ("bullish", "bearish", "neutral") and -1 <= a["score"] <= 1
    s = regime.risk_appetite(ub)
    assert s["index"] is None or 0 <= s["index"] <= 100
