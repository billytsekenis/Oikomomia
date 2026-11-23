from datetime import date

import pandas as pd
import pytest

from app.data.market import dividend_adjust, infer_splits, validate_bars


def test_split_inferred_only_when_share_count_confirms():
    divs = [(date(2020, 5, 8), 0.82), (date(2020, 8, 7), 0.82), (date(2020, 11, 6), 0.205)]
    shares_split = [(date(2020, 7, 17), 4.28e9), (date(2020, 10, 16), 17.0e9)]
    shares_cut = [(date(2020, 7, 17), 4.28e9), (date(2020, 10, 16), 4.27e9)]
    assert infer_splits(divs, shares_split) == {date(2020, 11, 6): 4}
    assert infer_splits(divs, shares_cut) == {}  # a 75% dividend cut is NOT a split


def test_dividend_adjustment_total_return():
    idx = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    df = pd.DataFrame({"close": [100.0, 100.0, 99.0, 99.0]}, index=idx)
    adj, n = dividend_adjust(df, [(date(2024, 1, 4), 1.0)], {})
    assert n == 1
    # price drop on ex-date exactly offset by the dividend: total return day = 0
    assert adj.iloc[2] / adj.iloc[1] - 1 == pytest.approx(0.0, abs=1e-12)
    assert adj.iloc[-1] == 99.0  # latest prices unchanged


def test_dividend_adjustment_uses_split_basis():
    idx = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    df = pd.DataFrame({"close": [25.0, 25.0, 24.75, 24.75]}, index=idx)  # split-adjusted prices (4:1 later)
    adj, _ = dividend_adjust(df, [(date(2024, 1, 4), 1.0)], {date(2024, 6, 1): 4})  # raw $1 = $0.25 post-split
    assert adj.iloc[2] / adj.iloc[1] - 1 == pytest.approx(0.0, abs=1e-12)


def test_validate_bars_repairs_and_drops():
    df = pd.DataFrame({"open": [10, 11, None], "high": [9, 12, 5], "low": [8, 10, 4], "close": [9.5, -1, 4.5],
                       "volume": [1, 2, None]}, index=[1, 2, 3])
    out, issues = validate_bars(df)
    assert len(out) == 2 and (out["high"] >= out[["open", "close"]].max(axis=1)).all()
    assert issues
