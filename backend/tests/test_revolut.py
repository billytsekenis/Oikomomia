"""Revolut statement import, split handling and Revolut fee model.

NOTE: the CSV fixture follows the column layout commonly seen in Revolut stocks account
statements (Date, Ticker, Type, Quantity, Price per share, Total Amount, Currency, FX Rate).
It could not be verified against an official specification; the parser is name-based and
tolerant, and every import is previewed before anything is saved."""
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analytics import paper
from app.analytics import portfolio as pf
from app.data import revolut
from app.main import app
from app.models import PaperAccount

CSV = """Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate
2024-01-02T10:00:00.000Z,,CASH TOP-UP,,,USD 2000,USD,1.0956
2024-01-03T15:31:12.345Z,AAPL,BUY - MARKET,2,USD 184.25,USD 368.50,USD,1.0919
2024-01-05T15:00:00Z,MSFT,BUY - LIMIT,0.5,USD 367.00,USD 184.50,USD,0.9158
2024-02-15T12:00:00Z,AAPL,DIVIDEND,,,USD 0.40,USD,1.0773
2024-03-01T09:00:00Z,,CUSTODY FEE,,,USD -0.12,USD,1.0838
2024-06-10T08:00:00Z,NVDA,BUY - MARKET,1,USD 1200.00,USD 1200.00,USD,1.0745
2024-06-10T23:00:00Z,NVDA,STOCK SPLIT,9,,,USD,1.0745
2024-07-01T15:00:00Z,AAPL,SELL - MARKET,1,USD 217.00,USD 216.50,USD,1.0743
2024-07-02T10:00:00Z,,CASH WITHDRAWAL,,,USD -50,USD,1.0752
"""


def test_parser_classifies_and_extracts_fee():
    rows, info = revolut.parse_csv(CSV)
    kinds = [r.kind for r in rows]
    assert kinds == ["SKIP", "BUY", "BUY", "DIV", "FEE", "BUY", "SPLIT", "SELL", "SKIP"]
    buy = rows[2]
    assert buy.quantity == 0.5 and buy.price == 367.0 and buy.fee == pytest.approx(1.0)  # 184.50 - 0.5*367
    sell = rows[7]
    assert sell.fee == pytest.approx(0.5)  # 217.00 - 216.50
    assert rows[3].total == pytest.approx(0.40) and rows[4].total == pytest.approx(0.12)
    assert {"date", "ticker", "type", "quantity", "price", "total", "currency", "fx"} <= set(info["columns_found"])


def test_parser_semicolon_and_decimal_comma():
    text = "Date;Ticker;Type;Quantity;Price per share;Total Amount;Currency\n03/01/2024 15:31:12;AAPL;BUY - MARKET;2;184,25;368,50;USD\n"
    rows, _ = revolut.parse_csv(text)
    assert rows[0].kind == "BUY" and rows[0].price == 184.25 and rows[0].trade_date == date(2024, 1, 3)


def test_parser_rejects_unknown_file():
    rows, info = revolut.parse_csv("foo,bar\n1,2\n")
    assert rows == [] and "error" in info


def test_fx_direction_resolved_against_ecb():
    assert revolut.resolve_fx(1.0919, 1.0920)[0] == pytest.approx(1.0919)          # USD per EUR
    assert revolut.resolve_fx(0.9158, 1.0920)[0] == pytest.approx(1 / 0.9158)      # EUR per USD -> inverted
    v, note = revolut.resolve_fx(1.40, 1.0920)
    assert v is None and "inconsistent" in note


def test_split_normalisation_keeps_cost_and_rescales():
    tx = [{"id": 1, "ticker": "N", "side": "BUY", "quantity": 1, "price": 1200.0, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-06-10"},
          {"id": 2, "ticker": "N", "side": "SPLIT", "quantity": 9, "price": 0.0, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-06-10"},
          {"id": 3, "ticker": "N", "side": "SELL", "quantity": 5, "price": 130.0, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-07-01"}]
    norm, notes = pf.normalize_splits(tx)
    assert [t["side"] for t in norm] == ["BUY", "SELL"] and "ratio 10" in notes[0]
    assert norm[0]["quantity"] == pytest.approx(10) and norm[0]["price"] == pytest.approx(120.0)
    lots, warn = pf.build_lots(norm, "USD", None)
    assert lots["N"].qty == pytest.approx(5) and not warn
    assert lots["N"].realized_price == pytest.approx(5 * (130 - 120))


def test_dividends_and_fees_reported_separately():
    tx = [{"ticker": "A", "side": "BUY", "quantity": 1, "price": 100, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-01-02"},
          {"ticker": "A", "side": "DIV", "quantity": 1, "price": 2.5, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-02-01"},
          {"ticker": "ACCOUNT", "side": "FEE", "quantity": 1, "price": 0.12, "currency": "USD", "fx_rate": 1.0, "trade_date": "2024-03-01"}]
    lots, _ = pf.build_lots(tx, "USD", None)
    v = pf.positions_view(lots, {"A": 110.0}, "USD", 1.0)
    assert v["dividends_received"] == pytest.approx(2.5) and v["other_fees"] == pytest.approx(0.12)
    assert v["unrealized_pnl"] == pytest.approx(10.0)


def test_revolut_commission_allowance_then_percentage(db):
    a = PaperAccount(name="r", initial_cash=10_000, cash=10_000, fee_model="pct", commission_pct=0.0025,
                     min_commission=1.0, free_trades_per_month=1, fx_fee_pct=0.0)
    db.add(a)
    db.commit()
    assert paper.commission(a, 1, 1000.0, db) == 0.0                     # within the free allowance
    db.add(paper.PaperTrade(account_id=a.id, ticker="X", side="BUY", quantity=1, price=1, reference_price=1,
                            commission=0, executed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()
    assert paper.commission(a, 1, 1000.0, db) == pytest.approx(2.5)      # 0.25% of 1000
    assert paper.commission(a, 1, 100.0, db) == pytest.approx(1.0)       # minimum applies
    a.fx_fee_pct = 0.005
    assert paper.commission(a, 1, 1000.0, db) == pytest.approx(2.5 + 5.0)


def test_import_api_preview_commit_and_dedupe(monkeypatch):
    from app.api import portfolio_api
    ecb = pd.Series({d.date(): 1.09 for d in pd.date_range("2023-12-01", "2024-12-31")})
    monkeypatch.setattr(portfolio_api, "eurusd_history", lambda start: ecb)
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"email": "rev@example.com", "password": "password123"})
        pid = c.get("/api/portfolios").json()[0]["id"]
        prev = c.post(f"/api/portfolios/{pid}/import/revolut", json={"csv": CSV, "commit": False}).json()
        assert prev["counts"] == {"new": 7, "duplicate": 0, "skipped": 2, "error": 0}
        assert not prev["committed"] and c.get(f"/api/portfolios/{pid}/transactions").json() == []
        row = next(r for r in prev["rows"] if r["ticker"] == "MSFT")
        assert row["fx_rate"] == pytest.approx(0.9158)  # EUR per USD taken from the statement (direction resolved)
        done = c.post(f"/api/portfolios/{pid}/import/revolut", json={"csv": CSV, "commit": True}).json()
        assert done["imported"] == 7
        again = c.post(f"/api/portfolios/{pid}/import/revolut", json={"csv": CSV, "commit": False}).json()
        assert again["counts"]["duplicate"] == 7 and again["counts"]["new"] == 0
        an = c.get(f"/api/portfolios/{pid}/analytics").json()
        nvda = next(p for p in an["summary"]["positions"] if p["ticker"] == "NVDA")
        assert nvda["quantity"] == pytest.approx(10)  # 1 share + 9 from the 10:1 split
        assert an["income"]["dividends"] > 0 and an["income"]["other_fees"] > 0
