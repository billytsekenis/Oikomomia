from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException

from ..analytics import scoring
from ..data.cache import get_entry, put
from ..data.http import ProviderError
from ..models import utcnow
from ..security import get_current_user
from .common import clean

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(get_current_user)])
_build_lock = threading.Lock()
CATEGORIES = ["undervalued", "growth", "momentum", "breakout", "oversold", "reversal", "earnings",
              "high_risk_high_reward", "overvalued_risk"]


def rebuild_snapshot() -> dict:
    with _build_lock:
        snap = clean(scoring.build_snapshot())
        put("discovery:snapshot", snap, "discovery engine", 30 * 86400)
        return snap


def get_snapshot(max_age_h: float = 8) -> dict:
    row = get_entry("discovery:snapshot")
    if row is not None and (utcnow() - row.fetched_at).total_seconds() < max_age_h * 3600:
        return row.value
    if row is not None and _build_lock.locked():
        return row.value  # a rebuild is running; serve the previous one
    try:
        return rebuild_snapshot()
    except ProviderError:
        if row is not None:
            return row.value
        raise


@router.get("")
def discovery(category: str = "composite", horizon: str = "medium", risk: str = "", sector: str = "", cap: str = "",
              min_return: float | None = None, limit: int = 60):
    if horizon not in scoring.HORIZON_FOR:
        raise HTTPException(400, "horizon must be short, medium or long")
    if category not in CATEGORIES + ["composite"]:
        raise HTTPException(400, f"category must be one of {CATEGORIES + ['composite']}")
    try:
        snap = get_snapshot()
    except ProviderError as exc:
        raise HTTPException(503, f"Discovery scan unavailable: {exc}") from exc
    rows = snap.get("rows", [])
    risks = {r for r in risk.split(",") if r}
    sectors = {s for s in sector.split(",") if s}
    caps = {c for c in cap.split(",") if c}
    h = scoring.HORIZON_FOR[horizon]
    out = []
    for r in rows:
        if risks and r["risk_level"] not in risks:
            continue
        if sectors and r["sector"] not in sectors:
            continue
        if caps and r["cap_bucket"] not in caps:
            continue
        f = (r.get("forecast") or {}).get(h)
        if min_return is not None and (not f or f["exp_ret"] < min_return):
            continue
        score = r["composite"][horizon]["score"] if category == "composite" else r["categories"].get(category)
        if score is None:
            continue
        out.append({"ticker": r["ticker"], "name": r["name"], "sector": r["sector"], "industry": r["industry"],
                    "price": r["price"], "as_of": r["as_of"], "score": score, "risk_level": r["risk_level"],
                    "cap_bucket": r["cap_bucket"], "market_cap": r["market_cap"], "factors": r["factors"],
                    "categories": r["categories"], "composite": r["composite"],
                    "forecast": {"horizon": h, **f} if f else None,
                    "ret_1d": r["ret_1d"], "ret_3m": r["ret_3m"], "rsi14": r["rsi14"], "vol63": r["vol63"],
                    "pe_ttm": r["pe_ttm"], "revenue_growth": r["revenue_growth"],
                    "earnings_next": r["earnings_next"],
                    "why": scoring.explain(r, category if category != "composite" else "undervalued" if horizon == "long" else "momentum")})
    out.sort(key=lambda x: -x["score"])
    return clean({"category": category, "horizon": horizon, "forecast_horizon": h, "n_universe": len(rows), "n": len(out),
                  "rows": out[:limit], "generated_at": snap.get("generated_at"), "methodology": snap.get("methodology"),
                  "forecast_skill": snap.get("forecast_skill"),
                  "sectors": sorted({r["sector"] for r in rows})})


@router.post("/rebuild")
def rebuild():
    if _build_lock.locked():
        return {"status": "already running"}
    threading.Thread(target=rebuild_snapshot, daemon=True).start()
    return {"status": "started"}
