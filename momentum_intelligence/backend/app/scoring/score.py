"""The 100-point momentum score.

Groups and maximum points (StrategyConfig.score_weights): trend 25, relative
strength 20, volume 15, price action 15, sector 10, momentum indicators 10,
risk/reward 5. Each group is a list of sub-checks with fixed points; a group's
points are scaled to its configured weight. A sub-check whose input is missing
is N/A: it is shown, scores nothing and is removed from the denominator, so

    total = 100 * earned / available,   coverage = available / 100

Every sub-check is returned (id, group, rule, value, points, max, status).
Only scale-invariant inputs are used (ratios, percentages, oscillators), so the
score is the same whether prices are adjusted point-in-time or back-adjusted.

Gate: only stocks eligible in that session's universe are scored. The score
never bypasses the risk engine and is not a recommendation.

Risk/reward here is a PROXY (ATR% and distance to the 50-day average) until
Phase 5 strategies set actual entry, stop and target levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import ScoreBands, ScoreWeights

VERSION = "1"


@dataclass(frozen=True)
class Check:
    id: str
    group: str
    rule: str
    max: float
    inputs: tuple[str, ...]
    fn: Callable[..., float]  # returns points in [0, max]


def _b(cond: bool, pts: float) -> float:
    return pts if cond else 0.0


CHECKS: list[Check] = [
    # Trend (25)
    Check("t_above_sma50", "trend", "close above 50-day SMA", 4, ("dist_sma_50_pct",), lambda d: _b(d > 0, 4)),
    Check("t_above_sma200", "trend", "close above 200-day SMA", 4, ("dist_sma_200_pct",), lambda d: _b(d > 0, 4)),
    Check("t_sma50_above_sma200", "trend", "50-day SMA above 200-day SMA", 4, ("sma50_over_sma200",), lambda r: _b(r > 1, 4)),
    Check("t_sma200_rising", "trend", "200-day SMA rising over 20 sessions", 3, ("sma_200_slope_20",), lambda s: _b(s > 0, 3)),
    Check("t_sma50_rising", "trend", "50-day SMA rising over 10 sessions", 3, ("sma_50_slope_10",), lambda s: _b(s > 0, 3)),
    Check("t_adx_trend", "trend", "ADX >= 20 with +DI above -DI", 4, ("adx_14", "di_spread"),
          lambda a, s: _b(a >= 20 and s > 0, 4)),
    Check("t_weekly_trend", "trend", "weekly close above rising 30-week SMA (completed weeks)", 3,
          ("w_close_over_sma30", "w_sma_30_slope_4"), lambda r, s: _b(r > 1 and s > 0, 3)),
    # Relative strength (20)
    Check("rs_rank", "relative_strength", "RS rank: >=90: 12, >=80: 9, >=70: 6, >=60: 3", 12, ("rs_rank",),
          lambda r: 12 if r >= 90 else 9 if r >= 80 else 6 if r >= 70 else 3 if r >= 60 else 0),
    Check("rs_vs_nifty_63", "relative_strength", "outperformed Nifty 50 over 63 sessions", 3, ("excess_63",), lambda e: _b(e > 0, 3)),
    Check("rs_vs_sector_63", "relative_strength", "outperformed its sector composite over 63 sessions", 3, ("excess_sector_63",),
          lambda e: _b(e > 0, 3)),
    Check("rs_not_falling", "relative_strength", "RS rank not lower than 20 sessions ago", 2, ("rs_rank_change",), lambda c: _b(c >= 0, 2)),
    # Volume (15)
    Check("v_expansion", "volume", "20-day average volume above 50-day average", 4, ("vol20_over_vol50",), lambda r: _b(r > 1, 4)),
    Check("v_obv_rising", "volume", "OBV higher than 20 sessions ago", 4, ("obv_slope_20",), lambda s: _b(s > 0, 4)),
    Check("v_updown", "volume", "up-day volume / down-day volume over 20 sessions >= 1.2", 4, ("updown_vol_ratio_20",),
          lambda r: _b(r >= 1.2, 4)),
    Check("v_demand_today", "volume", "today up on >= 1.5x the 20-day average volume", 3, ("rel_volume_20", "chg_1d_pct"),
          lambda rv, ch: _b(rv >= 1.5 and ch > 0, 3)),
    # Price action (15)
    Check("p_near_high", "price_action", "within 10% of 52-week high: 4; within 25%: 2", 4, ("dist_high_252_pct",),
          lambda d: 4 if d >= -10 else 2 if d >= -25 else 0),
    Check("p_off_low", "price_action", "at least 30% above 52-week low", 3, ("above_low_252_pct",), lambda d: _b(d >= 30, 3)),
    Check("p_contraction", "price_action", "volatility contraction: Bollinger width in lowest 30% of 126 sessions or ATR10/ATR50 < 0.85",
          3, ("bb_width_pctrank_126", "atr_10_50_ratio"), lambda p, a: _b(p <= 30 or a < 0.85, 3)),
    Check("p_at_55d_high", "price_action", "close within 3% of (or above) the prior 55-session high", 3, ("close_over_prior_high_55",),
          lambda r: _b(r >= 0.97, 3)),
    Check("p_not_extended", "price_action", "not extended: close at most 10% above the 20-day SMA", 2, ("dist_sma_20_pct",),
          lambda d: _b(d <= 10, 2)),
    # Sector (10)
    Check("s_rank", "sector", "sector rank in top third: 5; middle third: 2", 5, ("sector_rank_pct",),
          lambda p: 5 if p <= 1 / 3 else 2 if p <= 2 / 3 else 0),
    Check("s_trend", "sector", "sector composite above its 50-day average", 3, ("sector_above_sma50",), lambda b: _b(bool(b), 3)),
    Check("s_breadth", "sector", "at least 50% of sector constituents above their 50-day SMA", 2, ("sector_breadth50_pct",),
          lambda b: _b(b >= 50, 2)),
    # Momentum indicators (10)
    Check("m_rsi_zone", "momentum_indicators", "RSI(14) 55-75: 4; 50-55 or 75-80: 2", 4, ("rsi_14",),
          lambda r: 4 if 55 <= r <= 75 else 2 if (50 <= r < 55 or 75 < r <= 80) else 0),
    Check("m_macd", "momentum_indicators", "MACD above signal and above zero", 3, ("macd_over_signal", "macd_sign"),
          lambda o, s: _b(o > 0 and s > 0, 3)),
    Check("m_roc_63", "momentum_indicators", "63-session rate of change positive", 3, ("roc_63",), lambda r: _b(r > 0, 3)),
    # Risk / reward proxy (5)
    Check("r_atr_band", "risk_reward", "ATR% between 1.5% and 5% (stop distance workable for 2-8 weeks) [proxy]", 3, ("atr_pct",),
          lambda a: _b(1.5 <= a <= 5, 3)),
    Check("r_near_support", "risk_reward", "close within 15% of the 50-day SMA (structure for a stop) [proxy]", 2, ("dist_sma_50_pct",),
          lambda d: _b(-5 <= d <= 15, 2)),
]

GROUP_MAX = {}
for c in CHECKS:
    GROUP_MAX[c.group] = GROUP_MAX.get(c.group, 0) + c.max


def derive_inputs(ind: dict, rs: dict | None, sector: dict | None, n_sectors_ranked: int | None) -> dict:
    """Scale-invariant inputs from an indicator row, the RS record and the stock's sector record."""
    def ratio(a, b):
        x, y = ind.get(a), ind.get(b)
        return None if x is None or y in (None, 0) else x / y
    out = {k: ind.get(k) for k in ("dist_sma_50_pct", "dist_sma_200_pct", "dist_sma_20_pct", "sma_200_slope_20", "sma_50_slope_10",
                                   "adx_14", "obv_slope_20", "updown_vol_ratio_20", "rel_volume_20", "chg_1d_pct",
                                   "dist_high_252_pct", "above_low_252_pct", "bb_width_pctrank_126", "atr_10_50_ratio",
                                   "rsi_14", "roc_63", "atr_pct", "w_sma_30_slope_4")}
    out["sma50_over_sma200"] = ratio("sma_50", "sma_200")
    out["di_spread"] = None if ind.get("plus_di") is None or ind.get("minus_di") is None else ind["plus_di"] - ind["minus_di"]
    out["w_close_over_sma30"] = ratio("w_close", "w_sma_30")
    out["vol20_over_vol50"] = ratio("vol_sma_20", "vol_sma_50")
    out["close_over_prior_high_55"] = ratio("close", "prior_high_55")
    m, s = ind.get("macd"), ind.get("macd_signal")
    out["macd_over_signal"] = None if m is None or s is None else m - s
    out["macd_sign"] = m
    rs = rs or {}
    out["rs_rank"] = rs.get("rs_rank")
    out["rs_rank_change"] = rs.get("rs_rank_change")
    vals = rs.get("values", {})
    out["excess_63"] = vals.get("excess_63")
    out["excess_sector_63"] = vals.get("excess_sector_63")
    if sector and sector.get("rank") and n_sectors_ranked:
        out["sector_rank_pct"] = sector["rank"] / n_sectors_ranked
        out["sector_above_sma50"] = sector["values"].get("above_sma50")
        out["sector_breadth50_pct"] = sector["values"].get("breadth50_pct")
    else:
        out["sector_rank_pct"] = out["sector_above_sma50"] = out["sector_breadth50_pct"] = None
    return out


def band_of(total: float, bands: ScoreBands) -> str:
    return "priority" if total >= bands.priority else "strong" if total >= bands.strong else \
        "developing" if total >= bands.developing else "watchlist"


def score(inputs: dict, weights: ScoreWeights, bands: ScoreBands) -> dict:
    wmap = weights.model_dump()
    breakdown, earned, available = [], 0.0, 0.0
    groups: dict[str, dict] = {}
    for c in CHECKS:
        scale = wmap[c.group] / GROUP_MAX[c.group]  # group points scaled to its configured weight
        vals = [inputs.get(k) for k in c.inputs]
        g = groups.setdefault(c.group, {"points": 0.0, "available": 0.0, "max": wmap[c.group]})
        if any(v is None for v in vals):
            breakdown.append({"id": c.id, "group": c.group, "rule": c.rule, "status": "n/a", "points": 0.0,
                              "max": round(c.max * scale, 3), "value": None})
            continue
        pts = float(c.fn(*vals)) * scale
        mx = c.max * scale
        earned += pts
        available += mx
        g["points"] += pts
        g["available"] += mx
        breakdown.append({"id": c.id, "group": c.group, "rule": c.rule, "status": "pass" if pts >= mx - 1e-9 else ("partial" if pts > 0 else "fail"),
                          "points": round(pts, 3), "max": round(mx, 3),
                          "value": [round(float(v), 4) if isinstance(v, (int, float)) else v for v in vals]})
    total = 0.0 if available == 0 else round(earned / available * 100, 2)
    for g in groups.values():
        g["points"], g["available"] = round(g["points"], 3), round(g["available"], 3)
    return {"total": total, "coverage": round(available / 100, 3), "band": band_of(total, bands), "groups": groups,
            "breakdown": breakdown}
