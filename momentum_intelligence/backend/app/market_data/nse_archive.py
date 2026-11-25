"""NSE official end-of-day archives (archives.nseindia.com).

Files used (all public, published by the exchange):

* UDiFF bhavcopy ``content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`` (from July 2024):
  OHLC, previous close, volume, value, trades, ISIN.
* Legacy bhavcopy ``content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip`` (to July 2024):
  same fields in the old layout.
* Full bhavcopy ``products/content/sec_bhavdata_full_DDMMYYYY.csv``: adds delivery quantity and %.
* ``content/equities/EQUITY_L.csv``: listed equities with ISIN, listing date, face value.
* ``content/equities/symbolchange.csv``: historical symbol changes.
* ``content/indices/ind_niftytotalmarket_list.csv``: industry classification for Nifty Total Market.

The bhavcopy (UDiFF or legacy) is the primary record because it carries ISIN;
the full bhavcopy is merged in for delivery figures. ``PREV_CLOSE`` is kept as
published: on an ex-date NSE publishes it already adjusted, which is how
``adjustments`` derives corporate-action factors without a separate feed.

Raw files are cached under ``data/historical/nse`` with their SHA-256 so a
re-ingest reads the identical bytes and lineage can be re-verified.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .base import (BarFrame, DataMismatch, DataNotPublished, Lineage, ProviderError, ProviderUnavailable, Security, SymbolChange, sha256,
                   utcnow)
from .calendar import session_bar_start
from .http import HttpClient

BASE = "https://archives.nseindia.com"
EQUITY_SERIES = ("EQ", "BE", "BZ")  # main-board equities; SME (SM/ST) and debt series are excluded
UDIFF_FROM = date(2024, 7, 8)


def udiff_url(d: date) -> str:
    return f"{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def legacy_url(d: date) -> str:
    return f"{BASE}/content/historical/EQUITIES/{d:%Y}/{d.strftime('%b').upper()}/cm{d:%d}{d.strftime('%b').upper()}{d:%Y}bhav.csv.zip"


def full_bhav_url(d: date) -> str:
    return f"{BASE}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"


EQUITY_L_URL = f"{BASE}/content/equities/EQUITY_L.csv"
SYMBOL_CHANGE_URL = f"{BASE}/content/equities/symbolchange.csv"
INDUSTRY_URL = f"{BASE}/content/indices/ind_niftytotalmarket_list.csv"
SEC_LIST_URL = f"{BASE}/content/equities/sec_list.csv"


# ---------------------------------------------------------------- parsers (pure, unit-tested)

def _unzip_single(raw: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, found {names}")
        return zf.read(names[0])


def _read_csv(raw: bytes) -> pd.DataFrame:
    if raw[:4] == b"PK\x03\x04" and b"[Content_Types].xml" in raw[:200]:
        # NSE occasionally serves an Excel workbook under a .csv name (seen: full bhavcopy 8 Aug 2022)
        df = pd.read_excel(io.BytesIO(raw), dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(io.BytesIO(raw), dtype=str, skipinitialspace=True, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
    return df.apply(lambda s: s.str.strip())


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace({"": np.nan, "-": np.nan}), errors="coerce")


# Rights entitlements trade in the BE series as SYMBOL-RE / SYMBOL-RE1 ...; they are not shares.
RIGHTS_ENTITLEMENT = r"-RE\d*$"


def _finish(df: pd.DataFrame, session: date) -> pd.DataFrame:
    df = df[df["series"].isin(EQUITY_SERIES) & ~df["symbol"].str.contains(RIGHTS_ENTITLEMENT, regex=True)].copy()
    df["session_date"] = session
    df["ts"] = session_bar_start(session)
    for c in ("delivery_qty", "delivery_pct"):
        if c not in df:
            df[c] = np.nan
    return df.reset_index(drop=True)


def parse_udiff(raw_csv: bytes, session: date) -> pd.DataFrame:
    df = _read_csv(raw_csv)
    df = df[(df["Sgmt"] == "CM") & (df["FinInstrmTp"] == "STK")]
    dates = set(df["TradDt"])
    if dates and dates != {session.isoformat()}:
        raise DataMismatch(f"UDiFF file dates {sorted(dates)} do not match session {session}")
    out = pd.DataFrame({
        "symbol": df["TckrSymb"], "isin": df["ISIN"].replace("", None), "series": df["SctySrs"],
        "open": _num(df["OpnPric"]), "high": _num(df["HghPric"]), "low": _num(df["LwPric"]),
        "close": _num(df["ClsPric"]), "prev_close": _num(df["PrvsClsgPric"]),
        "volume": _num(df["TtlTradgVol"]), "turnover_inr": _num(df["TtlTrfVal"]),
        "trades": _num(df["TtlNbOfTxsExctd"]),
    })
    return _finish(out, session)


def parse_legacy(raw_csv: bytes, session: date) -> pd.DataFrame:
    df = _read_csv(raw_csv)
    dates = {datetime.strptime(x, "%d-%b-%Y").date() for x in df["TIMESTAMP"] if x}
    if dates and dates != {session}:
        raise DataMismatch(f"bhavcopy dates {sorted(dates)} do not match session {session}")
    out = pd.DataFrame({
        "symbol": df["SYMBOL"], "isin": df["ISIN"].replace("", None), "series": df["SERIES"],
        "open": _num(df["OPEN"]), "high": _num(df["HIGH"]), "low": _num(df["LOW"]),
        "close": _num(df["CLOSE"]), "prev_close": _num(df["PREVCLOSE"]),
        "volume": _num(df["TOTTRDQTY"]), "turnover_inr": _num(df["TOTTRDVAL"]),
        "trades": _num(df["TOTALTRADES"]),
    })
    return _finish(out, session)


def parse_full_bhav(raw_csv: bytes, session: date) -> pd.DataFrame:
    """sec_bhavdata_full: no ISIN, turnover in lakhs, delivery figures."""
    df = _read_csv(raw_csv)
    dates = {datetime.strptime(x, "%d-%b-%Y").date() for x in df["DATE1"] if x}
    if dates and dates != {session}:
        raise DataMismatch(f"full bhavcopy dates {sorted(dates)} do not match session {session}")
    out = pd.DataFrame({
        "symbol": df["SYMBOL"], "isin": None, "series": df["SERIES"],
        "open": _num(df["OPEN_PRICE"]), "high": _num(df["HIGH_PRICE"]), "low": _num(df["LOW_PRICE"]),
        "close": _num(df["CLOSE_PRICE"]), "prev_close": _num(df["PREV_CLOSE"]),
        "volume": _num(df["TTL_TRD_QNTY"]), "turnover_inr": _num(df["TURNOVER_LACS"]) * 1e5,
        "trades": _num(df["NO_OF_TRADES"]), "delivery_qty": _num(df["DELIV_QTY"]),
        "delivery_pct": _num(df["DELIV_PER"]),
    })
    return _finish(out, session)


def merge_delivery(bhav: pd.DataFrame, full: pd.DataFrame) -> pd.DataFrame:
    """Attach delivery figures from the full bhavcopy to the ISIN-bearing bhavcopy (match on symbol+series)."""
    d = full[["symbol", "series", "delivery_qty", "delivery_pct"]].drop_duplicates(["symbol", "series"])
    out = bhav.drop(columns=["delivery_qty", "delivery_pct"]).merge(d, on=["symbol", "series"], how="left")
    return out


def parse_equity_list(raw_csv: bytes) -> list[Security]:
    df = _read_csv(raw_csv)
    out = []
    for r in df.itertuples(index=False):
        rec = dict(zip(df.columns, r))
        try:
            listed = datetime.strptime(rec.get("DATE OF LISTING", ""), "%d-%b-%Y").date()
        except ValueError:
            listed = None
        fv = pd.to_numeric(rec.get("FACE VALUE"), errors="coerce")
        out.append(Security(symbol=rec["SYMBOL"], isin=rec.get("ISIN NUMBER") or None, name=rec.get("NAME OF COMPANY"),
                            series=rec.get("SERIES"), listing_date=listed, face_value=None if pd.isna(fv) else float(fv)))
    return out


def parse_symbol_changes(raw_csv: bytes) -> list[SymbolChange]:
    # The file has no reliable header row on every vintage; read positionally.
    df = pd.read_csv(io.BytesIO(raw_csv), header=None, dtype=str, keep_default_na=False, encoding="latin-1")
    out = []
    for name, old, new, eff in df.iloc[:, :4].itertuples(index=False):
        try:
            d = datetime.strptime(eff.strip(), "%d-%b-%Y").date()
        except ValueError:
            continue  # header or malformed row
        if old.strip() and new.strip():
            out.append(SymbolChange(old_symbol=old.strip(), new_symbol=new.strip(), effective=d, company=name.strip() or None))
    return out


def parse_industry_list(raw_csv: bytes) -> dict[str, str]:
    """ISIN -> industry (NSE's classification for the Nifty Total Market constituents)."""
    df = _read_csv(raw_csv)
    return {r["ISIN Code"]: r["Industry"] for _, r in df.iterrows() if r.get("ISIN Code")}


def parse_sec_list(raw_csv: bytes) -> list[dict]:
    """Current price band and GSM stage per symbol+series. 'No Band' -> None."""
    df = _read_csv(raw_csv)
    out = []
    for _, r in df.iterrows():
        band = r.get("Band", "")
        remark = (r.get("Remarks", "") or "").strip()
        out.append({"symbol": r["Symbol"], "series": r["Series"],
                    "price_band_pct": None if band.lower().startswith("no") else pd.to_numeric(band, errors="coerce"),
                    "gsm_stage": remark.replace("GSM STAGE - ", "") if remark.upper().startswith("GSM") else None})
    return out


# ---------------------------------------------------------------- provider

@dataclass
class Fetched:
    raw: bytes
    lineage: Lineage


class NSEArchiveProvider:
    name = "nse_archive"
    licence = "exchange_official"

    def __init__(self, cache_dir: Path, http: HttpClient | None = None, offline: bool = False):
        self.cache_dir = Path(cache_dir) / "historical" / "nse"
        self.http = http or HttpClient()
        self.offline = offline

    def _fetch(self, dataset: str, url: str, filename: str, as_of: date | None, cache: bool = True) -> Fetched:
        path = self.cache_dir / dataset / filename
        if cache and path.exists():
            raw = path.read_bytes()
            return Fetched(raw, Lineage(self.name, self.licence, dataset, url, utcnow(), as_of, sha256(raw), from_cache=True))
        if self.offline:
            raise DataNotPublished(f"offline and not cached: {path}")
        raw = self.http.get(url)
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(raw)
            tmp.replace(path)
        return Fetched(raw, Lineage(self.name, self.licence, dataset, url, utcnow(), as_of, sha256(raw)))

    def _parsed(self, dataset, url, filename, d, parser):
        """Fetch then parse; a file describing another session is evicted from the cache and re-raised."""
        f = self._fetch(dataset, url, filename, d)
        try:
            raw = _unzip_single(f.raw) if filename.endswith(".zip") else f.raw
            return parser(raw, d), f.lineage
        except DataMismatch:
            (self.cache_dir / dataset / filename).unlink(missing_ok=True)
            raise
        except (ValueError, KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            # Kept in the cache for inspection; the session is reported, never guessed.
            raise ProviderError(f"unreadable {dataset} file for {d} ({exc.__class__.__name__}: {exc})") from exc

    def bhavcopy(self, d: date) -> BarFrame:
        """One session's main-board equity bars, with ISIN and (when published) delivery figures.

        Raises ``DataNotPublished`` when there was no session (holiday) or it is not out yet.
        """
        lineage: list[Lineage] = []
        attempts = [("udiff", udiff_url(d), f"{d:%Y%m%d}.csv.zip", parse_udiff),
                    ("legacy_bhav", legacy_url(d), f"{d:%Y%m%d}.csv.zip", parse_legacy)]
        if d < UDIFF_FROM:
            attempts.reverse()
        bars, notes = None, []
        for dataset, url, fn, parser in attempts:
            try:
                bars, lin = self._parsed(dataset, url, fn, d, parser)
            except DataNotPublished:
                continue
            lineage.append(lin)
            break
        if bars is None:
            raise DataNotPublished(f"no bhavcopy for {d}")
        try:
            full, lin = self._parsed("full_bhav", full_bhav_url(d), f"{d:%Y%m%d}.csv", d, parse_full_bhav)
            bars = merge_delivery(bars, full)
            lineage.append(lin)
        except (DataNotPublished, DataMismatch, ProviderError) as exc:
            notes.append(f"delivery figures unavailable: {exc}")
        return BarFrame(bars, lineage, notes=notes)

    def daily_bars(self, symbols, start, end):  # pragma: no cover - per-session use goes through bhavcopy()
        raise NotImplementedError("use bhavcopy(d) per session; ingest assembles ranges")

    def intraday_bars(self, symbols, interval, start, end):
        raise ProviderUnavailable("NSE archives publish end-of-day files only; no intraday bars")

    def index_bars(self, index, start, end):
        raise ProviderUnavailable("NSE index history lives on www.nseindia.com, which this environment cannot reach")

    def security_master(self) -> tuple[list[Security], Lineage]:
        f = self._fetch("equity_list", EQUITY_L_URL, f"{date.today():%Y%m%d}.csv", date.today())
        return parse_equity_list(f.raw), f.lineage

    def symbol_changes(self) -> tuple[list[SymbolChange], Lineage]:
        f = self._fetch("symbol_change", SYMBOL_CHANGE_URL, f"{date.today():%Y%m%d}.csv", date.today())
        return parse_symbol_changes(f.raw), f.lineage

    def industries(self) -> tuple[dict[str, str], Lineage]:
        f = self._fetch("industry", INDUSTRY_URL, f"{date.today():%Y%m%d}.csv", date.today())
        return parse_industry_list(f.raw), f.lineage

    def sec_list(self) -> tuple[list[dict], Lineage]:
        f = self._fetch("sec_list", SEC_LIST_URL, f"{date.today():%Y%m%d}.csv", date.today())
        return parse_sec_list(f.raw), f.lineage
