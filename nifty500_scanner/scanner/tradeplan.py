"""Entry, stop, targets, sizing and watchlist assignment for each candidate."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .momentum import Assessment
from .patterns import swing_points

READY, BREAKOUT, PULLBACK = "READY TO TRADE", "BREAKOUT WATCH", "PULLBACK WATCH"


@dataclass
class TradePlan:
    watchlist: str
    setup: str
    cmp: float
    entry: float
    entry_low: float
    entry_high: float
    breakout_entry: float
    pullback_entry: float
    stop: float
    structure_stop: float
    atr_stop: float
    target1: float
    target2: float
    rr1: float
    rr2: float
    risk_pct: float
    holding: str
    invalidation: str
    trigger: str
    stop_logic: str
    target_note: str
    qty: int
    risk_amount: float


def _r(x: float) -> float:
    return round(float(x), 2)


def plan_trade(a: Assessment, df: pd.DataFrame, capital: float, risk_pct: float, max_alloc_pct: float = 25.0) -> TradePlan:
    r, p = df.iloc[-1], df.iloc[-2]
    atr = float(r.ATR14)
    close = float(r.Close)
    best = next((pt for pt in a.patterns), None)
    trend_ok = bool(r.Close > r.EMA50 and r.EMA20 > r.EMA50)
    near_ema20 = abs(close - r.EMA20) <= 1.2 * atr and close >= r.EMA20 - 0.3 * atr
    bullish_bar = close > float(p.High) or (close > r.Open and r.ClosePos >= 0.6)

    distribution = bool(close < float(p.Close) and r.ClosePos <= 0.3 and not np.isnan(r.RVOL) and r.RVOL >= 1.5)

    # ---- choose the setup and watchlist ----
    if distribution:
        wl, setup = PULLBACK, "High-volume selling bar, wait for it to be absorbed"
    elif a.extended:
        wl, setup = PULLBACK, "Extended leader, wait for pullback"
    elif best and best.stage == "retest":
        wl, setup = READY, best.name
    elif best and best.stage == "confirmed":
        vol_ok = not np.isnan(r.RVOL) and r.RVOL >= 1.5
        wl, setup = (READY, best.name) if vol_ok else (BREAKOUT, f"{best.name} (needs volume follow-through)")
    elif best and best.stage == "pre-breakout":
        wl, setup = BREAKOUT, best.name
    elif trend_ok and near_ema20:
        wl, setup = (READY, "Pullback to 20 EMA, bounce confirmed") if bullish_bar else (PULLBACK, "Pullback to 20 EMA")
    else:
        wl, setup = PULLBACK, "Trend continuation, wait for pullback"

    pivot = best.pivot if best else float(df["High"].iloc[-21:-1].max())
    ema20 = float(r.EMA20)
    breakout_entry = pivot + 0.1 * atr if close < pivot else close
    pull_level = max(ema20, pivot) if (best and best.stage in ("confirmed", "retest") and pivot < close) else ema20
    pullback_entry = pull_level + 0.2 * atr

    if wl == READY:
        entry = close
        lo, hi = (max(pivot, close - 0.5 * atr) if best and best.stage != "pre-breakout" else close - 0.5 * atr), close + 0.25 * atr
        trigger = (f"Buy near {close:.2f} on the next session if it holds above {lo:.2f}. "
                   f"Skip if it opens more than {hi:.2f} (gap up beyond the entry range).")
    elif wl == BREAKOUT:
        entry = breakout_entry
        lo, hi = breakout_entry, breakout_entry + 0.4 * atr
        trigger = (f"Buy only on a daily close above {pivot:.2f} with volume at least 1.5x average. "
                   f"Ideal fill between {lo:.2f} and {hi:.2f}.")
    else:
        entry = pullback_entry
        lo, hi = pull_level, pull_level + 0.5 * atr
        trigger = (f"Wait for a pullback into {lo:.2f}-{hi:.2f} (20 EMA{' / breakout level' if pull_level != ema20 else ''}) "
                   f"and a bullish reversal bar on lighter volume.")

    # ---- stops ----
    swing_lows = swing_points(df["Low"].iloc[-30:], 2, "low")
    recent_swing = float(df["Low"].iloc[-30:].iloc[swing_lows[-1]]) if swing_lows else float(df["Low"].iloc[-10:].min())
    if wl == PULLBACK:
        structure = min(recent_swing, float(r.EMA50)) - 0.2 * atr if entry > r.EMA50 else recent_swing - 0.2 * atr
        stop_logic = "below the most recent swing low / 50 EMA, the level that would break the uptrend"
    elif best is not None:
        structure = max(best.base_low, entry - 3 * atr) - 0.1 * atr
        stop_logic = f"below the {best.name.lower()} structure low ({best.base_low:.2f})"
    else:
        structure = recent_swing - 0.2 * atr
        stop_logic = f"below the recent swing low ({recent_swing:.2f})"
    atr_stop = entry - 2.0 * atr
    stop = structure
    if entry - structure > 2.5 * atr:
        stop, stop_logic = atr_stop, "2x ATR below entry (structure low was too far away for a swing trade)"
    elif entry - structure < 1.0 * atr:
        stop = entry - 1.0 * atr
        stop_logic += ", widened to 1 ATR so normal daily noise does not trigger it"
    risk = entry - stop

    # ---- targets ----
    look = df.iloc[-252:]
    highs_idx = swing_points(look["High"], 5, "high")
    floor = max(entry, close) + 1.0 * atr  # levels inside today's range are the trigger, not a target
    overhead = sorted(float(look["High"].iloc[i]) for i in highs_idx if look["High"].iloc[i] > floor)
    t1 = entry + 2 * risk
    target_note = "2R (no overhead resistance within the past year)"
    if overhead and overhead[0] < t1:
        t1 = overhead[0]
        target_note = f"overhead resistance at {overhead[0]:.2f}, which caps the first target below 2R"
    elif overhead:
        target_note = f"2R; next resistance at {overhead[0]:.2f}"
    measured = pivot + (pivot - best.base_low) if best else 0.0
    t2 = max(entry + 3 * risk, measured if measured > t1 else 0.0)
    if t2 > entry + 5 * risk:
        t2 = entry + 5 * risk
    rr1, rr2 = (t1 - entry) / risk, (t2 - entry) / risk

    # ---- holding period, invalidation, size ----
    sessions = (t1 - entry) / (0.45 * atr) if atr else 10
    lo_s, hi_s = int(np.clip(round(sessions * 0.7), 3, 20)), int(np.clip(round(sessions * 1.4), 3, 20))
    holding = f"{lo_s}-{hi_s} sessions" if hi_s > lo_s else f"about {lo_s} sessions"
    if wl == PULLBACK:
        invalidation = f"Daily close below {float(r.EMA50):.2f} (50 EMA) or below {recent_swing:.2f}"
    elif best is not None:
        invalidation = f"Daily close back below {pivot:.2f} (failed breakout) or below {best.base_low:.2f}"
    else:
        invalidation = f"Daily close below {recent_swing:.2f}"
    risk_amt = capital * risk_pct / 100.0
    qty = int(risk_amt // risk) if risk > 0 else 0
    qty = min(qty, int(capital * max_alloc_pct / 100.0 // entry))
    return TradePlan(
        watchlist=wl, setup=setup, cmp=_r(close), entry=_r(entry), entry_low=_r(lo), entry_high=_r(hi),
        breakout_entry=_r(breakout_entry), pullback_entry=_r(pullback_entry), stop=_r(stop),
        structure_stop=_r(structure), atr_stop=_r(atr_stop), target1=_r(t1), target2=_r(t2),
        rr1=round(rr1, 2), rr2=round(rr2, 2), risk_pct=round(risk / entry * 100, 2), holding=holding,
        invalidation=invalidation, trigger=trigger, stop_logic=stop_logic, target_note=target_note,
        qty=qty, risk_amount=round(qty * risk, 0),
    )


def build_portfolio(rows: list[dict], max_open_risk_pct: float, risk_pct: float, max_per_sector: int = 2) -> list[dict]:
    """Pick READY trades in score order until open risk or sector caps are hit."""
    chosen, sectors, open_risk = [], {}, 0.0
    for row in rows:
        if row["Watchlist"] != READY or row["R:R"] < 2:
            continue
        if sectors.get(row["Sector"], 0) >= max_per_sector:
            continue
        if open_risk + risk_pct > max_open_risk_pct + 1e-9:
            break
        chosen.append(row)
        sectors[row["Sector"]] = sectors.get(row["Sector"], 0) + 1
        open_risk += risk_pct
    return chosen


def max_positions(max_open_risk_pct: float, risk_pct: float) -> int:
    return max(1, math.floor(max_open_risk_pct / risk_pct + 1e-9))
