"""Trading calendar and market hours (IST).

A date counts as a trading session only when an official source shows trading
on it (the NSE bhavcopy exists). Weekends are skipped without asking. Holiday
lists are not hard-coded: a weekday with no bhavcopy after publication time is
a holiday; before publication time it is "unknown", never assumed.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
# NSE publishes the end-of-day bhavcopy in the evening; before this the day's file is "not yet published".
BHAVCOPY_READY = time(19, 0)

INTERVAL_MINUTES = {"15m": 15, "60m": 60}


def now_ist() -> datetime:
    return datetime.now(IST)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_weekday(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def bhavcopy_expected(d: date, now: datetime | None = None) -> bool:
    """True when the bhavcopy for ``d`` should exist by now if ``d`` was a session."""
    now = (now or now_ist()).astimezone(IST)
    if d < now.date():
        return True
    return d == now.date() and now.time() >= BHAVCOPY_READY


def session_bar_start(d: date) -> datetime:
    return datetime.combine(d, MARKET_OPEN, tzinfo=IST)


def is_market_open(now: datetime | None = None) -> bool:
    now = (now or now_ist()).astimezone(IST)
    return is_weekday(now.date()) and MARKET_OPEN <= now.time() < MARKET_CLOSE


def daily_bar_complete(d: date, now: datetime | None = None) -> bool:
    """A daily bar is complete only after the close; during the session it is a partial bar."""
    now = (now or now_ist()).astimezone(IST)
    return d < now.date() or (d == now.date() and now.time() >= MARKET_CLOSE)


def intraday_bar_complete(bar_start: datetime, interval: str, now: datetime | None = None) -> bool:
    """Completed-candle rule: a bar is usable only once its end time has passed (end capped at the close)."""
    now = (now or now_ist()).astimezone(IST)
    start = bar_start.astimezone(IST)
    end = start + timedelta(minutes=INTERVAL_MINUTES[interval])
    close_dt = datetime.combine(start.date(), MARKET_CLOSE, tzinfo=IST)
    return now >= min(end, close_dt)


def previous_session(sessions: list[date], d: date) -> date | None:
    earlier = [s for s in sessions if s < d]
    return max(earlier) if earlier else None
