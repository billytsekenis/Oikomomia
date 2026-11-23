"""Background jobs (APScheduler, in-process).

Jobs are idempotent catch-up jobs, so a host that sleeps (free tiers) simply
catches up when it wakes: evaluation looks for *all* due predictions, logging
skips already-logged (ticker, as_of, horizon, version) rows, etc.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

from sqlalchemy import select

from .analytics import alerts, market_hours, paper, tracking
from .analytics.context import load_model, model_metrics, reset_cache
from .config import get_settings
from .db import SessionLocal
from .models import JobRun, PaperAccount, utcnow

log = logging.getLogger("oik.jobs")
_running: dict[str, dict] = {}
_lock = threading.Lock()
_scheduler = None
notifier = None  # set by main: callable(user_id, payload)


def _record(name: str, fn, *args):
    with _lock:
        if name in _running:
            return {"status": "already running"}
        _running[name] = {"started": time.time(), "progress": 0.0, "message": ""}
    with SessionLocal() as db:
        jr = JobRun(name=name)
        db.add(jr)
        db.commit()
        jid = jr.id
    try:
        res = fn(*args)
        status, msg = "ok", str(res)[:2000]
    except Exception as exc:  # noqa: BLE001 - jobs must never crash the scheduler
        log.error("job %s failed: %s", name, traceback.format_exc())
        status, msg, res = "error", f"{type(exc).__name__}: {exc}"[:2000], None
    with SessionLocal() as db:
        jr = db.get(JobRun, jid)
        jr.status, jr.message, jr.finished_at = status, msg, utcnow()
        db.commit()
    with _lock:
        _running.pop(name, None)
    return res


def progress(name: str):
    def f(frac, message):
        with _lock:
            if name in _running:
                _running[name].update(progress=frac, message=message)
    return f


# ------------------------------------------------------------------ job bodies
def job_train():
    from .analytics import train
    return train.train(progress=progress("train"))


def job_discovery():
    from .api.discovery_api import rebuild_snapshot
    snap = rebuild_snapshot()
    from .api.common import clear_memo
    clear_memo("")
    return {"rows": len(snap.get("rows", []))}


def job_log_predictions():
    if load_model() is None:
        return {"skipped": "model not trained"}
    with SessionLocal() as db:
        return tracking.log_predictions(db)


def job_evaluate():
    with SessionLocal() as db:
        return tracking.evaluate_due(db)


def job_paper_orders():
    if not market_hours.status()["is_open"]:
        return {"skipped": "market closed"}
    with SessionLocal() as db:
        return {"filled": paper.process_all(db)}


def job_alerts():
    with SessionLocal() as db:
        fired = alerts.run_all(db)
        out = [{"user_id": n.user_id, "message": n.message, "kind": n.kind, "ticker": n.ticker} for n in fired]
    if notifier:
        for n in out:
            try:
                notifier(n["user_id"], {"type": "alert", **n})
            except Exception:  # noqa: BLE001
                pass
    return {"triggered": len(out)}


def job_equity_snapshot():
    with SessionLocal() as db:
        paper.snapshot_equity(db)
    return {"ok": True}


def job_system_strategy():
    """The system paper-trades its own top-10 30D ranking (weekly), to evaluate itself forward."""
    from .api.discovery_api import get_snapshot
    if not market_hours.status()["is_open"]:
        return {"skipped": "market closed"}
    with SessionLocal() as db:
        acct = db.execute(select(PaperAccount).where(PaperAccount.is_system.is_(True))).scalars().first()
        if acct is None:
            acct = PaperAccount(user_id=None, name="System strategy: top-10 by 30D forecast score", is_system=True,
                                initial_cash=100_000, cash=100_000,
                                strategy={"rule": "Weekly: hold the 10 universe stocks with highest 30D expected return/sigma, equal weight",
                                          "last_rebalance": None})
            db.add(acct)
            db.commit()
        last = (acct.strategy or {}).get("last_rebalance")
        today = market_hours.now_et().date()
        if last and (today - __import__("datetime").date.fromisoformat(last)).days < 7:
            return {"skipped": "rebalanced recently"}
        snap = get_snapshot()
        rows = [r for r in snap.get("rows", []) if (r.get("forecast") or {}).get("30D")]
        rows.sort(key=lambda r: -(r["forecast"]["30D"]["mu"] / r["forecast"]["30D"]["sigma"]))
        target = [r["ticker"] for r in rows[:10]]
        view = paper.account_view(db, acct)
        equity = view["equity"]
        held = {p["ticker"]: p for p in view["positions"]}
        orders = []
        for t, p in held.items():
            if t not in target:
                orders.append(paper.place_order(db, acct, t, "SELL", "market", p["quantity"]))
        db.refresh(acct)
        per = equity * 0.98 / max(len(target), 1)
        for r in rows[:10]:
            t = r["ticker"]
            cur = held.get(t, {}).get("market_value", 0.0)
            px = r["price"]
            diff = per - cur
            qty = int(abs(diff) / px)
            if qty < 1:
                continue
            try:
                if diff > 0:
                    orders.append(paper.place_order(db, acct, t, "BUY", "market", qty))
                elif held.get(t):
                    orders.append(paper.place_order(db, acct, t, "SELL", "market", min(qty, held[t]["quantity"])))
            except paper.OrderError as exc:
                log.info("system order skipped %s: %s", t, exc)
        acct.strategy = {**(acct.strategy or {}), "last_rebalance": today.isoformat(), "targets": target}
        db.commit()
        return {"orders": len(orders), "targets": target}


JOBS = {
    "train": job_train, "discovery": job_discovery, "log_predictions": job_log_predictions, "evaluate": job_evaluate,
    "paper_orders": job_paper_orders, "alerts": job_alerts, "equity_snapshot": job_equity_snapshot,
    "system_strategy": job_system_strategy,
}


def run_async(name: str):
    if name not in JOBS:
        raise KeyError(name)
    t = threading.Thread(target=_record, args=(name, JOBS[name]), daemon=True)
    t.start()
    return {"status": "started", "job": name}


def status() -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(JobRun).order_by(JobRun.id.desc()).limit(60)).scalars().all()
        recent = [{"name": r.name, "started_at": r.started_at.isoformat() + "Z", "status": r.status,
                   "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None, "message": r.message[:300]} for r in rows]
    with _lock:
        running = {k: dict(v) for k, v in _running.items()}
    return {"running": running, "recent": recent, "scheduler": _scheduler is not None and _scheduler.running}


def _bootstrap():
    """At start-up: train if no model, then build discovery, log & evaluate predictions."""
    time.sleep(3)
    if model_metrics() is None:
        _record("train", job_train)
        reset_cache()
    _record("discovery", job_discovery)
    _record("evaluate", job_evaluate)
    _record("log_predictions", job_log_predictions)


def start():
    global _scheduler
    if not get_settings().enable_scheduler or _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    s = BackgroundScheduler(timezone="America/New_York", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    s.add_job(lambda: _record("paper_orders", job_paper_orders), "interval", minutes=1, id="paper_orders")
    s.add_job(lambda: _record("alerts", job_alerts), "interval", minutes=5, id="alerts")
    s.add_job(lambda: _record("discovery", job_discovery), CronTrigger(day_of_week="mon-fri", hour="9,12,17", minute=40), id="discovery")
    s.add_job(lambda: _record("evaluate", job_evaluate), CronTrigger(day_of_week="mon-fri", hour=18, minute=15), id="evaluate")
    s.add_job(lambda: _record("log_predictions", job_log_predictions), CronTrigger(day_of_week="mon-fri", hour=18, minute=30), id="log_predictions")
    s.add_job(lambda: _record("equity_snapshot", job_equity_snapshot), CronTrigger(day_of_week="mon-fri", hour=16, minute=45), id="equity_snapshot")
    s.add_job(lambda: _record("system_strategy", job_system_strategy), CronTrigger(day_of_week="mon", hour=10, minute=5), id="system_strategy")
    s.add_job(lambda: _record("train", job_train), CronTrigger(day_of_week="sat", hour=6, minute=0), id="train")
    s.start()
    _scheduler = s
    threading.Thread(target=_bootstrap, daemon=True).start()


def stop():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
