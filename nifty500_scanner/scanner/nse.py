"""NSE archive data: delivery volumes (bhavcopy) and trading-restriction flags.

Only the public ``archives.nseindia.com`` files are used. NSE's live JSON API
(ASM lists, pledging, order book) blocks automated clients, so those fields
are reported as unavailable rather than guessed.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

CACHE = Path(__file__).resolve().parent.parent / "cache" / "nse"
ARCHIVE = "https://archives.nseindia.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,*/*;q=0.8",
}


def _get(url: str, cache_name: str | None, timeout: int = 25) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name if cache_name else None
    if path and path.exists() and path.stat().st_size > 100:
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code != 200 or r.text.lstrip().startswith("<"):
            log.debug("NSE %s -> HTTP %s", url, r.status_code)
            return None
        if path:
            path.write_text(r.text, encoding="utf-8")
        return r.text
    except requests.RequestException as exc:
        log.debug("NSE %s failed: %s", url, exc)
        return None


def load_bhavcopy(day: pd.Timestamp) -> pd.DataFrame | None:
    """Full bhavcopy for one session, including delivery quantity and %."""
    name = f"sec_bhavdata_full_{day:%d%m%Y}.csv"
    text = _get(f"{ARCHIVE}/products/content/{name}", name)
    if not text:
        return None
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    df["SERIES"] = df["SERIES"].astype(str).str.strip()
    df = df[df["SERIES"].isin(["EQ", "BE", "BZ"])]
    for c in ["TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER", "CLOSE_PRICE"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.strip().replace("-", None), errors="coerce")
    df["DATE"] = day.normalize()
    return df[["DATE", "SYMBOL", "SERIES", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"]]


def delivery_stats(trading_days: list[pd.Timestamp], lookback: int = 21) -> pd.DataFrame:
    """Per-symbol delivery metrics over the last ``lookback`` sessions.

    Returns columns: DelivPct, DelivPct20, DelivQtyRatio, AvgTradeValue
    (rupees per trade, a rough proxy for participant size), BhavDate.
    """
    frames = []
    for d in trading_days[-lookback:]:
        b = load_bhavcopy(pd.Timestamp(d))
        if b is not None:
            frames.append(b)
    if not frames:
        log.warning("No NSE bhavcopy files could be fetched; delivery data will be N/A")
        return pd.DataFrame()
    all_ = pd.concat(frames).sort_values("DATE")
    last_day = all_["DATE"].max()
    g = all_.groupby("SYMBOL")
    last = all_[all_["DATE"] == last_day].set_index("SYMBOL")
    prior = all_[all_["DATE"] < last_day].groupby("SYMBOL")
    out = pd.DataFrame(index=last.index)
    out["DelivPct"] = last["DELIV_PER"]
    out["DelivPct20"] = prior["DELIV_PER"].mean()
    out["DelivQtyRatio"] = last["DELIV_QTY"] / prior["DELIV_QTY"].mean()
    out["AvgTradeValue"] = (last["TURNOVER_LACS"] * 1e5) / last["NO_OF_TRADES"]
    out["BhavSeries"] = last["SERIES"]
    out["BhavDate"] = last_day
    out["BhavSessions"] = g.size().reindex(out.index)
    log.info("Delivery data: %d symbols from %d bhavcopy files (latest %s)", len(out), len(frames), last_day.date())
    return out


def restriction_flags() -> pd.DataFrame:
    """Series, price band and GSM stage per symbol from NSE's security list."""
    text = _get(f"{ARCHIVE}/content/equities/sec_list.csv", f"sec_list_{pd.Timestamp.today():%Y%m%d}.csv")
    if not text:
        log.warning("NSE security list unavailable; surveillance flags will be N/A")
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df["Remarks"] = df["Remarks"].astype(str).str.strip()
    # A symbol can appear in several series; keep the main trading series.
    df["rank"] = df["Series"].map({"EQ": 0, "BE": 1, "BZ": 2}).fillna(9)
    df = df.sort_values("rank").drop_duplicates("Symbol").set_index("Symbol")
    out = pd.DataFrame(index=df.index)
    out["Series"] = df["Series"].str.strip()
    out["PriceBand"] = df["Band"].astype(str).str.strip()
    out["GSM"] = df["Remarks"].where(df["Remarks"].str.contains("GSM", case=False), "")
    return out
