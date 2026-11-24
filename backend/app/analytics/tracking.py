"""Live prediction track record.

log_predictions(): after each completed session, store every horizon's
forecast for the tracked tickers with timestamp, the last bar date used
(as_of), the target date and the model version. Rows are unique per
(ticker, as_of, horizon, model_version) and are never modified except to
add the evaluation once the target date's close exists.

evaluate_due(): for every pending prediction whose target session has
closed, fetch the actual close and record: realised return, direction hit,
inside the 80% / 90% interval, Brier score and absolute error. Failures are
kept - nothing is deleted or filtered.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..data import market
from ..data.http import ProviderError
from ..models import PredictionRecord, utcnow
from ..universe import STOCK_TICKERS
from .forecast import forecast
from .market_hours import last_completed_session

log = logging.getLogger("oik.track")


def tracked_tickers() -> list[str]:
    s = get_settings().track_record_tickers.strip()
    return [t.strip().upper() for t in s.split(",") if t.strip()] if s else list(STOCK_TICKERS) + ["SPY", "QQQ"]


def log_predictions(db: Session, tickers: list[str] | None = None, n_paths: int = 2000) -> dict:
    written = skipped = failed = stale = 0
    for t in tickers or tracked_tickers():
        try:
            fc = forecast(t, n_paths=n_paths, include_paths=False)
        except (ProviderError, ValueError) as exc:
            log.info("forecast failed for %s: %s", t, exc)
            failed += 1
            continue
        if not fc.get("available"):
            failed += 1
            continue
        as_of = date.fromisoformat(fc["as_of"])
        if as_of < last_completed_session():
            # The data lags the last completed session: a forecast logged now would be made after
            # an outcome it targets was already knowable. Skip rather than pollute the record.
            stale += 1
            continue
        for h in fc["horizons"]:
            exists = db.scalar(select(func.count(PredictionRecord.id)).where(
                PredictionRecord.ticker == t, PredictionRecord.as_of_date == as_of,
                PredictionRecord.horizon == h["horizon"], PredictionRecord.model_version == fc["model_version"]))
            if exists:
                skipped += 1
                continue
            q = h["quantiles"]
            rec = PredictionRecord(
                ticker=t, as_of_date=as_of, horizon=h["horizon"], horizon_days=h["sessions"],
                target_date=date.fromisoformat(h["target_date"]), model_version=fc["model_version"],
                price_at_prediction=fc["price"], price_source=(fc["provenance"].get("source") or "")[:40],
                expected_return=h["expected_return"], median_return=h["median_return"], p_up=h["p_up"],
                q05=q["q05"], q10=q["q10"], q50=q["q50"], q90=q["q90"], q95=q["q95"], sigma=h["sigma"],
                details={"skill": h["skill"], "ml_weight": h["ml_weight"], "components": h["components"],
                         "model_trained_at": fc.get("model_trained_at")})
            db.add(rec)
            try:
                db.commit()
                written += 1
            except IntegrityError:
                db.rollback()
                skipped += 1
    return {"written": written, "skipped_existing": skipped, "failed": failed, "skipped_stale_data": stale}


def evaluate_due(db: Session) -> dict:
    last = last_completed_session()
    due = db.execute(select(PredictionRecord).where(PredictionRecord.status == "pending",
                                                    PredictionRecord.target_date <= last)).scalars().all()
    done = missing = 0
    cache: dict = {}
    for r in due:
        if r.ticker not in cache:
            try:
                cache[r.ticker] = market.bars(r.ticker)
            except ProviderError:
                cache[r.ticker] = None
        df = cache[r.ticker]
        if df is None:
            missing += 1
            continue
        if r.target_date not in df.index or r.as_of_date not in df.index:
            if df.index[-1] < r.target_date:
                missing += 1  # data not yet available
                continue
            # target not a bar (data gap): use the first bar on/after target
            pos = df.index.searchsorted(r.target_date)
            if pos >= len(df):
                missing += 1
                continue
            tgt = df.index[pos]
        else:
            tgt = r.target_date
        # use dividend-adjusted closes relative to the as-of bar (same basis as the model)
        base = df["adj_close"].get(r.as_of_date)
        if base is None or not np.isfinite(base):
            missing += 1
            continue
        actual_ret = float(df["adj_close"][tgt] / base - 1)
        r.actual_price = float(df["close"][tgt])
        r.actual_return = actual_ret
        r.direction_hit = (actual_ret > 0) == (r.p_up > 0.5)
        r.in_80 = r.q10 <= actual_ret <= r.q90
        r.in_90 = r.q05 <= actual_ret <= r.q95
        r.brier = (r.p_up - (1.0 if actual_ret > 0 else 0.0)) ** 2
        r.abs_error = abs(actual_ret - r.median_return)
        r.status = "evaluated"
        r.evaluated_at = utcnow()
        done += 1
    db.commit()
    return {"evaluated": done, "awaiting_data": missing, "checked": len(due)}


def summary(db: Session) -> dict:
    rows = db.execute(select(PredictionRecord)).scalars().all()
    by_h: dict = {}
    for r in rows:
        d = by_h.setdefault(r.horizon, {"total": 0, "pending": 0, "evaluated": 0, "hits": 0, "in80": 0, "in90": 0,
                                         "brier": [], "abs_err": [], "up": 0, "p_up": []})
        d["total"] += 1
        if r.status != "evaluated":
            d["pending"] += 1
            continue
        d["evaluated"] += 1
        d["hits"] += int(bool(r.direction_hit))
        d["in80"] += int(bool(r.in_80))
        d["in90"] += int(bool(r.in_90))
        d["brier"].append(r.brier)
        d["abs_err"].append(r.abs_error)
        d["up"] += int((r.actual_return or 0) > 0)
        d["p_up"].append(r.p_up)
    out = {}
    for h, d in by_h.items():
        n = d["evaluated"]
        base = d["up"] / n if n else None
        clim = base * (1 - base) if n else None  # Brier of always forecasting the (hindsight) realised up-rate
        out[h] = {"total": d["total"], "pending": d["pending"], "evaluated": n,
                  "directional_accuracy": d["hits"] / n if n else None, "realized_up_rate": base,
                  "coverage_80": d["in80"] / n if n else None, "coverage_90": d["in90"] / n if n else None,
                  "brier": float(np.mean(d["brier"])) if n else None,
                  "brier_vs_hindsight_base_rate": clim,
                  "mean_abs_error": float(np.mean(d["abs_err"])) if n else None,
                  "sufficient": n >= 100}
    first = min((r.created_at for r in rows), default=None)
    return {"by_horizon": out, "n_records": len(rows), "first_logged": first.isoformat() + "Z" if first else None}
