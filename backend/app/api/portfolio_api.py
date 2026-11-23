"""Portfolio Intelligence endpoints."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import portfolio as pfa
from ..analytics.context import get_market_context
from ..data import fx, market
from ..data.cache import cached
from ..data.http import ProviderError
from ..db import get_db
from ..models import Portfolio, Transaction, User
from ..security import get_current_user
from ..universe import UNIVERSE
from .common import clean

router = APIRouter(prefix="/api/portfolios", tags=["portfolio"])


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_currency: str = "EUR"
    benchmark: str = "SPY"


class TxnIn(BaseModel):
    ticker: str
    side: str
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    currency: str = "USD"
    fee: float = Field(default=0.0, ge=0)
    fee_currency: str | None = None
    fx_rate: float | None = Field(default=None, gt=0)
    trade_date: date
    note: str = ""


class WhatIf(BaseModel):
    trades: list[dict]


def _own(db: Session, pid: int, user: User) -> Portfolio:
    pf = db.get(Portfolio, pid)
    if pf is None or pf.user_id != user.id:
        raise HTTPException(404, "portfolio not found")
    return pf


def eurusd_history(start: date) -> pd.Series:
    start = min(start, date.today() - timedelta(days=400))
    env = cached(f"fx:EURUSD:{start.year}", 12 * 3600, fx.SOURCE,
                 lambda: {str(k): v for k, v in fx.history(date(start.year, 1, 1)).items()})
    s = pd.Series({date.fromisoformat(k): float(v) for k, v in env["value"].items()}).sort_index()
    return s


def _txn_dict(t: Transaction) -> dict:
    return {"id": t.id, "ticker": t.ticker, "side": t.side, "quantity": t.quantity, "price": t.price, "currency": t.currency,
            "fee": t.fee, "fee_currency": t.fee_currency, "fx_rate": t.fx_rate, "fx_source": t.fx_source,
            "trade_date": t.trade_date.isoformat(), "note": t.note}


@router.get("")
def list_portfolios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)).scalars().all()
    return [{"id": p.id, "name": p.name, "base_currency": p.base_currency, "benchmark": p.benchmark,
             "n_transactions": len(p.transactions)} for p in rows]


@router.post("")
def create_portfolio(body: PortfolioIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.base_currency not in ("EUR", "USD"):
        raise HTTPException(400, "base_currency must be EUR or USD")
    p = Portfolio(user_id=user.id, name=body.name, base_currency=body.base_currency, benchmark=body.benchmark.upper())
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.patch("/{pid}")
def update_portfolio(pid: int, body: PortfolioIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = _own(db, pid, user)
    if body.base_currency not in ("EUR", "USD"):
        raise HTTPException(400, "base_currency must be EUR or USD")
    p.name, p.base_currency, p.benchmark = body.name, body.base_currency, body.benchmark.upper()
    db.commit()
    return {"ok": True}


@router.delete("/{pid}")
def delete_portfolio(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_own(db, pid, user))
    db.commit()
    return {"ok": True}


@router.get("/{pid}/transactions")
def list_txns(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_txn_dict(t) for t in _own(db, pid, user).transactions]


@router.post("/{pid}/transactions")
def add_txn(pid: int, body: TxnIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = _own(db, pid, user)
    side = body.side.upper()
    if side not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")
    if body.currency not in ("USD", "EUR"):
        raise HTTPException(400, "currency must be USD or EUR")
    if body.trade_date > date.today():
        raise HTTPException(400, "trade date cannot be in the future")
    t = body.ticker.upper().strip()
    if side == "SELL":
        held = sum((x.quantity if x.side == "BUY" else -x.quantity) for x in pf.transactions
                   if x.ticker == t and x.trade_date <= body.trade_date)
        if body.quantity > held + 1e-9:
            raise HTTPException(400, f"Cannot sell {body.quantity} {t}: only {held:g} held on {body.trade_date}")
    fx_rate, fx_src = body.fx_rate, "user supplied" if body.fx_rate else ""
    if fx_rate is None and body.currency != pf.base_currency:
        try:
            s = eurusd_history(body.trade_date)
            rate = fx.rate_on(s, body.trade_date)
            if rate is None:
                raise ProviderError("no ECB fixing near the trade date")
            fx_rate = pfa.fx_to_base(body.currency, pf.base_currency, rate)
            fx_src = f"{fx.SOURCE}, fixing on/before {body.trade_date}"
        except ProviderError as exc:
            raise HTTPException(503, f"FX rate unavailable - enter it manually: {exc}") from exc
    elif fx_rate is None:
        fx_rate, fx_src = 1.0, "same currency"
    tx = Transaction(portfolio_id=pf.id, ticker=t, side=side, quantity=body.quantity, price=body.price,
                     currency=body.currency, fee=body.fee, fee_currency=body.fee_currency or pf.base_currency,
                     fx_rate=fx_rate, fx_source=fx_src, trade_date=body.trade_date, note=body.note[:500])
    db.add(tx)
    db.commit()
    return _txn_dict(tx)


@router.delete("/{pid}/transactions/{tid}")
def delete_txn(pid: int, tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = _own(db, pid, user)
    tx = db.get(Transaction, tid)
    if tx is None or tx.portfolio_id != pf.id:
        raise HTTPException(404, "transaction not found")
    db.delete(tx)
    db.commit()
    return {"ok": True}


def compute_analytics(db: Session, pf: Portfolio, custom_shock: tuple[str, float] | None = None,
                      extra_trades: list[dict] | None = None) -> dict:
    txns = [_txn_dict(t) for t in pf.transactions] + list(extra_trades or [])
    base = pf.base_currency
    if not txns:
        return {"empty": True, "base_currency": base, "summary": {"positions": [], "market_value": 0}}
    first = min(date.fromisoformat(str(t["trade_date"])) for t in txns)
    try:
        eurusd = eurusd_history(first)
        fx_now = float(eurusd.iloc[-1])
        fx_date = str(eurusd.index[-1])
    except ProviderError:
        eurusd, fx_now, fx_date = None, None, None
    tickers = sorted({t["ticker"] for t in txns})
    needed = set(tickers) | {pf.benchmark, "SPY", "QQQ", "TLT"}
    bars = market.bars_many(sorted(needed))
    missing = [t for t in tickers if t not in bars]
    lots, warnings = pfa.build_lots(txns, base, eurusd)
    prices, prev = {}, {}
    for t in tickers:
        try:
            q = market.quote(t)
            prices[t], prev[t] = q["price"], q.get("previous_close")
        except ProviderError:
            if t in bars:
                prices[t], prev[t] = float(bars[t]["close"].iloc[-1]), float(bars[t]["close"].iloc[-2])
    if fx_now is None and any(t.get("currency", "USD") != base for t in txns):
        return {"error": "FX rates unavailable; cannot value cross-currency portfolio", "base_currency": base}
    summary = pfa.positions_view(lots, prices, base, fx_now or 1.0, prev)
    currencies = {t["ticker"]: t.get("currency", "USD") for t in txns}
    held_bars = {t: bars[t] for t in tickers if t in bars}
    tw = pfa.twr_series(txns, held_bars, base, eurusd)
    ctx = get_market_context()
    rf = float(ctx["mf"]["rf"].dropna().tail(252).mean()) if ctx["mf"]["rf"].notna().any() else 0.0
    perf = pfa.perf_stats(tw["return"].iloc[1:], rf) if len(tw) > 2 else {"insufficient": True}
    bench_ccy = {pf.benchmark: "USD", "SPY": "USD", "QQQ": "USD", "TLT": "USD"}
    allrets = pfa.base_returns({**held_bars, **{k: bars[k] for k in bench_ccy if k in bars}}, {**currencies, **bench_ccy}, base, eurusd)
    bench_perf = None
    series = []
    if len(tw) > 2 and pf.benchmark in allrets:
        br = allrets[pf.benchmark].reindex(tw.index).fillna(0.0).iloc[1:]
        bench_perf = pfa.perf_stats(br, rf)
        bidx = (1 + br).cumprod()
        step = max(1, len(tw) // 400)
        for d in tw.index[1::step]:
            series.append({"date": str(d), "portfolio": float(tw.at[d, "twr_index"]), "benchmark": float(bidx.get(d, float("nan"))),
                           "value": float(tw.at[d, "value"])})
    weights = {p["ticker"]: p["weight"] for p in summary["positions"]}
    sectors = {t: UNIVERSE.get(t, {}).get("sector", "Other") for t in weights}
    value = summary["market_value"]
    risk = pfa.risk_metrics(weights, allrets, allrets.get("SPY", pd.Series(dtype=float)), value, sectors) if weights else {}
    factor = {k: allrets[k] for k in ("SPY", "QQQ", "TLT") if k in allrets}
    stress = pfa.stress_tests(weights, bars, allrets, value, factor) if weights else {}
    custom = None
    if custom_shock and weights:
        f, shock = custom_shock
        fr = factor.get(f)
        if fr is not None:
            import numpy as np
            exp, var_res, detail = 0.0, 0.0, []
            for t, w in weights.items():
                beta, rsd = pfa._beta(allrets.get(t), fr, with_resid=True)
                if beta is None:
                    continue
                exp += w * beta * shock
                var_res += (w * rsd * np.sqrt(10)) ** 2
                detail.append({"ticker": t, "beta": beta, "expected_return": beta * shock})
            band = 1.645 * float(np.sqrt(var_res))
            custom = {"factor": f, "shock": shock, "expected_return": exp, "expected_pnl": exp * value,
                      "range_90": [(exp - band) * value, (exp + band) * value], "by_position": detail}
    ccy_exp = {}
    for p in summary["positions"]:
        ccy_exp[p["currency"]] = ccy_exp.get(p["currency"], 0) + p["weight"]
    fx_sens = None
    if fx_now and base == "EUR":
        usd_w = ccy_exp.get("USD", 0)
        fx_sens = {"description": "Impact on portfolio value (EUR) if EUR/USD rises 10% (USD weakens)",
                   "pnl": -value * usd_w * (1 - 1 / 1.10), "pct": -usd_w * (1 - 1 / 1.10)}
    cf = None
    if pf.benchmark in bars:
        fake = pfa.counterfactual_benchmark(txns, bars[pf.benchmark], base, eurusd, pf.benchmark)["transactions"]
        lots_b, _ = pfa.build_lots(fake, base, eurusd)
        bq = market.quote(pf.benchmark)["price"]
        alt = pfa.positions_view(lots_b, {pf.benchmark: bq}, base, fx_now or 1.0)
        cf = {"benchmark": pf.benchmark, "value_if_benchmark": alt["market_value"] + alt["realized_pnl"],
              "actual_value_plus_realized": value + summary["realized_pnl"],
              "difference": (value + summary["realized_pnl"]) - (alt["market_value"] + alt["realized_pnl"]),
              "method": "Every buy/sell replaced by the same base-currency amount of the benchmark on the same date."}
    return {"base_currency": base, "fx": {"eurusd": fx_now, "date": fx_date, "source": fx.SOURCE}, "summary": summary,
            "performance": perf, "benchmark": pf.benchmark, "benchmark_performance": bench_perf, "series": series,
            "risk": risk, "stress": stress, "custom_shock": custom, "currency_exposure": ccy_exp, "fx_sensitivity": fx_sens,
            "counterfactual": cf, "warnings": warnings + ([f"No price data for {', '.join(missing)}"] if missing else []),
            "notes": ["Average-cost accounting; fees capitalised on buys and deducted on sells.",
                      "P&L split into price effect (at purchase FX) and FX effect.",
                      "Performance is time-weighted, dividend-adjusted where the data source provides dividends."]}


@router.get("/{pid}/analytics")
def analytics(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return clean(compute_analytics(db, _own(db, pid, user)))


@router.post("/{pid}/stress")
def custom_stress(pid: int, factor: str = "QQQ", shock: float = -0.10, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if factor not in ("SPY", "QQQ", "TLT") or not -0.9 <= shock <= 0.9:
        raise HTTPException(400, "factor must be SPY/QQQ/TLT and shock within +/-90%")
    an = compute_analytics(db, _own(db, pid, user), custom_shock=(factor, shock))
    return clean({"custom_shock": an.get("custom_shock"), "base_currency": an.get("base_currency")})


@router.post("/{pid}/whatif")
def whatif(pid: int, body: WhatIf, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = _own(db, pid, user)
    before = compute_analytics(db, pf)
    extra = []
    today = date.today()
    for tr in body.trades[:20]:
        t = str(tr.get("ticker", "")).upper()
        q = float(tr.get("quantity", 0))
        side = str(tr.get("side", "BUY")).upper()
        if not t or q <= 0 or side not in ("BUY", "SELL"):
            raise HTTPException(400, "each trade needs ticker, side BUY/SELL and positive quantity")
        px = market.quote(t)["price"]
        extra.append({"ticker": t, "side": side, "quantity": q, "price": px, "currency": "USD", "fee": 0.0,
                      "fee_currency": pf.base_currency, "fx_rate": None, "trade_date": today.isoformat(), "id": 10**9})
    after = compute_analytics(db, pf, extra_trades=extra)
    keys = ["beta", "volatility", "var_1d_95_hist", "var_pct_1d_95", "effective_n", "top5_weight", "avg_pairwise_correlation"]
    pick = lambda a: {k: (a.get("risk") or {}).get(k) for k in keys} | {"market_value": a["summary"]["market_value"],  # noqa: E731
                                                                      "sector_weights": (a.get("risk") or {}).get("sector_weights")}
    return clean({"before": pick(before), "after": pick(after), "trades": extra,
                  "flags_after": (after.get("risk") or {}).get("flags"),
                  "note": "Hypothetical trades at current quotes, no fees; risk measured on 1-year history of the resulting weights."})
