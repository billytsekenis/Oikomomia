"""Data access for the analyst. Every method returns real engine output (or
None when unavailable). Tests substitute a fake toolkit with fixed data."""
from __future__ import annotations

import logging

from ..data import fundamentals_store as fs
from ..data import market
from ..data.http import ProviderError
from ..universe import UNIVERSE

log = logging.getLogger("oik.analyst")


class Toolkit:
    def __init__(self, db=None, user=None):
        self.db, self.user = db, user
        self._snap = None

    def known_ticker(self, t: str) -> bool:
        if t in UNIVERSE:
            return True
        try:
            return t in fs.ticker_map()
        except ProviderError:
            return False

    def name_map(self) -> dict[str, str]:
        out = {}
        for t, v in UNIVERSE.items():
            if v["type"] == "stock":
                out[v["name"].lower()] = t
                first = v["name"].split()[0].lower().strip(",.'")
                if len(first) > 3:
                    out.setdefault(first, t)
        return out

    def quote(self, t):
        try:
            return market.quote(t)
        except ProviderError:
            return None

    def stock(self, t):
        from ..api.stock_api import stock_summary
        try:
            return stock_summary(t)
        except (ProviderError, ValueError, KeyError) as exc:
            log.info("stock summary failed %s: %s", t, exc)
            return None

    def forecast(self, t):
        from ..analytics.forecast import compact, forecast
        try:
            return compact(forecast(t, n_paths=2000, include_paths=False))
        except (ProviderError, ValueError) as exc:
            log.info("forecast failed %s: %s", t, exc)
            return None

    def discovery(self):
        from ..api.discovery_api import get_snapshot
        if self._snap is None:
            try:
                self._snap = get_snapshot()
            except ProviderError:
                self._snap = None
        return self._snap

    def regime(self):
        from ..api.market_api import regime_payload
        try:
            return regime_payload()
        except ProviderError:
            return None

    def news(self, t):
        try:
            items = fs.news_env(t)["value"]
        except ProviderError:
            return []
        return items

    def model_skill(self):
        from ..analytics.context import model_metrics
        m = model_metrics() or {}
        return {h: {"verdict": v.get("verdict"), "brier_skill_score": v.get("brier_skill_score"),
                    "coverage_90": v.get("coverage_90")} for h, v in (m.get("metrics") or {}).items()}

    def portfolio_stress(self, shock_factor: str, shock: float):
        if self.db is None or self.user is None:
            return None
        from sqlalchemy import select

        from ..api.portfolio_api import compute_analytics
        from ..models import Portfolio
        pf = self.db.execute(select(Portfolio).where(Portfolio.user_id == self.user.id).order_by(Portfolio.id)).scalars().first()
        if pf is None:
            return None
        an = compute_analytics(self.db, pf, custom_shock=(shock_factor, shock))
        return {"portfolio": pf.name, "base_currency": pf.base_currency, "value": an["summary"]["market_value"],
                "custom_shock": an.get("custom_shock"), "positions": [
                    {"ticker": p["ticker"], "weight": p["weight"]} for p in an["summary"]["positions"]]}
