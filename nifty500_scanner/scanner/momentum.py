"""Institutional-style momentum scoring model.

Seven categories with fixed weights (sum 100). Each category is built from
sub-checks; a sub-check whose data is unavailable is dropped from that
category's denominator instead of being scored as zero. A category with no
data at all is N/A, and the final score is renormalised over the weight that
*was* available. ``Coverage`` reports how much of the 100 points had data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import ema, resample_ohlcv
from .patterns import Pattern, detect_patterns, higher_highs_lows

WEIGHTS = {
    "Trend": 20,
    "Relative strength": 20,
    "Volume": 15,
    "Breakout": 15,
    "Momentum": 10,
    "Fundamentals": 10,
    "Sector": 10,
}


@dataclass
class Category:
    name: str
    points: float = 0.0
    available: float = 0.0
    evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    missing_pts: float = 0.0

    def add(self, ok: bool | None, pts: float, text: str, partial: float | None = None) -> None:
        """Score a sub-check. ``ok=None`` means the input data is unavailable."""
        if ok is None:
            self.missing.append(text)
            self.missing_pts += pts
            return
        self.available += pts
        if partial is not None:
            self.points += pts * float(np.clip(partial, 0, 1))
            if partial > 0:
                self.evidence.append(text)
        elif ok:
            self.points += pts
            self.evidence.append(text)

    @property
    def score(self) -> float | None:
        if self.available == 0:
            return None
        return self.points / self.available * WEIGHTS[self.name]


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and np.isnan(x))


def _ret(c: pd.Series, n: int) -> float:
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1.0) * 100.0 if len(c) > n else np.nan


@dataclass
class StockContext:
    symbol: str
    df: pd.DataFrame  # enriched daily
    bench50: pd.DataFrame | None
    bench500: pd.DataFrame | None
    rs_rating: float  # 0-99 percentile vs universe
    sector: str
    sector_row: pd.Series | None
    delivery: pd.Series | None


def relative_returns(df: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    if bench is None:
        return {}
    c = df["Close"]
    b = bench["Close"].reindex(c.index).ffill()
    return {p: _ret(c, n) - _ret(b, n) for p, n in (("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126))}


def correction_resilience(df: pd.DataFrame, bench: pd.DataFrame | None, days: int = 63) -> float | None:
    """Average excess return on sessions where the benchmark fell more than 0.5%."""
    if bench is None or len(df) < days + 1:
        return None
    s = df["Close"].pct_change().iloc[-days:]
    b = bench["Close"].reindex(df.index).ffill().pct_change().iloc[-days:]
    down = b < -0.005
    if down.sum() < 3:
        return None
    return float((s[down] - b[down]).mean() * 100.0)


def rs_line_new_high(df: pd.DataFrame, bench: pd.DataFrame | None, n: int = 50) -> bool | None:
    if bench is None or len(df) < n + 5:
        return None
    line = df["Close"] / bench["Close"].reindex(df.index).ffill()
    return bool(line.iloc[-1] >= line.iloc[-n:].max() * 0.995)


def rs_composite(df: pd.DataFrame) -> float:
    """IBD-style weighted return used to rank RS across the universe."""
    c = df["Close"]
    parts = [(_ret(c, 63), 0.4), (_ret(c, 126), 0.2), (_ret(c, 189), 0.2), (_ret(c, 252), 0.2)]
    parts = [(v, w) for v, w in parts if not np.isnan(v)]
    if not parts:
        return np.nan
    return sum(v * w for v, w in parts) / sum(w for _, w in parts)


@dataclass
class Assessment:
    symbol: str
    categories: dict[str, Category]
    patterns: list[Pattern]
    rel: dict
    rel500: dict
    resilience: float | None
    extended: bool
    extension_note: str
    flags: list[str]
    weekly_ok: bool | None
    monthly_ok: bool | None

    @property
    def score(self) -> float:
        avail = {k: c.score for k, c in self.categories.items() if c.score is not None}
        wsum = sum(WEIGHTS[k] for k in avail)
        return round(sum(avail.values()) / wsum * 100.0, 1) if wsum else 0.0

    @property
    def coverage(self) -> int:
        """Points out of 100 backed by data (categories weighted by sub-check availability)."""
        total = 0.0
        for k, c in self.categories.items():
            full = c.available + c.missing_pts
            total += WEIGHTS[k] * (c.available / full if full else 0)
        return int(round(total))

    def category_scores(self) -> dict[str, float | None]:
        return {k: (None if c.score is None else round(c.score, 1)) for k, c in self.categories.items()}


def assess_technical(ctx: StockContext) -> Assessment:
    df, r = ctx.df, ctx.df.iloc[-1]
    flags: list[str] = []

    # ---------------- Trend (20) ----------------
    t = Category("Trend")
    t.add(r.Close > r.EMA20, 3, "close above 20 EMA")
    t.add(r.Close > r.EMA50, 3, "close above 50 EMA")
    t.add(r.EMA20 > r.EMA50, 2, "20 EMA above 50 EMA")
    t.add(None if _nan(r.EMA200) else r.EMA50 > r.EMA200, 3, "50 EMA above 200 EMA")
    t.add(r.EMA20 > df["EMA20"].iloc[-6], 2, "20 EMA rising")
    t.add(r.EMA50 > df["EMA50"].iloc[-11], 2, "50 EMA rising")
    hh, hl = higher_highs_lows(df)
    t.add(hh and hl, 2, "higher highs and higher lows", partial=(0.5 * hh + 0.5 * hl))
    wk = resample_ohlcv(df, "W-FRI")
    weekly_ok = None
    if len(wk) >= 35:
        w10, w30 = ema(wk["Close"], 10), ema(wk["Close"], 30)
        weekly_ok = bool(wk["Close"].iloc[-1] > w10.iloc[-1] > w30.iloc[-1])
    t.add(weekly_ok, 2, "weekly close above rising 10/30-week EMAs")
    mo = resample_ohlcv(df, "ME")
    monthly_ok = None
    if len(mo) >= 12:
        m10 = ema(mo["Close"], 10)
        monthly_ok = bool(mo["Close"].iloc[-1] > m10.iloc[-1] and m10.iloc[-1] >= m10.iloc[-2])
    t.add(monthly_ok, 1, "monthly close above rising 10-month EMA")

    # ---------------- Relative strength (20) ----------------
    rs = Category("Relative strength")
    rel = relative_returns(df, ctx.bench50)
    rel500 = relative_returns(df, ctx.bench500)
    rs.add(None if np.isnan(ctx.rs_rating) else True, 8, f"RS rating {ctx.rs_rating:.0f} of 99",
           partial=None if np.isnan(ctx.rs_rating) else max(0.0, (ctx.rs_rating - 40) / 59))
    for label, d in (("Nifty 50", rel), ("Nifty 500", rel500)):
        for p in ("1W", "1M", "3M"):
            v = d.get(p)
            key = f"outperforming {label} over {p}"
            rs.add(None if _nan(v) else v > 0, 1, key)
    res = correction_resilience(df, ctx.bench500 if ctx.bench500 is not None else ctx.bench50)
    rs.add(None if res is None else res > 0, 3, "outperforms on index down days" + (f" ({res:+.2f}% avg)" if res is not None else ""),
           partial=None if res is None else min(1.0, max(0.0, res / 0.8)))
    nh = rs_line_new_high(df, ctx.bench500 if ctx.bench500 is not None else ctx.bench50)
    rs.add(nh, 3, "RS line at a 50-day high")

    # ---------------- Volume (15) ----------------
    v = Category("Volume")
    rvol = r.RVOL
    v.add(None if _nan(rvol) else rvol >= 1.5, 4, f"volume {rvol:.1f}x the 20-day average",
          partial=None if _nan(rvol) else (rvol - 1.0) / 0.5)
    d = ctx.delivery
    if d is not None and not _nan(d.get("DelivPct")) and not _nan(d.get("DelivPct20")):
        strong = d["DelivPct"] >= max(d["DelivPct20"] * 1.1, 40) or (not _nan(d.get("DelivQtyRatio")) and d["DelivQtyRatio"] >= 1.5)
        v.add(strong, 3, f"delivery {d['DelivPct']:.0f}% vs {d['DelivPct20']:.0f}% 20-day average"
              + (f", delivered qty {d['DelivQtyRatio']:.1f}x" if not _nan(d.get("DelivQtyRatio")) else ""))
    else:
        v.add(None, 3, "delivery volume")
    last50 = df.iloc[-50:]
    up = last50[last50["Close"] > last50["Close"].shift(1)]["Volume"].mean()
    dn = last50[last50["Close"] < last50["Close"].shift(1)]["Volume"].mean()
    ud = up / dn if dn else np.nan
    v.add(None if _nan(ud) else ud > 1.2, 3, f"up-day volume {ud:.2f}x down-day volume (50 sessions)",
          partial=None if _nan(ud) else (ud - 1.0) / 0.4)
    pull = df.iloc[-10:]
    pull_dn = pull[pull["Close"] < pull["Close"].shift(1)]
    contraction = None if len(pull_dn) == 0 else bool(pull_dn["Volume"].mean() < r.Vol20)
    v.add(contraction, 2, "volume contracts on down days")
    obv_hi = df["OBV"].iloc[-1] >= df["OBV"].iloc[-50:].max() * 0.98 if len(df) > 50 else None
    adl_up = df["ADL"].iloc[-1] > df["ADL"].iloc[-21] if len(df) > 21 else None
    v.add(None if obv_hi is None else bool(obv_hi or adl_up), 3, "accumulation (OBV near 50-day high or A/D line rising)",
          partial=None if obv_hi is None else 0.6 * bool(obv_hi) + 0.4 * bool(adl_up))
    if not _nan(rvol) and rvol >= 5:
        flags.append(f"Abnormal volume spike ({rvol:.1f}x average). Check for news or a block deal.")

    # ---------------- Breakout quality (15) ----------------
    pats = detect_patterns(df)
    b = Category("Breakout")
    atr = r.ATR14
    ext_atr = (r.Close - r.EMA20) / atr if atr else np.nan
    ret5 = _ret(df["Close"], 5)
    best = pats[0] if pats else None
    ext_pivot = (r.Close / best.pivot - 1) * 100 if best else np.nan
    extended = bool((not _nan(ext_atr) and ext_atr > 3.0) or (not _nan(ret5) and ret5 > 15)
                    or (best is not None and best.stage == "confirmed" and ext_pivot > 5))
    ext_note = f"{ext_atr:.1f} ATR above the 20 EMA, {ret5:+.1f}% in 5 sessions"
    b.add(True, 10, best.name if best else "no constructive base or breakout", partial=(best.quality / 10) if best else 0)
    early = 0.0
    if best is not None:
        if extended:
            early = 0.0
        elif best.stage in ("pre-breakout", "retest"):
            early = 1.0
        else:
            early = max(0.0, 1 - max(ext_pivot, 0) / 5)
    b.add(True, 5, "early stage, close to the pivot" if early >= 0.5 else "not early stage", partial=early)
    if extended:
        flags.append(f"Extended: {ext_note}. Do not chase; wait for a pullback.")

    # ---------------- Momentum indicators (10) ----------------
    m = Category("Momentum")
    rsi = r.RSI14
    rsi_pts = 1.0 if 55 <= rsi <= 75 else 0.67 if 75 < rsi <= 85 else 0.33 if 50 <= rsi < 55 else 0.0
    m.add(True, 3, f"RSI {rsi:.0f}", partial=rsi_pts)
    m.add(bool(r.MACD > r.Signal and r.Hist > 0), 2, "MACD above signal, histogram positive")
    fresh = bool((df["Hist"].iloc[-6:-1] <= 0).any() and r.Hist > 0)
    m.add(fresh, 1, "fresh MACD bullish crossover (5 sessions)")
    m.add(bool(r.ADX > 20 and r["+DI"] > r["-DI"]), 2, f"ADX {r.ADX:.0f} with +DI above -DI")
    m.add(bool(r.ST_Dir == 1), 1, "Supertrend (10,3) bullish")
    sq = df["BBW_pctile"].iloc[-12:-1]
    expanding = bool((sq <= 0.2).any() and r.BB_Width > df["BB_Width"].iloc[-3] and r.Close > r.BB_Mid)
    m.add(expanding, 1, "Bollinger squeeze resolving upward")
    if rsi > 80:
        flags.append(f"RSI {rsi:.0f} is stretched; strength is real but entries carry more pullback risk.")

    # ---------------- Sector (10) ----------------
    s = Category("Sector")
    sr = ctx.sector_row
    if sr is not None:
        s.add(True, 6, f"{ctx.sector} ranked {int(sr['Rank'])} by relative strength", partial=float(sr["Pctile"]))
        s.add(sr["Rotation"] == "Rotating in" or (sr["Rotation"] == "Steady" and sr["RS1M"] > 0), 2,
              f"sector momentum {sr['Rotation'].lower()}")
        s.add(True, 2, f"{sr['Above50DMA%']:.0f}% of sector above 50-day average", partial=sr["Above50DMA%"] / 70)
    else:
        for lbl, pts in (("sector rank", 6), ("sector rotation", 2), ("sector breadth", 2)):
            s.add(None, pts, lbl)

    cats = {c.name: c for c in (t, rs, v, b, m, Category("Fundamentals"), s)}
    return Assessment(ctx.symbol, cats, pats, rel, rel500, res, extended, ext_note, flags, weekly_ok, monthly_ok)


def classify(score: float) -> str:
    if score >= 85:
        return "High priority"
    if score >= 75:
        return "Strong watchlist"
    if score >= 65:
        return "Developing"
    return "Reject"
