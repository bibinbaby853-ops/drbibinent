"""Download and cache daily OHLCV data for the universe via Yahoo Finance."""

from __future__ import annotations

import logging
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _cache_path(tag: str, period: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"ohlcv_{tag}_{period}_{date.today():%Y-%m-%d}.pkl"


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame | None:
    """Normalise one ticker's frame to Open/High/Low/Close/Volume floats."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).title() for c in df.columns]
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        return None
    df = df[OHLCV_COLS].astype(float)
    df = df.dropna(subset=["Close"])
    df = df[df["Volume"].fillna(0) >= 0]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if len(df) < 5:
        return None
    return df


def _split_download(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        # yfinance with group_by="ticker": level 0 = ticker, level 1 = field
        level0 = set(raw.columns.get_level_values(0))
        if any(t in level0 for t in tickers):
            for t in tickers:
                if t in level0:
                    cleaned = _clean_frame(raw[t])
                    if cleaned is not None:
                        out[t] = cleaned
        else:  # level 0 = field, level 1 = ticker
            for t in tickers:
                try:
                    sub = raw.xs(t, axis=1, level=1)
                except KeyError:
                    continue
                cleaned = _clean_frame(sub)
                if cleaned is not None:
                    out[t] = cleaned
    elif len(tickers) == 1:
        cleaned = _clean_frame(raw)
        if cleaned is not None:
            out[tickers[0]] = cleaned
    return out


def download_ohlcv(
    tickers: list[str],
    period: str = "2y",
    batch_size: int = 60,
    cache_tag: str | None = None,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Download daily bars for ``tickers``. Returns ``{ticker: DataFrame}``.

    Results are cached on disk for the current calendar day so repeated
    scans do not hammer Yahoo.
    """
    import yfinance as yf  # imported lazily so offline/demo mode works without it

    tz_dir = CACHE_DIR / "yf_tz"
    tz_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(tz_dir))

    cache_file = _cache_path(cache_tag or f"{len(tickers)}syms", period) if use_cache else None
    if cache_file and cache_file.exists():
        with open(cache_file, "rb") as fh:
            cached = pickle.load(fh)
        if set(tickers).issubset(cached):
            log.info("Loaded %d tickers from cache %s", len(tickers), cache_file.name)
            return {t: cached[t] for t in tickers}

    data: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        log.info("Downloading batch %d-%d of %d", i + 1, i + len(batch), len(tickers))
        try:
            raw = yf.download(
                batch,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Batch download failed (%s); retrying tickers one by one", exc)
            raw = None
        got = _split_download(raw, batch) if raw is not None else {}
        for t in batch:
            if t not in got:
                try:
                    single = yf.download(t, period=period, interval="1d", auto_adjust=True, progress=False)
                    cleaned = _clean_frame(single.droplevel(1, axis=1) if isinstance(single.columns, pd.MultiIndex) else single)
                    if cleaned is not None:
                        got[t] = cleaned
                except Exception as exc:  # noqa: BLE001
                    log.debug("No data for %s: %s", t, exc)
        data.update(got)

    log.info("Downloaded data for %d of %d tickers", len(data), len(tickers))
    if cache_file:
        with open(cache_file, "wb") as fh:
            pickle.dump(data, fh)
    return data


def download_benchmark(symbol: str = "^NSEI", period: str = "2y") -> pd.DataFrame | None:
    """Download the benchmark index used for relative-strength calculations."""
    got = download_ohlcv([symbol], period=period, batch_size=1, cache_tag="bench_" + symbol.strip("^"))
    return got.get(symbol)


def synthetic_ohlcv(
    n_days: int = 400,
    seed: int = 0,
    drift: float = 0.0005,
    vol: float = 0.02,
    start_price: float = 500.0,
) -> pd.DataFrame:
    """Generate a plausible random-walk OHLCV frame (used by tests and demo mode)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)
    rets = rng.normal(drift, vol, n_days)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, vol / 3, n_days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n_days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n_days)))
    volume = rng.lognormal(13, 0.5, n_days).round()
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
