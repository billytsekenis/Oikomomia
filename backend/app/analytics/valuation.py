"""'What is priced in' - reverse discounted-cash-flow analysis.

Solves for the constant 10-year FCF growth rate g that equates the present value
of free cash flows (+ Gordon terminal value) with the current enterprise value:

    EV = sum_{t=1..10} FCF0 (1+g)^t / (1+r)^t + FCF10 (1+gT) / ((r - gT)(1+r)^10)

r = WACC built from CAPM (rf = 10y Treasury, ERP assumption 5.0%), after-tax
cost of debt rf + 1.5% at 21% tax. These are stated assumptions, shown in the
UI with a sensitivity grid, because the result depends heavily on them.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

ERP = 0.05
TERMINAL_G = 0.025
TAX = 0.21
CREDIT_SPREAD = 0.015


def wacc(rf: float, beta: float, mcap: float, debt: float) -> float:
    ke = rf + max(beta, 0.3) * ERP
    kd = (rf + CREDIT_SPREAD) * (1 - TAX)
    v = mcap + max(debt, 0)
    return (mcap / v) * ke + (max(debt, 0) / v) * kd if v > 0 else ke


def dcf_value(fcf0: float, g: float, r: float, years: int = 10, gt: float = TERMINAL_G) -> float:
    t = np.arange(1, years + 1)
    flows = fcf0 * (1 + g) ** t / (1 + r) ** t
    tv = fcf0 * (1 + g) ** years * (1 + gt) / (r - gt) / (1 + r) ** years
    return float(flows.sum() + tv)


def implied_growth(ev: float, fcf0: float, r: float, gt: float = TERMINAL_G) -> float | None:
    if fcf0 is None or fcf0 <= 0 or ev is None or ev <= 0 or r <= gt:
        return None
    f = lambda g: dcf_value(fcf0, g, r, gt=gt) - ev  # noqa: E731
    try:
        lo, hi = -0.5, 1.0
        if f(lo) > 0 or f(hi) < 0:
            return None
        return float(brentq(f, lo, hi, xtol=1e-6))
    except ValueError:
        return None


def reverse_dcf(snapshot: dict, rf: float | None, beta: float | None) -> dict:
    fcf = snapshot.get("fcf_ttm")
    ev = snapshot.get("enterprise_value")
    mcap = snapshot.get("market_cap")
    if snapshot.get("is_financial"):
        return {"available": False, "reason": "Reverse DCF on free cash flow is not meaningful for financial companies."}
    if rf is None or beta is None or not mcap or not ev:
        return {"available": False, "reason": "Missing risk-free rate, beta, market cap or enterprise value."}
    if not fcf or fcf <= 0:
        return {"available": False, "reason": "Trailing free cash flow is negative or unavailable; implied growth undefined."}
    r = wacc(rf, beta, mcap, snapshot.get("total_debt") or 0)
    g = implied_growth(ev, fcf, r)
    grid = []
    for dr in (-0.01, 0.0, 0.01):
        row = []
        for dgt in (-0.005, 0.0, 0.005):
            row.append(implied_growth(ev, fcf, r + dr, TERMINAL_G + dgt))
        grid.append({"wacc": r + dr, "values": row})
    hist = snapshot.get("revenue_cagr_3y")
    verdict = None
    if g is not None and hist is not None:
        gap = g - hist
        verdict = ("Market-implied FCF growth is well ABOVE the trailing 3-year revenue CAGR - the price embeds "
                   "acceleration; disappointment risk is elevated." if gap > 0.05 else
                   "Market-implied FCF growth is well BELOW the trailing 3-year revenue CAGR - expectations look "
                   "undemanding relative to history." if gap < -0.05 else
                   "Market-implied growth is roughly in line with recent history.")
    return {"available": True, "implied_fcf_growth_10y": g, "wacc": r, "risk_free": rf, "beta": beta, "erp": ERP,
            "terminal_growth": TERMINAL_G, "fcf_ttm": fcf, "enterprise_value": ev,
            "historical_revenue_cagr_3y": hist, "sensitivity": {"terminal_growth": [TERMINAL_G - 0.005, TERMINAL_G, TERMINAL_G + 0.005], "rows": grid},
            "interpretation": verdict}
