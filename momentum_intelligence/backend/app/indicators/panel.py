"""Full indicator set for one stock, as of a session, without look-ahead.

``asof_adjust`` rescales raw bars for corporate actions with ex-date <= as_of
only (ADR-014), so price levels on ``as_of`` equal the traded price and
nothing that happened later leaks in. ``compute`` returns one row per
session; ``latest`` returns the row for ``as_of``.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import core
from .weekly import to_weekly

INDICATOR_VERSION = "1"

# Sessions of history each indicator needs before it is defined (documented; used by the universe filter).
LOOKBACK = {"sma_200": 200, "high_252": 252, "roc_252": 253, "adx_14": 28, "macd": 34, "w_sma_30": 150}


def asof_adjust(raw: pd.DataFrame, factors: list[tuple[date, float]], as_of: date) -> pd.DataFrame:
    """Raw daily bars <= as_of, adjusted with factors whose ex-date is in (bar date, as_of]."""
    b = raw[raw["session_date"] <= as_of].sort_values("session_date").reset_index(drop=True).copy()
    known = [(ex, f) for ex, f in factors if ex <= as_of]
    cf = np.ones(len(b))
    dates = b["session_date"].to_numpy()
    for ex, f in known:
        cf[dates < ex] *= f
    for c in ("open", "high", "low", "close"):
        b[c] = b[c] * cf
    if "volume" in b:
        b["volume"] = b["volume"] / cf
    return b


def compute(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily indicator panel. ``bars``: session_date, open, high, low, close, volume (adjusted, sorted)."""
    b = bars.sort_values("session_date").reset_index(drop=True)
    o, h, l, c, v = (b[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    out = pd.DataFrame({"session_date": b["session_date"], "close": c})
    for n in (10, 20, 50, 100, 200):
        out[f"sma_{n}"] = core.sma(c, n)
    for n in (10, 20, 50, 100, 200):
        out[f"ema_{n}"] = core.ema(c, n)
    out["sma_50_slope_10"] = core.slope_pct(out["sma_50"], 10)
    out["sma_200_slope_20"] = core.slope_pct(out["sma_200"], 20)
    for n in (20, 50, 200):
        out[f"dist_sma_{n}_pct"] = (c / out[f"sma_{n}"] - 1) * 100
    out["rsi_14"] = core.rsi(c, 14)
    out["atr_14"] = core.atr(h, l, c, 14)
    out["atr_pct"] = out["atr_14"] / c * 100
    out = out.join(core.adx(h, l, c, 14).rename(columns={"adx": "adx_14"}))
    out = out.join(core.macd(c))
    out = out.join(core.bollinger(c))
    # Volatility contraction: Bollinger width relative to its own last 126 sessions, and short vs long ATR
    out["bb_width_pctrank_126"] = core.pct_rank(out["bb_width"], 126)
    out["atr_10_50_ratio"] = core.atr(h, l, c, 10) / core.atr(h, l, c, 50)
    for n in (5, 21, 63, 126, 252):
        out[f"roc_{n}"] = core.roc(c, n)
    for n in (20, 55):
        out[f"prior_high_{n}"] = core.prior_high(h, n)
        out[f"prior_low_{n}"] = core.prior_low(l, n)
    out["high_252"] = core.rolling_high(h, 252)
    out["low_252"] = core.rolling_low(l, 252)
    out["dist_high_252_pct"] = (c / out["high_252"] - 1) * 100
    out["above_low_252_pct"] = (c / out["low_252"] - 1) * 100
    out["vol_sma_20"] = core.sma(v, 20)
    out["vol_sma_50"] = core.sma(v, 50)
    out["rel_volume_20"] = core.relative_volume(v, 20)
    out["rel_volume_50"] = core.relative_volume(v, 50)
    out["obv"] = core.obv(c, v)
    out["obv_slope_20"] = out["obv"] - out["obv"].shift(20)
    out["rvol_20"] = core.realized_vol(c, 20)
    # Tightness: range of the last 10 sessions relative to price (low = consolidation)
    out["range_10_pct"] = (core.rolling_high(h, 10) / core.rolling_low(l, 10) - 1) * 100
    out["sessions"] = np.arange(1, len(b) + 1)
    return out


def weekly_trend(bars: pd.DataFrame, as_of: date, *, week_closed: bool = False) -> dict:
    """Trend-timeframe (1w) indicators from completed weeks only."""
    w = to_weekly(bars, as_of=as_of, week_closed=week_closed)
    if w.empty:
        return {"w_weeks": 0}
    c = w["close"].astype(float)
    last = len(w) - 1
    s10, s30, r14 = core.sma(c, 10), core.sma(c, 30), core.rsi(c, 14)

    def at(s):
        x = s.iat[last]
        return None if pd.isna(x) else float(x)

    return {"w_weeks": int(len(w)), "w_last_week": w["week"].iat[last].isoformat(), "w_close": float(c.iat[last]),
            "w_sma_10": at(s10), "w_sma_30": at(s30), "w_rsi_14": at(r14),
            "w_sma_30_slope_4": at(core.slope_pct(s30, 4))}


def latest(raw: pd.DataFrame, factors: list[tuple[date, float]], as_of: date, *, week_closed: bool = False) -> dict | None:
    """All indicators for ``as_of`` (None if the stock did not trade that session)."""
    b = asof_adjust(raw, factors, as_of)
    if b.empty or b["session_date"].iat[-1] != as_of:
        return None
    row = compute(b).iloc[-1].to_dict()
    row.update(weekly_trend(b, as_of, week_closed=week_closed))
    clean = {}
    for k, val in row.items():
        if isinstance(val, (float, np.floating)):
            clean[k] = None if np.isnan(val) else round(float(val), 6)
        elif isinstance(val, (np.integer,)):
            clean[k] = int(val)
        else:
            clean[k] = val
    clean["session_date"] = as_of.isoformat()
    clean["indicator_version"] = INDICATOR_VERSION
    return clean
