"""4-hour confirmation and 1-hour entry timing from Yahoo 60-minute bars.

Yahoo intraday data for NSE is delayed and only available for about 60 days.
VWAP here is computed from 60-minute bars of the latest session, so it is an
approximation of the true tick-level VWAP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import ema, macd

log = logging.getLogger(__name__)


@dataclass
class Intraday:
    last_bar: pd.Timestamp
    close_1h: float
    vwap: float
    above_vwap: bool
    above_ema20_1h: bool
    confirm_4h: bool
    note_4h: str
    note_1h: str


def to_4h(h: pd.DataFrame) -> pd.DataFrame:
    """Two bars per NSE session: 09:15-13:15 and 13:15-15:30."""
    h = h.copy()
    idx = h.index
    half = np.where(idx.hour < 13, 0, 1)
    key = pd.Series(list(zip(idx.date, half)), index=idx)
    agg = h.groupby(key.values).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    stamps = h.groupby(key.values).apply(lambda g: g.index[0])
    agg.index = pd.DatetimeIndex(stamps.values)
    return agg.sort_index()


def analyse(h: pd.DataFrame) -> Intraday | None:
    if h is None or len(h) < 40:
        return None
    h = h.dropna(subset=["Close"])
    last_day = h.index[-1].date()
    sess = h[h.index.date == last_day]
    tp = (sess["High"] + sess["Low"] + sess["Close"]) / 3
    vwap = float((tp * sess["Volume"]).sum() / sess["Volume"].sum()) if sess["Volume"].sum() > 0 else float(sess["Close"].mean())
    e20h = ema(h["Close"], 20)
    close = float(h["Close"].iloc[-1])
    four = to_4h(h)
    e20_4 = ema(four["Close"], 20)
    hist4 = macd(four["Close"])["Hist"]
    c4 = bool(four["Close"].iloc[-1] > e20_4.iloc[-1] and hist4.iloc[-1] > 0)
    rising4 = bool(e20_4.iloc[-1] > e20_4.iloc[-4])
    note4 = (("4-hour close above a rising 20 EMA" if rising4 else "4-hour close above the 20 EMA")
             if four["Close"].iloc[-1] > e20_4.iloc[-1] else "4-hour close below the 20 EMA")
    note4 += ", MACD histogram positive" if hist4.iloc[-1] > 0 else ", MACD histogram negative"
    a_vwap = close > vwap
    a_e20 = bool(close > e20h.iloc[-1])
    note1 = (f"last hourly close {close:.2f} {'above' if a_vwap else 'below'} session VWAP {vwap:.2f} "
             f"and {'above' if a_e20 else 'below'} the hourly 20 EMA")
    return Intraday(h.index[-1], close, vwap, a_vwap, a_e20, c4, note4, note1)


def fetch_intraday(symbols: list[str]) -> dict[str, Intraday]:
    import yfinance as yf

    if not symbols:
        return {}
    tickers = [f"{s}.NS" for s in symbols]
    try:
        raw = yf.download(tickers, period="60d", interval="60m", group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Intraday download failed: %s", exc)
        return {}
    frames = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            if t in set(raw.columns.get_level_values(0)):
                sub = raw[t].dropna(subset=["Close"])
                if len(sub):
                    sub.index = sub.index.tz_convert("Asia/Kolkata").tz_localize(None)
                    frames[t] = sub
    out = {}
    for t, h in frames.items():
        try:
            res = analyse(h)
        except Exception:  # noqa: BLE001
            res = None
        if res is not None:
            out[t[:-3]] = res
    log.info("Intraday data for %d of %d candidates", len(out), len(symbols))
    return out
