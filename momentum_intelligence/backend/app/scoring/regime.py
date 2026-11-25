"""Five-state market regime from explicit, documented component scores.

Each component is scored on -2..+2, weighted, and the weighted mean is scaled
to -100..+100. Components without data are left out and the score is
renormalised; ``coverage`` reports the share of weight that had data. The
thresholds and the per-regime entry policy are configuration
(``StrategyConfig.regime``). A regime only restricts NEW entries.

| Component | Weight | +2 ... -2 |
|---|---|---|
| nifty50_trend | 2.0 | close vs SMA50, close vs SMA200, SMA50 vs SMA200, SMA200 20-session slope: +1/-1 each, halved |
| nifty500_trend | 1.0 | same, Nifty 500 |
| breadth_200 | 1.5 | % eligible above SMA200: >=65, >=50, >=35, >=20, else |
| breadth_50 | 1.0 | % eligible above SMA50: >=70, >=55, >=40, >=25, else |
| sector_participation | 1.0 | % ranked sectors with composite above its SMA50: >=70, >=55, >=40, >=25, else |
| volatility | 1.0 | India VIX <13: +1, <18: 0, <24: -1, else -2; a 10-session rise >25% subtracts 1 (floor -2) |
| rs_distribution | 1.0 | median 63-session return of the eligible universe: >8%, >3%, >-3%, >-8%, else |
"""

from __future__ import annotations

from ..config import RegimeConfig

VERSION = "1"
WEIGHTS = {"nifty50_trend": 2.0, "nifty500_trend": 1.0, "breadth_200": 1.5, "breadth_50": 1.0,
           "sector_participation": 1.0, "volatility": 1.0, "rs_distribution": 1.0}


def _ladder(x: float | None, cuts: tuple[float, float, float, float]) -> float | None:
    if x is None:
        return None
    a, b, c, d = cuts
    return 2.0 if x >= a else 1.0 if x >= b else 0.0 if x >= c else -1.0 if x >= d else -2.0


def trend_points(close, sma50, sma200, sma200_slope) -> float | None:
    if None in (close, sma50, sma200, sma200_slope):
        return None
    pts = sum(1 if cond else -1 for cond in (close > sma50, close > sma200, sma50 > sma200, sma200_slope > 0))
    return pts / 2.0


def volatility_points(vix: float | None, vix_change_10_pct: float | None) -> float | None:
    if vix is None:
        return None
    p = 1.0 if vix < 13 else 0.0 if vix < 18 else -1.0 if vix < 24 else -2.0
    if vix_change_10_pct is not None and vix_change_10_pct > 25:
        p -= 1
    return max(p, -2.0)


def component_points(inp: dict) -> dict[str, float | None]:
    return {
        "nifty50_trend": trend_points(*(inp.get(k) for k in ("n50_close", "n50_sma50", "n50_sma200", "n50_sma200_slope"))),
        "nifty500_trend": trend_points(*(inp.get(k) for k in ("n500_close", "n500_sma50", "n500_sma200", "n500_sma200_slope"))),
        "breadth_200": _ladder(inp.get("breadth200_pct"), (65, 50, 35, 20)),
        "breadth_50": _ladder(inp.get("breadth50_pct"), (70, 55, 40, 25)),
        "sector_participation": _ladder(inp.get("sector_participation_pct"), (70, 55, 40, 25)),
        "volatility": volatility_points(inp.get("vix"), inp.get("vix_change_10_pct")),
        "rs_distribution": _ladder(None if inp.get("median_ret_63_pct") is None else inp["median_ret_63_pct"],
                                   (8.0, 3.0, -3.0, -8.0)),
    }


def classify(inp: dict, cfg: RegimeConfig) -> dict:
    pts = component_points(inp)
    have = {k: v for k, v in pts.items() if v is not None}
    total_w = sum(WEIGHTS.values())
    used_w = sum(WEIGHTS[k] for k in have)
    score = 0.0 if used_w == 0 else sum(WEIGHTS[k] * v for k, v in have.items()) / used_w / 2.0 * 100
    score = round(score, 2)
    if score >= cfg.strong_bullish_min:
        regime = "strong_bullish"
    elif score >= cfg.moderately_bullish_min:
        regime = "moderately_bullish"
    elif score > cfg.sideways_min:
        regime = "sideways"
    elif score > cfg.moderately_bearish_min:
        regime = "moderately_bearish"
    else:
        regime = "strong_bearish"
    coverage = round(used_w / total_w, 3)
    # Too little evidence: never call it bullish
    if coverage < 0.5 and regime in ("strong_bullish", "moderately_bullish"):
        regime = "sideways"
    return {"regime": regime, "score": score, "coverage": coverage,
            "components": {k: {"points": pts[k], "weight": WEIGHTS[k]} for k in WEIGHTS},
            "inputs": {k: (None if v is None else round(float(v), 4)) for k, v in inp.items()},
            "policy": cfg.policies[regime].model_dump()}
