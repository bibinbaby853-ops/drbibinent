from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.market_data.adjustments import derive_factors
from app.market_data.base import ProviderUnavailable
from app.market_data.calendar import IST
from app.market_data.demo import SPLIT_SYMBOL, DemoProvider
from app.market_data.kite import KiteProvider
from app.market_data.validation import check_session
from app.market_data.yahoo import YahooProvider, tidy


def fake_intraday(tickers, interval, start, end):
    idx = pd.DatetimeIndex([datetime(2026, 11, 25, 9, 15), datetime(2026, 11, 25, 10, 15), datetime(2026, 11, 25, 11, 15)]).tz_localize(IST)
    cols = pd.MultiIndex.from_product([["ALPHA.NS"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
    data = np.array([[100, 101, 99, 100.5, 100.5, 10], [100.5, 102, 100, 101, 101, 12], [101, 101.5, 100.5, 101.2, 101.2, 3]])
    return pd.DataFrame(data, index=idx, columns=cols)


def test_yahoo_tidy_keeps_only_completed_candles():
    now = datetime(2026, 11, 25, 11, 40, tzinfo=IST)  # the 11:15 bar is still forming
    df = tidy(fake_intraday(None, None, None, None), {"ALPHA.NS": "ALPHA"}, "60m", now)
    assert [t.hour for t in df.ts] == [9, 10]


def test_yahoo_frames_are_labelled_unofficial_and_report_missing_symbols():
    y = YahooProvider(downloader=lambda tickers, **kw: fake_intraday(tickers, **kw))
    f = y.intraday_bars(["ALPHA", "MISSING"], "60m", date.today(), date.today())
    assert f.lineage[0].licence == "unofficial" and f.lineage[0].provider == "yahoo"
    assert any("MISSING" in n for n in f.notes)


def test_yahoo_rejects_unknown_index():
    with pytest.raises(ProviderUnavailable):
        YahooProvider(downloader=lambda *a, **k: None).index_bars("NOT AN INDEX", date.today(), date.today())


def test_demo_is_deterministic_labelled_and_valid():
    a, b = DemoProvider(end=date(2026, 11, 20)), DemoProvider(end=date(2026, 11, 20))
    fa, fb = a.bhavcopy(date(2026, 11, 20)), b.bhavcopy(date(2026, 11, 20))
    pd.testing.assert_frame_equal(fa.data, fb.data)
    assert fa.lineage[0].licence == "demo" and all(s.startswith("DEMO") for s in fa.data.symbol)
    assert [i for i in check_session(fa.data, require_isin=False) if i.severity == "blocking"] == []


def test_demo_split_is_detectable_from_prev_close():
    p = DemoProvider(end=date(2026, 11, 20))
    ev = derive_factors(p.daily_bars([SPLIT_SYMBOL], p.start, p.end).data, symbol=SPLIT_SYMBOL)
    assert [(e.ex_date, e.factor) for e in ev] == [(p.split_date, 0.5)]


def test_demo_intraday_stays_inside_daily_range():
    p = DemoProvider(end=date(2026, 11, 20))
    d = date(2026, 11, 20)
    day = p.daily_bars(["DEMO01"], d, d).data.iloc[0]
    bars = p.intraday_bars(["DEMO01"], "60m", d, d).data
    assert len(bars) == 7 and bars.high.max() <= day.high + 1e-9 and bars.low.min() >= day.low - 1e-9


def test_kite_is_unavailable_and_has_no_order_methods():
    k = KiteProvider()
    for call in (k.daily_bars, k.intraday_bars, k.index_bars, k.security_master):
        with pytest.raises(ProviderUnavailable, match="api.kite.trade"):
            call()
    forbidden = [m for m in dir(k) if any(w in m.lower() for w in ("order", "gtt", "trade", "position", "margin"))]
    assert forbidden == []
