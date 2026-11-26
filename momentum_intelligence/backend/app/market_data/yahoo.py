"""Yahoo Finance via yfinance: UNOFFICIAL and DELAYED.

Used for intraday bars (60m, 15m), index levels (Nifty 50, India VIX, sector
indices) and as a cross-check on NSE closes. Never the primary daily source.
Every frame is labelled ``licence="unofficial"``.

Rules applied here:
* only completed candles are returned (the bar still forming is dropped)
* prices are raw (``auto_adjust=False``); adjustment happens in ``adjustments``
* nothing is filled: a missing bar stays missing
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from .base import BarFrame, Lineage, ProviderError, ProviderUnavailable, utcnow
from .calendar import IST, daily_bar_complete, intraday_bar_complete

INDEX_TICKERS = {
    "NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "INDIA VIX": "^INDIAVIX", "NIFTY 500": "^CRSLDX",
    "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO", "NIFTY PHARMA": "^CNXPHARMA", "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY MEDIA": "^CNXMEDIA", "NIFTY PSU BANK": "^CNXPSUBANK", "NIFTY INFRA": "^CNXINFRA",
}
INTRADAY_LOOKBACK_DAYS = {"15m": 59, "60m": 729}  # Yahoo's limits


def _download(tickers: list[str], **kw) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise ProviderUnavailable("yfinance is not installed") from exc
    return yf.download(tickers, auto_adjust=False, actions=False, progress=False, threads=False,
                       group_by="ticker", **kw)


def tidy(raw: pd.DataFrame, ticker_to_symbol: dict[str, str], interval: str, now: datetime | None = None) -> pd.DataFrame:
    """yfinance wide frame -> canonical long frame, completed bars only."""
    rows = []
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["symbol", "ts", "session_date", "open", "high", "low", "close", "volume"])
    multi = isinstance(raw.columns, pd.MultiIndex)
    for ticker, symbol in ticker_to_symbol.items():
        if multi:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker]
        else:
            df = raw
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
        if df.empty:
            continue
        idx = pd.DatetimeIndex(df.index)
        idx = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
        if interval == "1d":
            idx = idx.normalize() + pd.Timedelta(hours=9, minutes=15)
        out = pd.DataFrame({"symbol": symbol, "ts": idx, "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
                            "low": df["low"].to_numpy(), "close": df["close"].to_numpy(),
                            "volume": df["volume"].fillna(0).to_numpy()})
        out["session_date"] = out["ts"].dt.date
        if interval == "1d":
            keep = out["session_date"].map(lambda d: daily_bar_complete(d, now))
        else:
            keep = out["ts"].map(lambda t: intraday_bar_complete(t.to_pydatetime(), interval, now))
        rows.append(out[keep])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["symbol", "ts", "session_date", "open", "high", "low", "close", "volume"])


class YahooProvider:
    name = "yahoo"
    licence = "unofficial"

    def __init__(self, downloader=_download):
        self._download = downloader

    def _frame(self, mapping: dict[str, str], interval: str, start: date, end: date, dataset: str) -> BarFrame:
        yf_interval = {"1d": "1d", "60m": "60m", "15m": "15m"}[interval]
        try:
            raw = self._download(list(mapping), interval=yf_interval, start=start.isoformat(),
                                 end=(end + timedelta(days=1)).isoformat())
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - yfinance raises many types
            raise ProviderError(f"yahoo download failed: {exc.__class__.__name__}: {exc}") from exc
        data = tidy(raw, mapping, interval)
        missing = sorted(set(mapping.values()) - set(data["symbol"]))
        lin = Lineage(self.name, self.licence, dataset, "yfinance", utcnow(), end, None,
                      params={"interval": interval, "start": start.isoformat(), "end": end.isoformat(), "symbols": len(mapping)})
        notes = [f"no data from Yahoo for: {', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}"] if missing else []
        return BarFrame(data, [lin], interval=interval, notes=notes)

    def daily_bars(self, symbols: list[str], start: date, end: date) -> BarFrame:
        return self._frame({f"{s}.NS": s for s in symbols}, "1d", start, end, "yahoo_daily")

    def intraday_bars(self, symbols: list[str], interval: str, start: date, end: date) -> BarFrame:
        if interval not in INTRADAY_LOOKBACK_DAYS:
            raise ValueError(f"unsupported intraday interval {interval}")
        earliest = date.today() - timedelta(days=INTRADAY_LOOKBACK_DAYS[interval])
        return self._frame({f"{s}.NS": s for s in symbols}, interval, max(start, earliest), end, f"yahoo_{interval}")

    def index_bars(self, index: str, start: date, end: date, interval: str = "1d") -> BarFrame:
        if index not in INDEX_TICKERS:
            raise ProviderUnavailable(f"no Yahoo ticker known for index {index!r}")
        return self._frame({INDEX_TICKERS[index]: index}, interval, start, end, f"yahoo_index_{interval}")

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        """Yahoo's split/bonus history for one NSE symbol as (ex_date, shares_multiplier), e.g. 1:10 split -> 10.0.

        A bonus 1:1 appears as 2.0. Used only to confirm factors inferred from price gaps.
        """
        try:
            import yfinance as yf
            ser = yf.Ticker(f"{symbol}.NS").splits
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yahoo splits failed for {symbol}: {exc}") from exc
        if ser is None or len(ser) == 0:
            return []
        idx = pd.DatetimeIndex(ser.index)
        idx = idx.tz_convert(IST) if idx.tz is not None else idx
        return [(ts.date(), float(v)) for ts, v in zip(idx, ser.to_numpy()) if v and v > 0]

    def security_master(self):
        raise ProviderUnavailable("Yahoo has no security master; use NSE EQUITY_L")
