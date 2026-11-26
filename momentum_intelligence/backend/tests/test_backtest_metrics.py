"""Metric formulas on fixtures with hand-computed answers."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtesting.engine import BacktestResult, Trade
from app.backtesting.metrics import benchmark_stats, curve_stats, drawdown, summarize, trade_stats

S, E = date(2025, 1, 1), date(2026, 1, 1)  # 365 days -> 0.9993 years


def test_drawdown_depth_and_duration():
    dd, dur = drawdown(pd.Series([100, 110, 99, 105, 120, 108]))
    assert dd == pytest.approx(0.10) and dur == 2


def test_curve_stats_on_a_known_path():
    eq = pd.Series([101.0, 99.99, 102.0, 103.02])
    cs, r = curve_stats(eq, 100.0, S, E, rf=0.0)
    assert r.round(6).tolist() == [0.01, -0.01, round(102 / 99.99 - 1, 6), 0.01]
    assert cs["total_return_pct"] == pytest.approx(3.02)
    assert cs["cagr_pct"] == pytest.approx(((1.0302) ** (365.25 / 365) - 1) * 100, abs=1e-3)
    sd = r.std(ddof=1)
    assert cs["sharpe_rf0"] == pytest.approx(r.mean() / sd * math.sqrt(252), abs=1e-3)
    assert cs["volatility_pct"] == pytest.approx(sd * math.sqrt(252) * 100, abs=1e-3)
    assert cs["max_drawdown_pct"] == pytest.approx((1 - 99.99 / 101) * 100, abs=1e-3)


def test_risk_free_rate_lowers_sharpe_and_zero_volatility_is_undefined():
    eq = pd.Series(100 * np.cumprod(np.full(252, 1.0004)))
    cs, _ = curve_stats(eq, 100.0, S, E, rf=0.065)
    assert cs["sharpe_rf0"] is None and cs["sortino"] is None and cs["max_drawdown_pct"] == 0


def t(r, pnl, strat="breakout", days=(date(2025, 1, 6), date(2025, 1, 10))):
    tr = Trade(1, "X", strat, "IT", days[0], 100.0, 10, initial_risk=abs(pnl / r) if r else 100.0, exit_date=days[1], pnl=pnl, charges=5.0)
    return tr


def test_trade_stats():
    trades = [t(2.0, 200), t(-1.0, -100), t(-1.0, -100, "pullback"), t(3.0, 300, "pullback")]
    s = trade_stats(trades)
    assert s["trades"] == 4 and s["win_rate_pct"] == 50 and s["expectancy_r"] == pytest.approx(0.75)
    assert s["avg_win_r"] == 2.5 and s["avg_loss_r"] == -1.0 and s["profit_factor"] == 2.5
    assert s["avg_holding_sessions"] == 5 and s["by_strategy"]["pullback"]["expectancy_r"] == 1.0


def test_benchmark_beta_alpha_and_information_ratio():
    sessions = list(pd.bdate_range("2025-01-01", periods=6).date)
    bench = pd.Series([100, 101, 99, 100, 102, 101], index=sessions, dtype=float)
    br = bench.pct_change().fillna(0).reset_index(drop=True)
    r = 2 * br                                    # twice the benchmark: beta 2, alpha 0
    b = benchmark_stats(r, sessions, bench, sessions[0], sessions[-1])
    assert b["beta"] == pytest.approx(2.0) and b["alpha_annual_pct"] == pytest.approx(0.0, abs=1e-9) and b["correlation"] == pytest.approx(1.0)
    assert b["bench_total_return_pct"] == pytest.approx(1.0)


def test_summary_exposure_and_costs():
    sessions = list(pd.bdate_range("2025-01-01", periods=3).date)
    eq = pd.DataFrame({"session": sessions, "equity": [100_000.0, 100_500.0, 100_200.0], "cash": [100_000.0, 60_000.0, 100_200.0],
                       "invested": [0.0, 40_500.0, 0.0], "drawdown_pct": [0, 0, 0.3], "open_positions": [0, 1, 0],
                       "agg_risk": [0.0, 500.0, 0.0], "charges": [0.0, 50.0, 120.0]})
    res = BacktestResult(eq, [t(0.4, 200)], {"fills": 1}, {}, sessions[0], sessions[-1])
    s = summarize(res, 100_000.0)
    assert s["exposure_pct"] == pytest.approx(40_500 / 100_500 / 3 * 100, abs=1e-3)
    assert s["time_in_market_pct"] == pytest.approx(100 / 3, abs=1e-3) and s["charges_total"] == 120
    assert s["max_aggregate_initial_risk_pct"] == pytest.approx(500 / 100_500 * 100, abs=1e-3)
