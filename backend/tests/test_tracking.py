from datetime import date

from app.analytics import tracking
from app.data import market
from app.models import PredictionRecord


def test_evaluation_scores_hits_and_misses(db):
    df = market.bars("AAPL")  # demo-mode synthetic series
    i0, i1 = len(df) - 30, len(df) - 9
    as_of, target = df.index[i0], df.index[i1]
    actual = float(df["adj_close"].iloc[i1] / df["adj_close"].iloc[i0] - 1)
    common = dict(ticker="AAPL", as_of_date=as_of, horizon="30D", horizon_days=21, target_date=target,
                  price_at_prediction=float(df["close"].iloc[i0]), price_source="demo", expected_return=0.0,
                  median_return=0.0, q05=-0.2, q10=-0.1, q50=0.0, q90=0.1, q95=0.2, sigma=0.1)
    right = PredictionRecord(model_version="t-right", p_up=0.8 if actual > 0 else 0.2, **common)
    wrong = PredictionRecord(model_version="t-wrong", p_up=0.2 if actual > 0 else 0.8, **common)
    future = PredictionRecord(model_version="t-future", p_up=0.6, **{**common, "target_date": date(2099, 1, 1)})
    db.add_all([right, wrong, future])
    db.commit()
    res = tracking.evaluate_due(db)
    db.refresh(right), db.refresh(wrong), db.refresh(future)
    assert res["evaluated"] >= 2
    assert right.direction_hit is True and wrong.direction_hit is False
    assert abs(right.actual_return - actual) < 1e-12
    assert right.brier == (0.8 - 1) ** 2 if actual > 0 else (0.2 - 0) ** 2
    assert future.status == "pending"  # not evaluated before its horizon ends
    summ = tracking.summary(db)["by_horizon"]["30D"]
    assert summ["evaluated"] >= 2 and summ["pending"] >= 1  # failures are kept, not hidden
