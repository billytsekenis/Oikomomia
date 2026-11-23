"""Strategy backtesting on walk-forward out-of-sample predictions.

Rules that prevent look-ahead:
  * signals at date t come from models trained only on data whose targets
    were realised before the fold start (see model_engine);
  * positions are entered at the close of the NEXT session (t+1) and earn
    returns from t+1 onward (one-day implementation lag);
  * transaction costs (bps of traded notional) and slippage (bps) are charged on
    every change in weights, including the initial build-up.
Benchmarks: equal-weighted universe (same data & adjustment method, rebalanced
on the same dates) and SPY buy-and-hold.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .portfolio import perf_stats

STRATEGIES = {
    "top_n": "Long the N stocks with the highest ensemble expected return/sigma, equal weight.",
    "long_short": "Long top-quintile, short bottom-quintile by ensemble expected return/sigma, dollar neutral.",
    "prob_threshold": "Long every stock whose ensemble P(up) exceeds the threshold, equal weight; otherwise cash.",
}


def _daily_returns(bars: dict[str, pd.DataFrame], tickers: list[str]) -> pd.DataFrame:
    px = pd.DataFrame({t: bars[t]["adj_close"] for t in tickers if t in bars}).sort_index()
    return px.pct_change(fill_method=None)


def run(oos: pd.DataFrame, bars: dict[str, pd.DataFrame], cfg: dict, rf_daily: pd.Series | None = None) -> dict:
    h = cfg.get("horizon", "30D")
    strat = cfg.get("strategy", "top_n")
    n_top = int(cfg.get("top_n", 10))
    thr = float(cfg.get("threshold", 0.55))
    rebal = int(cfg.get("rebalance_sessions", 21))
    cost_bps = float(cfg.get("cost_bps", 5.0))
    slip_bps = float(cfg.get("slippage_bps", 5.0))
    start = cfg.get("start")
    end = cfg.get("end")
    sig = oos[oos["horizon"] == h].copy()
    if sig.empty:
        raise ValueError(f"no out-of-sample predictions for horizon {h}")
    sig["score"] = sig["ensemble"] / sig["sigma"]
    if start:
        sig = sig[sig["date"] >= date.fromisoformat(start)]
    if end:
        sig = sig[sig["date"] <= date.fromisoformat(end)]
    tickers = sorted(sig["ticker"].unique())
    R = _daily_returns(bars, tickers)
    sig_dates = sorted(sig["date"].unique())
    idx = list(R.index)
    pos_of = {d: i for i, d in enumerate(idx)}
    # choose rebalance signal dates spaced >= rebal sessions apart
    chosen, last_i = [], -10**9
    for d in sig_dates:
        i = pos_of.get(d)
        if i is not None and i - last_i >= rebal and i + 1 < len(idx):
            chosen.append(d)
            last_i = i
    if len(chosen) < 3:
        raise ValueError("not enough rebalance dates in the selected window")
    targets = {}
    ew_targets = {}
    for d in chosen:
        g = sig[sig["date"] == d].dropna(subset=["score"])
        if strat == "top_n":
            sel = g.nlargest(n_top, "score")["ticker"].tolist()
            w = {t: 1 / len(sel) for t in sel} if sel else {}
        elif strat == "long_short":
            q = g["score"].rank(pct=True)
            longs, shorts = g.loc[q >= 0.8, "ticker"].tolist(), g.loc[q <= 0.2, "ticker"].tolist()
            w = {t: 0.5 / len(longs) for t in longs} | {t: -0.5 / len(shorts) for t in shorts} if longs and shorts else {}
        elif strat == "prob_threshold":
            sel = g.loc[g["p_up"] > thr, "ticker"].tolist()
            w = {t: 1 / len(sel) for t in sel} if sel else {}
        else:
            raise ValueError(f"unknown strategy {strat}")
        exec_day = idx[pos_of[d] + 1]  # trade at next session's close
        targets[exec_day] = w
        ew_targets[exec_day] = {t: 1 / len(g) for t in g["ticker"]}

    cols = list(R.columns)
    col_ix = {t: i for i, t in enumerate(cols)}
    Rv = np.nan_to_num(R.values, nan=0.0)
    rf_v = rf_daily.reindex(R.index).fillna(0.0).values if rf_daily is not None else np.zeros(len(R))

    def simulate(tg: dict) -> tuple[pd.Series, pd.Series, list[dict]]:
        first = min(tg)
        i0 = pos_of[first]
        w = np.zeros(len(cols))
        rets, turns, trades = [], [], []
        for i in range(i0, len(idx)):
            d = idx[i]
            r = float(w @ Rv[i])
            if w.any():
                w = w * (1 + Rv[i])
                if 1 + r > 0:
                    w = w / (1 + r)
            cash = 1 - w[w > 0].sum() if strat != "long_short" else 0.0
            if cash > 0:
                r += cash * rf_v[i]
            turnover = 0.0
            if d in tg:
                tw = np.zeros(len(cols))
                for t, v in tg[d].items():
                    if t in col_ix:
                        tw[col_ix[t]] = v
                turnover = float(np.abs(tw - w).sum())
                r -= turnover * (cost_bps + slip_bps) / 1e4
                trades.append({"date": str(d), "turnover": turnover, "n_positions": int((tw != 0).sum()),
                               "holdings": [cols[j] for j in np.argsort(-tw)[:15] if tw[j] > 0]})
                w = tw
            rets.append(r)
            turns.append(turnover)
        days = idx[i0:]
        return pd.Series(rets, index=days), pd.Series(turns, index=days), trades

    s_ret, s_turn, trades = simulate(targets)
    ew_ret, _, _ = simulate(ew_targets)
    spy = bars.get("SPY")
    spy_ret = spy["adj_close"].pct_change(fill_method=None).reindex(s_ret.index).fillna(0.0) if spy is not None else None
    ew_ret = ew_ret.reindex(s_ret.index).fillna(0.0)
    rf_ann = float(rf_daily.reindex(s_ret.index).mean() * 252) if rf_daily is not None else 0.0
    stats = perf_stats(s_ret, rf_ann)
    # period (rebalance-to-rebalance) returns for profit factor & hit rate
    periods = []
    keys = sorted(targets)
    for a, b in zip(keys, keys[1:] + [s_ret.index[-1]]):
        seg = s_ret[(s_ret.index > a) & (s_ret.index <= b)]
        if len(seg):
            periods.append(float((1 + seg).prod() - 1))
    periods = np.array(periods)
    gains, losses = periods[periods > 0].sum(), -periods[periods < 0].sum()
    stats.update({
        "profit_factor": float(gains / losses) if losses > 0 else None,
        "period_hit_rate": float((periods > 0).mean()) if len(periods) else None,
        "n_rebalances": len(keys), "avg_turnover": float(s_turn[s_turn > 0].mean()) if (s_turn > 0).any() else 0.0,
        "total_cost_drag": float(s_turn.sum() * (cost_bps + slip_bps) / 1e4),
    })
    bench = {"equal_weight_universe": perf_stats(ew_ret, rf_ann)}
    if spy_ret is not None:
        bench["SPY"] = perf_stats(spy_ret, rf_ann)
    excess = s_ret - ew_ret
    te = excess.std() * np.sqrt(252)
    stats["information_ratio_vs_ew"] = float(excess.mean() * 252 / te) if te > 0 else None
    if spy_ret is not None and spy_ret.var() > 0:
        stats["beta_vs_spy"] = float(np.cov(s_ret, spy_ret)[0, 1] / spy_ret.var())
    eq = pd.DataFrame({"strategy": (1 + s_ret).cumprod(), "equal_weight": (1 + ew_ret).cumprod()})
    if spy_ret is not None:
        eq["SPY"] = (1 + spy_ret).cumprod()
    step = max(1, len(eq) // 600)
    curve = [{"date": str(d), **{k: float(v) for k, v in row.items()}} for d, row in eq.iloc[::step].iterrows()]
    dd = eq["strategy"] / eq["strategy"].cummax() - 1
    yearly = []
    for yr in sorted({d.year for d in s_ret.index}):
        m = [d.year == yr for d in s_ret.index]
        yearly.append({"year": yr, "strategy": float((1 + s_ret[m]).prod() - 1),
                       "equal_weight": float((1 + ew_ret.reindex(s_ret.index)[m].fillna(0)).prod() - 1),
                       "SPY": float((1 + spy_ret[m]).prod() - 1) if spy_ret is not None else None})
    return {"config": cfg, "strategy_description": STRATEGIES.get(strat), "stats": stats, "benchmarks": bench,
            "equity_curve": curve, "drawdown": [{"date": str(d), "dd": float(v)} for d, v in dd.iloc[::step].items()],
            "by_year": yearly, "rebalances": trades[-60:],
            "period": [str(s_ret.index[0]), str(s_ret.index[-1])],
            "caveats": ["Universe is today's large caps (survivorship bias inflates absolute returns of ALL series "
                        "equally; compare the strategy with the equal-weight universe benchmark).",
                        "SPY benchmark may exclude dividends if only price data was available - see Data Sources.",
                        "Costs are a flat bps assumption; real costs depend on size, liquidity and market impact."]}
