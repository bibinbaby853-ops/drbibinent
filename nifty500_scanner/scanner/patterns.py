"""Price-structure detectors for early-stage momentum entries.

Each detector inspects an indicator-enriched daily frame (``indicators.enrich``)
and returns a ``Pattern`` or ``None``. Patterns carry the pivot (the price that
confirms the setup), a structural low used for the stop, a 0-10 quality score
and a stage:

* ``confirmed``  - price closed through the pivot in the last couple of sessions
* ``pre-breakout`` - structure is in place, price is just below the pivot
* ``retest``     - a recent breakout pulled back to the pivot and is holding

Chart-pattern recognition is heuristic. Treat a detection as "worth opening
the chart", not as proof of the pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Pattern:
    name: str
    stage: str
    pivot: float
    base_low: float
    quality: float  # 0-10
    detail: str


def swing_points(series: pd.Series, window: int, kind: str) -> list[int]:
    """Positional indices of swing highs (kind='high') or lows (kind='low')."""
    vals = series.to_numpy()
    out = []
    for i in range(window, len(vals) - window):
        seg = vals[i - window : i + window + 1]
        if np.isnan(vals[i]):
            continue
        if kind == "high" and vals[i] == np.nanmax(seg):
            out.append(i)
        elif kind == "low" and vals[i] == np.nanmin(seg):
            out.append(i)
    return out


def higher_highs_lows(df: pd.DataFrame, lookback: int = 120, window: int = 5) -> tuple[bool, bool]:
    d = df.iloc[-lookback:]
    hi = swing_points(d["High"], window, "high")
    lo = swing_points(d["Low"], window, "low")
    hh = len(hi) >= 2 and d["High"].iloc[hi[-1]] > d["High"].iloc[hi[-2]]
    hl = len(lo) >= 2 and d["Low"].iloc[lo[-1]] > d["Low"].iloc[lo[-2]]
    return bool(hh), bool(hl)


def _vol_ok(r: pd.Series, mult: float = 1.5) -> bool:
    return not np.isnan(r.RVOL) and r.RVOL >= mult


def _stage_for(close: float, pivot: float, atr: float, crossed_recently: bool) -> str | None:
    if crossed_recently and close >= pivot:
        return "confirmed"
    if close < pivot and (pivot - close) <= max(0.03 * pivot, 1.0 * atr):
        return "pre-breakout"
    return None


def _crossed(df: pd.DataFrame, level: float, bars: int = 2) -> bool:
    """Closed above ``level`` within the last ``bars`` sessions after being below it."""
    c = df["Close"]
    recent = c.iloc[-bars:]
    before = c.iloc[-bars - 5 : -bars]
    return bool((recent > level).any() and (before <= level).any())


# --------------------------------------------------------------------------- #

def high_breakout(df: pd.DataFrame, n: int) -> Pattern | None:
    """Close above the prior n-day high on volume (n = 20 or 50)."""
    if len(df) < n + 10:
        return None
    r = df.iloc[-1]
    level = float(df["High"].iloc[-n - 1 : -1].max())
    if r.Close <= level:
        return None
    vol_ok = _vol_ok(r)
    base_low = float(df["Low"].iloc[-n - 1 : -1].min())
    q = 5 + (2 if vol_ok else -2) + (1 if r.ClosePos >= 0.7 else 0) + (1 if n >= 50 else 0) + (1 if r.Close >= r.High252 * 0.98 else 0)
    return Pattern(f"{n}-day high breakout", "confirmed", level, base_low, float(np.clip(q, 0, 10)),
                   f"closed {(r.Close / level - 1) * 100:.1f}% above the prior {n}-day high of {level:.2f}"
                   + (f" on {r.RVOL:.1f}x volume" if vol_ok else " but volume did not confirm"))


def consolidation_breakout(df: pd.DataFrame) -> Pattern | None:
    """Tight 15-40 bar base (range <= 12% or <= 4 ATR), price at or through its top."""
    if len(df) < 60:
        return None
    r = df.iloc[-1]
    best = None
    for length in (15, 20, 30, 40):
        base = df.iloc[-length - 1 : -1]
        top, bot = float(base["High"].max()), float(base["Low"].min())
        width = (top - bot) / bot
        if width > 0.12 and (top - bot) > 4 * r.ATR14:
            continue
        stage = _stage_for(r.Close, top, r.ATR14, _crossed(df, top))
        if stage is None:
            continue
        q = 6 + (2 if width < 0.08 else 0) + (1 if length >= 30 else 0) + (1 if stage == "confirmed" and _vol_ok(r) else 0)
        if stage == "confirmed" and not _vol_ok(r, 1.2):
            q -= 2
        p = Pattern("Consolidation breakout" if stage == "confirmed" else "Tight consolidation", stage, top, bot,
                    float(np.clip(q, 0, 10)), f"{length}-bar base, {width * 100:.1f}% deep, top at {top:.2f}")
        if best is None or p.quality > best.quality:
            best = p
    return best


def long_base_emergence(df: pd.DataFrame) -> Pattern | None:
    """Stock clearing a 6-month range that was no wider than 35%."""
    if len(df) < 140:
        return None
    r = df.iloc[-1]
    base = df.iloc[-126:-3]
    top, bot = float(base["High"].max()), float(base["Low"].min())
    width = (top - bot) / bot
    if width > 0.35:
        return None
    stage = _stage_for(r.Close, top, r.ATR14, _crossed(df, top, 3))
    if stage is None:
        return None
    q = 7 + (1 if width < 0.25 else 0) + (2 if stage == "confirmed" and _vol_ok(r) else 0)
    return Pattern("Emerging from long consolidation", stage, top, bot, float(np.clip(q, 0, 10)),
                   f"6-month range of {width * 100:.0f}% with resistance at {top:.2f}")


def vcp(df: pd.DataFrame) -> Pattern | None:
    """Volatility contraction: >= 3 successively shallower pullbacks, last one < 10%."""
    if len(df) < 90:
        return None
    d = df.iloc[-90:]
    highs = swing_points(d["High"], 4, "high")
    if len(highs) < 3:
        return None
    depths = []
    for k, i in enumerate(highs):
        j_end = highs[k + 1] if k + 1 < len(highs) else len(d)
        seg_low = d["Low"].iloc[i:j_end].min()
        depths.append((d["High"].iloc[i] - seg_low) / d["High"].iloc[i])
    depths = depths[-4:]
    if len(depths) < 3 or not all(depths[k] > depths[k + 1] for k in range(len(depths) - 1)):
        return None
    if depths[-1] > 0.10:
        return None
    r = df.iloc[-1]
    pivot = float(d["High"].iloc[highs[-1]:].max())
    stage = _stage_for(r.Close, pivot, r.ATR14, _crossed(df, pivot))
    if stage is None:
        return None
    last_leg_vol = d["Volume"].iloc[highs[-1]:].mean()
    dry_up = last_leg_vol < d["Vol50"].iloc[-1]
    q = 7 + (1 if dry_up else 0) + (1 if len(depths) >= 4 else 0) + (1 if stage == "confirmed" and _vol_ok(r) else 0)
    base_low = float(d["Low"].iloc[highs[-1]:].min())
    return Pattern("Volatility contraction (VCP)", stage, pivot, base_low, float(np.clip(q, 0, 10)),
                   "pullbacks contracting " + " > ".join(f"{x * 100:.0f}%" for x in depths)
                   + (", volume drying up" if dry_up else ""))


def cup_and_handle(df: pd.DataFrame) -> Pattern | None:
    """Rounded 12-40% cup over 7-30 weeks with a shallow handle near the left lip."""
    if len(df) < 160:
        return None
    r = df.iloc[-1]
    win = df.iloc[-160:]
    left = win.iloc[:-35]
    lip_pos = int(np.argmax(left["High"].to_numpy()))
    lip = float(left["High"].iloc[lip_pos])
    after = win.iloc[lip_pos:]
    bottom = float(after["Low"].min())
    depth = (lip - bottom) / lip
    if not (0.12 <= depth <= 0.40):
        return None
    bottom_pos = int(np.argmin(after["Low"].to_numpy()))
    if bottom_pos < 15 or len(after) - bottom_pos < 15:
        return None  # V-shaped or still at the bottom
    handle = df.iloc[-12:]
    right_lip = float(df["High"].iloc[-35:].max())
    if right_lip < lip * 0.92:
        return None
    h_low = float(handle["Low"].min())
    h_depth = (right_lip - h_low) / right_lip
    if h_depth > 0.12 or h_low < bottom + (lip - bottom) / 2:
        return None
    pivot = float(handle["High"].max())
    stage = _stage_for(r.Close, pivot, r.ATR14, _crossed(df, pivot))
    if stage is None:
        return None
    q = 7 + (1 if h_depth < 0.08 else 0) + (1 if depth < 0.30 else 0) + (1 if stage == "confirmed" and _vol_ok(r) else 0)
    return Pattern("Cup and handle", stage, pivot, h_low, float(np.clip(q, 0, 10)),
                   f"{depth * 100:.0f}% cup, {h_depth * 100:.0f}% handle, pivot {pivot:.2f}")


def flag_or_pennant(df: pd.DataFrame) -> Pattern | None:
    """Pole of >= 15% in <= 15 bars, then 3-12 bars of tight drift holding the upper half."""
    if len(df) < 60:
        return None
    r = df.iloc[-1]
    for flag_len in range(3, 13):
        pole_top_pos = len(df) - 1 - flag_len
        pole_top = float(df["High"].iloc[pole_top_pos])
        if pole_top < df["High"].iloc[pole_top_pos - 5 : pole_top_pos + 1].max():
            continue
        pole_base = float(df["Low"].iloc[pole_top_pos - 15 : pole_top_pos].min())
        pole = pole_top - pole_base
        if pole / pole_base < 0.15 or pole < 3 * r.ATR14:
            continue
        flag = df.iloc[pole_top_pos + 1 :]
        f_hi, f_lo = float(flag["High"].max()), float(flag["Low"].min())
        if f_hi > pole_top * 1.01 or (pole_top - f_lo) > 0.5 * pole:
            continue
        pole_vol = df["Volume"].iloc[pole_top_pos - 10 : pole_top_pos + 1].mean()
        quiet = flag["Volume"].mean() < pole_vol
        his, los = flag["High"].to_numpy(), flag["Low"].to_numpy()
        converging = len(flag) >= 4 and his[-1] < his[0] and los[-1] > los[0]
        name = "Bull pennant" if converging else "Bull flag"
        pivot = max(f_hi, pole_top)
        stage = "confirmed" if r.Close > float(flag["High"].iloc[:-1].max() if len(flag) > 1 else pivot) and _vol_ok(r, 1.3) else "pre-breakout"
        if stage == "pre-breakout" and (pivot - r.Close) > max(0.05 * pivot, 1.5 * r.ATR14):
            continue
        q = 6 + (2 if quiet else 0) + (1 if (pole_top - f_lo) < 0.33 * pole else 0) + (1 if stage == "confirmed" else 0)
        return Pattern(name, stage, pivot, f_lo, float(np.clip(q, 0, 10)),
                       f"{pole / pole_base * 100:.0f}% pole, {flag_len}-bar {name.split()[-1].lower()} retracing "
                       f"{(pole_top - f_lo) / pole * 100:.0f}% of it" + (", volume contracting" if quiet else ""))
    return None


def breakout_retest(df: pd.DataFrame) -> Pattern | None:
    """Broke a 20/50-day high 3-15 sessions ago, dipped back to the level, holding with a bullish bar."""
    if len(df) < 80:
        return None
    r, p = df.iloc[-1], df.iloc[-2]
    for back in range(3, 16):
        i = len(df) - 1 - back
        level = float(df["High"].iloc[i - 50 : i].max())
        bar = df.iloc[i]
        if not (bar.Close > level and bar.RVOL >= 1.3):
            continue
        since = df.iloc[i + 1 :]
        touched = since["Low"].min() <= level * 1.025 or since["Low"].min() <= level + 0.5 * r.ATR14
        holding = r.Close >= level and since["Close"].min() >= level * 0.98
        bullish = r.Close > r.Open or r.Close > p.High
        if touched and holding and bullish:
            q = 7 + (1 if since["Volume"].mean() < bar.Volume else 0) + (1 if r.Close > p.High else 0) + (1 if _vol_ok(r, 1.2) else 0)
            return Pattern("Breakout retest", "retest", level, float(since["Low"].min()), float(np.clip(q, 0, 10)),
                           f"broke {level:.2f} {back} sessions ago, retested it and is holding with a bullish bar")
    return None


def higher_lows_at_resistance(df: pd.DataFrame) -> Pattern | None:
    """Three rising swing lows pressing into a flat 50-day resistance (ascending-triangle style)."""
    if len(df) < 80:
        return None
    r = df.iloc[-1]
    res = float(df["High"].iloc[-60:-1].max())
    if not (res * 0.96 <= r.Close <= res):
        return None
    d = df.iloc[-60:]
    lows = swing_points(d["Low"], 3, "low")
    if len(lows) < 3:
        return None
    lv = [d["Low"].iloc[k] for k in lows[-3:]]
    if not (lv[0] < lv[1] < lv[2]):
        return None
    q = 7 + (1 if (res - lv[-1]) / res < 0.06 else 0) + (1 if d["Volume"].iloc[-10:].mean() < d["Vol50"].iloc[-1] else 0)
    return Pattern("Higher lows into resistance", "pre-breakout", res, float(lv[-1]), float(np.clip(q, 0, 10)),
                   f"three rising swing lows ({lv[0]:.2f}, {lv[1]:.2f}, {lv[2]:.2f}) under resistance at {res:.2f}")


DETECTORS = [
    breakout_retest,
    vcp,
    cup_and_handle,
    flag_or_pennant,
    long_base_emergence,
    consolidation_breakout,
    higher_lows_at_resistance,
    lambda d: high_breakout(d, 50),
    lambda d: high_breakout(d, 20),
]


def detect_patterns(df: pd.DataFrame) -> list[Pattern]:
    found = []
    for fn in DETECTORS:
        try:
            p = fn(df)
        except Exception:  # noqa: BLE001 - one odd frame must not stop the scan
            p = None
        if p is not None:
            found.append(p)
    return sorted(found, key=lambda p: p.quality, reverse=True)


def failed_breakout(df: pd.DataFrame, lookback: int = 10) -> str | None:
    """A 20/50-day high breakout in the last ``lookback`` sessions that has closed back below its level."""
    if len(df) < 80:
        return None
    r = df.iloc[-1]
    for back in range(1, lookback + 1):
        i = len(df) - 1 - back
        window = df["High"].iloc[i - 20 : i]
        level = float(window.max())
        age = len(window) - 1 - int(np.argmax(window.to_numpy()))
        bar = df.iloc[i]
        if age >= 5 and bar.Close > level and bar.RVOL >= 1.3 and r.Close < level * 0.99:
            return f"broke {level:.2f} {back} sessions ago on {bar.RVOL:.1f}x volume, now back at {r.Close:.2f}"
    return None
