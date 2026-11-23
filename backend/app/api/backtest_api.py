"""Backtesting engine & prediction track record endpoints."""
from __future__ import annotations

from datetime import date

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import backtest, tracking
from ..analytics.context import get_market_context, load_model, model_metrics
from ..data import market
from ..db import get_db
from ..models import BacktestRun, PredictionRecord, User
from ..security import get_current_user
from .common import clean

router = APIRouter(prefix="/api", tags=["backtest"])


class BacktestIn(BaseModel):
    horizon: str = "30D"
    strategy: str = "top_n"
    top_n: int = 10
    threshold: float = 0.55
    rebalance_sessions: int = 21
    cost_bps: float = 5.0
    slippage_bps: float = 5.0
    start: str | None = None
    end: str | None = None


@router.get("/backtest/model-evaluation")
def model_evaluation(_: User = Depends(get_current_user)):
    m = model_metrics()
    if not m:
        return {"available": False, "reason": "The model has not been trained yet (background job runs at start-up)."}
    return clean({"available": True, **m,
                  "protocol": [
                      "Expanding-window walk-forward: models re-fitted every ~6 months on data available at that time.",
                      "Purging + embargo: a training sample at t is used only if t + horizon + 5 sessions < fold start.",
                      "Out-of-sample: every evaluated prediction was made by a model that never saw its outcome.",
                      "No hyper-parameter tuning on test data (parameters fixed a priori and listed below).",
                      "Ensemble weights and probability calibration are fitted only on EARLIER out-of-sample results.",
                      "Point-in-time fundamentals (SEC filing dates); no future-dated information.",
                      "Survivorship bias: universe = today's large caps (disclosed; compare with the equal-weight benchmark).",
                      "All horizons and all years are reported - none are hidden.",
                  ]})


@router.post("/backtest/run")
def run_backtest(body: BacktestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    art = load_model()
    if not art or art.get("oos") is None:
        raise HTTPException(409, "No out-of-sample predictions yet - the model must be trained first.")
    if body.strategy not in backtest.STRATEGIES:
        raise HTTPException(400, f"strategy must be one of {list(backtest.STRATEGIES)}")
    if not (1 <= body.top_n <= 50) or not (1 <= body.rebalance_sessions <= 252) or body.cost_bps < 0 or body.slippage_bps < 0:
        raise HTTPException(400, "invalid parameters")
    cfg = body.model_dump() | {"model_version": art["version"], "model_trained_at": art["trained_at"]}
    oos = art["oos"]
    tickers = sorted(oos["ticker"].unique())
    bars = market.bars_many(tickers + ["SPY"])
    ctx = get_market_context()
    rf_daily = (ctx["mf"]["rf"].ffill() / 252).fillna(0.0)
    try:
        res = backtest.run(oos, bars, cfg, rf_daily)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    res = clean(res)
    from ..analytics.model_engine import config_hash
    run = BacktestRun(user_id=user.id, kind="strategy", config=cfg, config_hash=config_hash(cfg), summary={
        "stats": res["stats"], "benchmarks": res["benchmarks"], "period": res["period"]}, results=res)
    db.add(run)
    db.commit()
    return {"id": run.id, **res}


@router.get("/backtest/runs")
def runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(200)).scalars().all()
    return clean([{"id": r.id, "created_at": r.created_at.isoformat() + "Z", "config": r.config, "summary": r.summary,
                   "mine": r.user_id == user.id} for r in rows])


@router.get("/backtest/runs/{rid}")
def run_detail(rid: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(BacktestRun, rid)
    if r is None:
        raise HTTPException(404, "run not found")
    return clean({"id": r.id, "created_at": r.created_at.isoformat() + "Z", **r.results})


@router.get("/backtest/oos-sample")
def oos_sample(horizon: str = "30D", ticker: str = "", _: User = Depends(get_current_user)):
    """Raw out-of-sample predictions vs outcomes for inspection (scatter / calibration charts)."""
    art = load_model()
    if not art or art.get("oos") is None:
        return {"available": False}
    o = art["oos"]
    o = o[(o["horizon"] == horizon) & np.isfinite(o["y"])]
    if ticker:
        o = o[o["ticker"] == ticker.upper()]
    o = o.sample(min(len(o), 1500), random_state=0) if len(o) else o
    return clean({"available": True, "rows": [{"date": str(r.date), "ticker": r.ticker, "pred": float(r.ensemble), "actual": float(r.y),
                                               "p_up": float(r.p_up), "sigma": float(r.sigma)} for r in o.itertuples()]})


# ------------------------------------------------------------------ track record
@router.get("/track-record")
def track_record(horizon: str = "", status: str = "", ticker: str = "", limit: int = 300,
                 _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(PredictionRecord)
    if horizon:
        q = q.where(PredictionRecord.horizon == horizon)
    if status:
        q = q.where(PredictionRecord.status == status)
    if ticker:
        q = q.where(PredictionRecord.ticker == ticker.upper())
    rows = db.execute(q.order_by(PredictionRecord.created_at.desc(), PredictionRecord.id.desc()).limit(min(limit, 2000))).scalars().all()
    items = [{"id": r.id, "ticker": r.ticker, "created_at": r.created_at.isoformat() + "Z", "as_of": str(r.as_of_date),
              "horizon": r.horizon, "target_date": str(r.target_date), "model_version": r.model_version,
              "price": r.price_at_prediction, "expected_return": r.expected_return, "median_return": r.median_return,
              "p_up": r.p_up, "range_80": [r.q10, r.q90], "range_90": [r.q05, r.q95], "status": r.status,
              "actual_return": r.actual_return, "actual_price": r.actual_price, "direction_hit": r.direction_hit,
              "in_80": r.in_80, "in_90": r.in_90, "brier": r.brier, "skill": (r.details or {}).get("skill")} for r in rows]
    return clean({"summary": tracking.summary(db), "items": items,
                  "policy": "Every logged prediction is kept, including failures. Records are immutable apart from the evaluation "
                            "added when the target session's close is available.",
                  "today": date.today().isoformat()})


@router.post("/track-record/evaluate")
def evaluate(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return tracking.evaluate_due(db)
