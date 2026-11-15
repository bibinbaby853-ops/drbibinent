"""Build the list of symbols to scan.

Primary source is the official NSE constituent CSV. If that cannot be
reached (NSE blocks many non-browser clients, or the machine is offline)
the bundled snapshot in ``data/nifty500_fallback.csv`` is used instead.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

NSE_INDEX_URLS = {
    "nifty50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "nifty500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "midcap150": "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "smallcap250": "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}


def _parse_constituent_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        sym = (row.get("Symbol") or row.get("symbol") or "").strip().upper()
        if not sym or sym.startswith("DUMMY"):  # NSE adds DUMMY* placeholders during corporate actions
            continue
        rows.append(
            {
                "symbol": sym,
                "name": (row.get("Company Name") or row.get("name") or "").strip(),
                "industry": (row.get("Industry") or row.get("industry") or "").strip(),
            }
        )
    return rows


def fetch_nse_constituents(index: str = "nifty500", timeout: int = 20) -> list[dict]:
    """Download the live constituent list from NSE. Raises on failure."""
    url = NSE_INDEX_URLS[index]
    session = requests.Session()
    session.headers.update(_HEADERS)
    # NSE sometimes wants a cookie from the main site before serving archives.
    try:
        session.get("https://www.nseindia.com/", timeout=timeout)
    except requests.RequestException:
        pass
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    rows = _parse_constituent_csv(resp.text)
    if len(rows) < 10:
        raise ValueError(f"NSE constituent file for {index} looked empty")
    return rows


def load_fallback(index: str = "nifty500") -> list[dict]:
    path = DATA_DIR / f"{index}_fallback.csv"
    if not path.exists():
        path = DATA_DIR / "nifty500_fallback.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        return _parse_constituent_csv(fh.read())


def load_symbol_file(path: str | os.PathLike) -> list[dict]:
    """Load a user-supplied CSV (needs a Symbol column) or plain text list."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if "," in text.splitlines()[0]:
        rows = _parse_constituent_csv(text)
        if rows:
            return rows
    return [
        {"symbol": line.strip().upper(), "name": "", "industry": ""}
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def get_universe(index: str = "nifty500", offline: bool = False) -> list[dict]:
    """Return a list of ``{symbol, name, industry}`` dicts for the universe."""
    if os.path.exists(index):
        rows = load_symbol_file(index)
        log.info("Loaded %d symbols from %s", len(rows), index)
        return rows
    index = index.lower()
    if index not in NSE_INDEX_URLS:
        raise ValueError(f"Unknown universe '{index}'. Choose from {sorted(NSE_INDEX_URLS)} or a file path.")
    if not offline:
        try:
            rows = fetch_nse_constituents(index)
            log.info("Fetched %d %s constituents from NSE", len(rows), index)
            return rows
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch %s list from NSE (%s); using bundled snapshot", index, exc)
    rows = load_fallback(index)
    log.info("Loaded %d symbols from bundled snapshot", len(rows))
    return rows


def to_yahoo_ticker(symbol: str) -> str:
    """NSE symbol -> Yahoo Finance ticker (``RELIANCE`` -> ``RELIANCE.NS``)."""
    return f"{symbol.strip().upper()}.NS"


def from_yahoo_ticker(ticker: str) -> str:
    return ticker[:-3] if ticker.upper().endswith(".NS") else ticker
