"""Fundamental quality and event risk from Yahoo Finance.

Yahoo's coverage of Indian companies is partial: quarterly statements have
gaps, and shareholding is a single snapshot. Every metric therefore comes back
as a value or ``None``; nothing is estimated. Promoter holding is approximated
by Yahoo's "insider" percentage and is labelled as such.
"""

from __future__ import annotations

import logging
import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .momentum import Category

log = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parent.parent / "cache"

FINANCIAL_SECTORS = {"Financial Services"}


@dataclass
class Fundamentals:
    symbol: str
    revenue_yoy: float | None = None
    profit_yoy: float | None = None
    margin_now: float | None = None
    margin_year_ago: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    roce: float | None = None
    promoter_pct: float | None = None  # Yahoo "insider" holding, an approximation
    institutional_pct: float | None = None
    earnings_date: date | None = None
    latest_quarter: str | None = None
    yahoo_sector: str | None = None
    errors: list[str] = field(default_factory=list)


def _row(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series | None:
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s.sort_index()
    return None


def _yoy(s: pd.Series | None) -> tuple[float | None, str | None]:
    """Latest quarter vs the same quarter a year earlier, matched by date."""
    if s is None or len(s) < 2:
        return None, None
    last_d, last_v = s.index[-1], s.iloc[-1]
    target = last_d - pd.DateOffset(years=1)
    near = [d for d in s.index[:-1] if abs((d - target).days) <= 20]
    if not near:
        return None, f"{last_d:%b %Y}"
    prev = s.loc[near[0]]
    if prev == 0 or np.isnan(prev):
        return None, f"{last_d:%b %Y}"
    return float((last_v - prev) / abs(prev) * 100.0), f"{last_d:%b %Y}"


def _fetch_one(symbol: str) -> Fundamentals:
    import yfinance as yf

    f = Fundamentals(symbol)
    t = yf.Ticker(f"{symbol}.NS")
    info = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        f.errors.append(f"info: {exc.__class__.__name__}")
    try:
        q = t.quarterly_income_stmt
        rev = _row(q, ("Total Revenue", "Operating Revenue"))
        ni = _row(q, ("Net Income", "Net Income Common Stockholders"))
        op = _row(q, ("Operating Income", "EBIT"))
        f.revenue_yoy, f.latest_quarter = _yoy(rev)
        f.profit_yoy, _ = _yoy(ni)
        if rev is not None and op is not None:
            m = (op / rev.reindex(op.index)).dropna() * 100
            if len(m):
                f.margin_now = float(m.iloc[-1])
                target = m.index[-1] - pd.DateOffset(years=1)
                near = [d for d in m.index[:-1] if abs((d - target).days) <= 20]
                if near:
                    f.margin_year_ago = float(m.loc[near[0]])
        ebit = _row(q, ("EBIT", "Operating Income"))
        b = t.quarterly_balance_sheet
        ta, cl = _row(b, ("Total Assets",)), _row(b, ("Current Liabilities",))
        if ebit is not None and len(ebit) >= 4 and ta is not None and cl is not None:
            cap_employed = ta.iloc[-1] - cl.iloc[-1]
            if cap_employed > 0:
                f.roce = float(ebit.iloc[-4:].sum() / cap_employed * 100.0)
    except Exception as exc:  # noqa: BLE001
        f.errors.append(f"statements: {exc.__class__.__name__}")
    if f.revenue_yoy is None and info.get("revenueGrowth") is not None:
        f.revenue_yoy = float(info["revenueGrowth"]) * 100
    if f.profit_yoy is None and info.get("earningsGrowth") is not None:
        f.profit_yoy = float(info["earningsGrowth"]) * 100
    if info.get("debtToEquity") is not None:
        f.debt_to_equity = float(info["debtToEquity"]) / 100.0
    if info.get("returnOnEquity") is not None:
        f.roe = float(info["returnOnEquity"]) * 100
    if info.get("heldPercentInsiders") is not None:
        f.promoter_pct = float(info["heldPercentInsiders"]) * 100
    if info.get("heldPercentInstitutions") is not None:
        f.institutional_pct = float(info["heldPercentInstitutions"]) * 100
    f.yahoo_sector = info.get("sector")
    try:
        cal = t.calendar or {}
        ed = cal.get("Earnings Date")
        if ed:
            future = [d for d in ed if d >= date.today()]
            f.earnings_date = future[0] if future else None
    except Exception as exc:  # noqa: BLE001
        f.errors.append(f"calendar: {exc.__class__.__name__}")
    if f.earnings_date is None and info.get("earningsTimestamp"):
        d = datetime.fromtimestamp(info["earningsTimestamp"]).date()
        if d >= date.today():
            f.earnings_date = d
    return f


def fetch_fundamentals(symbols: list[str], workers: int = 4) -> dict[str, Fundamentals]:
    path = CACHE / f"fundamentals_{date.today():%Y-%m-%d}.pkl"
    cached: dict[str, Fundamentals] = {}
    if path.exists():
        with open(path, "rb") as fh:
            cached = pickle.load(fh)
    todo = [s for s in symbols if s not in cached]
    if todo:
        log.info("Fetching fundamentals for %d stocks", len(todo))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for f in ex.map(_fetch_one, todo):
                cached[f.symbol] = f
        CACHE.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(cached, fh)
    return {s: cached[s] for s in symbols if s in cached}


def _band(v: float | None, full: float, part: float, low: float = 0.0) -> float | None:
    if v is None or np.isnan(v):
        return None
    if v >= full:
        return 1.0
    if v >= part:
        return 0.6
    if v >= low:
        return 0.25
    return 0.0


def score_fundamentals(f: Fundamentals | None, sector: str, as_of: date) -> tuple[Category, list[str], list[str]]:
    """Returns (category, quality flags, event flags)."""
    c = Category("Fundamentals")
    flags, events = [], []
    if f is None:
        for lbl, pts in (("revenue growth", 2), ("profit growth", 2), ("margin trend", 1.5), ("debt to equity", 1.5), ("ROE", 1.5), ("ROCE", 1.5)):
            c.add(None, pts, lbl)
        return c, flags, events
    q = f" ({f.latest_quarter} quarter, YoY)" if f.latest_quarter else " (YoY)"
    p = _band(f.revenue_yoy, 15, 5, 0)
    c.add(None if p is None else True, 2, f"revenue {f.revenue_yoy:+.0f}%{q}" if p is not None else "revenue growth", partial=p)
    p = _band(f.profit_yoy, 15, 5, 0)
    c.add(None if p is None else True, 2, f"net profit {f.profit_yoy:+.0f}%{q}" if p is not None else "profit growth", partial=p)
    if f.margin_now is not None and f.margin_year_ago is not None:
        delta = f.margin_now - f.margin_year_ago
        c.add(delta >= -1.0, 1.5, f"operating margin {f.margin_now:.1f}% vs {f.margin_year_ago:.1f}% a year ago")
        if delta < -3:
            flags.append(f"Operating margin contracted {abs(delta):.1f} points year on year.")
    else:
        c.add(None, 1.5, "margin trend")
    if sector in FINANCIAL_SECTORS:
        c.add(None, 1.5, "debt to equity (not meaningful for lenders)")
    elif f.debt_to_equity is not None:
        de = f.debt_to_equity
        c.add(True, 1.5, f"debt to equity {de:.2f}", partial=1.0 if de < 0.5 else 0.6 if de < 1 else 0.2 if de < 2 else 0.0)
        if de > 2:
            flags.append(f"High leverage: debt to equity {de:.1f}.")
    else:
        c.add(None, 1.5, "debt to equity")
    p = _band(f.roe, 18, 12, 8)
    c.add(None if p is None else True, 1.5, f"ROE {f.roe:.1f}%" if p is not None else "ROE", partial=p)
    p = _band(f.roce, 18, 12, 8)
    c.add(None if p is None else True, 1.5, f"ROCE {f.roce:.1f}% (trailing 4 quarters)" if p is not None else "ROCE", partial=p)
    if f.revenue_yoy is not None and f.profit_yoy is not None and f.revenue_yoy < 0 and f.profit_yoy < 0:
        flags.append("Deteriorating fundamentals: revenue and profit both down year on year.")
    if f.earnings_date is not None:
        days = (f.earnings_date - as_of).days
        if 0 <= days <= 30:
            events.append(f"Results expected {f.earnings_date:%d %b %Y} ({days} days). Gap risk inside the holding window.")
        elif days < 0:
            pass
    return c, flags, events
