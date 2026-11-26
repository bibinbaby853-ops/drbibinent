import numpy as np
import pandas as pd

from scanner import strategies as st
from scanner.data import synthetic_ohlcv
from scanner.indicators import enrich
from scanner.scoring import Filters, build_row, passes_filters, relative_strength, trade_plan


def _base(n=300, price=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(0)
    close = price + np.cumsum(rng.normal(0.15, 0.2, n))  # steady uptrend
    df = pd.DataFrame(
        {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1_000_000.0},
        index=idx,
    )
    return df


def test_breakout_detected_on_volume_surge():
    df = _base()
    hi = df["High"].iloc[-21:-1].max()
    df.loc[df.index[-1], ["Open", "Low", "Close", "High", "Volume"]] = [hi, hi - 0.2, hi + 1.5, hi + 1.6, 3_500_000.0]
    sig = st.breakout_20d(enrich(df))
    assert sig is not None and sig.direction == "LONG" and sig.strength > 50


def test_breakout_absent_without_volume():
    df = _base()
    hi = df["High"].iloc[-21:-1].max()
    df.loc[df.index[-1], ["Open", "Low", "Close", "High"]] = [hi, hi - 0.2, hi + 1.5, hi + 1.6]
    assert st.breakout_20d(enrich(df)) is None


def test_oversold_dip_in_uptrend():
    df = _base(n=320)
    for k in range(6, 0, -1):  # six straight down days
        i = df.index[-k]
        prev = df["Close"].iloc[-k - 1]
        df.loc[i, ["Open", "High", "Low", "Close"]] = [prev, prev + 0.1, prev - 1.2, prev - 1.0]
    sig = st.oversold_in_uptrend(enrich(df))
    assert sig is not None and sig.setup == "Oversold Dip"


def test_breakdown_only_with_shorts_enabled():
    df = _base()
    lo = df["Low"].iloc[-21:-1].min()
    df.loc[df.index[-1], ["Open", "High", "Close", "Low", "Volume"]] = [lo, lo + 0.2, lo - 1.5, lo - 1.6, 4_000_000.0]
    # force price below SMA50 by dropping the last 60 bars
    df.iloc[-60:, df.columns.get_loc("Close")] -= 15
    df.iloc[-60:, df.columns.get_loc("High")] -= 15
    df.iloc[-60:, df.columns.get_loc("Low")] -= 15
    df.iloc[-60:, df.columns.get_loc("Open")] -= 15
    e = enrich(df)
    assert all(s.direction == "LONG" for s in st.detect(e, include_shorts=False))
    assert any(s.setup == "Breakdown 20d" for s in st.detect(e, include_shorts=True))


def test_detect_never_raises_on_short_history():
    df = enrich(synthetic_ohlcv(n_days=30, seed=5))
    assert st.detect(df, include_shorts=True) == []


def test_filters_reject_cheap_and_illiquid():
    df = enrich(synthetic_ohlcv(n_days=300, seed=6, start_price=5.0))
    ok, why = passes_filters(df, Filters())
    assert not ok and why == "price too low"
    thin = synthetic_ohlcv(n_days=300, seed=7, start_price=100.0)
    thin["Volume"] = 100.0
    ok, why = passes_filters(enrich(thin), Filters())
    assert not ok and why == "illiquid"


def test_trade_plan_long_and_short():
    df = enrich(synthetic_ohlcv(n_days=300, seed=8))
    long_plan = trade_plan(df, "LONG", capital=500_000, risk_pct=1.0)
    short_plan = trade_plan(df, "SHORT", capital=500_000, risk_pct=1.0)
    assert long_plan["Stop"] < long_plan["Entry"] < long_plan["Target"]
    assert short_plan["Target"] < short_plan["Entry"] < short_plan["Stop"]
    assert long_plan["Qty"] * (long_plan["Entry"] - long_plan["Stop"]) <= 5_000 + long_plan["Entry"]
    assert long_plan["Qty"] * long_plan["Entry"] <= 500_000


def test_relative_strength_sign():
    strong = synthetic_ohlcv(n_days=300, seed=9, drift=0.003, vol=0.005)
    weak = synthetic_ohlcv(n_days=300, seed=10, drift=-0.003, vol=0.005)
    bench = synthetic_ohlcv(n_days=300, seed=11, drift=0.0, vol=0.005)
    assert relative_strength(strong, bench) > 0
    assert relative_strength(weak, bench) < 0
    assert relative_strength(strong, None) is None


def test_build_row_shape():
    df = enrich(synthetic_ohlcv(n_days=300, seed=12, drift=0.002, vol=0.01))
    sig = st.Signal("Momentum Leader", "LONG", 70.0, ["x"])
    row = build_row("TEST", {"name": "Test Co", "industry": "IT"}, df, [sig], 5.0, 100_000, 1.0)
    assert row["Symbol"] == "TEST" and row["Direction"] == "LONG"
    assert 0 <= row["Score"] <= 130
    assert set(["Entry", "Stop", "Target", "Qty", "Setups"]).issubset(row)
