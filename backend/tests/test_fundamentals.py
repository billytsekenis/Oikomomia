from datetime import date

import pytest

from app.analytics import fundamentals as fa
from app.analytics import valuation
from app.data.sec import _quarterize


def F(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def test_quarterize_ytd_cashflow_differencing():
    # Cash-flow items are reported year-to-date: Q1, H1, 9M, FY
    facts = [F("2025-01-01", "2025-03-31", 10, "2025-05-01"), F("2025-01-01", "2025-06-30", 25, "2025-08-01"),
             F("2025-01-01", "2025-09-30", 45, "2025-11-01"), F("2025-01-01", "2025-12-31", 70, "2026-02-01", "10-K")]
    q = _quarterize(facts)
    vals = {k.isoformat(): v for k, (v, _) in q.items()}
    assert vals == {"2025-03-31": 10, "2025-06-30": 15, "2025-09-30": 20, "2025-12-31": 25}
    # filed date of a differenced quarter = the later filing (point-in-time correct)
    assert q[date(2025, 12, 31)][1] == date(2026, 2, 1)


def test_quarterize_prefers_first_reported_value():
    facts = [F("2025-01-01", "2025-03-31", 10, "2025-05-01"), F("2025-01-01", "2025-03-31", 12, "2026-05-01")]
    q = _quarterize(facts)
    assert q[date(2025, 3, 31)][0] == 10


def test_quarterize_16_week_retail_quarter():
    facts = [F("2025-09-01", "2025-11-23", 60, "2025-12-15"), F("2025-05-12", "2025-08-31", 90, "2025-10-08", "10-K")]
    q = _quarterize(facts)
    assert q[date(2025, 8, 31)][0] == 90  # 111-day quarter accepted


def _cf():
    q = [["2025-03-31", 100.0, "2025-05-01"], ["2025-06-30", 110.0, "2025-08-01"], ["2025-09-30", 120.0, "2025-11-01"],
         ["2025-12-31", 130.0, "2026-02-01"], ["2026-03-31", 140.0, "2026-05-01"], ["2026-06-30", 150.0, "2026-08-01"],
         ["2026-09-30", 160.0, "2026-11-01"], ["2026-12-31", 170.0, "2027-02-01"]]
    ni = [[e, v * 0.2, f] for e, v, f in q]
    return {"quarterly": {"revenue": q, "net_income": ni},
            "instant": {"shares_outstanding": [["2026-10-20", 10.0, "2026-11-01"]],
                        "equity": [["2026-09-30", 500.0, "2026-11-01"]]}, "annual": {}}


def test_ttm_and_point_in_time():
    cf = _cf()
    v, end = fa.ttm(cf, "revenue", date(2026, 11, 15))
    assert v == 140 + 150 + 160 + 130 and end == date(2026, 9, 30)
    # as of 2026-10-15 the Q3-2026 10-Q was NOT yet filed: must not be used
    v2, end2 = fa.ttm(cf, "revenue", date(2026, 10, 15))
    assert end2 == date(2026, 6, 30) and v2 == 120 + 130 + 140 + 150


def test_growth_yoy():
    cf = _cf()
    g = fa.growth(cf, "revenue", date(2027, 3, 1))
    assert g == pytest.approx((140 + 150 + 160 + 170) / (100 + 110 + 120 + 130) - 1)


def test_snapshot_ratios():
    cf = _cf()
    s = fa.snapshot(cf, price=20.0, asof=date(2026, 11, 15))
    ni = (140 + 150 + 160 + 130) * 0.2
    assert s["market_cap"] == 200.0
    assert s["pe_ttm"] == pytest.approx(20.0 / (ni / 10.0))
    assert s["net_margin"] == pytest.approx(0.2)


def test_reverse_dcf_roundtrip():
    r, g = 0.09, 0.07
    ev = valuation.dcf_value(100.0, g, r)
    assert valuation.implied_growth(ev, 100.0, r) == pytest.approx(g, abs=1e-5)


def test_reverse_dcf_refuses_negative_fcf():
    out = valuation.reverse_dcf({"fcf_ttm": -5, "enterprise_value": 100, "market_cap": 100}, 0.04, 1.0)
    assert out["available"] is False
