"""HTTP-level tests of the main user journeys (demo-mode data, no network)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        r = c.post("/api/auth/register", json={"email": "api@example.com", "password": "password123"})
        assert r.status_code == 200
        yield c


def test_auth_required():
    with TestClient(app) as c:
        assert c.get("/api/market/overview").status_code == 401
        assert c.post("/api/auth/login", json={"email": "api@example.com", "password": "wrong-pass"}).status_code == 401


def test_health_reports_demo(client):
    assert client.get("/api/health").json()["demo_mode"] is True


def test_stock_pages(client):
    s = client.get("/api/stocks/AAPL/summary").json()
    assert s["provenance"]["demo"] is True and "DEMO" in s["source"]
    h = client.get("/api/stocks/AAPL/history?range=6m").json()
    assert len(h["bars"]) > 100 and "rsi14" in h["bars"][-1]
    f = client.get("/api/stocks/AAPL/forecast").json()
    assert f["available"] and len(f["horizons"]) == 6
    for hz in f["horizons"]:
        assert 0 <= hz["p_up"] <= 1 and hz["price_range_90"][0] <= hz["price_range_80"][0] <= hz["price_range_80"][1] <= hz["price_range_90"][1]
    assert any("DEMO" in n for n in f["notes"])


def test_watchlist_and_alerts(client):
    wl = client.get("/api/watchlists").json()
    assert wl and wl[0]["items"]
    wid = wl[0]["id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"ticker": "TSLA"}).status_code == 200
    r = client.post("/api/alerts", json={"kind": "price_above", "ticker": "AAPL", "params": {"price": 0.01}})
    assert r.status_code == 200 and r.json()["triggered_now"]
    assert client.post("/api/alerts", json={"kind": "price_above", "ticker": "AAPL", "params": {}}).status_code == 400
    assert any("above your alert level" in n["message"] for n in client.get("/api/notifications").json())


def test_portfolio_flow(client):
    pid = client.get("/api/portfolios").json()[0]["id"]
    r = client.post(f"/api/portfolios/{pid}/transactions", json={"ticker": "AAPL", "side": "BUY", "quantity": 5, "price": 100,
                                                                  "currency": "EUR", "trade_date": "2024-03-01"})
    assert r.status_code == 200
    bad = client.post(f"/api/portfolios/{pid}/transactions", json={"ticker": "AAPL", "side": "SELL", "quantity": 50, "price": 100,
                                                                    "currency": "EUR", "trade_date": "2024-03-02"})
    assert bad.status_code == 400
    an = client.get(f"/api/portfolios/{pid}/analytics").json()
    assert an["summary"]["positions"][0]["quantity"] == 5
    assert "risk" in an and "stress" in an


def test_paper_validation(client):
    aid = client.get("/api/paper/accounts").json()[0]["id"]
    r = client.post(f"/api/paper/accounts/{aid}/orders", json={"ticker": "AAPL", "side": "SELL", "quantity": 1})
    assert r.status_code == 400 and "short" in r.json()["detail"].lower()


def test_analyst_chat(client):
    r = client.post("/api/analyst/chat", json={"message": "Confirm AAPL will definitely rise"}).json()
    assert {"confirmation_bias", "guaranteed_returns"} & {f["type"] for f in r["flags"]}
    assert "not configured" in r["llm_status"]


def test_other_users_cannot_access_portfolio(client):
    pid = client.get("/api/portfolios").json()[0]["id"]
    with TestClient(app) as c2:
        c2.post("/api/auth/register", json={"email": "other@example.com", "password": "password123"})
        assert c2.get(f"/api/portfolios/{pid}/analytics").status_code == 404


def test_model_eval_and_track_record_endpoints(client):
    assert client.get("/api/backtest/model-evaluation").status_code == 200
    tr = client.get("/api/track-record").json()
    assert "policy" in tr and "summary" in tr
