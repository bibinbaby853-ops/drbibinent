"""Turn raw signals plus context into a ranked opportunity table."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategies import Signal


@dataclass
class Filters:
    min_price: float = 20.0
    min_turnover_cr: float = 5.0  # average daily traded value, in INR crore
    max_atr_pct: float = 8.0  # skip wildly volatile names
    min_history: int = 120


def passes_filters(df: pd.DataFrame, f: Filters) -> tuple[bool, str]:
    if len(df) < f.min_history:
        return False, "short history"
    r = df.iloc[-1]
    if r.Close < f.min_price:
        return False, "price too low"
    turnover_cr = (r.Turnover20 or 0) / 1e7
    if np.isnan(turnover_cr) or turnover_cr < f.min_turnover_cr:
        return False, "illiquid"
    if not np.isnan(r.ATR_pct) and r.ATR_pct > f.max_atr_pct:
        return False, "too volatile"
    return True, ""


def trend_score(r: pd.Series) -> float:
    """0-100: how healthy the trend structure is."""
    s = 0.0
    if r.Close > r.SMA200:
        s += 25
    if r.Close > r.SMA50:
        s += 20
    if r.SMA50 > r.SMA200:
        s += 20
    if r.EMA20 > r.EMA50:
        s += 15
    if not np.isnan(r.ADX):
        s += min(r.ADX, 40) * 0.5
    return float(min(s, 100))


def momentum_score(r: pd.Series) -> float:
    s = 50.0
    for col, w in (("Ret1M", 0.6), ("Ret3M", 0.4), ("Ret6M", 0.2)):
        v = r.get(col)
        if v is not None and not np.isnan(v):
            s += max(-25, min(25, v * w))
    return float(max(0, min(100, s)))


def relative_strength(df: pd.DataFrame, bench: pd.DataFrame | None, lookback: int = 63) -> float | None:
    """Stock return minus benchmark return over ``lookback`` bars, in percent."""
    if bench is None or len(df) <= lookback or len(bench) <= lookback:
        return None
    s = df["Close"]
    b = bench["Close"].reindex(s.index).ffill()
    if b.iloc[-lookback - 1] is np.nan or np.isnan(b.iloc[-lookback - 1]):
        return None
    stock_ret = s.iloc[-1] / s.iloc[-lookback - 1] - 1.0
    bench_ret = b.iloc[-1] / b.iloc[-lookback - 1] - 1.0
    return float((stock_ret - bench_ret) * 100.0)


def trade_plan(df: pd.DataFrame, direction: str, capital: float, risk_pct: float) -> dict:
    """Simple ATR / swing based stop, 2R target and position size."""
    r = df.iloc[-1]
    atr_v = r.ATR14 if not np.isnan(r.ATR14) else r.Close * 0.02
    recent = df.iloc[-5:]
    if direction == "LONG":
        stop = min(float(recent["Low"].min()), float(r.Close - 2 * atr_v))
        risk_per_share = r.Close - stop
        target = r.Close + 2 * risk_per_share
    else:
        stop = max(float(recent["High"].max()), float(r.Close + 2 * atr_v))
        risk_per_share = stop - r.Close
        target = r.Close - 2 * risk_per_share
    risk_amt = capital * risk_pct / 100.0
    qty = int(risk_amt // risk_per_share) if risk_per_share > 0 else 0
    max_affordable = int(capital // r.Close)
    qty = min(qty, max_affordable)
    return {
        "Entry": round(float(r.Close), 2),
        "Stop": round(stop, 2),
        "Target": round(target, 2),
        "Risk%": round(risk_per_share / r.Close * 100.0, 2),
        "Qty": qty,
    }


def composite(signals: list[Signal], r: pd.Series, rs: float | None) -> float:
    best = max(s.strength for s in signals)
    multi = min(len(signals) - 1, 3) * 5  # confluence bonus
    t = trend_score(r)
    m = momentum_score(r)
    rs_part = 0.0 if rs is None else max(-10, min(10, rs * 0.5))
    if signals[0].direction == "SHORT":
        t, m = 100 - t, 100 - m
    return float(round(0.45 * best + 0.25 * t + 0.2 * m + multi + rs_part, 1))


def build_row(symbol: str, meta: dict, df: pd.DataFrame, signals: list[Signal], rs: float | None,
              capital: float, risk_pct: float) -> dict:
    r = df.iloc[-1]
    longs = [s for s in signals if s.direction == "LONG"]
    shorts = [s for s in signals if s.direction == "SHORT"]
    group = longs if len(longs) >= len(shorts) else shorts
    direction = group[0].direction
    group = sorted(group, key=lambda s: s.strength, reverse=True)
    plan = trade_plan(df, direction, capital, risk_pct)
    return {
        "Symbol": symbol,
        "Name": meta.get("name", ""),
        "Industry": meta.get("industry", ""),
        "Direction": direction,
        "Score": composite(group, r, rs),
        "Setups": " | ".join(s.setup for s in group),
        "Notes": "; ".join("; ".join(s.notes) for s in group if s.notes),
        "Close": round(float(r.Close), 2),
        "Chg%": round(float(r.Close / df["Close"].iloc[-2] - 1.0) * 100.0, 2) if len(df) > 1 else np.nan,
        "RVOL": round(float(r.RVOL), 2) if not np.isnan(r.RVOL) else np.nan,
        "RSI14": round(float(r.RSI14), 1) if not np.isnan(r.RSI14) else np.nan,
        "ADX": round(float(r.ADX), 1) if not np.isnan(r.ADX) else np.nan,
        "Ret1M%": round(float(r.Ret1M), 1) if not np.isnan(r.Ret1M) else np.nan,
        "Ret3M%": round(float(r.Ret3M), 1) if not np.isnan(r.Ret3M) else np.nan,
        "RS3M%": round(rs, 1) if rs is not None else np.nan,
        "Off52wHigh%": round(float(r.Dist52wHigh_pct), 1) if not np.isnan(r.Dist52wHigh_pct) else np.nan,
        "Turnover(Cr)": round(float(r.Turnover20) / 1e7, 1) if not np.isnan(r.Turnover20) else np.nan,
        "Trend": round(trend_score(r)),
        "Momentum": round(momentum_score(r)),
        **plan,
    }
