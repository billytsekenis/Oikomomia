from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import PaperAccount, Portfolio, User, Watchlist, WatchlistItem
from ..security import COOKIE_NAME, create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=s.cookie_secure,
                    max_age=s.access_token_minutes * 60, path="/")


def _user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "base_currency": u.base_currency}


@router.post("/register")
def register(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    s = get_settings()
    n_users = db.scalar(select(func.count(User.id))) or 0
    if not s.allow_registration and n_users > 0:
        raise HTTPException(403, "Registration is disabled on this server")
    email = body.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    u = User(email=email, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    # sensible starting objects so every page is usable immediately
    wl = Watchlist(user_id=u.id, name="My watchlist")
    db.add(wl)
    db.flush()
    for t in ("AAPL", "MSFT", "NVDA", "SPY"):
        db.add(WatchlistItem(watchlist_id=wl.id, ticker=t))
    db.add(Portfolio(user_id=u.id, name="Main portfolio", base_currency="EUR"))
    db.add(PaperAccount(user_id=u.id, name="Paper account (Revolut Standard fees)", initial_cash=100_000, cash=100_000,
                        fee_model="pct", commission_pct=0.0025, min_commission=1.0, free_trades_per_month=1,
                        fx_fee_pct=0.0, fee_profile="Revolut Standard"))
    db.commit()
    tok = create_token(u.id)
    _set_cookie(resp, tok)
    return {"user": _user_dict(u), "token": tok}


@router.post("/login")
def login(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    tok = create_token(u.id)
    _set_cookie(resp, tok)
    return {"user": _user_dict(u), "token": tok}


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_dict(user)}


@router.get("/config")
def auth_config(db: Session = Depends(get_db)):
    n = db.scalar(select(func.count(User.id))) or 0
    return {"registration_open": get_settings().allow_registration or n == 0, "has_users": n > 0}
