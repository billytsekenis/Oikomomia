"""Stock Discovery Engine - transparent multi-factor scores (0-100).

Every factor score is the cross-sectional percentile rank (0-100) of the
underlying metric within the analysed universe, so 70 means "better than 70%
of the universe on this factor". Category scores are fixed, documented
weighted averages of factor scores and rule-based technical conditions
(METHODOLOGY below). Missing inputs are not imputed with fake values: the
weights of available inputs are re-normalised and coverage is reported.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..data import fundamentals_store as fs
from ..data import market
from ..data.http import ProviderError
from ..universe import STOCK_TICKERS, UNIVERSE
from . import fundamentals as fa
from . import model_engine
from . import volatility as volm
from .context import finite, get_market_context, load_model, model_metrics
from .features import ALL_FEATURES, HORIZONS, latest_feature_row
from .indicators import compute_all, max_drawdown

log = logging.getLogger("oik.scoring")

METHODOLOGY = {
    "factor_scores": {
        "value": "Avg percentile of earnings yield, FCF yield, EBITDA/EV and sales/price (higher = cheaper).",
        "growth": "Avg percentile of TTM revenue growth YoY, TTM net income growth YoY, 3y revenue CAGR, forward EPS growth.",
        "quality": "Avg percentile of ROE, operating margin, FCF margin, low debt/equity, interest coverage.",
        "momentum": "Avg percentile of 12-1 month return, 6-month return, distance above 200-day SMA, 3-month return relative to SPY.",
        "low_risk": "Avg percentile of low 63-day volatility, low beta, shallow 1-year max drawdown.",
        "technical": "Avg of RSI-trend score, MACD histogram sign/slope and price vs 20/50-day SMAs (0-100).",
        "forecast": "Percentile of expected return / sigma for the chosen horizon. Weight is set to zero when the "
                    "walk-forward validation shows no out-of-sample skill for that horizon.",
    },
    "categories": {
        "undervalued": "0.60 value + 0.25 quality + 0.15 (100 - overextension)",
        "growth": "0.60 growth + 0.20 quality + 0.20 momentum",
        "momentum": "0.70 momentum + 0.30 technical",
        "breakout": "Within 5% of 52-week high: 0.4 proximity + 0.35 volatility squeeze (low Bollinger bandwidth vs own year) + 0.25 relative volume",
        "oversold": "RSI(14) <= 35 or %B < 0.05: 0.5 (oversold depth) + 0.25 long-term trend intact + 0.25 quality",
        "reversal": "Price was below the 50-day SMA and MACD histogram turned positive within 5 sessions: 0.4 MACD turn + 0.3 RSI recovery from <40 + 0.3 value",
        "earnings": "Earnings within 14 days: 0.5 historical beat rate + 0.5 size of historical post-earnings moves (catalyst magnitude, not direction)",
        "high_risk_high_reward": "0.5 volatility percentile + 0.5 max(growth, momentum)",
        "overvalued_risk": "0.4 (100 - value) + 0.2 overbought RSI + 0.2 extension above 200-day SMA + 0.2 leverage",
    },
    "composite": {
        "short": "0.35 technical + 0.35 momentum + 0.30 forecast(30D)",
        "medium": "0.30 momentum + 0.25 value + 0.25 quality + 0.20 forecast(90D)",
        "long": "0.35 value + 0.30 quality + 0.20 growth + 0.15 forecast(12M)",
    },
    "filters": {"risk": "low: 63d vol < 25%, medium: 25-40%, high: > 40%",
                "cap": "mega > $200B, large $10-200B, mid $2-10B, small < $2B"},
    "caveats": [
        "Universe = fixed list of ~105 US large caps (survivorship bias: today's winners).",
        "Scores are relative rankings, not predictions of absolute returns.",
        "Fundamentals are trailing (SEC filings); forward data only where analyst consensus exists (Nasdaq).",
    ],
}

HORIZON_FOR = {"short": "30D", "medium": "90D", "long": "12M"}


def _pct(s: pd.Series, higher_better: bool = True) -> pd.Series:
    r = s.rank(pct=True) * 100
    return r if higher_better else 100 - r + (100 / max(s.notna().sum(), 1))


def _wavg(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    num = den = 0.0
    tot = sum(w for _, w in parts)
    for v, w in parts:
        if v is not None and np.isfinite(v):
            num += v * w
            den += w
    return (num / den if den > 0 else None), (den / tot if tot else 0)


def _earnings_moves(df: pd.DataFrame, dates: list[str]) -> float | None:
    """Mean absolute close-to-close move around past earnings release dates (8-K item 2.02)."""
    c = df["adj_close"]
    moves = []
    for d in dates[-12:]:
        dd = date.fromisoformat(d)
        pos = c.index.searchsorted(dd)
        if 1 <= pos < len(c) - 1:
            # releases are often pre-market (move on day) or after close (move next day): take the larger
            m1 = abs(c.iloc[pos] / c.iloc[pos - 1] - 1)
            m2 = abs(c.iloc[pos + 1] / c.iloc[pos] - 1)
            moves.append(max(m1, m2))
    return float(np.mean(moves)) if len(moves) >= 3 else None


def stock_row(t: str, ctx: dict, art: dict | None, weights: dict, earn_map: dict) -> dict | None:
    try:
        df = market.bars(t)
    except ProviderError:
        return None
    if len(df) < 260:
        return None
    ind = compute_all(df)
    last = ind.iloc[-1]
    c = df["adj_close"]
    price = float(df["close"].iloc[-1])
    lr = np.log(c).diff()
    spy = ctx["spy"]["adj_close"].reindex(df.index).ffill()
    cf = None
    try:
        cf = fs.company_facts_for(t)
    except ProviderError:
        pass
    fc_env = fs.nasdaq_env("forecast", t, ttl=3 * 86400)
    summ = fs.nasdaq_env("summary", t, ttl=7 * 86400)
    hint = ((summ or {}).get("value") or {}).get("market_cap")
    snap = fa.snapshot(cf, price, forecast=fc_env["value"] if fc_env else None, sector=UNIVERSE[t]["sector"],
                       market_cap_hint=hint) if cf else {}
    m_ret = ctx["mf"]["mkt_ret"].reindex(df.index)
    j = pd.concat([lr, m_ret], axis=1).dropna().tail(252)
    beta = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var()) if len(j) > 60 else None
    vol63 = float(lr.tail(63).std() * np.sqrt(252))
    # quick forecast (analytic, no Monte Carlo) for all horizons
    fc = {}
    try:
        vm = volm.fit(lr.dropna())
        mf = ctx["mf"]
        rf = finite(mf["rf"].dropna().iloc[-1]) if mf["rf"].notna().any() else 0.0
        feat = latest_feature_row(df, mf, cf, art.get("reference") if art else None)
        ml_ok = art is not None and feat[ALL_FEATURES].notna().sum(axis=1).iloc[0] >= 0.7 * len(ALL_FEATURES)
        for h, n in HORIZONS.items():
            s = float(vm.horizon_sigma(n))
            mu = float(model_engine.prior_mu(np.array([rf]), np.array([beta or 1.0]), np.array([s]), n)[0])
            if ml_ok and h in art["final_models"]:
                X = feat[ALL_FEATURES].astype(float).values
                ml = 0.5 * (art["final_models"][h]["ridge"].predict(X)[0] + art["final_models"][h]["gbm"].predict(X)[0])
                mu += float(art["weights"].get(h, 0)) * float(ml)
            fc[h] = {"mu": mu, "sigma": s, "p_up": float(1 - norm.cdf(-mu / s)), "exp_ret": float(math.exp(mu + s * s / 2) - 1)}
    except Exception as exc:  # noqa: BLE001
        log.info("quick forecast failed for %s: %s", t, exc)
    bw = ind["bb_width"].tail(252)
    squeeze_pct = float((bw.iloc[-1] <= bw).mean() * 100) if bw.notna().sum() > 50 else None
    hist_macd = ind["hist"].tail(8).values
    macd_turn = bool(len(hist_macd) >= 6 and np.nanmin(hist_macd[:-1]) < 0 and hist_macd[-1] > 0
                     and any(hist_macd[i] <= 0 < hist_macd[i + 1] for i in range(len(hist_macd) - 6, len(hist_macd) - 1)))
    rsi_series = ind["rsi14"].tail(15)
    was_below_50 = bool((c.tail(20) < ind["sma50"].tail(20)).iloc[:-5].any())
    subs = fs.submissions_env(t)
    earn_dates = (subs or {}).get("value", {}).get("earnings_release_dates", []) if subs else []
    surprise_env = fs.nasdaq_env("surprise", t, ttl=3 * 86400)
    surprises = (surprise_env or {}).get("value") or []
    beats = [s for s in surprises if s.get("surprise_pct") is not None]
    beat_rate = (sum(1 for s in beats if s["surprise_pct"] > 0) / len(beats)) if beats else None
    mcap = snap.get("market_cap")
    if mcap is None:
        summ = fs.nasdaq_env("summary", t, ttl=7 * 86400)
        mcap = ((summ or {}).get("value") or {}).get("market_cap")
    return {
        "ticker": t, "name": UNIVERSE[t]["name"], "sector": UNIVERSE[t]["sector"], "industry": UNIVERSE[t]["industry"],
        "price": price, "as_of": str(df.index[-1]), "source": df.attrs.get("source"),
        "ret_1d": float(c.iloc[-1] / c.iloc[-2] - 1), "ret_1m": float(c.iloc[-1] / c.iloc[-22] - 1),
        "ret_3m": float(c.iloc[-1] / c.iloc[-64] - 1), "ret_6m": float(c.iloc[-1] / c.iloc[-127] - 1),
        "ret_12_1": float(c.iloc[-22] / c.iloc[-253] - 1) if len(c) > 253 else None,
        "rel_3m": float((c.iloc[-1] / c.iloc[-64]) / (spy.iloc[-1] / spy.iloc[-64]) - 1) if spy.notna().iloc[-64] else None,
        "dist_sma200": finite(c.iloc[-1] / last["sma200"] - 1), "dist_sma50": finite(c.iloc[-1] / last["sma50"] - 1),
        "dist_sma20": finite(c.iloc[-1] / last["sma20"] - 1),
        "rsi14": finite(last["rsi14"]), "macd_hist": finite(last["hist"]),
        "macd_hist_slope": finite(ind["hist"].iloc[-1] - ind["hist"].iloc[-4]),
        "pct_b": finite(last["bb_pctb"]), "rvol20": finite(last["rvol20"]), "vol_z": finite(last["vol_z"]),
        "hi52_dist": finite(c.iloc[-1] / last["hi52"] - 1), "squeeze_pct": squeeze_pct,
        "vol63": vol63, "beta": beta, "max_dd_1y": max_drawdown(c.tail(252)),
        "macd_turn_up": macd_turn, "was_below_sma50": was_below_50,
        "rsi_min_15d": finite(rsi_series.min()),
        "market_cap": mcap,
        "earnings_yield": snap.get("earnings_yield"), "fcf_yield": snap.get("fcf_yield"),
        "ebitda_ev": (1 / snap["ev_ebitda"]) if snap.get("ev_ebitda") else None,
        "sales_yield": (1 / snap["ps_ttm"]) if snap.get("ps_ttm") else None,
        "pe_ttm": snap.get("pe_ttm"), "forward_pe": snap.get("forward_pe"), "ev_ebitda": snap.get("ev_ebitda"),
        "revenue_growth": snap.get("revenue_growth_yoy"), "ni_growth": snap.get("net_income_growth_yoy"),
        "revenue_cagr_3y": snap.get("revenue_cagr_3y"), "eps_growth_fwd": snap.get("eps_growth_forward"),
        "roe": snap.get("roe"), "operating_margin": snap.get("operating_margin"), "fcf_margin": snap.get("fcf_margin"),
        "debt_to_equity": snap.get("debt_to_equity"), "interest_coverage": snap.get("interest_coverage"),
        "earnings_next": earn_map.get(t), "beat_rate": beat_rate,
        "earnings_move": _earnings_moves(df, earn_dates) if earn_dates else None,
        "forecast": fc,
    }


def build_snapshot(tickers: list[str] | None = None, workers: int = 3) -> dict:
    tickers = tickers or STOCK_TICKERS
    ctx = get_market_context()
    art = load_model()
    metrics = (model_metrics() or {}).get("metrics", {})
    try:
        earn = fs.earnings_calendar(14)["value"]
    except ProviderError:
        earn = []
    earn_map = {}
    for e in earn:
        if e.get("ticker") in UNIVERSE and e["ticker"] not in earn_map:
            earn_map[e["ticker"]] = {"date": e["date"], "time": e["time"], "eps_forecast": e.get("eps_forecast")}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = [r for r in ex.map(lambda t: stock_row(t, ctx, art, {}, earn_map), tickers) if r]
    df = pd.DataFrame(rows)
    if df.empty:
        return {"rows": [], "generated_at": datetime.utcnow().isoformat() + "Z"}
    # ---------------- factor percentiles
    P = pd.DataFrame(index=df.index)
    val = pd.concat([_pct(df[c]) for c in ("earnings_yield", "fcf_yield", "ebitda_ev", "sales_yield")], axis=1)
    P["value"] = val.mean(axis=1, skipna=True)
    P["value_cov"] = val.notna().mean(axis=1)
    gr = pd.concat([_pct(df[c]) for c in ("revenue_growth", "ni_growth", "revenue_cagr_3y", "eps_growth_fwd")], axis=1)
    P["growth"] = gr.mean(axis=1, skipna=True)
    q = pd.concat([_pct(df["roe"]), _pct(df["operating_margin"]), _pct(df["fcf_margin"]),
                   _pct(df["debt_to_equity"], False), _pct(df["interest_coverage"])], axis=1)
    P["quality"] = q.mean(axis=1, skipna=True)
    mo = pd.concat([_pct(df["ret_12_1"]), _pct(df["ret_6m"]), _pct(df["dist_sma200"]), _pct(df["rel_3m"])], axis=1)
    P["momentum"] = mo.mean(axis=1, skipna=True)
    lr_ = pd.concat([_pct(df["vol63"], False), _pct(df["beta"], False), _pct(df["max_dd_1y"])], axis=1)
    P["low_risk"] = lr_.mean(axis=1, skipna=True)
    rsi = df["rsi14"]
    rsi_score = np.where(rsi.between(50, 70), 70 + (rsi - 50), np.where(rsi > 70, 90 - (rsi - 70) * 2, rsi))
    macd_score = np.where(df["macd_hist"] > 0, 60, 30) + np.where(df["macd_hist_slope"] > 0, 30, 0)
    sma_score = (np.where(df["dist_sma20"] > 0, 50, 0) + np.where(df["dist_sma50"] > 0, 50, 0))
    P["technical"] = (pd.Series(rsi_score, index=df.index) + macd_score + sma_score) / 3
    for hz, h in HORIZON_FOR.items():
        sharpe = df["forecast"].map(lambda f, h=h: (f[h]["mu"] / f[h]["sigma"]) if f and h in f else np.nan)
        P[f"forecast_{hz}"] = _pct(sharpe)
    P["volatility_pct"] = _pct(df["vol63"])
    ext = _pct(df["dist_sma200"])
    lev = _pct(df["debt_to_equity"])
    # ---------------- categories
    out_rows = []
    for i, r in df.iterrows():
        p = P.loc[i]
        cats = {}
        cats["undervalued"] = _wavg([(p["value"], .6), (p["quality"], .25), (100 - ext[i] if np.isfinite(ext[i]) else None, .15)])[0]
        cats["growth"] = _wavg([(p["growth"], .6), (p["quality"], .2), (p["momentum"], .2)])[0]
        cats["momentum"] = _wavg([(p["momentum"], .7), (p["technical"], .3)])[0]
        if r["hi52_dist"] is not None and r["hi52_dist"] > -0.05:
            prox = 100 * (1 + r["hi52_dist"] / 0.05)
            sq = (100 - r["squeeze_pct"]) if r["squeeze_pct"] is not None else None
            rv = min(100, max(0, (r["rvol20"] or 0) * 50)) if r["rvol20"] is not None else None
            cats["breakout"] = _wavg([(prox, .4), (sq, .35), (rv, .25)])[0]
        if (r["rsi14"] is not None and r["rsi14"] <= 35) or (r["pct_b"] is not None and r["pct_b"] < 0.05):
            depth = min(100, max(0, (40 - (r["rsi14"] or 40)) * 5 + (50 if (r["pct_b"] or 1) < 0.05 else 0)))
            trend = 100 if (r["dist_sma200"] or -1) > 0 else 20
            cats["oversold"] = _wavg([(depth, .5), (trend, .25), (p["quality"], .25)])[0]
        if r["macd_turn_up"] and r["was_below_sma50"]:
            rec = 100 if (r["rsi_min_15d"] or 50) < 40 and (r["rsi14"] or 0) > (r["rsi_min_15d"] or 0) + 5 else 40
            cats["reversal"] = _wavg([(100, .4), (rec, .3), (p["value"], .3)])[0]
        if r["earnings_next"]:
            mv = min(100, (r["earnings_move"] or 0) / 0.08 * 100) if r["earnings_move"] is not None else None
            br = r["beat_rate"] * 100 if r["beat_rate"] is not None else None
            cats["earnings"] = _wavg([(br, .5), (mv, .5)])[0]
        cats["high_risk_high_reward"] = _wavg([(p["volatility_pct"], .5), (max(p["growth"] if np.isfinite(p["growth"]) else 0, p["momentum"] if np.isfinite(p["momentum"]) else 0), .5)])[0]
        ob = min(100, max(0, ((r["rsi14"] or 50) - 50) * 3.3))
        cats["overvalued_risk"] = _wavg([(100 - p["value"] if np.isfinite(p["value"]) else None, .4), (ob, .2),
                                         (ext[i] if np.isfinite(ext[i]) else None, .2), (lev[i] if np.isfinite(lev[i]) else None, .2)])[0]
        composite = {}
        for hz, h in HORIZON_FOR.items():
            skill = (metrics.get(h) or {})
            has_skill = bool(skill) and ((skill.get("brier_skill_score") or 0) > 0 or
                                         (((skill.get("models") or {}).get("ensemble") or {}).get("ic_tstat_overlap_adj") or 0) > 2)
            fw = 0.30 if hz == "short" else 0.20 if hz == "medium" else 0.15
            f = p[f"forecast_{hz}"] if has_skill else None
            if hz == "short":
                parts = [(p["technical"], .35), (p["momentum"], .35), (f, fw)]
            elif hz == "medium":
                parts = [(p["momentum"], .30), (p["value"], .25), (p["quality"], .25), (f, fw)]
            else:
                parts = [(p["value"], .35), (p["quality"], .30), (p["growth"], .20), (f, fw)]
            score, cov = _wavg(parts)
            composite[hz] = {"score": score, "coverage": cov, "forecast_used": has_skill}
        vol = r["vol63"]
        risk = "low" if vol < 0.25 else "medium" if vol < 0.40 else "high"
        mc = r["market_cap"]
        cap = None if mc is None else "mega" if mc > 2e11 else "large" if mc > 1e10 else "mid" if mc > 2e9 else "small"
        clean = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()}
        out_rows.append({**clean,
                         "factors": {k: finite(p[k]) for k in ("value", "growth", "quality", "momentum", "low_risk", "technical",
                                                               "forecast_short", "forecast_medium", "forecast_long")},
                         "fundamental_coverage": finite(p["value_cov"]),
                         "categories": {k: (round(v, 1) if v is not None else None) for k, v in cats.items()},
                         "composite": composite, "risk_level": risk, "cap_bucket": cap})
    return {"rows": out_rows, "generated_at": datetime.utcnow().isoformat() + "Z", "n": len(out_rows),
            "methodology": METHODOLOGY,
            "forecast_skill": {h: (metrics.get(h) or {}).get("verdict") for h in HORIZONS}}


def explain(row: dict, category: str) -> list[str]:
    """Human-readable reasons drawn only from the row's actual numbers."""
    f = row.get("factors", {})
    why = []
    fmt = lambda v: "n/a" if v is None else f"{v:.0f}"  # noqa: E731
    if category in ("undervalued", "overvalued_risk"):
        why.append(f"Value percentile {fmt(f.get('value'))}; P/E {row['pe_ttm']:.1f}" if row.get("pe_ttm") else f"Value percentile {fmt(f.get('value'))}")
        if row.get("fcf_yield") is not None:
            why.append(f"FCF yield {row['fcf_yield'] * 100:.1f}%")
    if category in ("growth", "high_risk_high_reward"):
        if row.get("revenue_growth") is not None:
            why.append(f"Revenue growth YoY {row['revenue_growth'] * 100:.1f}%")
    if category in ("momentum", "breakout", "high_risk_high_reward"):
        if row.get("ret_12_1") is not None:
            why.append(f"12-1M return {row['ret_12_1'] * 100:.1f}%; {row['hi52_dist'] * 100:.1f}% from 52w high")
    if category in ("oversold", "reversal"):
        why.append(f"RSI(14) {row['rsi14']:.0f} (15-day min {row['rsi_min_15d']:.0f}); %B {row['pct_b']:.2f}")
    if category == "earnings" and row.get("earnings_next"):
        e = row["earnings_next"]
        mv = f"; avg post-earnings move {row['earnings_move'] * 100:.1f}%" if row.get("earnings_move") else ""
        br = f"; beat rate {row['beat_rate'] * 100:.0f}%" if row.get("beat_rate") is not None else ""
        why.append(f"Reports {e['date']} ({e['time']}){mv}{br}")
    why.append(f"63-day volatility {row['vol63'] * 100:.0f}%, beta {row['beta']:.2f}" if row.get("beta") else f"63-day volatility {row['vol63'] * 100:.0f}%")
    return why
