"""Alert evaluation. Each alert kind reads only real data; when the needed data
is unavailable the alert is skipped (and its state records why), never
triggered on guesses."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data import fundamentals_store as fs
from ..data import market
from ..data.http import ProviderError
from ..models import Alert, Notification, utcnow
from .indicators import compute_all

log = logging.getLogger("oik.alerts")

KINDS = {
    "price_above": {"params": {"price": "number"}, "label": "Price rises above"},
    "price_below": {"params": {"price": "number"}, "label": "Price falls below"},
    "pct_change": {"params": {"pct": "number"}, "label": "Daily move exceeds +/- %"},
    "rsi_above": {"params": {"level": "number"}, "label": "RSI(14) crosses above"},
    "rsi_below": {"params": {"level": "number"}, "label": "RSI(14) crosses below"},
    "macd_cross": {"params": {"direction": "bullish|bearish"}, "label": "MACD signal-line cross"},
    "sma_cross": {"params": {"period": "50|200", "direction": "above|below"}, "label": "Price crosses SMA"},
    "earnings_within": {"params": {"days": "number"}, "label": "Earnings release within N days"},
    "unusual_volume": {"params": {"rvol": "number"}, "label": "Relative volume (vs 20d) above"},
    "news_sentiment": {"params": {"threshold": "number"}, "label": "Strongly positive/negative headline"},
    "prediction_change": {"params": {"horizon": "1D|7D|30D|90D|6M|12M", "change": "number"},
                          "label": "Forecast P(up) changes by more than"},
    "portfolio_risk": {"params": {"portfolio_id": "number", "metric": "var_pct|drawdown|max_weight", "threshold": "number"},
                       "label": "Portfolio risk threshold breached"},
}


def _notify(db: Session, a: Alert, msg: str, data: dict | None = None) -> Notification:
    n = Notification(user_id=a.user_id, alert_id=a.id, kind=a.kind, ticker=a.ticker, message=msg, data=data or {})
    db.add(n)
    a.last_triggered_at = utcnow()
    if not a.repeat:
        a.active = False
    return n


def _cross(prev, cur, level, up: bool) -> bool:
    if prev is None or cur is None:
        return False
    return (prev < level <= cur) if up else (prev > level >= cur)


def evaluate(db: Session, a: Alert) -> Notification | None:
    p = a.params or {}
    st = dict(a.state or {})
    a.last_checked_at = utcnow()
    if a.repeat and a.last_triggered_at and utcnow() - a.last_triggered_at < timedelta(hours=20):
        return None
    t = a.ticker
    try:
        if a.kind in ("price_above", "price_below", "pct_change"):
            q = market.quote(t)
            px = q["price"]
            if a.kind == "price_above" and px >= float(p["price"]):
                return _notify(db, a, f"{t} at ${px:,.2f} is above your alert level ${float(p['price']):,.2f}", {"price": px, "source": q["source"]})
            if a.kind == "price_below" and px <= float(p["price"]):
                return _notify(db, a, f"{t} at ${px:,.2f} is below your alert level ${float(p['price']):,.2f}", {"price": px, "source": q["source"]})
            if a.kind == "pct_change" and q.get("change_pct") is not None and abs(q["change_pct"]) >= float(p["pct"]):
                return _notify(db, a, f"{t} moved {q['change_pct']:+.2f}% today (threshold {float(p['pct'])}%)", {"change_pct": q["change_pct"]})
            return None
        if a.kind in ("rsi_above", "rsi_below", "macd_cross", "sma_cross", "unusual_volume"):
            df = market.bars(t)
            ind = compute_all(df)
            last_d = str(df.index[-1])
            if st.get("last_bar") == last_d:
                return None
            st["last_bar"] = last_d
            a.state = st
            cur, prev = ind.iloc[-1], ind.iloc[-2]
            if a.kind == "rsi_above" and _cross(prev["rsi14"], cur["rsi14"], float(p["level"]), True):
                return _notify(db, a, f"{t} RSI(14) crossed above {p['level']} ({cur['rsi14']:.1f}) on {last_d}")
            if a.kind == "rsi_below" and _cross(prev["rsi14"], cur["rsi14"], float(p["level"]), False):
                return _notify(db, a, f"{t} RSI(14) crossed below {p['level']} ({cur['rsi14']:.1f}) on {last_d}")
            if a.kind == "macd_cross":
                bull = prev["hist"] <= 0 < cur["hist"]
                bear = prev["hist"] >= 0 > cur["hist"]
                if (p.get("direction", "bullish") == "bullish" and bull) or (p.get("direction") == "bearish" and bear):
                    return _notify(db, a, f"{t} MACD {p.get('direction', 'bullish')} signal-line cross on {last_d}")
            if a.kind == "sma_cross":
                n = int(p.get("period", 50))
                c, cp = df["adj_close"].iloc[-1], df["adj_close"].iloc[-2]
                s, sp = cur[f"sma{n}"], prev[f"sma{n}"]
                up = cp <= sp and c > s
                down = cp >= sp and c < s
                if (p.get("direction", "above") == "above" and up) or (p.get("direction") == "below" and down):
                    return _notify(db, a, f"{t} closed {p.get('direction', 'above')} its {n}-day SMA on {last_d}")
            if a.kind == "unusual_volume" and cur["rvol20"] >= float(p.get("rvol", 2)):
                return _notify(db, a, f"{t} traded {cur['rvol20']:.1f}x its 20-day average volume on {last_d}")
            return None
        if a.kind == "earnings_within":
            cal = fs.earnings_calendar(int(p.get("days", 7)) + 1)["value"]
            hit = next((e for e in cal if e["ticker"] == t), None)
            if hit and st.get("notified_for") != hit["date"]:
                st["notified_for"] = hit["date"]
                a.state = st
                return _notify(db, a, f"{t} reports earnings on {hit['date']} ({hit['time']}); consensus EPS {hit.get('eps_forecast')}", hit)
            return None
        if a.kind == "news_sentiment":
            items = fs.news_env(t)["value"]
            seen = set(st.get("seen", []))
            thr = float(p.get("threshold", 0.5))
            for it in items:
                key = it["title"][:80]
                if key in seen or it.get("suspicious"):
                    continue
                seen.add(key)
                if abs(it["sentiment"]) >= thr:
                    st["seen"] = list(seen)[-200:]
                    a.state = st
                    tone = "positive" if it["sentiment"] > 0 else "negative"
                    return _notify(db, a, f"{t}: strongly {tone} headline ({it['sentiment']:+.2f}) - {it['title']}", {"url": it.get("url")})
            st["seen"] = list(seen)[-200:]
            a.state = st
            return None
        if a.kind == "prediction_change":
            from .forecast import forecast
            h = p.get("horizon", "30D")
            fc = forecast(t, n_paths=1500, include_paths=False)
            if not fc.get("available"):
                return None
            cur = next(x for x in fc["horizons"] if x["horizon"] == h)["p_up"]
            prev = st.get("p_up")
            st["p_up"] = cur
            st["as_of"] = fc["as_of"]
            a.state = st
            if prev is not None and abs(cur - prev) >= float(p.get("change", 0.1)):
                return _notify(db, a, f"{t} {h} forecast P(up) moved from {prev:.0%} to {cur:.0%}", {"from": prev, "to": cur})
            return None
        if a.kind == "portfolio_risk":
            from ..api.portfolio_api import compute_analytics
            from ..models import Portfolio
            pf = db.get(Portfolio, int(p["portfolio_id"]))
            if pf is None or pf.user_id != a.user_id:
                return None
            an = compute_analytics(db, pf)
            metric, thr = p.get("metric", "var_pct"), float(p.get("threshold", 0.03))
            val = None
            if metric == "var_pct":
                val = (an.get("risk") or {}).get("var_pct_1d_95")
            elif metric == "drawdown":
                val = -((an.get("performance") or {}).get("max_drawdown") or 0)
            elif metric == "max_weight":
                pos = an.get("summary", {}).get("positions", [])
                val = max((x["weight"] for x in pos), default=None)
            if val is not None and val >= thr:
                return _notify(db, a, f"Portfolio '{pf.name}': {metric} = {val:.2%} breached threshold {thr:.2%}", {"value": val})
            return None
    except (ProviderError, KeyError, ValueError, StopIteration) as exc:
        st["last_error"] = str(exc)[:200]
        a.state = st
        return None
    return None


def run_all(db: Session) -> list[Notification]:
    out = []
    for a in db.execute(select(Alert).where(Alert.active.is_(True))).scalars().all():
        n = evaluate(db, a)
        if n is not None:
            out.append(n)
    db.commit()
    return out


_ = (date, datetime)
