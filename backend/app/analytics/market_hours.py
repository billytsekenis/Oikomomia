"""NYSE trading calendar: regular hours 09:30-16:00 America/New_York, full
holidays (rule-based, incl. observed dates and Good Friday) and 13:00 early
closes. Valid for 2000-2099 under current NYSE holiday rules."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, weekday: int) -> date:
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def holidays(y: int) -> frozenset[date]:
    h = set()
    ny = date(y, 1, 1)
    if ny.weekday() == 6:
        h.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:  # NYSE does not observe NY Day on the prior Friday
        h.add(ny)
    h.add(_nth_weekday(y, 1, 0, 3))   # MLK
    h.add(_nth_weekday(y, 2, 0, 3))   # Presidents
    h.add(_easter(y) - timedelta(days=2))  # Good Friday
    h.add(_last_weekday(y, 5, 0))     # Memorial
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))  # Juneteenth
    h.add(_observed(date(y, 7, 4)))
    h.add(_nth_weekday(y, 9, 0, 1))   # Labor
    h.add(_nth_weekday(y, 11, 3, 4))  # Thanksgiving
    h.add(_observed(date(y, 12, 25)))
    return frozenset(h)


def early_close(d: date) -> bool:
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24 and d.weekday() < 5:
        return True
    jul3 = date(d.year, 7, 3)
    return d == jul3 and d.weekday() < 5 and date(d.year, 7, 4).weekday() not in (5, 6)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def session(d: date) -> tuple[datetime, datetime] | None:
    if not is_trading_day(d):
        return None
    close = EARLY_CLOSE if early_close(d) else CLOSE
    return datetime.combine(d, OPEN, ET), datetime.combine(d, close, ET)


def now_et() -> datetime:
    return datetime.now(ET)


def status(at: datetime | None = None) -> dict:
    at = (at or now_et()).astimezone(ET)
    s = session(at.date())
    if s and s[0] <= at < s[1]:
        state = "open"
    elif s and at < s[0] and at.time() >= time(4, 0):
        state = "pre-market"
    elif s and s[1] <= at < datetime.combine(at.date(), time(20, 0), ET):
        state = "after-hours"
    else:
        state = "closed"
    nxt = next_open(at)
    return {"state": state, "is_open": state == "open", "now_et": at.isoformat(),
            "next_open": nxt.isoformat(), "session_close": s[1].isoformat() if s else None,
            "early_close": bool(s and early_close(at.date()))}


def next_open(at: datetime) -> datetime:
    at = at.astimezone(ET)
    d = at.date()
    for i in range(0, 15):
        dd = d + timedelta(days=i)
        s = session(dd)
        if s and s[0] > at:
            return s[0]
    raise RuntimeError("no session found")


def last_completed_session(at: datetime | None = None) -> date:
    """Most recent session whose close has passed (data for it should exist)."""
    at = (at or now_et()).astimezone(ET)
    d = at.date()
    s = session(d)
    if s and at >= s[1] + timedelta(minutes=30):
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def add_trading_days(d: date, n: int) -> date:
    """Calendar date that is n trading sessions after d."""
    cur = d
    k = 0
    while k < n:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            k += 1
    return cur


def trading_days_between(a: date, b: date) -> int:
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            n += 1
    return n
