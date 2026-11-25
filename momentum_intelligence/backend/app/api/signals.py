"""Read-only strategy signal endpoints (Phase 5)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import default_strategy_config
from ..models import SignalEvent, Stock, TradeSignal
from ..strategies.pipeline import strategy_hash
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1", dependencies=[Authed], tags=["signals"])
NOTE = "Rule-based setups from completed daily bars. Not advice; every entry passes the risk engine and the regime policy."


def _row(s: TradeSignal) -> dict:
    return {"id": s.id, "symbol": s.symbol, "strategy": s.strategy, "state": s.state, "open": s.is_open, "opened": s.opened,
            "closed": s.closed, "confirmed": s.confirmed, "entry": s.entry, "stop": s.stop, "invalidation": s.invalidation,
            "trigger": s.trigger, "conditions": s.reasons}


@router.get("/signals")
def signals(session_date: date | None = Query(None, alias="date"), state: str | None = None, strategy: str | None = None,
            limit: int = Query(500, le=5000), db: Session = Depends(db_session)) -> dict:
    h = strategy_hash(default_strategy_config())
    d = session_date or db.scalar(select(func.max(TradeSignal.opened)).where(TradeSignal.config_hash == h))
    if d is None:
        raise HTTPException(404, "no signals built; run `python -m app.strategies.cli build`")
    q = select(TradeSignal).where(TradeSignal.config_hash == h,
                                  or_(TradeSignal.confirmed == d, TradeSignal.closed == d,
                                      (TradeSignal.opened <= d) & (or_(TradeSignal.closed.is_(None), TradeSignal.closed > d))))
    if state:
        q = q.where(TradeSignal.state == state)
    if strategy:
        q = q.where(TradeSignal.strategy == strategy)
    rows = db.execute(q.order_by(TradeSignal.state, TradeSignal.symbol).limit(limit)).scalars().all()
    return {"session_date": d, "note": NOTE, "signals": [_row(s) for s in rows]}


@router.get("/stocks/{symbol}/signals")
def stock_signals(symbol: str, limit: int = Query(20, le=500), db: Session = Depends(db_session)) -> dict:
    stock = db.execute(select(Stock).where(Stock.symbol == symbol.upper(), Stock.kind == "equity")).scalars().first()
    if stock is None:
        raise HTTPException(404, f"unknown symbol {symbol}")
    rows = db.execute(select(TradeSignal).where(TradeSignal.stock_id == stock.id, TradeSignal.config_hash == strategy_hash(default_strategy_config()))
                      .order_by(TradeSignal.opened.desc()).limit(limit)).scalars().all()
    out = []
    for s in rows:
        ev = db.execute(select(SignalEvent).where(SignalEvent.signal_id == s.id).order_by(SignalEvent.id)).scalars().all()
        out.append({**_row(s), "events": [{"date": e.session_date, "from": e.from_state, "to": e.to_state, "reason": e.reason,
                                            "values": e.values} for e in ev]})
    return {"symbol": stock.symbol, "note": NOTE, "signals": out}
