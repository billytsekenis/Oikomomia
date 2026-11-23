"""Paper trading engine - virtual money only, never touches a real broker.

Execution model
  * Market hours: NYSE regular session only (09:30-16:00 ET, holidays, early closes).
    Orders placed while closed queue as 'pending' and are processed at the open.
  * Quotes: Nasdaq real-time last sale + bid/ask when available. If no live
    bid/ask exists, a spread is MODELLED with the Corwin-Schultz (2012)
    high-low estimator over the last 20 sessions (floor 2 bps) and labelled.
  * Buys fill at ask, sells at bid, plus slippage from a square-root impact
    model: slippage_bps = 1 + 10 * sqrt(order notional / 20-day avg $ volume), cap 50.
  * Limit orders fill when the executable side crosses the limit (price improvement
    passed through on gaps); stop orders become market orders when triggered;
    stop-limit becomes a limit at trigger. Stop fills gap through (no guaranteed stop price).
  * Brackets: attached stop-loss / take-profit become an OCO pair after the entry fills.
  * Long-only, no margin: buys require cash, sells require shares.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data import market
from ..data.http import ProviderError
from ..models import PaperAccount, PaperEquity, PaperOrder, PaperPosition, PaperTrade, utcnow
from . import market_hours
from .portfolio import perf_stats

log = logging.getLogger("oik.paper")
ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}


class OrderError(ValueError):
    pass


def corwin_schultz_spread(df) -> float | None:
    """Corwin & Schultz (2012) bid-ask spread estimator from daily highs/lows."""
    d = df.tail(21)
    if len(d) < 5:
        return None
    hi, lo = np.log(d["high"].values), np.log(d["low"].values)
    beta = (hi[1:] - lo[1:]) ** 2 + (hi[:-1] - lo[:-1]) ** 2
    h2 = np.maximum(d["high"].values[1:], d["high"].values[:-1])
    l2 = np.minimum(d["low"].values[1:], d["low"].values[:-1])
    gamma = np.log(h2 / l2) ** 2
    k = 3 - 2 * math.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s = np.clip(s, 0, None)
    return float(np.mean(s))


def execution_quote(ticker: str) -> dict:
    q = market.quote(ticker, ttl=10)
    price = q.get("price")
    if not price:
        raise OrderError(f"No price available for {ticker}")
    bid, ask = q.get("bid"), q.get("ask")
    try:
        df = market.bars(ticker)
        adv = float((df["close"] * df["volume"]).tail(20).mean())
    except ProviderError:
        df, adv = None, None
    live_ok = bid and ask and ask >= bid > 0 and (ask - bid) / price < 0.02
    if live_ok:
        spread_bps = (ask - bid) / ((ask + bid) / 2) * 1e4
        src = q.get("source", "") + " bid/ask"
    else:
        cs = corwin_schultz_spread(df) if df is not None else None
        spread_bps = max(2.0, (cs or 0.0005) * 1e4)
        bid, ask = price * (1 - spread_bps / 2e4), price * (1 + spread_bps / 2e4)
        src = q.get("source", "") + " + modelled spread (Corwin-Schultz)"
    return {"price": float(price), "bid": float(bid), "ask": float(ask), "spread_bps": float(spread_bps),
            "adv_usd": adv, "source": src, "day_high": q.get("day_high"), "day_low": q.get("day_low")}


def slippage_bps(notional: float, adv: float | None) -> float:
    if not adv or adv <= 0:
        return 5.0
    return float(min(50.0, 1.0 + 10.0 * math.sqrt(notional / adv)))


def commission(acct: PaperAccount, qty: float) -> float:
    return max(acct.min_commission, acct.commission_per_share * qty)


def _position(db: Session, acct_id: int, ticker: str) -> PaperPosition | None:
    return db.get(PaperPosition, (acct_id, ticker))


def place_order(db: Session, acct: PaperAccount, ticker: str, side: str, order_type: str, quantity: float,
                limit_price: float | None = None, stop_price: float | None = None, tif: str = "GTC",
                stop_loss: float | None = None, take_profit: float | None = None, now: datetime | None = None) -> PaperOrder:
    ticker, side, order_type = ticker.upper(), side.upper(), order_type.lower()
    if side not in ("BUY", "SELL"):
        raise OrderError("side must be BUY or SELL")
    if order_type not in ORDER_TYPES:
        raise OrderError(f"order_type must be one of {sorted(ORDER_TYPES)}")
    if not quantity or quantity <= 0 or quantity != quantity:
        raise OrderError("quantity must be positive")
    if order_type in ("limit", "stop_limit") and (not limit_price or limit_price <= 0):
        raise OrderError("limit price required")
    if order_type in ("stop", "stop_limit") and (not stop_price or stop_price <= 0):
        raise OrderError("stop price required")
    if tif not in ("DAY", "GTC"):
        raise OrderError("time_in_force must be DAY or GTC")
    q = execution_quote(ticker)
    if side == "SELL":
        pos = _position(db, acct.id, ticker)
        reserved = sum(o.quantity for o in db.execute(select(PaperOrder).where(
            PaperOrder.account_id == acct.id, PaperOrder.ticker == ticker, PaperOrder.side == "SELL",
            PaperOrder.status == "pending", PaperOrder.oco_group.is_(None))).scalars())
        if not pos or pos.quantity + 1e-9 < quantity + reserved:
            raise OrderError("Insufficient shares (short selling is not supported)")
    else:
        ref = limit_price if order_type in ("limit", "stop_limit") else max(q["ask"], stop_price or 0)
        est = quantity * ref * (1 + slippage_bps(quantity * ref, q["adv_usd"]) / 1e4) + commission(acct, quantity)
        if est > acct.cash + 1e-6:
            raise OrderError(f"Insufficient cash: estimated cost ${est:,.2f} > available ${acct.cash:,.2f}")
        if stop_loss is not None and stop_loss >= q["price"]:
            raise OrderError("stop-loss must be below the current price for a buy")
        if take_profit is not None and take_profit <= q["price"]:
            raise OrderError("take-profit must be above the current price for a buy")
    o = PaperOrder(account_id=acct.id, ticker=ticker, side=side, order_type=order_type, quantity=float(quantity),
                   limit_price=limit_price, stop_price=stop_price, time_in_force=tif, status="pending",
                   attach_stop_loss=stop_loss if side == "BUY" else None,
                   attach_take_profit=take_profit if side == "BUY" else None)
    db.add(o)
    db.flush()
    process_order(db, o, now=now, quote=q)
    db.commit()
    return o


def _fill(db: Session, o: PaperOrder, ref_px: float, q: dict, reason: str) -> None:
    acct = db.get(PaperAccount, o.account_id)
    notional = o.quantity * ref_px
    slip = slippage_bps(notional, q.get("adv_usd"))
    px = ref_px * (1 + slip / 1e4) if o.side == "BUY" else ref_px * (1 - slip / 1e4)
    if o.order_type in ("limit", "stop_limit") and o.limit_price:
        px = min(px, o.limit_price) if o.side == "BUY" else max(px, o.limit_price)
    fee = commission(acct, o.quantity)
    pos = _position(db, acct.id, o.ticker)
    realized = 0.0
    if o.side == "BUY":
        cost = o.quantity * px + fee
        if cost > acct.cash + 1e-6:
            o.status, o.reason = "rejected", f"Insufficient cash at execution (${cost:,.2f})"
            return
        acct.cash -= cost
        if pos is None:
            pos = PaperPosition(account_id=acct.id, ticker=o.ticker, quantity=0.0, avg_price=0.0)
            db.add(pos)
        tot_cost = pos.avg_price * pos.quantity + o.quantity * px + fee
        pos.quantity += o.quantity
        pos.avg_price = tot_cost / pos.quantity
    else:
        if pos is None or pos.quantity + 1e-9 < o.quantity:
            o.status, o.reason = "rejected", "Insufficient shares at execution"
            return
        proceeds = o.quantity * px - fee
        realized = proceeds - pos.avg_price * o.quantity
        acct.cash += proceeds
        pos.quantity -= o.quantity
        pos.realized_pnl += realized
        if pos.quantity <= 1e-9:
            db.delete(pos)
    o.status, o.fill_price, o.filled_at, o.updated_at, o.reason = "filled", px, utcnow(), utcnow(), reason
    db.add(PaperTrade(account_id=acct.id, order_id=o.id, ticker=o.ticker, side=o.side, quantity=o.quantity, price=px,
                      reference_price=ref_px, commission=fee, spread_bps=q["spread_bps"], slippage_bps=slip,
                      realized_pnl=realized, quote_source=q["source"][:60]))
    # OCO: cancel siblings
    if o.oco_group:
        for sib in db.execute(select(PaperOrder).where(PaperOrder.oco_group == o.oco_group, PaperOrder.id != o.id,
                                                       PaperOrder.status == "pending")).scalars():
            sib.status, sib.reason, sib.updated_at = "cancelled", f"OCO: sibling #{o.id} filled", utcnow()
    # bracket children after an entry fill
    if o.side == "BUY" and (o.attach_stop_loss or o.attach_take_profit):
        grp = uuid.uuid4().hex[:12]
        if o.attach_stop_loss:
            db.add(PaperOrder(account_id=acct.id, ticker=o.ticker, side="SELL", order_type="stop", quantity=o.quantity,
                              stop_price=o.attach_stop_loss, time_in_force="GTC", role="stop_loss", parent_id=o.id,
                              oco_group=grp))
        if o.attach_take_profit:
            db.add(PaperOrder(account_id=acct.id, ticker=o.ticker, side="SELL", order_type="limit", quantity=o.quantity,
                              limit_price=o.attach_take_profit, time_in_force="GTC", role="take_profit", parent_id=o.id,
                              oco_group=grp))


def process_order(db: Session, o: PaperOrder, now: datetime | None = None, quote: dict | None = None) -> None:
    if o.status != "pending":
        return
    now = now or datetime.now(timezone.utc)
    st = market_hours.status(now)
    if o.time_in_force == "DAY":
        created_et = o.created_at.replace(tzinfo=timezone.utc).astimezone(market_hours.ET)
        sess = market_hours.session(created_et.date())
        end = sess[1] if sess and created_et < sess[1] else None
        if end is None:
            nxt = market_hours.next_open(created_et)
            sess2 = market_hours.session(nxt.date())
            end = sess2[1] if sess2 else None
        if end and now.astimezone(market_hours.ET) >= end:
            o.status, o.reason, o.updated_at = "expired", "DAY order not filled before the close", utcnow()
            return
    if not st["is_open"]:
        return  # queued until regular session
    q = quote or execution_quote(o.ticker)
    last, bid, ask = q["price"], q["bid"], q["ask"]
    # day high/low can be used only if the order existed before today's open
    created_et = o.created_at.replace(tzinfo=timezone.utc).astimezone(market_hours.ET)
    sess = market_hours.session(now.astimezone(market_hours.ET).date())
    use_range = sess is not None and created_et < sess[0] and q.get("day_high") and q.get("day_low")
    hi = max(last, q["day_high"]) if use_range else last
    lo = min(last, q["day_low"]) if use_range else last
    exe = ask if o.side == "BUY" else bid
    t = o.order_type
    if t == "market":
        _fill(db, o, exe, q, "market order")
    elif t == "limit":
        if o.side == "BUY" and (ask <= o.limit_price or lo <= o.limit_price):
            _fill(db, o, min(ask, o.limit_price), q, "limit reached")
        elif o.side == "SELL" and (bid >= o.limit_price or hi >= o.limit_price):
            _fill(db, o, max(bid, o.limit_price), q, "limit reached")
    elif t in ("stop", "stop_limit"):
        trig = (o.side == "BUY" and (last >= o.stop_price or hi >= o.stop_price)) or \
               (o.side == "SELL" and (last <= o.stop_price or lo <= o.stop_price))
        if trig:
            if t == "stop":
                ref = max(exe, o.stop_price) if o.side == "BUY" else min(exe, o.stop_price)
                # if the market already gapped through the stop, the fill is at the (worse) current price
                ref = exe if (o.side == "SELL" and exe < o.stop_price) or (o.side == "BUY" and exe > o.stop_price) else ref
                _fill(db, o, ref, q, "stop triggered")
            else:
                o.order_type, o.reason, o.updated_at = "limit", f"stop {o.stop_price} triggered -> limit", utcnow()
                process_order(db, o, now=now, quote=q)


def process_all(db: Session, now: datetime | None = None) -> int:
    n = 0
    for o in db.execute(select(PaperOrder).where(PaperOrder.status == "pending").order_by(PaperOrder.id)).scalars().all():
        try:
            process_order(db, o, now=now)
            n += o.status != "pending"
        except (ProviderError, OrderError) as exc:
            log.warning("order %s not processed: %s", o.id, exc)
    db.commit()
    return n


def cancel(db: Session, o: PaperOrder) -> None:
    if o.status != "pending":
        raise OrderError(f"Order is {o.status}")
    o.status, o.reason, o.updated_at = "cancelled", "cancelled by user", utcnow()
    db.commit()


def account_view(db: Session, acct: PaperAccount, live: bool = True) -> dict:
    positions = db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id)).scalars().all()
    rows, mv = [], 0.0
    for p in positions:
        try:
            px = market.quote(p.ticker)["price"] if live else market.bars(p.ticker)["close"].iloc[-1]
        except ProviderError:
            px = p.avg_price
        v = p.quantity * px
        mv += v
        rows.append({"ticker": p.ticker, "quantity": p.quantity, "avg_price": p.avg_price, "price": px,
                     "market_value": v, "unrealized_pnl": v - p.avg_price * p.quantity,
                     "unrealized_pct": px / p.avg_price - 1 if p.avg_price else None, "realized_pnl": p.realized_pnl})
    equity = acct.cash + mv
    trades = db.execute(select(PaperTrade).where(PaperTrade.account_id == acct.id).order_by(PaperTrade.executed_at)).scalars().all()
    closed = [t.realized_pnl for t in trades if t.side == "SELL"]
    wins = [x for x in closed if x > 0]
    losses = [-x for x in closed if x < 0]
    eq_rows = db.execute(select(PaperEquity).where(PaperEquity.account_id == acct.id).order_by(PaperEquity.date)).scalars().all()
    curve = [{"date": str(e.date), "equity": e.equity} for e in eq_rows]
    stats = {}
    if len(curve) >= 2:
        import pandas as pd
        s = pd.Series([c["equity"] for c in curve])
        stats = perf_stats(s.pct_change().dropna())
    return {"account": {"id": acct.id, "name": acct.name, "currency": acct.currency, "cash": acct.cash,
                        "initial_cash": acct.initial_cash, "is_system": acct.is_system, "strategy": acct.strategy,
                        "commission_per_share": acct.commission_per_share, "min_commission": acct.min_commission},
            "equity": equity, "market_value": mv, "total_return": equity / acct.initial_cash - 1,
            "positions": rows, "n_trades": len(trades),
            "closed_trades": len(closed), "win_rate": len(wins) / len(closed) if closed else None,
            "profit_factor": sum(wins) / sum(losses) if losses else None,
            "realized_pnl": sum(closed), "fees_paid": sum(t.commission for t in trades),
            "equity_curve": curve, "stats": stats, "market": market_hours.status()}


def snapshot_equity(db: Session) -> None:
    today = market_hours.last_completed_session()
    for acct in db.execute(select(PaperAccount)).scalars():
        v = account_view(db, acct, live=False)
        row = db.get(PaperEquity, (acct.id, today)) or PaperEquity(account_id=acct.id, date=today, equity=0, cash=0)
        row.equity, row.cash = v["equity"], acct.cash
        db.merge(row)
    db.commit()


def order_dict(o: PaperOrder) -> dict:
    return {k: getattr(o, k) for k in ("id", "ticker", "side", "order_type", "quantity", "limit_price", "stop_price",
                                        "time_in_force", "status", "role", "parent_id", "oco_group", "attach_stop_loss",
                                        "attach_take_profit", "fill_price", "reason")} | {
        "created_at": o.created_at.isoformat() + "Z", "filled_at": o.filled_at.isoformat() + "Z" if o.filled_at else None}


def trade_dict(t: PaperTrade) -> dict:
    return {k: getattr(t, k) for k in ("id", "order_id", "ticker", "side", "quantity", "price", "reference_price",
                                        "commission", "spread_bps", "slippage_bps", "realized_pnl", "quote_source")} | {
        "executed_at": t.executed_at.isoformat() + "Z"}


_ = timedelta
