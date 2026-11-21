"""Market regime, breadth and sector strength.

Everything here is computed from end-of-day closes of the index and of the
scanned universe itself, so it needs no extra data source.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import ema


def _ret(s: pd.Series, n: int) -> float:
    if len(s) <= n:
        return np.nan
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1.0) * 100.0


@dataclass
class IndexTrend:
    name: str
    close: float
    ret_1w: float
    ret_1m: float
    ret_3m: float
    above_ema20: bool
    above_ema50: bool
    above_ema200: bool
    ema50_over_200: bool
    ema20_rising: bool
    drawdown_pct: float  # from 52-week high

    @property
    def score(self) -> int:
        pts = [self.above_ema20, self.above_ema50, self.above_ema200, self.ema50_over_200, self.ema20_rising]
        return int(sum(pts))

    @property
    def label(self) -> str:
        return {5: "Strong uptrend", 4: "Uptrend", 3: "Mixed", 2: "Weakening"}.get(self.score, "Downtrend")


def index_trend(name: str, df: pd.DataFrame | None) -> IndexTrend | None:
    if df is None or len(df) < 210:
        return None
    c = df["Close"]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    return IndexTrend(
        name=name,
        close=float(c.iloc[-1]),
        ret_1w=_ret(c, 5),
        ret_1m=_ret(c, 21),
        ret_3m=_ret(c, 63),
        above_ema20=bool(c.iloc[-1] > e20.iloc[-1]),
        above_ema50=bool(c.iloc[-1] > e50.iloc[-1]),
        above_ema200=bool(c.iloc[-1] > e200.iloc[-1]),
        ema50_over_200=bool(e50.iloc[-1] > e200.iloc[-1]),
        ema20_rising=bool(e20.iloc[-1] > e20.iloc[-6]),
        drawdown_pct=float(c.iloc[-1] / c.iloc[-252:].max() - 1.0) * 100.0,
    )


def breadth(frames: dict[str, pd.DataFrame]) -> dict:
    """Universe breadth on the latest session (expects enriched frames)."""
    rows = []
    for df in frames.values():
        if len(df) < 60:
            continue
        r, p = df.iloc[-1], df.iloc[-2]
        rows.append(
            {
                "adv": r.Close > p.Close,
                "dec": r.Close < p.Close,
                "a20": r.Close > r.EMA20 if not np.isnan(r.EMA20) else np.nan,
                "a50": r.Close > r.SMA50 if not np.isnan(r.SMA50) else np.nan,
                "a200": r.Close > r.SMA200 if not np.isnan(r.SMA200) else np.nan,
                "nh": (r.High >= r.High252) if not np.isnan(r.High252) else False,
                "nl": (r.Low <= r.Low252) if not np.isnan(r.Low252) else False,
            }
        )
    d = pd.DataFrame(rows)
    if d.empty:
        return {}
    return {
        "stocks": len(d),
        "advances": int(d["adv"].sum()),
        "declines": int(d["dec"].sum()),
        "pct_above_20": float(d["a20"].mean() * 100),
        "pct_above_50": float(d["a50"].mean() * 100),
        "pct_above_200": float(d["a200"].mean() * 100),
        "new_highs": int(d["nh"].sum()),
        "new_lows": int(d["nl"].sum()),
    }


def breadth_history(frames: dict[str, pd.DataFrame], days: int = 10) -> pd.Series:
    """% of stocks above their 50-day SMA for each of the last ``days`` sessions."""
    cols = {}
    for sym, df in frames.items():
        if len(df) >= 60:
            cols[sym] = (df["Close"] > df["SMA50"]).where(df["SMA50"].notna()).iloc[-days:]
    if not cols:
        return pd.Series(dtype=float)
    return pd.DataFrame(cols).mean(axis=1) * 100


@dataclass
class Regime:
    label: str  # RISK-ON / NEUTRAL / RISK-OFF
    score: float  # 0-100
    risk_per_trade_pct: float
    max_open_risk_pct: float
    min_score_ready: float
    notes: list[str] = field(default_factory=list)


def market_regime(n50: IndexTrend | None, n500: IndexTrend | None, br: dict, br_hist: pd.Series,
                  user_risk_pct: float, user_max_open: float) -> Regime:
    pts, maxpts, notes = 0.0, 0.0, []
    for it in (n50, n500):
        if it is None:
            continue
        pts += it.score * 6
        maxpts += 30
        notes.append(f"{it.name}: {it.label.lower()}, {it.ret_1m:+.1f}% over 1 month, {it.drawdown_pct:.1f}% from its 52-week high")
    if br:
        pts += min(br["pct_above_50"], 80) / 80 * 25
        pts += min(br["pct_above_200"], 80) / 80 * 15
        maxpts += 40
        nh, nl = br["new_highs"], br["new_lows"]
        notes.append(f"Breadth: {br['pct_above_50']:.0f}% of stocks above the 50-day average, {br['pct_above_200']:.0f}% above the 200-day")
        notes.append(f"Last session: {br['advances']} advances vs {br['declines']} declines, {nh} new 52-week highs vs {nl} new lows")
    if len(br_hist) >= 5:
        delta = br_hist.iloc[-1] - br_hist.iloc[0]
        notes.append(f"Breadth trend: {'improving' if delta > 3 else 'deteriorating' if delta < -3 else 'flat'} ({delta:+.0f} points over {len(br_hist)} sessions)")
    if br and br["pct_above_200"] < 50 <= br["pct_above_50"]:
        notes.append("Short-term breadth has recovered but most stocks are still below their 200-day average: "
                     "an early-stage recovery, so favour leaders and keep stops tight")
    score = pts / maxpts * 100 if maxpts else 50.0
    if score >= 65:
        return Regime("RISK-ON", score, user_risk_pct, user_max_open, 75, notes)
    if score >= 45:
        return Regime("NEUTRAL", score, min(user_risk_pct, 0.75), min(user_max_open, 2.0), 80, notes)
    return Regime("RISK-OFF", score, min(user_risk_pct, 0.5), min(user_max_open, 1.0), 85, notes)


def sector_strength(frames: dict[str, pd.DataFrame], industry: dict[str, str], bench: pd.DataFrame | None) -> pd.DataFrame:
    """Median sector returns relative to the median stock in the universe, breadth and rotation.

    Comparing a sector's median stock with a cap-weighted index would make every
    sector look weak whenever a few heavyweights lead, so the benchmark here is
    the median stock of the whole universe.
    """
    rows = []
    for sym, df in frames.items():
        ind = industry.get(sym) or "Unclassified"
        if len(df) < 70:
            continue
        c = df["Close"]
        rows.append({
            "Sector": ind, "Symbol": sym,
            "r1w": _ret(c, 5), "r1m": _ret(c, 21), "r3m": _ret(c, 63),
            "a50": float(c.iloc[-1] > df["SMA50"].iloc[-1]),
        })
    d = pd.DataFrame(rows)
    if d.empty:
        return pd.DataFrame()
    b1w, b1m, b3m = d["r1w"].median(), d["r1m"].median(), d["r3m"].median()
    g = d.groupby("Sector")
    s = pd.DataFrame({
        "Stocks": g.size(),
        "Ret1W%": g["r1w"].median(),
        "Ret1M%": g["r1m"].median(),
        "Ret3M%": g["r3m"].median(),
        "Above50DMA%": g["a50"].mean() * 100,
    })
    s["RS1W"] = s["Ret1W%"] - b1w
    s["RS1M"] = s["Ret1M%"] - b1m
    s["RS3M"] = s["Ret3M%"] - b3m
    s["Composite"] = 0.2 * s["RS1W"] + 0.3 * s["RS1M"] + 0.5 * s["RS3M"]
    s = s[s["Stocks"] >= 3].sort_values("Composite", ascending=False)
    s["Rank"] = np.arange(1, len(s) + 1)
    rank_1m = s["RS1M"].rank(ascending=False)
    rank_3m = s["RS3M"].rank(ascending=False)
    shift = rank_3m - rank_1m  # positive = recent performance better than longer-term
    s["Rotation"] = np.where(shift >= 3, "Rotating in", np.where(shift <= -3, "Rotating out", "Steady"))
    s["Pctile"] = 1 - (s["Rank"] - 1) / max(len(s) - 1, 1)
    return s.round(2)
