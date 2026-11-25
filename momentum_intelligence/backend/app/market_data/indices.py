"""Benchmark indices (Nifty 50, Nifty 500, India VIX) from Yahoo: UNOFFICIAL, labelled as such.

Stored as ``stocks`` rows with ``kind='index'`` and key ``INDEX:<name>``, candles with
``source='yahoo'``, ``licence='unofficial'``. ``check_benchmark`` compares the
Nifty 50 against a composite of the 50 most liquid eligible NSE stocks built
from exchange data, and records days where they disagree.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import DataQualityIssue, Stock
from .ingest import upsert_candles, write_lineage

BENCHMARKS = ("NIFTY 50", "NIFTY 500", "INDIA VIX")


def ensure_index(db: Session, name: str) -> int:
    key = f"INDEX:{name}"
    sid = db.scalar(select(Stock.id).where(Stock.key == key))
    if sid is None:
        db.execute(insert(Stock).values(key=key, symbol=name, name=name, kind="index", status="active",
                                        classification_source="yahoo").on_conflict_do_nothing())
        sid = db.scalar(select(Stock.id).where(Stock.key == key))
    return sid


def ingest_indices(db: Session, yahoo, start: date, end: date, names=BENCHMARKS) -> dict:
    out = {}
    for name in names:
        f = yahoo.index_bars(name, start, end)
        sid = ensure_index(db, name)
        bars = f.data.copy()
        if bars.empty:
            out[name] = 0
            continue
        bars["isin"] = None
        bars["series"] = None
        log_id = write_lineage(db, f.lineage[0], status="success", rows=len(bars))
        # upsert_candles keys rows by ISIN or DEMO:symbol; give it a mapping for this one index
        bars["symbol"] = name
        n = upsert_candles(db, bars.assign(isin=f"INDEX:{name}"), {f"INDEX:{name}": sid}, source="yahoo",
                           licence="unofficial", dataset_id=log_id, demo=False)
        out[name] = n
    db.flush()
    return out


_BENCH = text("""
    select c.session_date, c.close from market_candles c join stocks s on s.id = c.stock_id
    where s.key = :key and c.source = :src and c.interval = '1d' order by c.session_date
""")

DEMO_BENCHMARK = "DEMO INDEX"  # stands in for NIFTY 50 in demo mode only; always labelled demo


def index_closes(db: Session, name: str, *, source: str = "yahoo") -> pd.Series:
    df = pd.read_sql(_BENCH, db.connection(), params={"key": f"INDEX:{name}", "src": source})
    return df.set_index("session_date")["close"] if not df.empty else pd.Series(dtype=float)


def ingest_demo_index(db: Session, demo, start: date, end: date) -> int:
    """Synthetic benchmark from the demo provider, stored with demo=True and source 'demo'."""
    f = demo.index_bars(DEMO_BENCHMARK, start, end)
    key = f"INDEX:{DEMO_BENCHMARK}"
    sid = db.scalar(select(Stock.id).where(Stock.key == key))
    if sid is None:
        db.execute(insert(Stock).values(key=key, symbol=DEMO_BENCHMARK, name="Demo index (synthetic)", kind="index",
                                        status="active", demo=True).on_conflict_do_nothing())
        sid = db.scalar(select(Stock.id).where(Stock.key == key))
    log_id = write_lineage(db, f.lineage[0], status="success", rows=len(f.data), demo=True)
    bars = f.data.assign(isin=key, series=None)
    return upsert_candles(db, bars, {key: sid}, source="demo", licence="demo", dataset_id=log_id, demo=False)


def check_benchmark(db: Session, panel_close: pd.DataFrame, eligible: pd.DataFrame, *, tolerance: float = 0.03) -> dict:
    """Compare Yahoo Nifty 50 daily returns with an equal-weighted top-50-by-liquidity NSE composite.

    panel_close: dates x stock_id adjusted closes; eligible: dates x stock_id median traded value (NaN = not eligible).
    Records a warning for each day where the two returns differ by more than ``tolerance``.
    """
    nifty = index_closes(db, "NIFTY 50")
    if nifty.empty:
        return {"status": "no_benchmark"}
    rets = panel_close.pct_change(fill_method=None)
    top = eligible.rank(axis=1, ascending=False) <= 50
    comp = rets.where(top.shift(1, fill_value=False)).mean(axis=1)  # yesterday's top-50, today's return
    nret = nifty.pct_change().reindex(comp.index)
    both = pd.concat({"nifty": nret, "composite": comp}, axis=1).dropna()
    if both.empty:
        return {"status": "no_overlap"}
    corr = float(both["nifty"].corr(both["composite"]))
    bad = both[(both["nifty"] - both["composite"]).abs() > tolerance]
    missing = sorted(set(panel_close.index) - set(nifty.index))
    rows = [{"source": "yahoo:benchmark", "symbol": "NIFTY 50", "session_date": d, "check": "benchmark_disagreement",
             "severity": "warning", "details": {"nifty_return": round(float(r.nifty), 5), "composite_return": round(float(r.composite), 5)}}
            for d, r in bad.iterrows()]
    rows += [{"source": "yahoo:benchmark", "symbol": "NIFTY 50", "session_date": d, "check": "benchmark_missing_session",
              "severity": "warning", "details": {"note": "NSE session with no Yahoo Nifty 50 bar"}} for d in missing]
    db.execute(text("delete from data_quality_issues where source = 'yahoo:benchmark'"))
    if rows:
        db.execute(insert(DataQualityIssue), rows)
    return {"status": "ok", "days": int(len(both)), "daily_return_correlation": round(corr, 4),
            "tracking_error_annual_pct": round(float((both["nifty"] - both["composite"]).std() * np.sqrt(252) * 100), 2),
            "disagreement_days": int(len(bad)), "missing_sessions": len(missing)}
