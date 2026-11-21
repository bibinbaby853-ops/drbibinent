import numpy as np
import pandas as pd

from scanner.backtest import _simulate, backtest_stock, BTConfig, summarise
from scanner.data import synthetic_ohlcv


def _bars(rows):
    idx = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def test_stop_assumed_first_when_both_in_one_bar():
    df = _bars([(100, 101, 99, 100), (100, 115, 90, 100)])
    j, px, why, t1, _ = _simulate(df, 1, 100, 95, 110, 115, 20)
    assert why == "stop" and px == 95 and not t1


def test_gap_through_stop_exits_at_open():
    df = _bars([(100, 101, 99, 100), (100, 102, 99, 101), (90, 92, 88, 91)])
    j, px, why, _, _ = _simulate(df, 1, 100, 95, 110, 115, 20)
    assert why == "stop (gap)" and px == 90


def test_target_and_time_exit():
    df = _bars([(100, 101, 99, 100), (100, 104, 99, 103), (103, 111, 102, 110)])
    j, px, why, t1, _ = _simulate(df, 1, 100, 95, 110, 120, 20)
    assert why == "target 1" and px == 110 and t1
    flat = _bars([(100, 101, 99, 100)] * 30)
    j, px, why, _, _ = _simulate(flat, 1, 100, 95, 110, 120, 5)
    assert why == "time" and j == 5


def test_backtest_stock_runs_without_lookahead_errors():
    raw = synthetic_ohlcv(n_days=700, seed=3, drift=0.0015, vol=0.015)
    raw["Volume"] = raw["Volume"] * 50
    trades = backtest_stock(("SYN", raw, None, BTConfig(start=str(raw.index[350].date()), min_turnover_cr=0.0)))
    df = pd.DataFrame(trades)
    assert not df.empty
    assert (pd.to_datetime(df["entry_date"]) > pd.to_datetime(df["signal_date"])).all()
    assert (df["entry"] > df["stop"]).all()
    s = summarise(df, "path")
    assert {"Trades", "Win%", "AvgR"}.issubset(s.columns)


def test_no_trade_entered_almost_on_the_stop():
    raw = synthetic_ohlcv(n_days=700, seed=3, drift=0.0015, vol=0.015)
    raw["Volume"] = raw["Volume"] * 50
    trades = pd.DataFrame(backtest_stock(("SYN", raw, None, BTConfig(start=str(raw.index[350].date()), min_turnover_cr=0.0))))
    pats = trades[~trades["path"].str.startswith("BASELINE")]
    assert ((pats["entry"] - pats["stop"]) / pats["entry"] > 0.003).all()
