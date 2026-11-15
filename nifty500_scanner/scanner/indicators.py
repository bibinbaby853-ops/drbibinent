"""Technical indicators implemented in plain pandas/numpy (no TA-Lib needed)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(avg_loss != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Returns a frame with ``+DI``, ``-DI`` and ``ADX`` (Wilder smoothing)."""
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df)
    alpha = 1.0 / n
    tr_s = tr.ewm(alpha=alpha, adjust=False, min_periods=n).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=n).mean() / tr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=n).mean() / tr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_s = dx.ewm(alpha=alpha, adjust=False, min_periods=n).mean()
    return pd.DataFrame({"+DI": plus_di, "-DI": minus_di, "ADX": adx_s})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"MACD": line, "Signal": sig, "Hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    return pd.DataFrame({"BB_Mid": mid, "BB_Upper": upper, "BB_Lower": lower, "BB_Width": width})


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every indicator the strategies need to an OHLCV frame."""
    out = df.copy()
    c = out["Close"]
    out["SMA20"] = sma(c, 20)
    out["SMA50"] = sma(c, 50)
    out["SMA200"] = sma(c, 200)
    out["EMA10"] = ema(c, 10)
    out["EMA20"] = ema(c, 20)
    out["EMA50"] = ema(c, 50)
    out["RSI14"] = rsi(c, 14)
    out["RSI2"] = rsi(c, 2)
    out["ATR14"] = atr(out, 14)
    out["ATR10"] = atr(out, 10)
    out["ATR50"] = atr(out, 50)
    out["ATR_pct"] = out["ATR14"] / c * 100.0
    out = out.join(adx(out, 14))
    out = out.join(macd(c))
    out = out.join(bollinger(c, 20, 2.0))
    out["BBW_pctile"] = out["BB_Width"].rolling(120, min_periods=60).rank(pct=True)
    out["Vol20"] = out["Volume"].rolling(20, min_periods=20).mean()
    out["Vol50"] = out["Volume"].rolling(50, min_periods=50).mean()
    out["RVOL"] = out["Volume"] / out["Vol20"]
    out["Turnover20"] = (c * out["Volume"]).rolling(20, min_periods=20).mean()
    out["High20"] = out["High"].rolling(20, min_periods=20).max().shift(1)
    out["Low20"] = out["Low"].rolling(20, min_periods=20).min().shift(1)
    out["High55"] = out["High"].rolling(55, min_periods=55).max().shift(1)
    out["Low55"] = out["Low"].rolling(55, min_periods=55).min().shift(1)
    out["High252"] = out["High"].rolling(252, min_periods=120).max()
    out["Low252"] = out["Low"].rolling(252, min_periods=120).min()
    out["Ret1M"] = c.pct_change(21) * 100.0
    out["Ret3M"] = c.pct_change(63) * 100.0
    out["Ret6M"] = c.pct_change(126) * 100.0
    out["Ret12M"] = c.pct_change(252) * 100.0
    out["Range"] = out["High"] - out["Low"]
    out["ClosePos"] = (c - out["Low"]) / out["Range"].replace(0.0, np.nan)  # 0 = close at low, 1 = at high
    out["NR7"] = out["Range"] <= out["Range"].rolling(7, min_periods=7).min()
    out["InsideBar"] = (out["High"] <= out["High"].shift(1)) & (out["Low"] >= out["Low"].shift(1))
    out["DistEMA20_pct"] = (c / out["EMA20"] - 1.0) * 100.0
    out["DistSMA50_pct"] = (c / out["SMA50"] - 1.0) * 100.0
    out["Dist52wHigh_pct"] = (c / out["High252"] - 1.0) * 100.0
    return out
