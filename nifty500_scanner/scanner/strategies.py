"""Swing-trading setup detectors.

Each detector receives an indicator-enriched frame (see ``indicators.enrich``)
and returns ``None`` when the setup is absent, or a ``Signal`` describing the
opportunity.  All detectors look only at the *last* bar so the scan reflects
tonight's close.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Signal:
    setup: str
    direction: str  # "LONG" or "SHORT"
    strength: float  # 0-100, setup-specific quality score
    notes: list[str] = field(default_factory=list)


def _last(df: pd.DataFrame) -> pd.Series:
    return df.iloc[-1]


def _ok(*vals) -> bool:
    return all(v is not None and not (isinstance(v, float) and np.isnan(v)) for v in vals)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def in_uptrend(row: pd.Series) -> bool:
    return _ok(row.SMA50, row.SMA200) and row.Close > row.SMA50 > row.SMA200


def in_downtrend(row: pd.Series) -> bool:
    return _ok(row.SMA50, row.SMA200) and row.Close < row.SMA50 < row.SMA200


# --------------------------------------------------------------------------- #
# Long setups
# --------------------------------------------------------------------------- #

def breakout_20d(df: pd.DataFrame) -> Signal | None:
    """Close above the prior 20-day high on expanding volume, strong close."""
    r = _last(df)
    if not _ok(r.High20, r.RVOL, r.ClosePos, r.SMA50):
        return None
    if r.Close <= r.High20 or r.RVOL < 1.5 or r.ClosePos < 0.6 or r.Close < r.SMA50:
        return None
    ext = (r.Close / r.High20 - 1.0) * 100.0
    if ext > 6.0:  # already too far above the pivot
        return None
    strength = _clip(40 + min(r.RVOL, 4) * 10 + r.ClosePos * 20 - ext * 3)
    notes = [f"RVOL {r.RVOL:.1f}x", f"+{ext:.1f}% over 20d high"]
    if _ok(r.High55) and r.Close > r.High55:
        strength = _clip(strength + 10)
        notes.append("also clears 55d high")
    return Signal("Breakout 20d", "LONG", strength, notes)


def new_52w_high(df: pd.DataFrame) -> Signal | None:
    """Fresh 52-week high with volume confirmation and a healthy trend."""
    r = _last(df)
    if not _ok(r.High252, r.RVOL, r.SMA200, r.RSI14) or len(df) < 150:
        return None
    if r.Close < r.High252 * 0.995 or r.Close < r.SMA200 or r.RVOL < 1.2:
        return None
    if r.RSI14 > 85:
        return None
    strength = _clip(50 + min(r.RVOL, 4) * 8 + (r.ClosePos or 0) * 15)
    return Signal("52w High", "LONG", strength, [f"RVOL {r.RVOL:.1f}x", f"RSI {r.RSI14:.0f}"])


def pullback_to_ema20(df: pd.DataFrame) -> Signal | None:
    """Uptrend, price has pulled into the 20 EMA and is turning back up."""
    r = _last(df)
    if len(df) < 210 or not in_uptrend(r) or not _ok(r.EMA20, r.RSI14, r.ADX):
        return None
    recent = df.iloc[-4:]
    touched = (recent["Low"] <= recent["EMA20"] * 1.01).any()
    if not touched or r.Close < r.EMA20 * 0.98:
        return None
    if not (35 <= r.RSI14 <= 60):
        return None
    prev = df.iloc[-2]
    bounce = r.Close > prev.High  # close above yesterday's high = reversal confirmation
    if r.ADX < 18:
        return None
    strength = _clip(45 + (15 if bounce else 0) + min(r.ADX, 40) * 0.5 + (r.Ret3M or 0) * 0.3)
    notes = [f"RSI {r.RSI14:.0f}", f"ADX {r.ADX:.0f}", "bounce confirmed" if bounce else "awaiting bounce"]
    return Signal("Pullback EMA20", "LONG", strength, notes)


def momentum_leader(df: pd.DataFrame) -> Signal | None:
    """Strong, orderly uptrend that is not over-extended: trend-following entry."""
    r = _last(df)
    if len(df) < 210 or not in_uptrend(r) or not _ok(r.Ret3M, r.Ret6M, r.ADX, r.DistEMA20_pct):
        return None
    if r.Ret3M < 10 or r.Ret6M < 20 or r.ADX < 25:
        return None
    if r.DistEMA20_pct > 8 or r.DistEMA20_pct < -2:
        return None
    if r.RSI14 > 78:
        return None
    strength = _clip(30 + min(r.Ret3M, 60) * 0.5 + min(r.Ret6M, 100) * 0.2 + min(r.ADX, 50) * 0.4)
    notes = [f"3M +{r.Ret3M:.0f}%", f"6M +{r.Ret6M:.0f}%", f"ADX {r.ADX:.0f}"]
    return Signal("Momentum Leader", "LONG", strength, notes)


def oversold_in_uptrend(df: pd.DataFrame) -> Signal | None:
    """Mean-reversion long: sharp dip inside a long-term uptrend."""
    r = _last(df)
    if len(df) < 210 or not _ok(r.SMA200, r.RSI2, r.RSI14, r.BB_Lower):
        return None
    if r.Close < r.SMA200:
        return None
    oversold = r.RSI2 < 10 or r.RSI14 < 32 or r.Close < r.BB_Lower
    if not oversold:
        return None
    drawdown = (r.Close / df["Close"].iloc[-20:].max() - 1.0) * 100.0
    if drawdown < -20:  # something may be broken, not just a dip
        return None
    strength = _clip(40 + (10 - min(r.RSI2, 10)) * 2 + (35 - min(r.RSI14, 35)) * 1.5 + (15 if r.Close < r.BB_Lower else 0))
    notes = [f"RSI2 {r.RSI2:.0f}", f"RSI14 {r.RSI14:.0f}", f"{drawdown:.1f}% off 20d high"]
    return Signal("Oversold Dip", "LONG", strength, notes)


def volatility_squeeze(df: pd.DataFrame) -> Signal | None:
    """Tight range (NR7 / inside bar / Bollinger squeeze) near highs in an uptrend."""
    r = _last(df)
    if len(df) < 150 or not _ok(r.BBW_pctile, r.ATR10, r.ATR50, r.SMA50, r.Dist52wHigh_pct):
        return None
    if r.Close < r.SMA50 or r.Dist52wHigh_pct < -15:
        return None
    squeeze = r.BBW_pctile <= 0.2 or (r.ATR10 / r.ATR50) < 0.75
    tight_bar = bool(r.NR7) or bool(r.InsideBar)
    if not (squeeze and tight_bar):
        return None
    strength = _clip(45 + (1 - r.BBW_pctile) * 30 + (10 if r.NR7 else 0) + (10 if r.InsideBar else 0) + r.Dist52wHigh_pct * 0.5)
    notes = [f"BB width pctile {r.BBW_pctile:.2f}", "NR7" if r.NR7 else "", "inside bar" if r.InsideBar else ""]
    return Signal("Volatility Squeeze", "LONG", strength, [n for n in notes if n])


def golden_cross(df: pd.DataFrame) -> Signal | None:
    """50-day SMA crossed above the 200-day SMA within the last 5 sessions."""
    if len(df) < 210:
        return None
    diff = df["SMA50"] - df["SMA200"]
    recent = diff.iloc[-6:]
    if recent.isna().any():
        return None
    crossed = (recent.iloc[0] <= 0) and (recent.iloc[-1] > 0)
    if not crossed:
        return None
    r = _last(df)
    strength = _clip(50 + min(r.ADX or 0, 40) * 0.5 + min(r.RVOL or 1, 3) * 5)
    return Signal("Golden Cross", "LONG", strength, [f"ADX {r.ADX:.0f}"])


def macd_turn_up(df: pd.DataFrame) -> Signal | None:
    """MACD histogram crosses above zero while price holds above the 50 SMA."""
    if len(df) < 60:
        return None
    hist = df["Hist"]
    r = _last(df)
    if not _ok(hist.iloc[-1], hist.iloc[-2], r.SMA50) or r.Close < r.SMA50:
        return None
    if not (hist.iloc[-2] <= 0 < hist.iloc[-1]):
        return None
    if r.MACD < 0 and r.RSI14 < 45:
        return None
    strength = _clip(40 + min(r.RVOL or 1, 3) * 8 + (r.Ret1M or 0) * 0.5)
    return Signal("MACD Turn", "LONG", strength, [f"RSI {r.RSI14:.0f}"])


# --------------------------------------------------------------------------- #
# Short setups (opt-in)
# --------------------------------------------------------------------------- #

def breakdown_20d(df: pd.DataFrame) -> Signal | None:
    r = _last(df)
    if not _ok(r.Low20, r.RVOL, r.ClosePos, r.SMA50):
        return None
    if r.Close >= r.Low20 or r.RVOL < 1.5 or r.ClosePos > 0.4 or r.Close > r.SMA50:
        return None
    ext = (1.0 - r.Close / r.Low20) * 100.0
    if ext > 6.0:
        return None
    strength = _clip(40 + min(r.RVOL, 4) * 10 + (1 - r.ClosePos) * 20 - ext * 3)
    return Signal("Breakdown 20d", "SHORT", strength, [f"RVOL {r.RVOL:.1f}x"])


def overbought_in_downtrend(df: pd.DataFrame) -> Signal | None:
    r = _last(df)
    if len(df) < 210 or not in_downtrend(r) or not _ok(r.RSI2, r.RSI14, r.EMA20):
        return None
    if r.RSI2 < 90 and r.RSI14 < 60:
        return None
    if r.Close < r.EMA20 * 0.99:
        return None
    strength = _clip(40 + (r.RSI2 - 80) + (r.RSI14 - 50))
    return Signal("Rally Into Resistance", "SHORT", strength, [f"RSI2 {r.RSI2:.0f}"])


LONG_SETUPS = [
    breakout_20d,
    new_52w_high,
    pullback_to_ema20,
    momentum_leader,
    oversold_in_uptrend,
    volatility_squeeze,
    golden_cross,
    macd_turn_up,
]
SHORT_SETUPS = [breakdown_20d, overbought_in_downtrend]


def detect(df: pd.DataFrame, include_shorts: bool = False) -> list[Signal]:
    found: list[Signal] = []
    for fn in LONG_SETUPS + (SHORT_SETUPS if include_shorts else []):
        try:
            sig = fn(df)
        except Exception:  # noqa: BLE001 - a bad frame must never kill the scan
            sig = None
        if sig is not None:
            found.append(sig)
    return found
