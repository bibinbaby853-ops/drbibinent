import numpy as np
import pandas as pd

from scanner import patterns as pt
from scanner.data import synthetic_ohlcv
from scanner.indicators import enrich_momentum, supertrend
from scanner.intraday import analyse, to_4h
from scanner.market import index_trend, market_regime, sector_strength
from scanner.momentum import WEIGHTS, Assessment, Category, StockContext, assess_technical
from scanner.tradeplan import READY, build_portfolio, plan_trade


def _trend(n=320, step=0.15, noise=0.2, seed=0, vol=1_000_000.0):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(step, noise, n))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"Open": c - 0.1, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": vol}, index=idx)


def test_supertrend_bullish_in_uptrend_bearish_in_downtrend():
    up = synthetic_ohlcv(300, seed=1, drift=0.004, vol=0.005)
    dn = synthetic_ohlcv(300, seed=2, drift=-0.004, vol=0.005)
    assert supertrend(up)["ST_Dir"].iloc[-1] == 1
    assert supertrend(dn)["ST_Dir"].iloc[-1] == -1


def test_category_normalises_over_available_data():
    c = Category("Volume")
    c.add(True, 4, "a")
    c.add(None, 3, "delivery")
    c.add(False, 3, "b")
    assert c.available == 7 and c.missing_pts == 3
    assert np.isclose(c.score, 4 / 7 * WEIGHTS["Volume"])
    assert Category("Fundamentals").score is None


def test_high_breakout_detected_with_volume():
    df = _trend()
    hi = df["High"].iloc[-51:-1].max()
    df.loc[df.index[-1], ["Open", "Low", "Close", "High", "Volume"]] = [hi, hi - 0.2, hi + 2, hi + 2.1, 3_000_000]
    p = pt.high_breakout(enrich_momentum(df), 50)
    assert p is not None and p.stage == "confirmed" and p.quality >= 7


def test_flag_detected_after_pole():
    df = _trend(step=0.05)
    n = len(df)
    base = df["Close"].iloc[-20]
    pole = np.linspace(base, base * 1.25, 10)
    flag = base * 1.25 - np.array([0.8, 1.5, 1.2, 1.8, 1.4, 1.6])
    closes = np.concatenate([pole, flag])
    for k, c in enumerate(closes):
        i = df.index[n - len(closes) + k]
        df.loc[i, ["Open", "High", "Low", "Close"]] = [c - 0.2, c + 0.6, c - 0.6, c]
    p = pt.flag_or_pennant(enrich_momentum(df))
    assert p is not None and p.name in ("Bull flag", "Bull pennant")


def test_failed_breakout_needs_an_established_level():
    df = _trend(step=0.0, noise=0.05)
    n = len(df)
    lvl = df["High"].iloc[-30:-12].max()
    i_break = df.index[n - 4]
    df.loc[i_break, ["Close", "High", "Volume"]] = [lvl + 1.5, lvl + 1.7, 3_000_000]
    df.loc[df.index[-1], ["Open", "High", "Low", "Close"]] = [lvl - 1, lvl - 0.5, lvl - 2, lvl - 1.5]
    assert pt.failed_breakout(enrich_momentum(df)) is not None


def test_trade_plan_invariants_and_sizing():
    e = enrich_momentum(_trend(seed=3))
    ctx = StockContext("TEST", e, None, None, 80.0, "IT", None, None)
    a = assess_technical(ctx)
    plan = plan_trade(a, e, capital=1_000_000, risk_pct=0.75)
    assert plan.stop < plan.entry <= plan.target1 <= plan.target2
    assert plan.rr1 >= 0
    assert plan.qty * (plan.entry - plan.stop) <= 7_500 + plan.entry
    assert plan.qty * plan.entry <= 250_000 + plan.entry


def test_targets_ignore_levels_already_cleared():
    e = enrich_momentum(_trend(seed=4, step=0.3))
    a = assess_technical(StockContext("T", e, None, None, 90.0, "IT", None, None))
    plan = plan_trade(a, e, 1_000_000, 0.75)
    assert plan.target1 > max(plan.entry, plan.cmp)


def test_assessment_without_benchmarks_marks_rs_missing():
    e = enrich_momentum(_trend(seed=5))
    a = assess_technical(StockContext("T", e, None, None, np.nan, "IT", None, None))
    rs = a.categories["Relative strength"]
    assert rs.score is None and rs.missing_pts == 20
    assert 0 <= a.score <= 100 and a.coverage < 100


def test_portfolio_respects_open_risk_and_sector_caps():
    rows = [{"Symbol": f"S{i}", "Watchlist": READY, "R:R": 2.0, "Sector": "IT" if i < 3 else "Bank"} for i in range(8)]
    chosen = build_portfolio(rows, max_open_risk_pct=3.0, risk_pct=0.75)
    assert len(chosen) == 4
    assert sum(r["Sector"] == "IT" for r in chosen) == 2


def test_regime_labels():
    up = index_trend("N", synthetic_ohlcv(400, seed=6, drift=0.002, vol=0.006))
    dn = index_trend("N", synthetic_ohlcv(400, seed=7, drift=-0.002, vol=0.006))
    strong = {"pct_above_50": 75, "pct_above_200": 70, "new_highs": 30, "new_lows": 2, "advances": 300, "declines": 150}
    weak = {"pct_above_50": 15, "pct_above_200": 20, "new_highs": 1, "new_lows": 40, "advances": 100, "declines": 380}
    assert market_regime(up, up, strong, pd.Series(dtype=float), 1.0, 3.0).label == "RISK-ON"
    off = market_regime(dn, dn, weak, pd.Series(dtype=float), 1.0, 3.0)
    assert off.label == "RISK-OFF" and off.risk_per_trade_pct <= 0.5 and off.min_score_ready == 85


def test_sector_strength_uses_median_stock_benchmark():
    frames, industry = {}, {}
    for i in range(12):
        sec = ["A", "B", "C"][i % 3]
        drift = {"A": 0.004, "B": 0.0, "C": -0.004}[sec]
        frames[f"S{i}"] = enrich_momentum(synthetic_ohlcv(300, seed=i, drift=drift, vol=0.008))
        industry[f"S{i}"] = sec
    s = sector_strength(frames, industry, None)
    assert list(s.index) == ["A", "B", "C"]
    assert s.loc["A", "RS3M"] > 0 > s.loc["C", "RS3M"]


def test_four_hour_bars_two_per_session():
    idx = pd.DatetimeIndex([f"2026-11-{d} {h}:15" for d in (12, 13) for h in (9, 10, 11, 12, 13, 14, 15)])
    h = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10.0}, index=idx)
    four = to_4h(h)
    assert len(four) == 4 and (four["Volume"].to_numpy() == [40, 30, 40, 30]).all()  # 09:15-12:15, 13:15-15:15


def test_intraday_vwap_side():
    idx = pd.date_range("2026-10-01 09:15", periods=600, freq="h")
    idx = idx[(idx.hour >= 9) & (idx.hour <= 15)]
    c = 100 * 1.004 ** np.arange(len(idx))  # accelerating, so MACD stays positive
    h = pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": 1000.0}, index=idx)
    res = analyse(h)
    assert res is not None and res.above_ema20_1h and res.confirm_4h


def test_high_breakout_still_recognised_while_holding_next_day():
    df = _trend()
    hi = df["High"].iloc[-52:-2].max()
    df.loc[df.index[-2], ["Open", "Low", "Close", "High", "Volume"]] = [hi, hi - 0.2, hi + 2, hi + 3, 3_000_000]
    df.loc[df.index[-1], ["Open", "Low", "Close", "High", "Volume"]] = [hi + 2, hi + 0.5, hi + 1.5, hi + 2.2, 800_000]
    p = pt.high_breakout(enrich_momentum(df), 50)
    assert p is not None and "holding" in p.detail
    df.loc[df.index[-1], ["Low", "Close"]] = [hi - 3, hi - 2]
    assert pt.high_breakout(enrich_momentum(df), 50) is None


def test_fill_missing_bars_from_bhavcopy(monkeypatch):
    from scanner import nse
    idx = pd.bdate_range("2026-01-01", periods=50)
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e5}, index=idx[:-1])
    jumpy = df.copy()
    bhav = pd.DataFrame({"DATE": [idx[-1]] * 2, "SYMBOL": ["AAA", "BBB"], "SERIES": ["EQ", "EQ"],
                         "OPEN_PRICE": [100.5, 100.0], "HIGH_PRICE": [103.0, 150.0], "LOW_PRICE": [100.0, 99.0],
                         "CLOSE_PRICE": [102.0, 150.0], "TTL_TRD_QNTY": [2e5, 1e5], "TURNOVER_LACS": [1, 1],
                         "NO_OF_TRADES": [1, 1], "DELIV_QTY": [1, 1], "DELIV_PER": [50, 50]})
    monkeypatch.setattr(nse, "load_bhavcopy", lambda d: bhav if d == idx[-1] else None)
    frames, filled = nse.fill_missing_bars({"AAA.NS": df, "BBB.NS": jumpy}, list(idx))
    assert filled == ["AAA"]
    assert frames["AAA.NS"].index[-1] == idx[-1] and frames["AAA.NS"]["Close"].iloc[-1] == 102.0
    assert frames["BBB.NS"].index[-1] == idx[-2]  # 50% jump rejected as a likely corporate action


def test_incomplete_session_bar_is_dropped_only_during_market_hours():
    from scanner.data import drop_incomplete_bars
    idx = pd.DatetimeIndex(["2026-11-16", "2026-11-17", "2026-11-18"])
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
    open_ = pd.Timestamp("2026-11-18 10:30", tz="Asia/Kolkata")
    after = pd.Timestamp("2026-11-18 16:30", tz="Asia/Kolkata")
    out, dropped = drop_incomplete_bars({"X": df}, now=open_)
    assert dropped and out["X"].index[-1] == pd.Timestamp("2026-11-17")
    out, dropped = drop_incomplete_bars({"X": df}, now=after)
    assert not dropped and out["X"].index[-1] == pd.Timestamp("2026-11-18")
