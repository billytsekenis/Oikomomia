"""Gather universe data and train/validate the prediction engine."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from ..data import fundamentals_store as fs
from ..data import market
from ..data.http import ProviderError
from ..universe import STOCK_TICKERS
from . import model_engine
from .context import get_market_context, reset_cache, save_model

log = logging.getLogger("oik.train")


def gather(tickers: list[str] | None = None, progress=None) -> tuple[dict, dict]:
    tickers = tickers or STOCK_TICKERS[: get_settings().train_max_tickers]

    def facts(t):
        try:
            return t, fs.company_facts_for(t)
        except ProviderError:
            return t, None

    with ThreadPoolExecutor(max_workers=3) as ex:
        fmap = dict(ex.map(facts, tickers))
    if progress:
        progress(0.05, "fundamentals loaded")
    bars = market.bars_many(tickers, workers=3)
    if progress:
        progress(0.1, f"prices loaded for {len(bars)} tickers")
    return bars, fmap


def train(progress=None) -> dict:
    bars, facts = gather(progress=progress)
    reset_cache()
    ctx = get_market_context(max_age=0)
    res = model_engine.run_walkforward(bars, ctx["mf"], facts,
                                       progress=(lambda f, m: progress(0.1 + 0.85 * f, m)) if progress else None)
    save_model(res)
    reset_cache()
    return {"version": res.version, "seconds": res.seconds, "tickers": len(res.tickers),
            "weights": res.weights, "verdicts": {h: m.get("verdict") for h, m in res.metrics.items()}}
