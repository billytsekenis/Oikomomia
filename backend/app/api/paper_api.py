from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..analytics import paper
from ..data.http import ProviderError
from ..db import get_db
from ..models import PaperAccount, PaperOrder, PaperTrade, User
from ..security import get_current_user
from .common import clean

router = APIRouter(prefix="/api/paper", tags=["paper"])


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    initial_cash: float = Field(default=100_000, gt=0, le=1e9)
    preset: str | None = None
    fee_model: str = "per_share"
    commission_per_share: float = Field(default=0.005, ge=0)
    commission_pct: float = Field(default=0.0, ge=0, le=0.05)
    min_commission: float = Field(default=1.0, ge=0)
    free_trades_per_month: int = Field(default=0, ge=0, le=1000)
    fx_fee_pct: float = Field(default=0.0, ge=0, le=0.05)


class OrderIn(BaseModel):
    ticker: str
    side: str
    order_type: str = "market"
    quantity: float = Field(gt=0)
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "GTC"
    stop_loss: float | None = None
    take_profit: float | None = None


def _acct(db: Session, aid: int, user: User, write: bool = False) -> PaperAccount:
    a = db.get(PaperAccount, aid)
    if a is None or (a.user_id != user.id and not (a.is_system and not write)):
        raise HTTPException(404, "account not found")
    return a


@router.get("/accounts")
def accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(PaperAccount).where(or_(PaperAccount.user_id == user.id, PaperAccount.is_system.is_(True)))
                      .order_by(PaperAccount.is_system, PaperAccount.id)).scalars().all()
    return [{"id": a.id, "name": a.name, "cash": a.cash, "initial_cash": a.initial_cash, "is_system": a.is_system,
             "strategy": a.strategy} for a in rows]


@router.post("/accounts")
def create_account(body: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cfg = body.model_dump()
    profile = ""
    if body.preset:
        pre = next((p for p in paper.FEE_PRESETS if p["id"] == body.preset), None)
        if pre is None:
            raise HTTPException(400, "unknown preset")
        profile = pre["label"]
        for k, v in pre.items():
            if k not in ("id", "label") and k not in body.model_fields_set:
                cfg[k] = v
    if cfg["fee_model"] not in ("per_share", "pct"):
        raise HTTPException(400, "fee_model must be per_share or pct")
    a = PaperAccount(user_id=user.id, name=body.name, initial_cash=body.initial_cash, cash=body.initial_cash,
                     commission_per_share=cfg["commission_per_share"], min_commission=cfg["min_commission"],
                     fee_model=cfg["fee_model"], commission_pct=cfg["commission_pct"],
                     free_trades_per_month=cfg["free_trades_per_month"], fx_fee_pct=cfg["fx_fee_pct"],
                     fee_profile=profile or ("custom" if cfg["fee_model"] == "pct" else ""))
    db.add(a)
    db.commit()
    return {"id": a.id}


@router.get("/fee-presets")
def fee_presets(_: User = Depends(get_current_user)):
    return {"presets": paper.FEE_PRESETS, "note": paper.FEE_PRESET_NOTE}


@router.get("/accounts/{aid}")
def account(aid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _acct(db, aid, user)
    return clean(paper.account_view(db, a))


@router.get("/accounts/{aid}/orders")
def orders(aid: int, status: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _acct(db, aid, user)
    q = select(PaperOrder).where(PaperOrder.account_id == a.id)
    if status:
        q = q.where(PaperOrder.status == status)
    return [paper.order_dict(o) for o in db.execute(q.order_by(PaperOrder.id.desc()).limit(300)).scalars()]


@router.get("/accounts/{aid}/trades")
def trades(aid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _acct(db, aid, user)
    return [paper.trade_dict(t) for t in db.execute(select(PaperTrade).where(PaperTrade.account_id == a.id)
                                                   .order_by(PaperTrade.id.desc()).limit(500)).scalars()]


@router.post("/accounts/{aid}/orders")
def place(aid: int, body: OrderIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _acct(db, aid, user, write=True)
    try:
        o = paper.place_order(db, a, body.ticker, body.side, body.order_type, body.quantity, body.limit_price,
                              body.stop_price, body.time_in_force, body.stop_loss, body.take_profit)
    except paper.OrderError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except ProviderError as exc:
        db.rollback()
        raise HTTPException(503, f"Quote unavailable: {exc}") from exc
    return paper.order_dict(o)


@router.post("/accounts/{aid}/orders/{oid}/cancel")
def cancel(aid: int, oid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _acct(db, aid, user, write=True)
    o = db.get(PaperOrder, oid)
    if o is None or o.account_id != a.id:
        raise HTTPException(404, "order not found")
    try:
        paper.cancel(db, o)
    except paper.OrderError as exc:
        raise HTTPException(400, str(exc)) from exc
    return paper.order_dict(o)


@router.get("/quote/{ticker}")
def quote(ticker: str, _: User = Depends(get_current_user)):
    try:
        q = paper.execution_quote(ticker.upper())
    except (paper.OrderError, ProviderError) as exc:
        raise HTTPException(404, str(exc)) from exc
    from ..analytics.market_hours import status
    return clean(q | {"market": status()})


@router.post("/process")
def process(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"processed": paper.process_all(db)}
