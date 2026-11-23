from datetime import date, datetime

from app.analytics import market_hours as mh


def test_nyse_2026_holidays():
    expected = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    assert set(mh.holidays(2026)) == expected


def test_saturday_new_year_not_observed_on_friday():
    # 2022-01-01 was a Saturday: NYSE stayed open on Friday 2021-12-31
    assert mh.is_trading_day(date(2021, 12, 31))


def test_early_closes():
    assert mh.early_close(date(2026, 11, 27))
    assert mh.early_close(date(2026, 12, 24))
    s = mh.session(date(2026, 11, 27))
    assert s[1].hour == 13


def test_status_open_closed():
    assert mh.status(datetime(2026, 11, 23, 10, 0, tzinfo=mh.ET))["is_open"]
    assert not mh.status(datetime(2026, 11, 26, 11, 0, tzinfo=mh.ET))["is_open"]  # Thanksgiving
    assert mh.status(datetime(2026, 11, 23, 17, 0, tzinfo=mh.ET))["state"] == "after-hours"


def test_trading_day_arithmetic():
    assert mh.add_trading_days(date(2026, 11, 25), 1) == date(2026, 11, 27)  # skips Thanksgiving
    assert mh.trading_days_between(date(2026, 11, 20), date(2026, 11, 27)) == 4
