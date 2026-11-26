import numpy as np
import pandas as pd

from scanner import indicators as ind
from scanner.data import synthetic_ohlcv


def test_sma_matches_rolling_mean():
    s = pd.Series(np.arange(1, 51, dtype=float))
    assert np.isclose(ind.sma(s, 5).iloc[-1], np.mean([46, 47, 48, 49, 50]))
    assert ind.sma(s, 5).iloc[:4].isna().all()


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert np.isclose(ind.rsi(up, 14).iloc[-1], 100.0)
    assert np.isclose(ind.rsi(down, 14).iloc[-1], 0.0)


def test_rsi_alternating_is_near_50():
    s = pd.Series([100.0 + (i % 2) for i in range(200)])
    assert 45 < ind.rsi(s, 14).iloc[-1] < 55


def test_atr_without_gaps_equals_mean_range():
    n = 100
    df = pd.DataFrame(
        {"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0, "Volume": 1000.0},
        index=pd.bdate_range("2024-01-01", periods=n),
    )
    assert np.isclose(ind.atr(df, 14).iloc[-1], 4.0)


def test_adx_strong_trend_is_high():
    df = synthetic_ohlcv(n_days=300, seed=1, drift=0.004, vol=0.004)
    out = ind.adx(df, 14)
    assert out["ADX"].iloc[-1] > 40
    assert out["+DI"].iloc[-1] > out["-DI"].iloc[-1]


def test_bollinger_contains_price_mostly():
    df = synthetic_ohlcv(n_days=400, seed=2)
    bb = ind.bollinger(df["Close"], 20, 2.0)
    inside = ((df["Close"] <= bb["BB_Upper"]) & (df["Close"] >= bb["BB_Lower"])).iloc[20:]
    assert inside.mean() > 0.85


def test_enrich_adds_expected_columns():
    df = ind.enrich(synthetic_ohlcv(n_days=400, seed=3))
    for col in ["SMA200", "EMA20", "RSI14", "ATR14", "ADX", "MACD", "BB_Width", "RVOL", "High20", "Ret3M", "NR7"]:
        assert col in df.columns, col
    assert not np.isnan(df["SMA200"].iloc[-1])
    assert np.isnan(df["High20"].iloc[0])
