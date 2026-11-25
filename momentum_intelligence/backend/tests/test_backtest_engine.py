"""Backtest engine on synthetic in-memory data: next-open fills, costs, eligibility gate, no look-ahead."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtesting.data import BacktestData, Signal
from app.backtesting.engine import Backtester
from app.config import CostConfig, ExecutionConfig, StrategyConfig

SESS = list(pd.bdate_range("2025-01-01", periods=60).date)
POL = {"allow_new_entries": True, "max_new_entries_per_day": 3, "risk_multiplier": 1.0, "min_score": 0}


def world(paths: dict[int, list[tuple]], eligible: dict[int, bool] | None = None):
    def wide(i):
        return pd.DataFrame({sid: [b[i] for b in p] for sid, p in paths.items()}, index=SESS)
    o, h, lo, c = wide(0), wide(1), wide(2), wide(3)
    elig = pd.DataFrame({sid: [(eligible or {}).get(sid, True)] * len(SESS) for sid in paths}, index=SESS)
    return BacktestData(sessions=SESS, raw_open=o, raw_high=h, raw_low=lo, raw_close=c, adj_open=o, adj_close=c,
                        atr_pit=(h - lo).rolling(14, min_periods=1).mean(), gap_p=c * 0 + 0.5, eligible=elig,
                        median_value=c * 0 + 60, sector=pd.Series({sid: f"S{sid}" for sid in paths}),
                        regime=pd.Series("strong_bullish", index=SESS), policy=pd.Series([POL] * len(SESS), index=SESS),
                        scores=pd.DataFrame({sid: [90.0] * len(SESS) for sid in paths}, index=SESS), factors={},
                        nifty=pd.Series(np.linspace(100, 110, len(SESS)), index=SESS), symbols={sid: f"S{sid}" for sid in paths})


def trend(start=100.0, step=0.5, n=60):
    return [(start + step * k, start + step * k + 1, start + step * k - 1, start + step * k + 0.3) for k in range(n)]


def test_entry_at_next_open_with_slippage_and_costs():
    d = world({1: trend()})
    sig = {SESS[10]: [Signal(1, "breakout", 103.3, 98.0)]}
    res = Backtester(d, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    t = res.trades[0]
    assert t.entry_date == SESS[11] and t.entry_price == round(round(105.5 * 1.0005 / 0.05) * 0.05, 2)
    assert t.charges > 0 and res.counts["fills"] == 1


def test_costs_are_applied_and_account_for_the_difference():
    d = world({1: trend()})
    sig = {SESS[10]: [Signal(1, "breakout", 103.3, 98.0)]}
    free = StrategyConfig(execution=ExecutionConfig(costs=CostConfig(stt_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_buy_pct=0,
                                                                      gst_pct=0, dp_charge_inr=0)))
    a = Backtester(d, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    b = Backtester(d, free, signals=sig).run(SESS[0], SESS[-1])
    assert a.trades[0].qty == b.trades[0].qty
    assert b.equity["equity"].iloc[-1] - a.equity["equity"].iloc[-1] == pytest.approx(a.equity["charges"].iloc[-1], abs=0.05)


def test_ineligible_stock_is_never_traded():
    d = world({1: trend(), 2: trend()}, eligible={2: False})
    sig = {SESS[10]: [Signal(1, "breakout", 103.3, 98.0), Signal(2, "breakout", 103.3, 98.0)]}
    res = Backtester(d, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    assert {t.stock_id for t in res.trades} == {1} and res.counts["rejected_data_quality"] == 1


def test_no_look_ahead_results_up_to_t_do_not_depend_on_later_data():
    rng = np.random.default_rng(3)
    paths = {}
    for sid in (1, 2, 3):
        c = 100 * np.cumprod(1 + rng.normal(0.002, 0.02, 60))
        paths[sid] = [(c[k] * 0.998, c[k] * 1.015, c[k] * 0.985, c[k]) for k in range(60)]
    d = world(paths)
    sig = {SESS[k]: [Signal(s, "pullback", round(paths[s][k][3], 2), round(paths[s][k][3] * 0.95, 2))] for k, s in ((5, 1), (12, 2), (20, 3), (33, 1))}
    full = Backtester(d, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    t = 30
    short = Backtester(d, StrategyConfig(), signals={k: v for k, v in sig.items() if k <= SESS[t]}).run(SESS[0], SESS[t])
    pd.testing.assert_frame_equal(full.equity.iloc[: t + 1].reset_index(drop=True), short.equity.reset_index(drop=True))
    # and future prices changing does not change the past
    d2 = world({s: p[: t + 1] + [(1.0, 1.0, 1.0, 1.0)] * (59 - t) for s, p in paths.items()})
    alt = Backtester(d2, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    pd.testing.assert_frame_equal(full.equity.iloc[: t + 1].reset_index(drop=True), alt.equity.iloc[: t + 1].reset_index(drop=True))


def test_regime_policy_comes_from_the_configuration_under_test():
    from app.backtesting.presets import _regime_open
    d = world({1: trend()})
    d.regime = pd.Series("strong_bearish", index=SESS)
    sig = {SESS[10]: [Signal(1, "breakout", 103.3, 98.0)]}
    res = Backtester(d, StrategyConfig(), signals=sig).run(SESS[0], SESS[-1])
    assert res.trades == [] and res.counts["rejected_regime"] == 1          # default: bearish blocks entries
    opened = Backtester(d, _regime_open(StrategyConfig()), signals=sig).run(SESS[0], SESS[-1])
    assert len(opened.trades) == 1                                            # variant with the gate off trades


def test_variant_capital_is_used():
    from app.backtesting.walkforward import sensitivity
    d = world({1: trend()})
    d.anchor = SESS[0]
    import app.backtesting.data as dmod
    d.signals = lambda cfg, a, b: {SESS[10]: [Signal(1, "breakout", 103.3, 98.0)]}
    out = sensitivity(d, {"1L": StrategyConfig(), "10L": StrategyConfig(simulated_capital_inr=1_000_000)}, SESS[0], SESS[-1], 100_000)
    assert out["1L"]["charges_total"] != out["10L"]["charges_total"]
