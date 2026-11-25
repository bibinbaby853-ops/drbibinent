"""Paper portfolio endpoints (Phase 6). PAPER ONLY: there is no broker connection behind any of these."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import default_strategy_config
from ..models import PaperOrder, PaperPosition, PortfolioSnapshot, RiskEvent, TradeJournal, TradeProposal
from ..portfolio import service as svc
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1/portfolio", dependencies=[Authed], tags=["paper portfolio"])
PAPER = "PAPER TRADING ONLY - simulated fills at the next open with slippage and Indian delivery charges; no orders reach a broker."


def _acct(db: Session, name: str):
    try:
        return svc.account_by_name(db, name)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.get("")
def portfolio(account: str = "main", db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    snap = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == acct.id)
                      .order_by(PortfolioSnapshot.session_date.desc()).limit(1)).scalars().first()
    pos = db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id, PaperPosition.status == "open")).scalars().all()
    pending = db.execute(select(PaperOrder).where(PaperOrder.account_id == acct.id, PaperOrder.status == "pending")).scalars().all()
    return {"mode": "paper", "notice": PAPER, "account": acct.name, "initial_capital": acct.initial_capital,
            "last_processed": acct.last_processed, "snapshot": None if snap is None else {
                k: getattr(snap, k) for k in ("session_date", "equity", "cash", "invested", "unrealised_pnl", "realised_pnl",
                                              "charges", "peak_equity", "drawdown_pct", "open_positions", "aggregate_initial_risk",
                                              "exposure", "reconciliation")},
            "positions": [{"id": p.id, "symbol": p.symbol, "sector": p.sector, "strategy": p.strategy, "qty": p.qty,
                           "avg_price": p.avg_price, "stop": p.stop, "initial_stop": p.initial_stop, "target": p.target,
                           "mark": p.mark, "unrealised_pnl": None if p.mark is None else round((p.mark - p.avg_price) * p.qty, 2),
                           "initial_risk": p.initial_risk, "entry_date": p.entry_date, "partial_done": p.partial_done} for p in pos],
            "pending_orders": [{"id": o.id, "symbol": o.symbol, "side": o.side, "kind": o.kind, "qty": o.qty,
                                "reference_price": o.reference_price, "created_session": o.created_session} for o in pending]}


@router.get("/history")
def history(account: str = "main", limit: int = Query(250, le=5000), db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    rows = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == acct.id)
                      .order_by(PortfolioSnapshot.session_date.desc()).limit(limit)).scalars().all()
    return {"account": acct.name, "equity_curve": [{"date": r.session_date, "equity": r.equity, "drawdown_pct": r.drawdown_pct,
                                                     "cash": r.cash} for r in reversed(rows)]}


@router.get("/orders")
def orders(account: str = "main", status: str | None = None, limit: int = Query(200, le=5000), db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    q = select(PaperOrder).where(PaperOrder.account_id == acct.id)
    if status:
        q = q.where(PaperOrder.status == status)
    rows = db.execute(q.order_by(PaperOrder.id.desc()).limit(limit)).scalars().all()
    return {"orders": [{c: getattr(o, c) for c in ("id", "symbol", "side", "kind", "qty", "status", "reference_price", "created_session",
                                                     "filled_session", "fill_price", "slippage_bps", "charges", "reason")} for o in rows]}


@router.get("/proposals")
def proposals(account: str = "main", limit: int = Query(100, le=2000), db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    rows = db.execute(select(TradeProposal).where(TradeProposal.account_id == acct.id).order_by(TradeProposal.id.desc()).limit(limit)).scalars().all()
    out = []
    for p in rows:
        checks = db.execute(select(RiskEvent).where(RiskEvent.proposal_id == p.id).order_by(RiskEvent.id)).scalars().all()
        out.append({"id": p.id, "symbol": p.symbol, "strategy": p.strategy, "session_date": p.session_date, "entry": p.entry,
                    "stop": p.stop, "target": p.target, "score": p.score, "status": p.status, "decision": p.decision,
                    "risk_checks": [{"check": c.check, "outcome": c.outcome, "details": c.details} for c in checks]})
    return {"proposals": out}


@router.get("/journal")
def journal(account: str = "main", limit: int = Query(200, le=5000), db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    rows = db.execute(select(TradeJournal).where(TradeJournal.account_id == acct.id).order_by(TradeJournal.id.desc()).limit(limit)).scalars().all()
    return {"journal": [{"date": j.session_date, "action": j.action, "reason": j.reason, "position_id": j.position_id,
                         "order_id": j.order_id, "data_as_of": j.data_as_of, "details": j.details} for j in rows]}


@router.get("/risk-events")
def risk_events(account: str = "main", check: str | None = None, limit: int = Query(200, le=5000), db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    q = select(RiskEvent).where(RiskEvent.account_id == acct.id)
    if check:
        q = q.where(RiskEvent.check == check)
    rows = db.execute(q.order_by(RiskEvent.id.desc()).limit(limit)).scalars().all()
    return {"events": [{"date": r.session_date, "proposal_id": r.proposal_id, "check": r.check, "outcome": r.outcome,
                        "details": r.details} for r in rows]}


class ProposalIn(BaseModel):
    symbol: str
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(None, gt=0)
    note: str | None = Field(None, max_length=500)


@router.post("/proposals", status_code=201)
def create_proposal(body: ProposalIn, account: str = "main", db: Session = Depends(db_session)) -> dict:
    """Queue a manual entry for the risk engine (paper). Evaluated at once if its session is already processed."""
    acct = _acct(db, account)
    try:
        p = svc.propose(db, acct, body.symbol, body.entry, body.stop, target=body.target, note=body.note, cfg=default_strategy_config())
        db.commit()
    except (LookupError, ValueError) as e:
        db.rollback()
        raise HTTPException(422, str(e))
    return {"id": p.id, "symbol": p.symbol, "session_date": p.session_date, "status": p.status, "decision": p.decision,
            "notice": PAPER}


class ExitIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/positions/{position_id}/exit", status_code=201)
def exit_position(position_id: int, body: ExitIn, account: str = "main", db: Session = Depends(db_session)) -> dict:
    acct = _acct(db, account)
    try:
        o = svc.manual_exit(db, acct, position_id, body.reason)
        db.commit()
    except LookupError as e:
        db.rollback()
        raise HTTPException(404, str(e))
    return {"order_id": o.id, "symbol": o.symbol, "fills": "at the next session's open", "notice": PAPER}
