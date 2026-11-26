"""Causal technical indicators.

Every function returns a Series aligned to its input where the value at
position t depends only on inputs at positions <= t (tested by truncation).
Values are NaN until enough history exists; nothing is back-filled.

Conventions (the common textbook definitions, as used by StockCharts):
* EMA: alpha = 2/(n+1), seeded with the simple average of the first n values
* Wilder smoothing (RSI, ATR, ADX): seeded with the simple average of the first n
  values, then avg_t = (avg_{t-1} * (n-1) + x_t) / n
* Bollinger bands use the population standard deviation (ddof=0)
* "prior" windows exclude the current bar (breakout references)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def _seeded(x: pd.Series, n: int, alpha: float) -> pd.Series:
    """Recursive smoothing seeded with the mean of the first n valid values."""
    v = x.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = np.flatnonzero(~np.isnan(v))
    if len(valid) < n:
        return pd.Series(out, index=x.index)
    start = valid[0]
    # require n consecutive valid values for the seed
    seed_end = start + n - 1
    if np.isnan(v[start:seed_end + 1]).any():
        return pd.Series(out, index=x.index)
    out[seed_end] = v[start:seed_end + 1].mean()
    for i in range(seed_end + 1, len(v)):
        out[i] = out[i - 1] if np.isnan(v[i]) else out[i - 1] + alpha * (v[i] - out[i - 1])
    return pd.Series(out, index=x.index)


def ema(x: pd.Series, n: int) -> pd.Series:
    return _seeded(x, n, 2.0 / (n + 1))


def wilder(x: pd.Series, n: int) -> pd.Series:
    return _seeded(x, n, 1.0 / n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain, loss = d.clip(lower=0), (-d).clip(lower=0)
    ag, al = wilder(gain, n), wilder(loss, n)
    rs = ag / al
    out = 100 - 100 / (1 + rs)
    out[(al == 0) & (ag > 0)] = 100.0
    out[(al == 0) & (ag == 0)] = 50.0
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1, skipna=True)
    tr.iloc[:1] = (high - low).iloc[:1]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    """Wilder's ADX with +DI / -DI. First smoothed TR/DM = sum of first n; first ADX = mean of first n DX."""
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = true_range(high, low, close)
    # Wilder sums: seeded with the sum of bars 1..n (bar 0 has no directional movement)
    def wsum(s: pd.Series) -> pd.Series:
        v = s.to_numpy(dtype=float)
        out = np.full(len(v), np.nan)
        if len(v) > n:
            out[n] = v[1:n + 1].sum()
            for i in range(n + 1, len(v)):
                out[i] = out[i - 1] - out[i - 1] / n + v[i]
        return pd.Series(out, index=s.index)
    str_, spdm, smdm = wsum(tr), wsum(plus_dm), wsum(minus_dm)
    pdi, mdi = 100 * spdm / str_, 100 * smdm / str_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    dx = dx.where((pdi + mdi) != 0, 0.0).where(pdi.notna())
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": pdi, "minus_di": mdi})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": (upper - lower) / mid, "bb_pct_b": (close - lower) / (upper - lower)})


def roc(close: pd.Series, n: int) -> pd.Series:
    """Rate of change in percent over n bars."""
    return (close / close.shift(n) - 1) * 100


def prior_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the n bars before the current one (breakout reference level)."""
    return high.shift(1).rolling(n, min_periods=n).max()


def prior_low(low: pd.Series, n: int) -> pd.Series:
    return low.shift(1).rolling(n, min_periods=n).min()


def rolling_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the last n bars including the current one (e.g. 52-week high)."""
    return high.rolling(n, min_periods=n).max()


def rolling_low(low: pd.Series, n: int) -> pd.Series:
    return low.rolling(n, min_periods=n).min()


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Volume divided by the average of the n prior bars."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()


def realized_vol(close: pd.Series, n: int = 20) -> pd.Series:
    """Annualised standard deviation of daily log returns (sample std), in percent."""
    return np.log(close).diff().rolling(n, min_periods=n).std(ddof=1) * np.sqrt(252) * 100


def slope_pct(x: pd.Series, n: int) -> pd.Series:
    """Percent change of a series over n bars (e.g. slope of a moving average)."""
    return (x / x.shift(n) - 1) * 100


def pct_rank(x: pd.Series, n: int) -> pd.Series:
    """Percentile (0-100) of the current value within the last n values, current included (causal)."""
    def rank(w: np.ndarray) -> float:
        return float((w <= w[-1]).sum() - 1) / (len(w) - 1) * 100
    return x.rolling(n, min_periods=n).apply(rank, raw=True)
