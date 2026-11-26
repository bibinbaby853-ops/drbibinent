from datetime import date

import pandas as pd
import pytest

from app.backtesting import walkforward as wf
from app.backtesting.data import Signal
from app.config import StrategyConfig
from tests.test_backtest_engine import SESS, trend, world


def test_windows_roll_without_overlap_between_oos_periods():
    ws = wf.windows(date(2021, 7, 1), date(2023, 3, 15), 12, 6)
    assert [(w.oos_start, w.oos_end) for w in ws] == [(date(2022, 7, 1), date(2023, 1, 1)), (date(2023, 1, 1), date(2023, 3, 15))]
    assert all(w.is_end == w.oos_start for w in ws)            # OOS strictly after IS
    assert ws[1].is_start == date(2022, 1, 1)


def test_months_add():
    assert wf.months_add(date(2025, 11, 5), 3) == date(2026, 2, 1)


def test_walk_forward_selects_by_in_sample_sharpe_and_runs_one_oos_account(monkeypatch):
    d = world({1: trend(), 2: trend(step=-0.3)})
    sig_good = {SESS[k]: [Signal(1, "breakout", 100 + 0.5 * k + 0.3, 95 + 0.5 * k)] for k in (2, 30)}
    sig_bad = {SESS[k]: [Signal(2, "breakout", 100 - 0.3 * k + 0.3, 95 - 0.3 * k)] for k in (2, 30)}
    good, bad = StrategyConfig(), StrategyConfig(max_positions=4)  # differ only so their cache keys can be told apart
    # inject signals per candidate through the data's signal factory
    monkeypatch.setattr(type(d), "signals", lambda self, cfg, a, b: sig_good if cfg.max_positions == 5 else sig_bad)
    # every candidate gets the same cache key (same strategy rules) unless we separate them:
    monkeypatch.setattr(wf, "run_config", lambda data, cfg, s, e, cache, capital: {
        "metrics": {"sharpe_rf0": 1.0 if cfg.max_positions == 5 else 3.0, "trades": 12, "total_return_pct": 0, "max_drawdown_pct": 0}})
    out = wf.walk_forward(d, {"default": good, "alt": bad}, "default", capital=100_000, is_months=1, oos_months=1,
                          first=SESS[0], last=SESS[-1], min_trades=10)
    assert all(w["chosen"] == "alt" for w in out["windows"])  # higher IS Sharpe wins
    assert out["oos"]["sessions"] > 0 and out["windows"][0]["oos"][0] > out["windows"][0]["is"][0]


def test_too_few_in_sample_trades_keep_the_default(monkeypatch):
    d = world({1: trend()})
    monkeypatch.setattr(type(d), "signals", lambda self, cfg, a, b: {})
    monkeypatch.setattr(wf, "run_config", lambda data, cfg, s, e, cache, capital: {
        "metrics": {"sharpe_rf0": 1.0 if cfg.max_positions == 5 else 9.0, "trades": 12 if cfg.max_positions == 5 else 3}})
    out = wf.walk_forward(d, {"default": StrategyConfig(), "alt": StrategyConfig(max_positions=4)}, "default", capital=100_000,
                          is_months=1, oos_months=1, first=SESS[0], last=SESS[-1])
    assert all(w["chosen"] == "default" for w in out["windows"])
