"""Strategy conditions on completed daily bars (causal: each row uses only data up to that session).

Every strategy returns, per session, a candidate level (0 none, 1 early warning, 2 potential setup,
3 confirmed), the levels for a trade (entry reference = close, stop, invalidation level) and the
reasons. Prices are whatever the input frame holds (adjusted series); the pipeline converts the
levels to the traded price of the decision session.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import StrategyRules

EARLY, POTENTIAL, CONFIRMED = 1, 2, 3
LEVEL_NAME = {EARLY: "early_warning", POTENTIAL: "potential_setup", CONFIRMED: "confirmed"}
NEEDED = ("open", "high", "low", "close", "volume", "ema_20", "ema_50", "sma_200", "sma_50_slope_10", "atr_14", "atr_pct", "adx_14",
          "rel_volume_20", "vol_sma_20", "prior_high_20", "prior_high_55", "bb_width_pctrank_126", "atr_10_50_ratio",
          "w_close", "w_sma_30", "w_sma_30_slope_4", "rs_rank", "excess_63")


def _clamp_stop(raw: pd.Series, close: pd.Series, atr: pd.Series, r: StrategyRules) -> pd.Series:
    return raw.clip(lower=close - r.max_stop_atr * atr, upper=close - r.min_stop_atr * atr)


def _level(conf, pot, early) -> pd.Series:
    return pd.Series(np.select([conf, pot, early], [CONFIRMED, POTENTIAL, EARLY], 0), index=conf.index)


def weekly_up(f: pd.DataFrame) -> pd.Series:
    return (f.w_close > f.w_sma_30) & (f.w_sma_30_slope_4 >= 0)


def breakout(f: pd.DataFrame, r: StrategyRules) -> pd.DataFrame:
    ctx = weekly_up(f) & (f.close > f.ema_20) & (f.close > f.ema_50)
    level = pd.Series(np.where(f.close > f.prior_high_55, f.prior_high_55, f.prior_high_20), index=f.index)
    above = f.close > f.prior_high_20
    vol_ok = f.rel_volume_20 >= r.bo_rvol_min
    rs_ok = (f.excess_63 > 0) & (f.rs_rank >= r.bo_rs_rank_min)
    extended = (f.close - level) > r.bo_max_extension_atr * f.atr_14
    conf = ctx & above & vol_ok & rs_ok & ~extended
    pot = ctx & above & ~extended & (vol_ok != rs_ok)
    early = ctx & ~above & (f.close >= f.prior_high_20 * (1 - r.bo_early_within_pct / 100))
    stop = _clamp_stop(level - r.bo_stop_below_level_atr * f.atr_14, f.close, f.atr_14, r)
    return pd.DataFrame({"level": _level(conf, pot, early), "stop": stop, "invalidation": level.where(above, f.prior_high_20),
                         "trigger": f.prior_high_20,
                         "why_ctx": ctx, "why_above": above, "why_vol": vol_ok, "why_rs": rs_ok, "why_not_extended": ~extended})


def continuation(f: pd.DataFrame, r: StrategyRules) -> pd.DataFrame:
    ph10 = f.high.shift(1).rolling(10, min_periods=10).max()
    pl10 = f.low.shift(1).rolling(10, min_periods=10).min()
    ctx = (f.close > f.ema_20) & (f.ema_20 > f.ema_50) & (f.ema_50 > f.sma_200) & (f.adx_14 >= r.co_adx_min) & (f.rs_rank >= r.co_rs_rank_min)
    contraction = ((f.bb_width_pctrank_126 <= r.co_bb_pctrank_max) | (f.atr_10_50_ratio <= r.co_atr_ratio_max)).shift(1, fill_value=False)
    base_pct = (ph10 / pl10 - 1) * 100
    tight = base_pct <= np.maximum(r.co_base_max_pct, 3 * f.atr_pct.shift(1))
    base = contraction & tight
    trig = (f.close > ph10) & (f.close > f.open)
    vol_ok = f.rel_volume_20 >= r.co_rvol_min
    extended = (f.close - f.ema_20) > r.co_max_extension_atr * f.atr_14
    conf = ctx & base & trig & vol_ok & ~extended
    pot = ctx & base & trig & ~vol_ok & ~extended
    early = ctx & base & ~trig
    stop = _clamp_stop(pl10 - r.co_stop_buffer_atr * f.atr_14, f.close, f.atr_14, r)
    return pd.DataFrame({"level": _level(conf, pot, early), "stop": stop, "invalidation": pl10, "trigger": ph10,
                         "why_ctx": ctx, "why_base": base, "why_trigger": trig, "why_vol": vol_ok, "why_not_extended": ~extended})


def pullback(f: pd.DataFrame, r: StrategyRules) -> pd.DataFrame:
    ctx = weekly_up(f) & (f.close > f.sma_200) & (f.ema_20 > f.ema_50) & (f.sma_50_slope_10 > 0)
    swing_low = f.low.rolling(5, min_periods=5).min()
    touched = swing_low <= f.ema_20 * (1 + r.pb_touch_pct / 100)
    lighter = f.volume.shift(1).rolling(3, min_periods=3).mean() < r.pb_lighter_volume * f.vol_sma_20.shift(1)
    confirm_bar = (f.close > f.open) & (f.close > f.high.shift(1)) & (f.close > f.ema_20)
    above50 = (f.close < f.ema_50).astype(int).rolling(5, min_periods=5).sum() == 0
    dist_day = (f.close.pct_change(fill_method=None) * 100 < -r.pb_distribution_drop_pct) & (f.volume > 1.5 * f.vol_sma_20.shift(1))
    no_dist = dist_day.astype(int).rolling(5, min_periods=5).sum() == 0
    conf = ctx & touched & lighter & confirm_bar & above50 & no_dist
    pot = ctx & touched & confirm_bar & above50 & no_dist & ~lighter
    early = ctx & touched & ~confirm_bar & above50
    stop = _clamp_stop(np.minimum(swing_low, f.ema_50) - r.pb_stop_buffer_atr * f.atr_14, f.close, f.atr_14, r)
    return pd.DataFrame({"level": _level(conf, pot, early), "stop": stop, "invalidation": swing_low, "trigger": f.high.shift(1),
                         "why_ctx": ctx, "why_touched": touched, "why_lighter_volume": lighter, "why_confirm_bar": confirm_bar,
                         "why_above_ema50": above50, "why_no_distribution": no_dist})


STRATEGIES = {"breakout": breakout, "continuation": continuation, "pullback": pullback}


def evaluate(f: pd.DataFrame, r: StrategyRules) -> dict[str, pd.DataFrame]:
    """All enabled strategies on one stock's frame. Missing inputs make a condition False (never a signal)."""
    missing = [c for c in NEEDED if c not in f.columns]
    if missing:
        raise KeyError(f"strategy inputs missing: {missing}")
    out = {}
    for name in r.enabled:
        res = STRATEGIES[name](f, r)
        res["level"] = res["level"].where(f.atr_14.notna() & f.close.notna(), 0).astype(int)
        out[name] = res
    return out
