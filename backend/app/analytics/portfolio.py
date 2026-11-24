"""Portfolio intelligence.

Accounting: weighted-average cost per position (fees capitalised on buys,
deducted from proceeds on sells). Every P&L figure in the base currency is
decomposed exactly into a price effect and an FX effect:
    q (P1 X1 - P0 X0) = q (P1 - P0) X0  +  q P1 (X1 - X0)
where P = price in instrument currency, X = base-currency units per instrument
currency unit. Performance is time-weighted (TWR) on daily returns in the base
currency using dividend-adjusted prices.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..universe import UNIVERSE

STRESS_WINDOWS = {
    "COVID crash (Feb-Mar 2020)": ("2020-02-19", "2020-03-23"),
    "Q4 2018 sell-off": ("2018-09-20", "2018-12-24"),
    "2022 rate-hike bear market": ("2022-01-03", "2022-10-12"),
    "Aug 2024 volatility spike": ("2024-07-16", "2024-08-05"),
    "Apr 2025 tariff shock": ("2025-04-02", "2025-04-08"),
}


@dataclass
class Lot:
    qty: float = 0.0
    cost_instr: float = 0.0       # sum q*P0 (instrument ccy)
    cost_base_ex_fee: float = 0.0  # sum q*P0*X0
    fees_base: float = 0.0        # capitalised buy fees (base)
    realized_price: float = 0.0
    realized_fx: float = 0.0
    realized_fees: float = 0.0
    currency: str = "USD"
    dividends: float = 0.0
    other_fees: float = 0.0
    trades: list = field(default_factory=list)

    @property
    def avg_price(self) -> float:
        return self.cost_instr / self.qty if self.qty > 1e-12 else 0.0

    @property
    def avg_fx(self) -> float:
        return self.cost_base_ex_fee / self.cost_instr if self.cost_instr > 1e-12 else 0.0


def fx_to_base(ccy: str, base: str, eurusd: float) -> float:
    """Base-currency units per 1 unit of `ccy`; eurusd = USD per EUR."""
    if ccy == base:
        return 1.0
    if base == "EUR" and ccy == "USD":
        return 1.0 / eurusd
    if base == "USD" and ccy == "EUR":
        return eurusd
    raise ValueError(f"unsupported currency pair {ccy}->{base}")


TRADE_SIDES = ("BUY", "SELL")


def normalize_splits(txns: list[dict]) -> tuple[list[dict], list[str]]:
    """Apply recorded stock splits retroactively so every trade is expressed on today's
    split-adjusted share basis (the basis of the price data). A SPLIT row carries the change in
    share count; ratio = (held_before + change) / held_before. Earlier trades of that ticker get
    quantity x ratio and price / ratio (cost is unchanged). SPLIT rows are then dropped."""
    rows = sorted([dict(t) for t in txns], key=lambda x: (str(x["trade_date"]), x.get("id", 0)))
    notes = []
    for i, t in enumerate(rows):
        if t["side"] != "SPLIT":
            continue
        held = sum((r["quantity"] if r["side"] == "BUY" else -r["quantity"]) for r in rows[:i]
                   if r["ticker"] == t["ticker"] and r["side"] in TRADE_SIDES)
        if held <= 1e-9:
            notes.append(f"{t['trade_date']} {t['ticker']}: split ignored (no shares held before it)")
            continue
        ratio = (held + float(t["quantity"])) / held
        if ratio <= 0:
            notes.append(f"{t['trade_date']} {t['ticker']}: split ignored (invalid share change)")
            continue
        for r in rows[:i]:
            if r["ticker"] == t["ticker"] and r["side"] in TRADE_SIDES:
                r["quantity"] = float(r["quantity"]) * ratio
                r["price"] = float(r["price"]) / ratio
        notes.append(f"{t['trade_date']} {t['ticker']}: split ratio {ratio:g} applied to earlier trades")
    return [r for r in rows if r["side"] != "SPLIT"], notes


def build_lots(txns: list[dict], base: str, eurusd_hist: pd.Series | None) -> tuple[dict[str, Lot], list[str]]:
    """txns: dicts with ticker, side, quantity, price, currency, fee, fee_currency, fx_rate, trade_date."""
    lots: dict[str, Lot] = defaultdict(Lot)
    warnings: list[str] = []
    for t in sorted(txns, key=lambda x: (x["trade_date"], x.get("id", 0))):
        lot = lots[t["ticker"]]
        lot.currency = t.get("currency", "USD")
        X = t.get("fx_rate")
        if X is None:
            X = _fx_on(t["currency"], base, t["trade_date"], eurusd_hist)
        Xfee = _fx_on(t.get("fee_currency", base), base, t["trade_date"], eurusd_hist) if t.get("fee") else 1.0
        fee_b = float(t.get("fee") or 0) * Xfee
        q, P = float(t["quantity"]), float(t["price"])
        if t["side"] == "DIV":  # cash dividend received: quantity 1 x price = amount (instrument ccy)
            lot.dividends += q * P * X
            lot.trades.append(t)
            continue
        if t["side"] == "FEE":  # standalone fee (custody, tax withheld, ...)
            lot.other_fees += q * P * X
            lot.trades.append(t)
            continue
        if t["side"] == "BUY":
            lot.qty += q
            lot.cost_instr += q * P
            lot.cost_base_ex_fee += q * P * X
            lot.fees_base += fee_b
        else:
            if q > lot.qty + 1e-9:
                warnings.append(f"{t['trade_date']} {t['ticker']}: sell {q} exceeds holding {lot.qty:.4f}; clipped")
                q = lot.qty
            if lot.qty <= 0:
                continue
            P0, X0 = lot.avg_price, lot.avg_fx
            frac = q / lot.qty
            fee_alloc = lot.fees_base * frac
            lot.realized_price += q * (P - P0) * X0
            lot.realized_fx += q * P * (X - X0)
            lot.realized_fees += fee_b + fee_alloc
            lot.qty -= q
            lot.cost_instr -= q * P0
            lot.cost_base_ex_fee -= q * P0 * X0
            lot.fees_base -= fee_alloc
            if lot.qty < 1e-9:
                lot.qty, lot.cost_instr, lot.cost_base_ex_fee, lot.fees_base = 0.0, 0.0, 0.0, 0.0
        lot.trades.append(t)
    return dict(lots), warnings


def _fx_on(ccy: str, base: str, d, eurusd_hist: pd.Series | None) -> float:
    if ccy == base:
        return 1.0
    if eurusd_hist is None or eurusd_hist.empty:
        raise ValueError("FX history unavailable")
    d = d if isinstance(d, date) else date.fromisoformat(str(d))
    s = eurusd_hist[eurusd_hist.index <= d]
    rate = float(s.iloc[-1]) if len(s) else float(eurusd_hist.iloc[0])
    return fx_to_base(ccy, base, rate)


def positions_view(lots: dict[str, Lot], prices: dict[str, float], base: str, eurusd_now: float,
                   prev_close: dict[str, float] | None = None) -> dict:
    rows = []
    tot_mv = 0.0
    for tkr, lot in lots.items():
        if lot.qty <= 1e-9:
            continue
        P1 = prices.get(tkr)
        if P1 is None:
            continue
        X1 = fx_to_base(lot.currency, base, eurusd_now)
        mv = lot.qty * P1 * X1
        price_eff = lot.qty * (P1 - lot.avg_price) * lot.avg_fx
        fx_eff = lot.qty * P1 * (X1 - lot.avg_fx)
        unreal = price_eff + fx_eff - lot.fees_base
        cost_base = lot.cost_base_ex_fee + lot.fees_base
        pc = (prev_close or {}).get(tkr)
        rows.append({"ticker": tkr, "name": UNIVERSE.get(tkr, {}).get("name", tkr),
                     "sector": UNIVERSE.get(tkr, {}).get("sector", "Other"), "currency": lot.currency,
                     "quantity": lot.qty, "avg_price": lot.avg_price, "avg_fx": lot.avg_fx, "last_price": P1,
                     "fx_now": X1, "market_value": mv, "cost_basis": cost_base, "unrealized_pnl": unreal,
                     "unrealized_price_effect": price_eff, "unrealized_fx_effect": fx_eff,
                     "unrealized_pct": unreal / cost_base if cost_base > 0 else None,
                     "day_change_value": (lot.qty * (P1 - pc) * X1) if pc else None,
                     "realized_pnl": lot.realized_price + lot.realized_fx - lot.realized_fees})
        tot_mv += mv
    for r in rows:
        r["weight"] = r["market_value"] / tot_mv if tot_mv > 0 else 0
    realized = {t: {"price": l.realized_price, "fx": l.realized_fx, "fees": l.realized_fees,
                    "total": l.realized_price + l.realized_fx - l.realized_fees} for t, l in lots.items()
                if abs(l.realized_price) + abs(l.realized_fx) + abs(l.realized_fees) > 0}
    return {"positions": sorted(rows, key=lambda r: -r["market_value"]), "market_value": tot_mv,
            "cost_basis": sum(r["cost_basis"] for r in rows),
            "unrealized_pnl": sum(r["unrealized_pnl"] for r in rows),
            "unrealized_price_effect": sum(r["unrealized_price_effect"] for r in rows),
            "unrealized_fx_effect": sum(r["unrealized_fx_effect"] for r in rows),
            "realized_pnl": sum(v["total"] for v in realized.values()), "realized_by_ticker": realized,
            "fees_paid": sum(l.realized_fees + l.fees_base for l in lots.values()),
            "dividends_received": sum(l.dividends for l in lots.values()),
            "other_fees": sum(l.other_fees for l in lots.values())}


def base_returns(bars: dict[str, pd.DataFrame], currencies: dict[str, str], base: str,
                 eurusd_hist: pd.Series | None) -> pd.DataFrame:
    """Daily total returns of each instrument expressed in the base currency."""
    cols = {}
    for t, df in bars.items():
        p = df["adj_close"].copy()
        p.index = pd.Index(p.index)
        ccy = currencies.get(t, "USD")
        if ccy != base and eurusd_hist is not None:
            fx = eurusd_hist.reindex(p.index.union(eurusd_hist.index)).ffill().reindex(p.index)
            x = 1 / fx if (base == "EUR" and ccy == "USD") else fx
            p = p * x
        cols[t] = p
    px = pd.DataFrame(cols).sort_index().ffill()
    return px.pct_change(fill_method=None)


def twr_series(txns: list[dict], bars: dict[str, pd.DataFrame], base: str, eurusd_hist: pd.Series | None) -> pd.DataFrame:
    """Daily holdings-weighted TWR in base currency, plus value series (close-based)."""
    txns = [t for t in txns if t["side"] in TRADE_SIDES]
    if not txns:
        return pd.DataFrame()
    start = min(date.fromisoformat(str(t["trade_date"])) for t in txns)
    currencies = {t["ticker"]: t.get("currency", "USD") for t in txns}
    rets = base_returns(bars, currencies, base, eurusd_hist)
    rets = rets[rets.index >= start]
    close_px = pd.DataFrame({t: df["close"] for t, df in bars.items()}).sort_index().ffill()
    close_px = close_px[close_px.index >= start]
    by_day = defaultdict(list)
    for t in txns:
        by_day[date.fromisoformat(str(t["trade_date"]))].append(t)
    qty = defaultdict(float)
    vals, rs, flows = [], [], []
    prev_val = 0.0
    prev_w = {}
    days = list(close_px.index)
    for d in days:
        # return on holdings carried from previous day
        r = 0.0
        if prev_val > 0:
            r = sum(prev_w.get(tk, 0) * (rets.at[d, tk] if tk in rets and np.isfinite(rets.at[d, tk]) else 0.0) for tk in prev_w)
        flow = 0.0
        fee = 0.0
        for t in by_day.get(d, []):
            X = t.get("fx_rate") or _fx_on(t.get("currency", "USD"), base, d, eurusd_hist)
            amt = float(t["quantity"]) * float(t["price"]) * X
            Xf = _fx_on(t.get("fee_currency", base), base, d, eurusd_hist) if t.get("fee") else 1.0
            fee += float(t.get("fee") or 0) * Xf
            if t["side"] == "BUY":
                qty[t["ticker"]] += float(t["quantity"])
                flow += amt
            else:
                qty[t["ticker"]] -= float(t["quantity"])
                flow -= amt
        val = 0.0
        w = {}
        for tk, q in qty.items():
            if q > 1e-9 and tk in close_px:
                px = close_px.at[d, tk]
                ccy = currencies.get(tk, "USD")
                X = _fx_on(ccy, base, d, eurusd_hist) if ccy != base else 1.0
                if np.isfinite(px):
                    w[tk] = q * px * X
                    val += q * px * X
        if prev_val > 0 and fee:
            r -= fee / prev_val
        rs.append(r if prev_val > 0 else 0.0)
        vals.append(val)
        flows.append(flow)
        prev_val = val
        prev_w = {k: v / val for k, v in w.items()} if val > 0 else {}
    out = pd.DataFrame({"value": vals, "return": rs, "net_flow": flows}, index=days)
    out["twr_index"] = (1 + out["return"]).cumprod()
    return out


def perf_stats(r: pd.Series, rf_annual: float = 0.0) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {"n_days": int(len(r)), "insufficient": True}
    idx = (1 + r).cumprod()
    years = len(r) / 252
    cagr = idx.iloc[-1] ** (1 / years) - 1 if years > 0 and idx.iloc[-1] > 0 else None
    vol = float(r.std() * np.sqrt(252))
    ex = r - rf_annual / 252
    downside = r[r < 0].std() * np.sqrt(252)
    dd = idx / idx.cummax() - 1
    return {"n_days": int(len(r)), "total_return": float(idx.iloc[-1] - 1), "cagr": cagr, "volatility": vol,
            "sharpe": float(ex.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else None,
            "sortino": float(ex.mean() * 252 / downside) if downside and downside > 0 else None,
            "max_drawdown": float(dd.min()), "best_day": float(r.max()), "worst_day": float(r.min())}


def risk_metrics(weights: dict[str, float], rets: pd.DataFrame, bench: pd.Series, value: float,
                 sectors: dict[str, str]) -> dict:
    tk = [t for t in weights if t in rets.columns]
    R = rets[tk].tail(252).dropna(how="all").fillna(0.0)
    w = np.array([weights[t] for t in tk])
    if len(R) < 60 or not tk:
        return {"insufficient": True}
    rp = R.values @ w
    rp_s = pd.Series(rp, index=R.index)
    b = bench.reindex(R.index).fillna(0.0)
    beta = float(np.cov(rp, b)[0, 1] / np.var(b)) if np.var(b) > 0 else None
    corr = R.corr()
    sig_i = R.std().values * np.sqrt(252)
    sig_p = float(rp.std() * np.sqrt(252))
    q95, q99 = np.quantile(rp, 0.05), np.quantile(rp, 0.01)
    rp10 = pd.Series(rp).rolling(10).sum().dropna().values
    hhi = float(np.sum(w ** 2))
    sec = defaultdict(float)
    for t, wt in weights.items():
        sec[sectors.get(t, "Other")] += wt
    flags = []
    if w.max() > 0.20:
        flags.append(f"Largest position {tk[int(w.argmax())]} is {w.max() * 100:.0f}% of the portfolio (> 20%).")
    big_sec = max(sec.items(), key=lambda x: x[1])
    if big_sec[1] > 0.40:
        flags.append(f"Sector concentration: {big_sec[0]} is {big_sec[1] * 100:.0f}% (> 40%).")
    if 1 / hhi < 5:
        flags.append(f"Effective number of positions is {1 / hhi:.1f} (< 5): low diversification.")
    avg_corr = float((corr.values.sum() - len(tk)) / max(len(tk) * (len(tk) - 1), 1)) if len(tk) > 1 else None
    if avg_corr is not None and avg_corr > 0.6:
        flags.append(f"Average pairwise correlation {avg_corr:.2f} is high: holdings move together.")
    from scipy.stats import norm
    return {
        "beta": beta, "volatility": sig_p,
        "var_1d_95_hist": float(-q95 * value), "var_1d_99_hist": float(-q99 * value),
        "cvar_1d_95_hist": float(-rp[rp <= q95].mean() * value),
        "var_10d_95_hist": float(-np.quantile(rp10, 0.05) * value) if len(rp10) > 50 else None,
        "var_1d_95_param": float(norm.ppf(0.95) * rp.std() * value),
        "var_pct_1d_95": float(-q95),
        "hhi": hhi, "effective_n": 1 / hhi, "top5_weight": float(np.sort(w)[::-1][:5].sum()),
        "diversification_ratio": float(np.dot(w, sig_i) / sig_p) if sig_p > 0 else None,
        "avg_pairwise_correlation": avg_corr,
        "correlation": {"tickers": tk, "matrix": [[round(float(x), 3) for x in row] for row in corr.values]},
        "sector_weights": dict(sec), "flags": flags,
        "method": "Historical simulation on last 252 sessions of current weights in base currency; parametric = normal."}


def stress_tests(weights: dict[str, float], bars: dict[str, pd.DataFrame], rets: pd.DataFrame, value: float,
                 factor_rets: dict[str, pd.Series]) -> dict:
    hist = []
    for name, (a, b) in STRESS_WINDOWS.items():
        da, db = date.fromisoformat(a), date.fromisoformat(b)
        tot, cov, used_beta = 0.0, 0.0, []
        spy = bars.get("SPY")
        for t, w in weights.items():
            df = bars.get(t)
            r = None
            if df is not None:
                c = df["adj_close"]
                ca, cb = c[c.index <= da], c[c.index <= db]
                if len(ca) and len(cb) and ca.index[-1] >= da - timedelta(days=5):
                    r = float(cb.iloc[-1] / ca.iloc[-1] - 1)
            if r is None and spy is not None:
                s = spy["adj_close"]
                rs = float(s[s.index <= db].iloc[-1] / s[s.index <= da].iloc[-1] - 1)
                beta = _beta(rets.get(t), factor_rets.get("SPY"))
                if beta is not None:
                    r = beta * rs
                    used_beta.append(t)
            if r is not None:
                tot += w * r
                cov += w
        hist.append({"scenario": name, "window": [a, b], "portfolio_return": tot, "pnl": tot * value,
                     "coverage": cov, "beta_proxied": used_beta,
                     "note": "Actual returns of each holding over the window (USD, dividend-adjusted); holdings without history use beta x SPY."})
    hypo = []
    shocks = [("Nasdaq-100 falls 10%", "QQQ", -0.10), ("S&P 500 falls 20%", "SPY", -0.20),
              ("S&P 500 rises 10%", "SPY", 0.10), ("Long Treasuries fall 10% (~+60bp in 20y+ yields)", "TLT", -0.10)]
    for name, f, shock in shocks:
        fr = factor_rets.get(f)
        if fr is None:
            continue
        exp, var_res = 0.0, 0.0
        detail = []
        for t, w in weights.items():
            x = rets.get(t)
            beta, resid_sd = _beta(x, fr, with_resid=True)
            if beta is None:
                continue
            exp += w * beta * shock
            var_res += (w * resid_sd * np.sqrt(10)) ** 2
            detail.append({"ticker": t, "beta": beta, "expected_return": beta * shock})
        band = 1.645 * np.sqrt(var_res)
        hypo.append({"scenario": name, "factor": f, "shock": shock, "expected_return": exp, "expected_pnl": exp * value,
                     "range_90": [(exp - band) * value, (exp + band) * value], "by_position": detail,
                     "method": f"Regression beta of each holding on {f} (last 252 sessions); 90% band from idiosyncratic "
                               "residual volatility over an assumed 10-session shock window, residuals independent."})
    return {"historical": hist, "hypothetical": hypo}


def _beta(x: pd.Series | None, f: pd.Series | None, with_resid: bool = False):
    if x is None or f is None:
        return (None, None) if with_resid else None
    j = pd.concat([x, f], axis=1).dropna().tail(252)
    if len(j) < 60 or j.iloc[:, 1].var() == 0:
        return (None, None) if with_resid else None
    beta = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())
    if not with_resid:
        return beta
    resid = j.iloc[:, 0] - beta * j.iloc[:, 1]
    return beta, float(resid.std())


def counterfactual_benchmark(txns: list[dict], bench: pd.DataFrame, base: str, eurusd_hist: pd.Series | None,
                             ticker: str = "SPY") -> dict:
    """'What if every buy/sell had been a buy/sell of the benchmark instead?'
    Same dates and base-currency amounts; fees replicated."""
    fake = []
    c = bench["close"]
    for t in [x for x in txns if x["side"] in TRADE_SIDES]:
        d = date.fromisoformat(str(t["trade_date"]))
        s = c[c.index <= d]
        if s.empty:
            continue
        p = float(s.iloc[-1])
        X_src = t.get("fx_rate") or _fx_on(t.get("currency", "USD"), base, d, eurusd_hist)
        amount_base = float(t["quantity"]) * float(t["price"]) * X_src
        Xb = _fx_on("USD", base, d, eurusd_hist)
        fake.append({**t, "ticker": ticker, "price": p, "currency": "USD", "fx_rate": Xb,
                     "quantity": amount_base / (p * Xb)})
    return {"transactions": fake}
