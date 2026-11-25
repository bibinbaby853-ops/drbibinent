"""Compute and store indicator snapshots for a session's eligible universe."""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..indicators.panel import INDICATOR_VERSION, latest
from ..models import IndicatorSnapshot
from .engine import sessions_upto

HISTORY_SESSIONS = 420  # > 252 for 52-week values plus the 30-week weekly average

_BARS = text("""
    select stock_id, session_date, open, high, low, close, volume from market_candles
    where stock_id = any(cast(:ids as integer[])) and session_date >= :start and session_date <= :d
      and interval = '1d' and is_adjusted = false and source = :source
    order by stock_id, session_date
""")
_FACTORS = text("""
    select stock_id, ex_date, factor from corporate_actions
    where stock_id = any(cast(:ids as integer[])) and confidence in ('high', 'medium') and source like :src
""")


def compute_for_session(db: Session, d: date, stock_ids: list[int], *, source: str = "nse_archive",
                        week_closed: bool | None = None, store: bool = True) -> dict[int, dict]:
    """``week_closed`` defaults to True on Fridays (and weekend special sessions): the run is after the close."""
    if week_closed is None:
        week_closed = d.weekday() >= 4
    if not stock_ids:
        return {}
    window = sessions_upto(db, d, HISTORY_SESSIONS, source)
    conn = db.connection()
    bars = pd.read_sql(_BARS, conn, params={"ids": stock_ids, "start": window[0], "d": d, "source": source})
    fac = pd.read_sql(_FACTORS, conn, params={"ids": stock_ids, "src": f"{source}:%"})
    factors = {sid: list(zip(g["ex_date"], g["factor"])) for sid, g in fac.groupby("stock_id")}
    out: dict[int, dict] = {}
    for sid, g in bars.groupby("stock_id", sort=False):
        vals = latest(g, factors.get(sid, []), d, week_closed=week_closed)
        if vals is not None:
            out[int(sid)] = vals
    if store and out:
        db.execute(delete(IndicatorSnapshot).where(IndicatorSnapshot.session_date == d, IndicatorSnapshot.source == source,
                                                   IndicatorSnapshot.version == INDICATOR_VERSION))
        rows = [{"session_date": d, "stock_id": sid, "version": INDICATOR_VERSION, "values": v, "source": source}
                for sid, v in out.items()]
        db.execute(insert(IndicatorSnapshot), rows)
        db.flush()
    return out
