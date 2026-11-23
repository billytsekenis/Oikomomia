"""Fundamental metrics from the compact SEC extract.

All functions accept an `asof` date and only use facts *filed* on or before it,
so the same code powers both the live view and point-in-time backtest features
(no look-ahead from restated or later-filed data).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np


def _rows(cf: dict, block: str, name: str, asof: date | None) -> list[tuple[date, float, date]]:
    out = []
    for e, v, f in (cf.get(block, {}) or {}).get(name, []) or []:
        fd = date.fromisoformat(f)
        if asof is None or fd <= asof:
            out.append((date.fromisoformat(e), float(v), fd))
    return out


def ttm(cf: dict, name: str, asof: date | None = None, offset_q: int = 0) -> tuple[float | None, date | None]:
    """Trailing-twelve-month sum of 4 consecutive quarters (offset_q=4 -> prior year TTM)."""
    q = _rows(cf, "quarterly", name, asof)
    if len(q) < 4 + offset_q:
        return None, None
    if (asof or date.today()) - q[-1][0] > timedelta(days=200):
        return None, None  # latest report too old to be "trailing"
    q = q[: len(q) - offset_q] if offset_q else q
    last4 = q[-4:]
    for (e1, *_), (e2, *_) in zip(last4, last4[1:]):
        if not 70 <= (e2 - e1).days <= 125:
            return None, None
    return float(sum(v for _, v, _ in last4)), last4[-1][0]


def latest_instant(cf: dict, name: str, asof: date | None = None) -> tuple[float | None, date | None]:
    r = _rows(cf, "instant", name, asof)
    if not r:
        return None, None
    e, v, _ = r[-1]
    if (asof or date.today()) - e > timedelta(days=400):
        return None, None
    return v, e


def _avg_instant(cf: dict, name: str, asof: date | None) -> float | None:
    r = _rows(cf, "instant", name, asof)
    if not r:
        return None
    vals = [v for _, v, _ in r[-5:]]
    return float(np.mean(vals)) if vals else None


def total_debt(cf: dict, asof: date | None = None) -> float | None:
    ltd, _ = latest_instant(cf, "long_term_debt", asof)
    if ltd is None:
        nc, _ = latest_instant(cf, "long_term_debt_noncurrent", asof)
        cur, _ = latest_instant(cf, "long_term_debt_current", asof)
        if nc is None and cur is None:
            ltd = None
        else:
            ltd = (nc or 0) + (cur or 0)
    st, _ = latest_instant(cf, "short_term_debt", asof)
    if ltd is None and st is None:
        return None
    return (ltd or 0) + (st or 0)


def shares(cf: dict, asof: date | None = None) -> float | None:
    s, _ = latest_instant(cf, "shares_outstanding", asof)
    if s:
        return s
    q = _rows(cf, "quarterly", "diluted_shares", asof)
    return q[-1][1] if q else None


def growth(cf: dict, name: str, asof: date | None = None) -> float | None:
    cur, _ = ttm(cf, name, asof)
    prev, _ = ttm(cf, name, asof, offset_q=4)
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1


def cagr_annual(cf: dict, name: str, years: int = 3, asof: date | None = None) -> float | None:
    a = _rows(cf, "annual", name, asof)
    if len(a) < years + 1:
        return None
    v0, v1 = a[-1 - years][1], a[-1][1]
    if v0 <= 0 or v1 <= 0:
        return None
    return (v1 / v0) ** (1 / years) - 1


def _div(a, b):
    if a is None or b is None or b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return a / b


def snapshot(cf: dict, price: float, asof: date | None = None, forecast: dict | None = None,
             sector: str | None = None, market_cap_hint: float | None = None) -> dict:
    """All headline fundamentals & valuation ratios at `asof`."""
    rev, rev_end = ttm(cf, "revenue", asof)
    ni, _ = ttm(cf, "net_income", asof)
    op, _ = ttm(cf, "operating_income", asof)
    dna, _ = ttm(cf, "dna", asof)
    ocf, _ = ttm(cf, "ocf", asof)
    capex, _ = ttm(cf, "capex", asof)
    gp, _ = ttm(cf, "gross_profit", asof)
    if gp is None:
        cor, _ = ttm(cf, "cost_of_revenue", asof)
        gp = (rev - cor) if rev is not None and cor is not None else None
    interest, _ = ttm(cf, "interest_expense", asof)
    eps_sum, _ = ttm(cf, "eps_diluted", asof)
    sh = shares(cf, asof)
    dq = _rows(cf, "quarterly", "diluted_shares", asof)
    dil = dq[-1][1] if dq else sh
    eps = _div(ni, dil) if ni is not None and dil else eps_sum
    cash, _ = latest_instant(cf, "cash", asof)
    sti, _ = latest_instant(cf, "short_term_investments", asof)
    debt = total_debt(cf, asof)
    equity, eq_end = latest_instant(cf, "equity", asof)
    assets, _ = latest_instant(cf, "assets", asof)
    is_fin = sector == "Financials"
    ebitda = (op + dna) if (op is not None and dna is not None and not is_fin) else None
    fcf = (ocf - capex) if ocf is not None and capex is not None else (ocf if ocf is not None and is_fin else None)
    mcap_src = "price x SEC shares outstanding"
    if not sh and market_cap_hint:
        sh, mcap_src = market_cap_hint / price, "Nasdaq market cap (SEC share count unavailable)"
    mcap = price * sh if sh else None
    ev = (mcap + (debt or 0) - (cash or 0) - (sti or 0)) if mcap is not None and not is_fin else None
    fwd_eps = None
    fwd_label = None
    if forecast and forecast.get("yearly"):
        # first fiscal year whose consensus exists -> "next fiscal year" forward EPS
        y = forecast["yearly"][0]
        fwd_eps, fwd_label = y.get("consensus_eps"), y.get("fiscal_end")
    pe = _div(price, eps) if eps and eps > 0 else (_div(mcap, ni) if mcap and ni and ni > 0 else None)
    fpe = _div(price, fwd_eps) if fwd_eps and fwd_eps > 0 else None
    eps_g_fwd = (fwd_eps / eps - 1) if (fwd_eps and eps and eps > 0) else None
    peg = _div(fpe, eps_g_fwd * 100) if fpe and eps_g_fwd and eps_g_fwd > 0 else None
    rev_g = growth(cf, "revenue", asof)
    ni_g = growth(cf, "net_income", asof)
    trailing_peg = _div(pe, ni_g * 100) if pe and ni_g and ni_g > 0 else None
    out = {
        "period_end": rev_end.isoformat() if rev_end else None,
        "balance_sheet_date": eq_end.isoformat() if eq_end else None,
        "revenue_ttm": rev, "gross_profit_ttm": gp, "operating_income_ttm": op, "net_income_ttm": ni,
        "ebitda_ttm": ebitda, "ocf_ttm": ocf, "capex_ttm": capex, "fcf_ttm": fcf, "eps_ttm": eps,
        "interest_expense_ttm": interest, "cash": cash, "short_term_investments": sti, "total_debt": debt,
        "net_debt": (debt or 0) - (cash or 0) - (sti or 0) if debt is not None else None,
        "equity": equity, "assets": assets, "shares_outstanding": sh, "market_cap": mcap, "enterprise_value": ev,
        "pe_ttm": pe, "forward_pe": fpe, "forward_eps": fwd_eps, "forward_eps_period": fwd_label,
        "peg_forward": peg, "peg_trailing": trailing_peg,
        "ps_ttm": _div(mcap, rev), "pb": _div(mcap, equity) if equity and equity > 0 else None,
        "ev_ebitda": _div(ev, ebitda) if ebitda and ebitda > 0 else None, "ev_sales": _div(ev, rev),
        "p_fcf": _div(mcap, fcf) if fcf and fcf > 0 else None,
        "fcf_yield": _div(fcf, mcap), "earnings_yield": _div(ni, mcap),
        "gross_margin": _div(gp, rev), "operating_margin": _div(op, rev), "net_margin": _div(ni, rev),
        "fcf_margin": _div(fcf, rev),
        "roe": _div(ni, _avg_instant(cf, "equity", asof)) if equity and equity > 0 else None,
        "roa": _div(ni, _avg_instant(cf, "assets", asof)),
        "debt_to_equity": _div(debt, equity) if equity and equity > 0 else None,
        "net_debt_to_ebitda": _div(((debt or 0) - (cash or 0) - (sti or 0)), ebitda) if debt is not None and ebitda and ebitda > 0 else None,
        "interest_coverage": _div(op, interest) if interest and interest > 0 else None,
        "revenue_growth_yoy": rev_g, "net_income_growth_yoy": ni_g,
        "fcf_growth_yoy": growth(cf, "ocf", asof),
        "revenue_cagr_3y": cagr_annual(cf, "revenue", 3, asof),
        "eps_growth_forward": eps_g_fwd,
        "is_financial": is_fin, "market_cap_method": mcap_src if mcap else None,
    }
    out["notes"] = []
    if is_fin:
        out["notes"].append("Financials: EBITDA/EV-based ratios are not meaningful for banks and are omitted.")
    if fwd_eps is None:
        out["notes"].append("No analyst consensus available: forward P/E and forward PEG omitted.")
    return out


def quarterly_table(cf: dict, n: int = 12) -> list[dict]:
    names = ["revenue", "gross_profit", "operating_income", "net_income", "dna", "ocf", "capex", "eps_diluted"]
    series = {k: {e: v for e, v, _ in _rows(cf, "quarterly", k, None)} for k in names}
    ends = sorted(set(series["revenue"]) | set(series["net_income"]))[-n:]
    out = []
    for e in ends:
        row = {"period_end": e.isoformat()}
        for k in names:
            row[k] = series[k].get(e)
        if row["operating_income"] is not None and row["dna"] is not None:
            row["ebitda"] = row["operating_income"] + row["dna"]
        if row["ocf"] is not None and row["capex"] is not None:
            row["fcf"] = row["ocf"] - row["capex"]
        out.append(row)
    debt_by = {}
    for nm in ("long_term_debt", "long_term_debt_noncurrent"):
        for e, v, _ in _rows(cf, "instant", nm, None):
            debt_by.setdefault(e, v)
    for row in out:
        row["debt"] = debt_by.get(date.fromisoformat(row["period_end"]))
    return out


def annual_table(cf: dict, n: int = 6) -> list[dict]:
    names = ["revenue", "operating_income", "net_income", "ocf", "capex", "eps_diluted", "dps"]
    series = {k: {e: v for e, v, _ in _rows(cf, "annual", k, None)} for k in names}
    ends = sorted(set(series["revenue"]) | set(series["net_income"]))[-n:]
    rows = []
    for e in ends:
        r = {"fiscal_year_end": e.isoformat(), **{k: series[k].get(e) for k in names}}
        if r["ocf"] is not None and r["capex"] is not None:
            r["fcf"] = r["ocf"] - r["capex"]
        rows.append(r)
    return rows


DEFINITIONS = {
    "pe_ttm": "Price / diluted EPS over the trailing four reported quarters (TTM net income / latest diluted shares).",
    "forward_pe": "Price / consensus EPS for the next fiscal year (Nasdaq analyst consensus).",
    "peg_forward": "Forward P/E divided by expected next-FY EPS growth in % (vs TTM EPS).",
    "peg_trailing": "TTM P/E divided by trailing YoY net-income growth in %.",
    "ev_ebitda": "(Market cap + total debt - cash - short-term investments) / (TTM operating income + D&A).",
    "fcf_ttm": "TTM operating cash flow minus TTM capital expenditure (SEC XBRL).",
    "roe": "TTM net income / average stockholders' equity over the last five reported balance sheets.",
}
