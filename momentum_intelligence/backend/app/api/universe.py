"""Read-only universe and indicator endpoints (Phase 3)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import default_strategy_config
from ..indicators.panel import INDICATOR_VERSION
from ..models import IndicatorSnapshot, Stock, UniverseSnapshot
from ..universe.engine import REASONS, config_hash
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1", dependencies=[Authed], tags=["universe"])


def _latest_snapshot_date(db: Session, h: str, source: str) -> date | None:
    return db.scalar(select(func.max(UniverseSnapshot.session_date)).where(UniverseSnapshot.config_hash == h,
                                                                           UniverseSnapshot.source == source))


@router.get("/universe")
def universe(session_date: date | None = Query(None, alias="date"), eligible_only: bool = True, limit: int = Query(500, le=5000),
             source: str = "nse_archive", db: Session = Depends(db_session)) -> dict:
    cfg = default_strategy_config()
    h = config_hash(cfg)
    d = session_date or _latest_snapshot_date(db, h, source)
    if d is None:
        raise HTTPException(404, "no universe snapshot for the current rules; run `python -m app.universe.cli snapshot`")
    q = select(UniverseSnapshot).where(UniverseSnapshot.session_date == d, UniverseSnapshot.config_hash == h,
                                       UniverseSnapshot.source == source)
    rows = db.execute(q).scalars().all()
    if not rows:
        raise HTTPException(404, f"no universe snapshot for {d} with the current rules")
    excluded: dict[str, int] = {}
    for r in rows:
        for w in r.reasons:
            excluded[w] = excluded.get(w, 0) + 1
    shown = sorted((r for r in rows if r.eligible or not eligible_only), key=lambda r: -(r.median_value_cr or 0))[:limit]
    return {
        "session_date": d.isoformat(), "source": source, "demo": source == "demo", "rules_hash": h,
        "rules": {"min_median_traded_value_cr": cfg.min_median_traded_value_cr,
                  "liquidity_lookback_sessions": cfg.liquidity_lookback_sessions, **cfg.universe_rules.model_dump(mode="json")},
        "candidates": len(rows), "eligible": sum(r.eligible for r in rows), "excluded_by": excluded, "reason_codes": REASONS,
        "stocks": [{"symbol": r.symbol, "sector": r.sector, "series": r.series, "close": r.close,
                    "median_value_cr": r.median_value_cr, "history_sessions": r.history_sessions,
                    "eligible": r.eligible, "reasons": r.reasons} for r in shown],
    }


@router.get("/stocks/{symbol}/indicators")
def stock_indicators(symbol: str, session_date: date | None = Query(None, alias="date"), source: str = "nse_archive",
                     db: Session = Depends(db_session)) -> dict:
    stock = db.execute(select(Stock).where(Stock.symbol == symbol.upper(), Stock.demo.is_(source == "demo"))).scalars().first()
    if stock is None:
        raise HTTPException(404, f"unknown symbol {symbol}")
    q = select(IndicatorSnapshot).where(IndicatorSnapshot.stock_id == stock.id, IndicatorSnapshot.source == source,
                                        IndicatorSnapshot.version == INDICATOR_VERSION)
    q = q.where(IndicatorSnapshot.session_date == session_date) if session_date else q.order_by(IndicatorSnapshot.session_date.desc())
    snap = db.execute(q.limit(1)).scalars().first()
    if snap is None:
        raise HTTPException(404, f"no indicator snapshot for {symbol}{' on ' + session_date.isoformat() if session_date else ''}")
    return {"symbol": stock.symbol, "isin": stock.isin, "sector": stock.sector, "session_date": snap.session_date.isoformat(),
            "source": source, "adjustment": "point-in-time (corporate actions with ex-date on or before the session)",
            "indicator_version": snap.version, "values": snap.values}
