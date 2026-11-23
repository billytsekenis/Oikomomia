from datetime import datetime, timezone

import pytest

from app.analytics import paper
from app.models import PaperAccount, PaperOrder, PaperPosition

OPEN = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)    # 10:00 ET Monday
CLOSED = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)  # Sunday


class Q:
    def __init__(self):
        self.q = {"price": 100.0, "bid": 99.95, "ask": 100.05, "spread_bps": 10.0, "adv_usd": 1e12, "source": "test",
                  "day_high": None, "day_low": None}

    def __call__(self, t):
        return dict(self.q)


@pytest.fixture()
def acct(db, monkeypatch):
    q = Q()
    monkeypatch.setattr(paper, "execution_quote", q)
    a = PaperAccount(name="t", initial_cash=10_000, cash=10_000, commission_per_share=0.01, min_commission=1.0)
    db.add(a)
    db.commit()
    return a, q


def test_market_buy_fills_at_ask_plus_slippage(db, acct):
    a, q = acct
    o = paper.place_order(db, a, "AAA", "BUY", "market", 10, now=OPEN)
    assert o.status == "filled"
    slip = paper.slippage_bps(10 * 100.05, 1e12)
    assert o.fill_price == pytest.approx(100.05 * (1 + slip / 1e4))
    assert a.cash == pytest.approx(10_000 - 10 * o.fill_price - 1.0)
    pos = db.get(PaperPosition, (a.id, "AAA"))
    assert pos.quantity == 10


def test_market_order_queues_when_closed(db, acct):
    a, _ = acct
    o = paper.place_order(db, a, "AAA", "BUY", "market", 1, now=CLOSED)
    assert o.status == "pending"
    paper.process_order(db, o, now=OPEN)
    assert o.status == "filled"


def test_limit_buy_waits_then_fills_with_price_improvement(db, acct):
    a, q = acct
    o = paper.place_order(db, a, "AAA", "BUY", "limit", 5, limit_price=95.0, now=OPEN)
    assert o.status == "pending"
    q.q.update(price=94.0, bid=93.95, ask=94.05)
    paper.process_order(db, o, now=OPEN)
    assert o.status == "filled" and o.fill_price <= 95.0


def test_bracket_stop_loss_gap_and_oco(db, acct):
    a, q = acct
    o = paper.place_order(db, a, "AAA", "BUY", "market", 10, stop_loss=90.0, take_profit=120.0, now=OPEN)
    db.commit()
    kids = db.query(PaperOrder).filter(PaperOrder.parent_id == o.id).all()
    assert {k.role for k in kids} == {"stop_loss", "take_profit"}
    q.q.update(price=85.0, bid=84.9, ask=85.1)  # gap below the stop
    for k in kids:
        paper.process_order(db, k, now=OPEN)
    db.commit()
    sl = next(k for k in kids if k.role == "stop_loss")
    tp = next(k for k in kids if k.role == "take_profit")
    assert sl.status == "filled" and sl.fill_price < 90.0  # stop is not a guaranteed price
    assert tp.status == "cancelled"
    assert db.get(PaperPosition, (a.id, "AAA")) is None


def test_no_short_selling_and_cash_check(db, acct):
    a, _ = acct
    with pytest.raises(paper.OrderError):
        paper.place_order(db, a, "AAA", "SELL", "market", 1, now=OPEN)
    with pytest.raises(paper.OrderError):
        paper.place_order(db, a, "AAA", "BUY", "market", 1000, now=OPEN)


def test_day_order_expires_after_close(db, acct):
    a, _ = acct
    o = paper.place_order(db, a, "AAA", "BUY", "limit", 1, limit_price=50.0, tif="DAY", now=OPEN)
    o.created_at = OPEN.replace(tzinfo=None)
    paper.process_order(db, o, now=datetime(2026, 11, 23, 21, 30, tzinfo=timezone.utc))
    assert o.status == "expired"


def test_corwin_schultz_non_negative():
    import pandas as pd
    df = pd.DataFrame({"high": [101, 102, 101.5, 103, 102.2, 104], "low": [99, 100, 99.8, 101, 100.5, 102.1]})
    s = paper.corwin_schultz_spread(df)
    assert s is not None and s >= 0
