from datetime import date, datetime

from app.market_data.calendar import IST, bhavcopy_expected, daily_bar_complete, intraday_bar_complete, is_market_open, weekdays


def ist(*a):
    return datetime(*a, tzinfo=IST)


def test_daily_bar_is_partial_during_the_session():
    d = date(2026, 11, 25)
    assert not daily_bar_complete(d, ist(2026, 11, 25, 11, 0))
    assert daily_bar_complete(d, ist(2026, 11, 25, 15, 30))
    assert daily_bar_complete(date(2026, 11, 23), ist(2026, 11, 25, 11, 0))


def test_intraday_completed_candle_rule():
    start = ist(2026, 11, 25, 10, 15)
    assert not intraday_bar_complete(start, "60m", ist(2026, 11, 25, 11, 14))
    assert intraday_bar_complete(start, "60m", ist(2026, 11, 25, 11, 15))
    last = ist(2026, 11, 25, 15, 15)  # final hourly bar ends at the 15:30 close, not 16:15
    assert intraday_bar_complete(last, "60m", ist(2026, 11, 25, 15, 30))
    assert not intraday_bar_complete(ist(2026, 11, 25, 15, 0), "15m", ist(2026, 11, 25, 15, 14))


def test_bhavcopy_expectation_and_market_hours():
    assert not bhavcopy_expected(date(2026, 11, 25), ist(2026, 11, 25, 12, 0))
    assert bhavcopy_expected(date(2026, 11, 25), ist(2026, 11, 25, 19, 30))
    assert bhavcopy_expected(date(2026, 11, 24), ist(2026, 11, 25, 9, 0))
    assert is_market_open(ist(2026, 11, 25, 9, 15)) and not is_market_open(ist(2026, 11, 25, 15, 30))
    assert not is_market_open(ist(2026, 11, 21, 11, 0))  # Saturday


def test_weekdays_skip_weekends():
    assert weekdays(date(2026, 11, 20), date(2026, 11, 23)) == [date(2026, 11, 20), date(2026, 11, 23)]
