"""Phase 5: rule fixtures per strategy, invalidation, expiry, duplicate suppression, causality."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.config import StrategyRules
from app.indicators.panel import compute, weekly_asof
from app.strategies import machine
from app.strategies.rules import CONFIRMED, EARLY, POTENTIAL, evaluate

R = StrategyRules()


def frame(close, vol=None, rng_pct=0.01, opens=None, rs=75, ex=0.1):
    n = len(close)
    close = np.asarray(close, float)
    o = np.r_[close[0], close[:-1]] if opens is None else np.asarray(opens, float)
    h = np.maximum(o, close) * (1 + rng_pct / 2)
    lo = np.minimum(o, close) * (1 - rng_pct / 2)
    v = np.full(n, 1e6) if vol is None else np.asarray(vol, float)
    b = pd.DataFrame({"session_date": pd.bdate_range("2024-01-01", periods=n).date, "open": o, "high": h, "low": lo, "close": close, "volume": v})
    f = compute(b).set_index("session_date").join(weekly_asof(b).set_index("session_date"))
    f = f.join(b.set_index("session_date")[["open", "high", "low", "volume"]])
    f["rs_rank"], f["excess_63"] = rs, ex
    return f


UP = list(np.linspace(80, 118, 260))
BASE = list(118 + np.sin(np.arange(30)) * 1.0)


def breakout_frame(last=120.5, vol_mult=3.0, rs=75, ex=0.1, extra=()):
    c = UP + BASE + [last] + list(extra)
    v = [1e6] * (len(UP) + len(BASE)) + [vol_mult * 1e6] + [1e6] * len(extra)
    return frame(c, v, rs=rs, ex=ex)


def lvl(f, name, i=-1):
    return int(evaluate(f, R)[name]["level"].iat[i])


# ------------------------------------------------------------------ breakout

def test_breakout_confirms_on_volume_and_rs_with_atr_bounded_stop():
    f = breakout_frame()
    res = evaluate(f, R)["breakout"]
    assert res["level"].iat[-1] == CONFIRMED
    atr, close = f.atr_14.iat[-1], f.close.iat[-1]
    assert close - 3 * atr - 1e-9 <= res["stop"].iat[-1] <= close - 1 * atr + 1e-9
    assert res["stop"].iat[-1] < res["invalidation"].iat[-1] < close        # stop below the breakout level


def test_breakout_missing_one_confirmation_is_a_potential_setup():
    assert lvl(breakout_frame(vol_mult=1.0), "breakout") == POTENTIAL       # no volume
    assert lvl(breakout_frame(rs=40), "breakout") == POTENTIAL              # weak RS
    assert lvl(breakout_frame(vol_mult=1.0, rs=40), "breakout") == 0        # neither


def test_breakout_extended_above_level_is_not_allowed():
    assert lvl(breakout_frame(last=140.0), "breakout") == 0


def test_breakout_early_warning_just_below_resistance():
    f = frame(UP + BASE + [119.0])
    assert lvl(f, "breakout") == EARLY


# ------------------------------------------------------------------ continuation

def continuation_frame(last=153.5, vol_mult=2.0, rs=90, extended_jump=None):
    trend = list(np.linspace(60, 150, 280))
    tight = list(150 + np.sin(np.arange(12)) * 0.6)
    c = trend + tight + [extended_jump or last]
    v = [1e6] * (len(c) - 1) + [vol_mult * 1e6]
    return frame(c, v, rs=rs, rng_pct=0.008)


def test_continuation_confirms_on_expansion_from_a_tight_base():
    f = continuation_frame()
    res = evaluate(f, R)["continuation"]
    assert res["level"].iat[-1] == CONFIRMED and res["why_base"].iat[-1]
    assert res["stop"].iat[-1] < res["invalidation"].iat[-1] + 1e-9         # below the consolidation low


def test_continuation_needs_strong_rs_and_volume_and_no_extension():
    assert lvl(continuation_frame(rs=70), "continuation") == 0
    assert lvl(continuation_frame(vol_mult=1.0), "continuation") == POTENTIAL
    assert lvl(continuation_frame(extended_jump=170.0), "continuation") == 0


# ------------------------------------------------------------------ pullback

def pullback_frame(dip_vol=0.6e6, confirm_close=139.0, extra_drop=None):
    t = list(np.linspace(70, 140, 280))
    dip = [138.5, 137.0, 136.0, 135.8]
    c = t + dip + [confirm_close]
    v = [1e6] * len(t) + [dip_vol] * 4 + [1.4e6]
    o = list(np.r_[c[0], c[:-1]])
    o[-1] = 136.0
    if extra_drop is not None:
        c[len(t) + 1] = extra_drop
    return frame(c, v, opens=o)


def test_pullback_confirms_after_light_volume_dip_to_ema20():
    res = evaluate(pullback_frame(), R)["pullback"]
    assert res["level"].iat[-1] == CONFIRMED
    assert res["stop"].iat[-1] < res["invalidation"].iat[-1]                 # below the swing low


def test_pullback_on_heavy_volume_is_only_potential():
    assert lvl(pullback_frame(dip_vol=1.2e6), "pullback") == POTENTIAL


def test_pullback_without_confirmation_bar_is_early_warning():
    assert lvl(pullback_frame(confirm_close=136.2), "pullback") == EARLY


def test_pullback_with_a_distribution_day_is_not_allowed():
    # a 2% drop on 1.5x+ volume inside the window
    f = pullback_frame(dip_vol=1.6e6, extra_drop=134.0)
    res = evaluate(f, R)["pullback"]
    assert not res["why_no_distribution"].iat[-1] and res["level"].iat[-1] != CONFIRMED


def test_missing_inputs_never_produce_a_signal():
    f = breakout_frame()
    f["rs_rank"] = np.nan
    f["excess_63"] = np.nan
    assert lvl(f, "breakout") != CONFIRMED


def test_rules_are_causal():
    f = breakout_frame(extra=[121, 122, 119, 118, 123, 125])
    full = evaluate(f, R)
    for t in (250, 290, len(f) - 3):
        part = evaluate(f.iloc[:t + 1], R)
        for s in full:
            assert full[s]["level"].iat[t] == part[s]["level"].iat[t]


# ------------------------------------------------------------------ state machine

def synthetic(levels: dict[str, list[int]], close=None, stop=90.0, inval=95.0):
    n = len(next(iter(levels.values())))
    dates = list(pd.bdate_range("2025-01-01", periods=n).date)
    results = {s: pd.DataFrame({"level": lv, "stop": stop, "invalidation": inval, "trigger": 100.0, "why_x": True}) for s, lv in levels.items()}
    close = pd.Series(close if close is not None else [100.0] * n)
    return dates, close, results


def test_machine_progresses_early_potential_confirmed_and_emits_once():
    d, c, res = synthetic({"breakout": [1, 1, 2, 3, 0, 0], "continuation": [0] * 6, "pullback": [0] * 6})
    sigs = machine.run(d, c, res, R)
    assert len(sigs) == 1 and sigs[0].confirmed == d[3] and sigs[0].state == "confirmed"
    assert [e["to_state"] for e in sigs[0].events] == ["early_warning", "potential_setup", "confirmed"]


def test_priority_breakout_over_continuation_over_pullback_when_equal():
    d, c, res = synthetic({"breakout": [3], "continuation": [3], "pullback": [3]})
    assert machine.run(d, c, res, R)[0].strategy == "breakout"
    d, c, res = synthetic({"breakout": [0], "continuation": [3], "pullback": [3]})
    assert machine.run(d, c, res, R)[0].strategy == "continuation"
    d, c, res = synthetic({"breakout": [1], "continuation": [0], "pullback": [3]})  # stronger level wins over priority
    assert machine.run(d, c, res, R)[0].strategy == "pullback"


def test_potential_setup_invalidated_when_close_falls_below_level():
    d, c, res = synthetic({"breakout": [2, 0, 0], "continuation": [0] * 3, "pullback": [0] * 3}, close=[100, 99, 94])
    s = machine.run(d, c, res, R)[0]
    assert s.state == "invalidated" and s.closed == d[2]


def test_early_warning_expires_without_progress():
    n = R.early_expiry_sessions + 2
    d, c, res = synthetic({"breakout": [1] + [0] * (n - 1), "continuation": [0] * n, "pullback": [0] * n})
    s = machine.run(d, c, res, R)[0]
    assert s.state == "expired" and s.closed == d[R.early_expiry_sessions]


def test_duplicate_confirmations_suppressed_during_cooldown():
    n = R.cooldown_sessions + 5
    lv = [3] * n
    d, c, res = synthetic({"breakout": lv, "continuation": [0] * n, "pullback": [0] * n})
    conf = [s.confirmed for s in machine.run(d, c, res, R) if s.confirmed]
    assert conf == [d[0], d[R.cooldown_sessions + 1]]   # next one only after the cooldown


def test_confirmation_requires_eligibility():
    d, c, res = synthetic({"breakout": [3, 3], "continuation": [0, 0], "pullback": [0, 0]})
    sigs = machine.run(d, c, res, R, eligible=pd.Series([False, True]))
    assert [s.confirmed for s in sigs if s.confirmed] == [d[1]]


def test_strategy_rules_config_validation():
    with pytest.raises(ValueError):
        StrategyRules(enabled=("breakout", "magic"))
    with pytest.raises(ValueError):
        StrategyRules(min_stop_atr=3, max_stop_atr=2)
