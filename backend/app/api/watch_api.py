from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..analytics import alerts as al
from ..data import market
from ..db import get_db
from ..models import Alert, Notification, User, Watchlist, WatchlistItem
from ..security import get_current_user
from .common import clean

router = APIRouter(prefix="/api", tags=["watchlists"])


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ItemIn(BaseModel):
    ticker: str


class AlertIn(BaseModel):
    kind: str
    ticker: str | None = None
    params: dict = {}
    repeat: bool = False


def _wl(db, wid, user) -> Watchlist:
    w = db.get(Watchlist, wid)
    if w is None or w.user_id != user.id:
        raise HTTPException(404, "watchlist not found")
    return w


@router.get("/watchlists")
def watchlists(quotes: bool = True, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id)).scalars().all()
    out = []
    for w in rows:
        tickers = [i.ticker for i in w.items]
        qs = market.quotes(tickers) if quotes and tickers else {}
        out.append({"id": w.id, "name": w.name, "items": [{"ticker": t, "quote": qs.get(t)} for t in tickers]})
    return clean(out)


@router.post("/watchlists")
def create_wl(body: WatchlistIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = Watchlist(user_id=user.id, name=body.name)
    db.add(w)
    db.commit()
    return {"id": w.id}


@router.patch("/watchlists/{wid}")
def rename_wl(wid: int, body: WatchlistIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _wl(db, wid, user)
    w.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/watchlists/{wid}")
def delete_wl(wid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_wl(db, wid, user))
    db.commit()
    return {"ok": True}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: ItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _wl(db, wid, user)
    t = body.ticker.upper().strip()
    if not t or len(t) > 12:
        raise HTTPException(400, "invalid ticker")
    if any(i.ticker == t for i in w.items):
        return {"ok": True, "duplicate": True}
    try:
        market.quote(t)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"Unknown or unavailable ticker {t}: {exc}") from exc
    w.items.append(WatchlistItem(ticker=t))
    db.commit()
    return {"ok": True}


@router.delete("/watchlists/{wid}/items/{ticker}")
def remove_item(wid: int, ticker: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _wl(db, wid, user)
    for i in list(w.items):
        if i.ticker == ticker.upper():
            w.items.remove(i)
    db.commit()
    return {"ok": True}


def _alert_dict(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "label": al.KINDS.get(a.kind, {}).get("label"), "ticker": a.ticker,
            "params": a.params, "active": a.active, "repeat": a.repeat, "state": {k: v for k, v in (a.state or {}).items() if k != "seen"},
            "created_at": a.created_at.isoformat() + "Z",
            "last_checked_at": a.last_checked_at.isoformat() + "Z" if a.last_checked_at else None,
            "last_triggered_at": a.last_triggered_at.isoformat() + "Z" if a.last_triggered_at else None}


@router.get("/alerts/kinds")
def kinds():
    return al.KINDS


@router.get("/alerts")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_alert_dict(a) for a in db.execute(select(Alert).where(Alert.user_id == user.id).order_by(Alert.id.desc())).scalars()]


@router.post("/alerts")
def create_alert(body: AlertIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.kind not in al.KINDS:
        raise HTTPException(400, f"kind must be one of {list(al.KINDS)}")
    if body.kind != "portfolio_risk" and not body.ticker:
        raise HTTPException(400, "ticker required")
    for p, typ in al.KINDS[body.kind]["params"].items():
        if p not in body.params:
            raise HTTPException(400, f"missing parameter '{p}'")
        if typ == "number":
            try:
                float(body.params[p])
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"parameter '{p}' must be a number") from exc
        elif "|" in typ and str(body.params[p]) not in typ.split("|"):
            raise HTTPException(400, f"parameter '{p}' must be one of {typ}")
    a = Alert(user_id=user.id, kind=body.kind, ticker=body.ticker.upper() if body.ticker else None, params=body.params,
              repeat=body.repeat)
    db.add(a)
    db.commit()
    n = al.evaluate(db, a)  # immediate first check
    db.commit()
    return {**_alert_dict(a), "triggered_now": n.message if n else None}


@router.patch("/alerts/{aid}")
def toggle_alert(aid: int, active: bool, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "alert not found")
    a.active = active
    db.commit()
    return _alert_dict(a)


@router.delete("/alerts/{aid}")
def delete_alert(aid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "alert not found")
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/alerts/check")
def check_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fired = []
    for a in db.execute(select(Alert).where(Alert.user_id == user.id, Alert.active.is_(True))).scalars().all():
        n = al.evaluate(db, a)
        if n:
            fired.append(n.message)
    db.commit()
    return {"triggered": fired}


@router.get("/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(100)).scalars().all()
    return [{"id": n.id, "kind": n.kind, "ticker": n.ticker, "message": n.message, "data": n.data, "read": n.read,
             "created_at": n.created_at.isoformat() + "Z"} for n in rows]


@router.post("/notifications/read")
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == user.id).values(read=True))
    db.commit()
    return {"ok": True}
