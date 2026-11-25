"""Phase 4 unit tests: RS normalisation and classes, sector composites and ranking, regime states, score rules."""

import numpy as np
import pandas as pd
import pytest

from app.config import RegimeConfig, RelativeStrengthConfig, ScoreBands, ScoreWeights, StrategyConfig
from app.scoring import regime as R
from app.scoring import relative_strength as RS
from app.scoring import score as S
from app.scoring import sectors as SEC

DATES = pd.bdate_range("2025-01-01", periods=300).date


def panel(series: dict[int, np.ndarray]) -> pd.DataFrame:
    return pd.DataFrame(series, index=DATES)


def growth(rate, n=300, start=100.0):
    return start * np.cumprod(np.r_[1.0, np.full(n - 1, 1 + rate)])


# ------------------------------------------------------------------ relative strength

def test_excess_return_is_relative_performance():
    assert RS.excess(pd.DataFrame({1: [0.20]}), pd.Series([0.10])).iat[0, 0] == pytest.approx(1.2 / 1.1 - 1)


def test_rank_is_1_to_99_over_eligible_only():
    v = pd.DataFrame([[0.1, 0.2, 0.3, 0.4, 9.9]], columns=[1, 2, 3, 4, 5])
    mask = pd.DataFrame([[True, True, True, True, False]], columns=[1, 2, 3, 4, 5])
    r = RS.rank_1_99(v, mask).iloc[0]
    assert r[1] == 1 and r[4] == 99 and np.isnan(r[5]) and r[2] < r[3]
    assert RS.rank_1_99(pd.DataFrame([[0.5]]), pd.DataFrame([[True]])).iat[0, 0] == 50


def test_rs_rank_orders_by_outperformance_and_needs_min_window():
    close = panel({1: growth(0.002), 2: growth(0.001), 3: growth(0.0), 4: growth(-0.001)})
    close.iloc[:250, 3] = np.nan          # stock 4 listed late: only ~50 sessions of history
    elig = close.notna()
    bench = pd.Series(growth(0.0005), index=DATES)
    out = RS.compute(close, elig, bench, RelativeStrengthConfig())
    last = out["rank"].iloc[-1]
    assert last[1] > last[2] > last[3] and np.isnan(last[4])   # < 63 sessions: not ranked
    assert out["excess_63"].iloc[-1][1] > 0 > out["excess_63"].iloc[-1][3]


def test_rs_renormalises_when_long_windows_are_missing():
    close = panel({1: growth(0.001), 2: growth(0.001)})
    close.iloc[:200, 1] = np.nan  # stock 2 has 100 sessions: 21 and 63 windows only
    out = RS.compute(close, close.notna(), pd.Series(growth(0.0), index=DATES), RelativeStrengthConfig())
    e21, e63 = out["excess_21"].iloc[-1][2], out["excess_63"].iloc[-1][2]
    assert out["combined"].iloc[-1][2] == pytest.approx((0.2 * e21 + 0.4 * e63) / 0.6)


def test_rs_classes_and_resilience():
    n = 300
    base = growth(0.0, n)
    rng = np.random.default_rng(0)
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=DATES)
    br = bench.pct_change().fillna(0).to_numpy()
    # stock 1: falls only half as much as the index on down days, flat on up days -> resilient
    s1 = 100 * np.cumprod(1 + np.where(br < 0, br * 0.5, 0.0))
    # stock 2: weak until the last 25 sessions, then surges -> improving
    s2 = np.r_[growth(-0.002, n - 25), growth(0.03, 25, growth(-0.002, n - 25)[-1])]
    others = {k: 100 * np.cumprod(1 + br * rng.uniform(0.8, 1.4) + rng.normal(0, 0.004, n)) for k in range(3, 23)}
    close = panel({1: s1, 2: s2, **others})
    out = RS.compute(close, close.notna(), bench, RelativeStrengthConfig())
    d = DATES[-1]
    assert out["cls_resilient"].at[d, 1]
    assert out["cls_improving"].at[d, 2] and out["rank_change"].at[d, 2] >= 20
    assert out["rank"].at[d, 2] > out["rank"].shift(20).at[d, 2]


def test_rs_has_no_look_ahead():
    rng = np.random.default_rng(5)
    close = panel({k: 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, 300)) for k in range(10)})
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 300)), index=DATES)
    full = RS.compute(close, close.notna(), bench, RelativeStrengthConfig())
    t = 200
    part = RS.compute(close.iloc[:t + 1], close.iloc[:t + 1].notna(), bench.iloc[:t + 1], RelativeStrengthConfig())
    pd.testing.assert_series_equal(full["rank"].iloc[t], part["rank"].iloc[t])
    pd.testing.assert_series_equal(full["combined"].iloc[t], part["combined"].iloc[t])


# ------------------------------------------------------------------ sectors

def test_sector_composite_uses_previous_session_members_and_skips_unclassified():
    close = panel({1: growth(0.01), 2: growth(0.0), 3: growth(-0.01), 4: growth(0.02)})
    elig = close.notna()
    elig.iloc[10:, 1] = False  # stock 2 leaves the universe on day 10
    sector_of = pd.Series({1: "A", 2: "A", 3: "B", 4: "Unclassified"})
    level, count = SEC.composites(close, elig, sector_of, "Unclassified")
    assert list(level.columns) == ["A", "B"]
    # day 5: members 1 and 2 -> mean of +1% and 0%; day 20: only stock 1
    assert level["A"].iat[5] / level["A"].iat[4] - 1 == pytest.approx(0.005)
    assert level["A"].iat[20] / level["A"].iat[19] - 1 == pytest.approx(0.01)
    assert count["A"].iat[20] == 1


def test_sector_ranking_breadth_and_thin_flag():
    cols = {}
    sector_of = {}
    for i in range(5):              # strong sector: 5 rising stocks
        cols[i] = growth(0.003)
        sector_of[i] = "Strong"
    for i in range(5, 10):          # weak sector: 5 falling stocks
        cols[i] = growth(-0.002)
        sector_of[i] = "Weak"
    cols[10], sector_of[10] = growth(0.01), "Tiny"  # one member only
    close = panel(cols)
    sec = SEC.compute(close, close * 1.01, close * 0 + 1000, close.notna(), pd.Series(sector_of), pd.Series(growth(0.0), index=DATES))
    rows = {r["sector"]: r for r in SEC.rows_for(sec, DATES[-1])}
    assert rows["Strong"]["rank"] == 1 and rows["Weak"]["rank"] == 2
    assert rows["Tiny"]["rank"] is None and "thin" in rows["Tiny"]["flags"]
    assert rows["Strong"]["values"]["breadth50_pct"] == 100 and rows["Weak"]["values"]["breadth50_pct"] == 0
    assert rows["Strong"]["values"]["trend_up"] and not rows["Weak"]["values"]["above_sma50"]


# ------------------------------------------------------------------ regime

def inputs(trend: int, breadth: float, vix: float, median: float, vix_jump=0.0, participation=None):
    """trend +1 = all four trend checks up, -1 = all down, 0 = mixed."""
    c = {1: (110, 100, 90, 1.0), -1: (80, 90, 100, -1.0), 0: (100, 95, 105, -0.1)}[trend]
    d = {}
    for k in ("n50", "n500"):
        d.update({f"{k}_close": c[0], f"{k}_sma50": c[1], f"{k}_sma200": c[2], f"{k}_sma200_slope": c[3]})
    d.update(breadth200_pct=breadth, breadth50_pct=breadth, sector_participation_pct=participation if participation is not None else breadth,
             vix=vix, vix_change_10_pct=vix_jump, median_ret_63_pct=median)
    return d


@pytest.mark.parametrize("inp,expected", [
    (inputs(1, 75, 11, 12), "strong_bullish"),
    # trend aligned up (+6 of 8.5 weight units) offset by neutral breadth and an elevated VIX: (6 - 1) / 8.5 -> +29
    (inputs(1, 45, 20, 0), "moderately_bullish"),
    # mixed trend (-1 per index = -3), neutral breadth and VIX: -3 / 8.5 -> -18
    (inputs(0, 45, 17, 0), "sideways"),
    # mixed trend (-3), weak breadth/participation (-3.5), falling median (-1): -7.5 / 8.5 -> -44
    (inputs(0, 30, 17, -4), "moderately_bearish"),
    (inputs(-1, 10, 30, -15, vix_jump=40), "strong_bearish"),
])
def test_regime_fixtures_cover_all_five_states(inp, expected):
    out = R.classify(inp, RegimeConfig())
    assert out["regime"] == expected and out["coverage"] == 1.0
    assert out["policy"] == RegimeConfig().policies[expected].model_dump()


def test_regime_bearish_states_block_new_entries_by_default():
    for state in ("moderately_bearish", "strong_bearish"):
        assert not RegimeConfig().policies[state].allow_new_entries


def test_regime_with_little_evidence_is_never_bullish():
    out = R.classify({"vix": 11.0, "median_ret_63_pct": 12.0}, RegimeConfig())
    assert out["coverage"] < 0.5 and out["regime"] == "sideways"


def test_vix_spike_lowers_volatility_points():
    assert R.volatility_points(15, 0) == 0 and R.volatility_points(15, 30) == -1 and R.volatility_points(30, 50) == -2


def test_regime_config_validation():
    with pytest.raises(ValueError):
        RegimeConfig(strong_bullish_min=10, moderately_bullish_min=20)
    with pytest.raises(ValueError):
        RegimeConfig(policies={})


# ------------------------------------------------------------------ score

ALL_PASS = {"dist_sma_50_pct": 5, "dist_sma_200_pct": 20, "sma50_over_sma200": 1.1, "sma_200_slope_20": 1, "sma_50_slope_10": 1,
            "adx_14": 30, "di_spread": 10, "w_close_over_sma30": 1.1, "w_sma_30_slope_4": 2, "rs_rank": 95, "excess_63": 0.1,
            "excess_sector_63": 0.05, "rs_rank_change": 3, "vol20_over_vol50": 1.3, "obv_slope_20": 1e6, "updown_vol_ratio_20": 1.5,
            "rel_volume_20": 2.0, "chg_1d_pct": 2.0, "dist_high_252_pct": -3, "above_low_252_pct": 60, "bb_width_pctrank_126": 20,
            "atr_10_50_ratio": 0.8, "close_over_prior_high_55": 1.01, "dist_sma_20_pct": 4, "sector_rank_pct": 0.1,
            "sector_above_sma50": True, "sector_breadth50_pct": 70, "rsi_14": 65, "macd_over_signal": 1, "macd_sign": 2,
            "roc_63": 15, "atr_pct": 2.5}


def test_check_catalogue_matches_configured_weights():
    assert len({c.id for c in S.CHECKS}) == len(S.CHECKS)
    assert S.GROUP_MAX == ScoreWeights().model_dump()
    needed = {k for c in S.CHECKS for k in c.inputs}
    assert needed == set(ALL_PASS)


def test_perfect_and_empty_scores():
    best = S.score(ALL_PASS, ScoreWeights(), ScoreBands())
    assert best["total"] == 100 and best["coverage"] == 1 and best["band"] == "priority"
    assert all(b["status"] == "pass" for b in best["breakdown"])
    worst = dict(ALL_PASS, dist_sma_50_pct=-30, dist_sma_200_pct=-30, sma50_over_sma200=0.9, sma_200_slope_20=-1,
                 sma_50_slope_10=-1, adx_14=10, w_close_over_sma30=0.9, rs_rank=10, excess_63=-0.1, excess_sector_63=-0.1,
                 rs_rank_change=-5, vol20_over_vol50=0.8, obv_slope_20=-1, updown_vol_ratio_20=0.5, rel_volume_20=0.5,
                 dist_high_252_pct=-50, above_low_252_pct=5, bb_width_pctrank_126=90, atr_10_50_ratio=1.3,
                 close_over_prior_high_55=0.7, dist_sma_20_pct=25, sector_rank_pct=0.9, sector_above_sma50=False,
                 sector_breadth50_pct=20, rsi_14=30, macd_over_signal=-1, roc_63=-10, atr_pct=8)
    w = S.score(worst, ScoreWeights(), ScoreBands())
    assert w["total"] == 0 and w["band"] == "watchlist"


def test_missing_inputs_are_na_and_renormalised():
    inp = dict(ALL_PASS, sector_rank_pct=None, sector_above_sma50=None, sector_breadth50_pct=None, excess_sector_63=None)
    out = S.score(inp, ScoreWeights(), ScoreBands())
    assert out["coverage"] == pytest.approx(0.87) and out["total"] == 100
    na = [b["id"] for b in out["breakdown"] if b["status"] == "n/a"]
    assert set(na) == {"s_rank", "s_trend", "s_breadth", "rs_vs_sector_63"}


def test_custom_weights_and_bands_change_the_result():
    inp = dict(ALL_PASS, rs_rank=10, excess_63=-1, excess_sector_63=-1, rs_rank_change=-1)  # RS group scores 0
    default = S.score(inp, ScoreWeights(), ScoreBands())["total"]
    heavy = S.score(inp, ScoreWeights(trend=15, relative_strength=30, volume=15, price_action=15, sector=10,
                                      momentum_indicators=10, risk_reward=5), ScoreBands())["total"]
    assert default == 80 and heavy == 70
    assert S.band_of(80, ScoreBands()) == "strong" and S.band_of(80, ScoreBands(priority=80, strong=70, developing=60)) == "priority"


def test_score_is_scale_invariant():
    from app.indicators.panel import compute, weekly_asof
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, 300)))
    b = pd.DataFrame({"session_date": DATES, "open": c * 0.995, "high": c * 1.01, "low": c * 0.99, "close": c,
                      "volume": rng.lognormal(12, 0.4, 300)})

    def total(bars):
        ind = compute(bars).iloc[-1].to_dict()
        ind.update(weekly_asof(bars).iloc[-1].to_dict())
        ind = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in ind.items()}
        return S.score(S.derive_inputs(ind, {"rs_rank": 70}, None, None), ScoreWeights(), ScoreBands())["total"]

    scaled = b.assign(**{k: b[k] * 0.1 for k in ("open", "high", "low", "close")}, volume=b.volume * 10)
    assert total(b) == total(scaled)


def test_regime_policy_defaults_in_strategy_config():
    pol = StrategyConfig().regime.policies
    assert pol["strong_bullish"].risk_multiplier == 1.0 and pol["sideways"].min_score == 85
