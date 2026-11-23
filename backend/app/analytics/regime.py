"""Market regime, market state (bullish/bearish/neutral) and Risk Appetite Index.

Regime = rule-based trend/volatility/breadth/credit/curve signals + a 2-state
Markov-switching model (Hamilton, 1989; statsmodels MarkovRegression with
switching mean and variance) on weekly S&P 500 (SPY) log returns, giving the
filtered probability of the high-volatility state.
"""
from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

from ..data import market
from ..data.http import ProviderError
from .context import finite, get_market_context

log = logging.getLogger("oik.regime")


def _pctile(s: pd.Series, window: int = 252) -> float | None:
    s = s.dropna().tail(window)
    if len(s) < 30:
        return None
    return float((s <= s.iloc[-1]).mean() * 100)


def markov_regime(spy: pd.DataFrame) -> dict:
    try:
        from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
        w = np.log(spy["adj_close"]).groupby(pd.Index([d.isocalendar()[:2] for d in spy.index])).last().diff().dropna() * 100
        w = w.tail(520)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = MarkovRegression(w.values, k_regimes=2, trend="c", switching_variance=True)
            res = mod.fit(disp=False, maxiter=200)
        prm = dict(zip(mod.param_names, np.asarray(res.params)))
        sig = [float(prm[f"sigma2[{i}]"]) for i in range(2)]
        hi = int(np.argmax(sig))
        p = res.filtered_marginal_probabilities
        p_hi = float(p[-1, hi]) if p.ndim == 2 else float(p[hi][-1])
        const = [float(prm[f"const[{i}]"]) for i in range(2)]
        return {"available": True, "p_high_vol_state": p_hi,
                "states": {"low_vol": {"weekly_mean_pct": const[1 - hi], "weekly_vol_pct": float(np.sqrt(sig[1 - hi]))},
                           "high_vol": {"weekly_mean_pct": const[hi], "weekly_vol_pct": float(np.sqrt(sig[hi]))}},
                "n_weeks": int(len(w)), "method": "2-state Markov switching (mean & variance), weekly SPY log returns, ~10y"}
    except Exception as exc:  # noqa: BLE001
        log.info("markov regime failed: %s", exc)
        return {"available": False, "reason": str(exc)}


def breadth(bars: dict[str, pd.DataFrame]) -> dict:
    above50 = above200 = n = adv = dec = highs = lows = 0
    for t, df in bars.items():
        c = df["adj_close"]
        if len(c) < 252:
            continue
        n += 1
        above50 += int(c.iloc[-1] > c.tail(50).mean())
        above200 += int(c.iloc[-1] > c.tail(200).mean())
        adv += int(c.iloc[-1] > c.iloc[-2])
        dec += int(c.iloc[-1] < c.iloc[-2])
        highs += int(df["high"].iloc[-1] * (c.iloc[-1] / df["close"].iloc[-1]) >= (df["high"] * c / df["close"]).tail(252).max() * 0.999)
        lows += int(df["low"].iloc[-1] * (c.iloc[-1] / df["close"].iloc[-1]) <= (df["low"] * c / df["close"]).tail(252).min() * 1.001)
    if n == 0:
        return {"n": 0}
    return {"n": n, "pct_above_sma50": above50 / n * 100, "pct_above_sma200": above200 / n * 100,
            "advancers": adv, "decliners": dec, "new_52w_highs": highs, "new_52w_lows": lows}


def _breadth_history(bars: dict[str, pd.DataFrame], window: int = 50) -> pd.Series:
    frames = []
    for df in bars.values():
        c = df["adj_close"]
        if len(c) >= 300:
            frames.append((c > c.rolling(window).mean()).astype(float).where(c.rolling(window).mean().notna()))
    if not frames:
        return pd.Series(dtype=float)
    return pd.concat(frames, axis=1).mean(axis=1) * 100


def analyse(universe_bars: dict[str, pd.DataFrame]) -> dict:
    ctx = get_market_context()
    spy = ctx["spy"]
    c = spy["adj_close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    vix = ctx["vix"]
    signals = []

    def add(name, value, score, detail):
        signals.append({"name": name, "value": value, "score": float(np.clip(score, -1, 1)), "detail": detail})

    d200 = float(c.iloc[-1] / sma200.iloc[-1] - 1)
    add("Trend (SPY vs 200d SMA)", d200, np.tanh(d200 / 0.05), f"SPY {d200 * 100:+.1f}% vs 200-day SMA")
    cross = float(sma50.iloc[-1] / sma200.iloc[-1] - 1)
    add("50d vs 200d SMA", cross, np.tanh(cross / 0.03), "Golden-cross structure" if cross > 0 else "Death-cross structure")
    r63 = float(c.iloc[-1] / c.iloc[-64] - 1)
    add("3-month momentum", r63, np.tanh(r63 / 0.06), f"SPY 63-session return {r63 * 100:+.1f}%")
    vix_last = vix_pct = term = None
    if vix is not None and len(vix):
        vix_last = float(vix.iloc[-1])
        vix_pct = _pctile(vix)
        add("Volatility (VIX)", vix_last, (20 - vix_last) / 10, f"VIX {vix_last:.1f} ({vix_pct:.0f}th pct of 1y)")
        try:
            v3 = market.bars("^VIX3M")["close"]
            term = float(vix.iloc[-1] / v3.reindex(vix.index).ffill().iloc[-1])
            add("VIX term structure (VIX/VIX3M)", term, (1 - term) / 0.1,
                "Backwardation - near-term stress" if term > 1 else "Contango - normal")
        except ProviderError:
            pass
    br = breadth(universe_bars)
    if br.get("n"):
        add("Breadth (% above 50d SMA)", br["pct_above_sma50"], (br["pct_above_sma50"] - 50) / 30,
            f"{br['pct_above_sma50']:.0f}% of {br['n']} universe stocks above 50-day SMA")
    rates = ctx.get("rates")
    curve = None
    if rates is not None and "10Y" in rates and "3M" in rates:
        curve = float(rates["10Y"].dropna().iloc[-1] - rates["3M"].dropna().iloc[-1])
        add("Yield curve (10Y-3M)", curve, curve / 1.0, f"10Y-3M spread {curve:+.2f} pp" + (" (inverted)" if curve < 0 else ""))
    credit = None
    try:
        hyg, tlt = market.bars("HYG")["adj_close"], market.bars("TLT")["adj_close"]
        j = pd.concat([hyg, tlt], axis=1).dropna()
        credit = float((j.iloc[-1, 0] / j.iloc[-64, 0]) - (j.iloc[-1, 1] / j.iloc[-64, 1]))
        add("Credit (HYG vs TLT, 3m)", credit, credit / 0.04, "High-yield outperforming Treasuries" if credit > 0 else "High-yield lagging Treasuries")
    except (ProviderError, IndexError):
        pass
    weights = {"Trend (SPY vs 200d SMA)": 2, "3-month momentum": 1.5, "Volatility (VIX)": 1.5, "Breadth (% above 50d SMA)": 1.5}
    tot = sum(weights.get(s["name"], 1) for s in signals)
    score = sum(s["score"] * weights.get(s["name"], 1) for s in signals) / tot if tot else 0
    state = "bullish" if score > 0.25 else "bearish" if score < -0.25 else "neutral"
    mk = markov_regime(spy)
    trend_up = d200 > 0
    high_vol = (vix_last is not None and vix_last > 22) or (mk.get("available") and mk["p_high_vol_state"] > 0.6)
    if trend_up and not high_vol:
        regime = "Risk-on uptrend (low volatility)"
    elif trend_up and high_vol:
        regime = "Volatile uptrend / correction within bull trend"
    elif not trend_up and high_vol:
        regime = "Risk-off: bearish trend with high volatility"
    else:
        regime = "Grinding downtrend (low volatility)"
    if abs(d200) < 0.015:
        regime = "Transition: price near 200-day SMA - " + regime.split(":")[0].split("(")[0].strip().lower()
    return {"state": state, "score": score, "regime": regime, "signals": signals, "markov": mk, "breadth": br,
            "as_of": str(spy.index[-1]), "sources": {"spy": spy.attrs.get("source"),
                                                      "vix": ctx["vix_df"].attrs.get("source") if ctx.get("vix_df") is not None else None,
                                                      "rates": (ctx.get("rates_meta") or {}).get("source")},
            "method": "Weighted average of signal scores in [-1,1]; > +0.25 bullish, < -0.25 bearish."}


def risk_appetite(universe_bars: dict[str, pd.DataFrame]) -> dict:
    """0 = extreme fear, 100 = extreme greed. Each component is the percentile of
    today's value within its own trailing 1-year history (except breadth, which
    is already on a 0-100 scale)."""
    ctx = get_market_context()
    c = ctx["spy"]["adj_close"]
    comps = []
    mom = c / c.rolling(125).mean() - 1
    comps.append(("Market momentum (SPY vs 125d SMA)", _pctile(mom), finite(mom.iloc[-1])))
    if ctx["vix"] is not None:
        comps.append(("Volatility (VIX, inverted)", 100 - (_pctile(ctx["vix"]) or 50), finite(ctx["vix"].iloc[-1])))
    bh = _breadth_history(universe_bars)
    if len(bh):
        comps.append(("Breadth (% above 50d SMA)", finite(bh.iloc[-1]), finite(bh.iloc[-1])))
    try:
        tlt = market.bars("TLT")["adj_close"].reindex(c.index).ffill()
        sh = c.pct_change(20) - tlt.pct_change(20)
        comps.append(("Safe-haven demand (SPY - TLT, 20d)", _pctile(sh), finite(sh.iloc[-1])))
        hyg = market.bars("HYG")["adj_close"].reindex(c.index).ffill()
        jk = hyg.pct_change(20) - tlt.pct_change(20)
        comps.append(("Junk-bond demand (HYG - TLT, 20d)", _pctile(jk), finite(jk.iloc[-1])))
    except ProviderError:
        pass
    br = breadth(universe_bars)
    if br.get("n"):
        hl = (br["new_52w_highs"] - br["new_52w_lows"]) / br["n"]
        comps.append(("New highs vs lows", float(np.clip(50 + hl * 250, 0, 100)), hl))
    vals = [v for _, v, _ in comps if v is not None]
    idx = float(np.mean(vals)) if vals else None
    label = None
    if idx is not None:
        label = ("Extreme fear" if idx < 20 else "Fear" if idx < 40 else "Neutral" if idx <= 60 else "Greed" if idx <= 80 else "Extreme greed")
    return {"index": idx, "label": label,
            "components": [{"name": n, "score": s, "raw": r} for n, s, r in comps],
            "method": "Equal-weighted average of component percentiles vs trailing 1-year history.",
            "as_of": str(c.index[-1])}
