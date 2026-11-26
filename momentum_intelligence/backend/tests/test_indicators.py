"""Indicators against textbook values and independent loop implementations, plus no-look-ahead checks."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.indicators import core
from app.indicators.panel import asof_adjust, compute, latest, weekly_trend
from app.indicators.weekly import to_weekly

# Wilder RSI example as published by StockCharts ("RSI" ChartSchool table)
RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
              46.0328, 45.614, 46.282, 46.282, 46.0028, 46.0328, 46.4116, 46.2222, 45.6418]
RSI_EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]


def ohlc(n=320, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.018, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.008, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.008, n)))
    v = rng.lognormal(12, 0.5, n).round()
    days = pd.bdate_range("2025-01-01", periods=n).date
    return pd.DataFrame({"session_date": days, "open": o, "high": h, "low": l, "close": c, "volume": v})


# ------------------------------------------------------------- independent reference implementations (plain loops)

def ref_ema(x, n):
    out, a = [np.nan] * len(x), 2 / (n + 1)
    out[n - 1] = sum(x[:n]) / n
    for i in range(n, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def ref_wilder(x, n, start):
    out = [np.nan] * len(x)
    out[start + n - 1] = sum(x[start:start + n]) / n
    for i in range(start + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def ref_atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return ref_wilder(tr, n, 0)


def ref_adx(h, l, c, n=14):
    tr, pdm, mdm = [], [], []
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    s_tr, s_p, s_m = [sum(tr[:n])], [sum(pdm[:n])], [sum(mdm[:n])]
    for i in range(n, len(tr)):
        s_tr.append(s_tr[-1] - s_tr[-1] / n + tr[i])
        s_p.append(s_p[-1] - s_p[-1] / n + pdm[i])
        s_m.append(s_m[-1] - s_m[-1] / n + mdm[i])
    pdi = [100 * p / t for p, t in zip(s_p, s_tr)]
    mdi = [100 * m / t for m, t in zip(s_m, s_tr)]
    dx = [100 * abs(p - m) / (p + m) if p + m else 0 for p, m in zip(pdi, mdi)]
    adx = [sum(dx[:n]) / n]
    for x in dx[n:]:
        adx.append((adx[-1] * (n - 1) + x) / n)
    # align: dx[0] belongs to bar n; adx[0] to bar 2n-1
    return [np.nan] * (2 * n - 1) + adx, [np.nan] * n + pdi


def close(a, b, tol=1e-9):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert np.array_equal(np.isnan(a), np.isnan(b)), "NaN pattern differs"
    m = ~np.isnan(a)
    np.testing.assert_allclose(a[m], b[m], rtol=tol, atol=tol)


# ------------------------------------------------------------- textbook / reference values

def test_rsi_matches_published_wilder_example():
    out = core.rsi(pd.Series(RSI_CLOSES))
    assert np.isnan(out.iloc[13]) and not np.isnan(out.iloc[14])
    np.testing.assert_allclose(out.iloc[14:20].to_numpy(), RSI_EXPECTED, atol=0.05)


def test_rsi_matches_loop_and_bounds():
    b = ohlc()
    c = b.close.tolist()
    d = [np.nan] + [c[i] - c[i - 1] for i in range(1, len(c))]
    g = ref_wilder([max(x, 0) if not np.isnan(x) else np.nan for x in d], 14, 1)
    lo = ref_wilder([max(-x, 0) if not np.isnan(x) else np.nan for x in d], 14, 1)
    ref = [np.nan if np.isnan(a) else 100 - 100 / (1 + a / bb) for a, bb in zip(g, lo)]
    close(core.rsi(b.close), ref)
    assert core.rsi(pd.Series(np.arange(1.0, 40))).iloc[-1] == 100
    assert core.rsi(pd.Series([5.0] * 30)).iloc[-1] == 50


def test_sma_ema_against_loops():
    b = ohlc()
    c = b.close.tolist()
    close(core.sma(b.close, 20), [np.nan] * 19 + [sum(c[i - 19:i + 1]) / 20 for i in range(19, len(c))])
    for n in (10, 20, 50):
        close(core.ema(b.close, n), ref_ema(c, n))


def test_atr_against_loop_and_constant_range():
    b = ohlc()
    close(core.atr(b.high, b.low, b.close), ref_atr(b.high.tolist(), b.low.tolist(), b.close.tolist()))
    flat = pd.Series([10.0] * 30)
    assert core.atr(flat + 1, flat - 1, flat).iloc[-1] == pytest.approx(2.0)


def test_adx_against_loop_and_trend_behaviour():
    b = ohlc()
    adx_ref, pdi_ref = ref_adx(b.high.tolist(), b.low.tolist(), b.close.tolist())
    out = core.adx(b.high, b.low, b.close)
    close(out["adx"], adx_ref, tol=1e-8)
    close(out["plus_di"], pdi_ref, tol=1e-8)
    up = pd.Series(np.linspace(100, 200, 80))
    t = core.adx(up + 1, up - 1, up)
    assert t["adx"].iloc[-1] > 40 and t["plus_di"].iloc[-1] > t["minus_di"].iloc[-1]


def test_macd_bollinger_roc_and_reference_levels():
    b = ohlc()
    c = b.close
    m = core.macd(c)
    close(m["macd"], core.ema(c, 12) - core.ema(c, 26))
    close(m["macd_signal"], core.ema(m["macd"], 9))
    bb = core.bollinger(c)
    i = 100
    w = c.iloc[i - 19:i + 1].to_numpy()
    assert bb["bb_upper"].iat[i] == pytest.approx(w.mean() + 2 * w.std(ddof=0))
    assert core.bollinger(pd.Series([7.0] * 25))["bb_width"].iloc[-1] == 0
    assert core.roc(c, 21).iat[i] == pytest.approx((c.iat[i] / c.iat[i - 21] - 1) * 100)
    h = pd.Series([1.0, 5, 2, 3, 4, 9])
    assert core.prior_high(h, 3).iat[5] == 4          # excludes today's 9
    assert core.rolling_high(h, 3).iat[5] == 9        # includes today
    v = pd.Series([100.0] * 20 + [300.0])
    assert core.relative_volume(v, 20).iat[20] == 3


def test_obv_and_realized_vol():
    c = pd.Series([10.0, 11, 10.5, 10.5, 12])
    v = pd.Series([100.0, 200, 300, 400, 500])
    assert core.obv(c, v).tolist() == [0, 200, -100, -100, 400]
    b = ohlc()
    r = np.diff(np.log(b.close.to_numpy()))
    assert core.realized_vol(b.close, 20).iat[50] == pytest.approx(r[30:50].std(ddof=1) * np.sqrt(252) * 100)


# ------------------------------------------------------------- no look-ahead

def test_panel_values_never_depend_on_future_bars():
    b = ohlc(320)
    full = compute(b)
    for t in (30, 120, 251, 260, 319):
        part = compute(b.iloc[:t + 1])
        for col in full.columns:
            a, p = full[col].iat[t], part[col].iat[t]
            if not isinstance(a, (int, float, np.number)):
                assert a == p, col
            elif np.isnan(a):
                assert np.isnan(p), col
            else:
                assert a == pytest.approx(p, rel=1e-12), (col, t)


def test_indicators_are_nan_until_enough_history():
    out = compute(ohlc(260))
    assert out["sma_200"].iloc[:199].isna().all() and not np.isnan(out["sma_200"].iat[199])
    assert out["high_252"].iloc[:251].isna().all() and not np.isnan(out["high_252"].iat[251])
    assert out["roc_252"].iloc[:252].isna().all()


def test_asof_adjustment_ignores_future_corporate_actions():
    b = ohlc(40)
    ex = b.session_date.iat[30]
    before = asof_adjust(b, [(ex, 0.5)], b.session_date.iat[29])
    assert before.close.tolist() == b.close.iloc[:30].tolist()  # split not known yet: raw prices
    after = asof_adjust(b, [(ex, 0.5)], b.session_date.iat[35])
    assert after.close.iat[29] == pytest.approx(b.close.iat[29] * 0.5)
    assert after.close.iat[35] == b.close.iat[35] and after.volume.iat[0] == pytest.approx(b.volume.iat[0] * 2)


def test_weekly_bars_use_completed_weeks_only():
    days = pd.bdate_range("2026-11-02", "2026-11-20").date  # three full weeks, Mon-Fri
    d = pd.DataFrame({"session_date": days, "open": range(1, 16), "high": [x + 1 for x in range(1, 16)],
                      "low": [x - 1 for x in range(1, 16)], "close": [x + 0.5 for x in range(1, 16)], "volume": [10] * 15})
    w = to_weekly(d)
    assert len(w) == 3 and w.open.iat[0] == 1 and w.close.iat[0] == 5.5 and w.high.iat[0] == 6 and w.low.iat[0] == 0
    assert w.volume.iat[0] == 50
    wed = date(2026, 11, 18)
    assert len(to_weekly(d[d.session_date <= wed], as_of=wed)) == 2   # current week still forming
    fri = date(2026, 11, 20)
    assert len(to_weekly(d, as_of=fri, week_closed=True)) == 3
    assert weekly_trend(d, wed)["w_last_week"] == "2026-11-09"


def test_latest_returns_clean_values_only_for_a_traded_session():
    b = ohlc(300)
    d = b.session_date.iat[-1]
    row = latest(b, [], d)
    assert row["session_date"] == d.isoformat() and row["sma_200"] is not None
    assert all(v is None or isinstance(v, (int, float, str)) for v in row.values())
    assert latest(b, [], d + timedelta(days=1)) is None


def test_percentile_rank_and_contraction_measures():
    x = pd.Series([5.0, 1, 2, 3, 4])
    assert core.pct_rank(x, 5).iat[4] == 75.0           # 4 is above 3 of the other 4 values
    assert core.pct_rank(pd.Series([3.0, 2, 1]), 3).iat[2] == 0
    out = compute(ohlc(300))
    assert out["bb_width_pctrank_126"].dropna().between(0, 100).all()
    assert out["atr_10_50_ratio"].dropna().gt(0).all() and out["ema_200"].iloc[:199].isna().all()
