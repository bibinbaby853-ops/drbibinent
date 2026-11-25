"""Corporate-action factors and adjusted series.

NSE publishes ``PREV_CLOSE`` already adjusted on an ex-date (splits, bonuses,
rights, large special dividends). So for consecutive sessions t-1, t of one
security:

    factor_t = PREV_CLOSE_t / CLOSE_{t-1}

A factor away from 1 (beyond tick rounding) is a price adjustment effective on
t (confidence high).

NSE does not always do this: when a split also changes the ISIN the published
PREV_CLOSE can be the unadjusted old close (seen for BNAGROCHEM, 16 Nov 2026,
about 1:10). So a second rule looks for a ratio-shaped gap: the raw move is far
outside any price band, and a simple ratio (1/2, 1/5, 1/10, 2/3 ...) brings
both open and close back within +-20% of the prior close. Such events are
"inferred" with medium confidence until a second source (Yahoo splits)
confirms them; they are flagged in the data-quality log either way.

Adjusted price on day d = raw price * product of factors with ex-date > d.
Adjusted volume divides by the same product so traded value is preserved.

Raw prices are never overwritten; adjusted bars are stored as separate rows.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .base import CorporateActionEvent
from .validation import PREV_CLOSE_TOLERANCE

MIN_ABS_GAP = 0.051  # just above one 0.05 tick, so rounding of low-priced stocks is not an "action"
INFER_MOVE = 0.35     # raw move beyond which a ratio-shaped gap is considered (bands are <= 20%)
BAND = 0.20


def _fractions(max_den: int = 20) -> list[tuple[int, int]]:
    seen, out = set(), []
    for den in range(2, max_den + 1):
        for num in range(1, den):
            f = round(num / den, 10)
            if f not in seen:
                seen.add(f)
                out.append((num, den))
    return out


_FRACTIONS = _fractions(10) + [(1, 20), (1, 25), (1, 50)]


# Factors seen in Indian splits and bonuses (face value 10->5/2/1, bonus 1:1, 2:1, 1:2, 3:1, 1:4, 2:3 ...).
COMMON_FACTORS = {1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 1 / 6, 1 / 10, 1 / 20, 1 / 25, 1 / 50,
                  4 / 5, 5 / 6}
UNCOMMON_PENALTY = 0.10  # an uncommon ratio must explain the gap 10 points better than a common one


def infer_ratio(prior: float, open_: float, close: float) -> tuple[float, str] | None:
    """Ratio that best explains a gap, as (factor, label); None if the move is ordinary or nothing fits.

    Score = mean residual of open and close after applying the ratio, plus a penalty for
    ratios that are rare in Indian corporate actions. Both residuals must lie within +-20%.
    """
    move = close / prior - 1
    if abs(move) < INFER_MOVE:
        return None
    best = None
    for num, den in _FRACTIONS:
        for f, label in ((num / den, f"{num}:{den}"), (den / num, f"{den}:{num} consolidation")):
            if (f < 1) != (move < 0):
                continue
            r_close, r_open = close / (prior * f) - 1, open_ / (prior * f) - 1
            if abs(r_close) > BAND or abs(r_open) > BAND:
                continue
            common = any(abs(f - c) < 1e-9 or abs(f - 1 / c) < 1e-9 for c in COMMON_FACTORS)
            score = (abs(r_close) + abs(r_open)) / 2 + (0 if common else UNCOMMON_PENALTY)
            if best is None or score < best[0]:
                best = (score, f, label)
    return (best[1], best[2]) if best else None


def derive_factors(bars: pd.DataFrame, *, symbol: str, isin: str | None = None,
                   source: str = "nse_prev_close") -> list[CorporateActionEvent]:
    """Factors from one security's raw bars (needs session_date, close, prev_close)."""
    b = bars.dropna(subset=["close"]).sort_values("session_date").reset_index(drop=True)
    out: list[CorporateActionEvent] = []
    for i in range(1, len(b)):
        pc, prior = b.at[i, "prev_close"], b.at[i - 1, "close"]
        if pd.isna(pc) or pc <= 0 or prior <= 0:
            continue
        gap = pc - prior
        gap_days = (b.at[i, "session_date"] - b.at[i - 1, "session_date"]).days
        if abs(gap) <= max(MIN_ABS_GAP, PREV_CLOSE_TOLERANCE * prior):
            open_ = b.at[i, "open"] if "open" in b and not pd.isna(b.at[i, "open"]) else b.at[i, "close"]
            hit = infer_ratio(prior, open_, b.at[i, "close"])
            if hit:
                f, label = hit
                out.append(CorporateActionEvent(symbol=symbol, isin=isin, ex_date=b.at[i, "session_date"], factor=round(f, 8),
                                                kind="inferred_split_or_bonus", source=source.replace("prev_close", "ratio_gap"),
                                                confidence="medium",
                                                details={"ratio": label, "prior_close": float(prior), "open": float(open_),
                                                         "close": float(b.at[i, "close"]), "prev_close_published": float(pc),
                                                         "calendar_gap_days": int(gap_days), "confirmed_by": None}))
            continue
        factor = float(pc / prior)
        ratio = 1 / factor
        exact = _simple_ratio(factor, prior)
        kind = "split_or_bonus" if exact is not None else "price_adjustment"
        published_factor = factor
        if exact is not None:
            factor = exact  # the published PREV_CLOSE is rounded to a tick; snap to the ratio it represents
        # A gap across a missing session (suspension) is lower confidence: the prior close may be stale.
        confidence = "high" if gap_days <= 5 else "medium"
        out.append(CorporateActionEvent(symbol=symbol, isin=isin, ex_date=b.at[i, "session_date"], factor=round(factor, 8),
                                        kind=kind, source=source, confidence=confidence,
                                        details={"prev_close_published": float(pc), "prior_close": float(prior),
                                                 "implied_ratio": round(ratio, 4), "published_factor": round(published_factor, 8),
                                                 "calendar_gap_days": int(gap_days)}))
    return out


def _simple_ratio(factor: float, prior: float) -> float | None:
    """The simple fraction (den <= 50) a published factor represents, if tick rounding explains the difference."""
    if factor >= 0.95 and factor <= 1.05:
        return None
    tol = 0.051 / prior + 1e-9  # one tick on the adjusted price, relative to the prior close
    for num, den in _FRACTIONS:
        for f in (num / den, den / num):
            if abs(f - factor) <= max(tol, 1e-6):
                return f
    return None


def cumulative_factor(dates: pd.Series, events: list[tuple[date, float]]) -> np.ndarray:
    """Product of factors with ex_date strictly after each date."""
    out = np.ones(len(dates))
    dvals = np.array(list(dates))
    for ex, f in events:
        out[dvals < ex] *= f
    return out


def adjust(bars: pd.DataFrame, events: list[tuple[date, float]]) -> pd.DataFrame:
    """Return a copy of ``bars`` with prices multiplied and volume divided by the cumulative factor."""
    a = bars.copy()
    cf = cumulative_factor(a["session_date"], events)
    # prev_close is published adjusted for its own ex-date, so the same later-factor product applies to it.
    for c in ("open", "high", "low", "close", "prev_close"):
        if c in a:
            a[c] = a[c] * cf
    for c in ("volume", "delivery_qty"):
        if c in a:
            a[c] = (a[c] / cf).round()
    return a
