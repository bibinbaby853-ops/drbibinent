"""Read-only market regime, sector rotation and momentum score endpoints (Phase 4).

Scores are research output, not recommendations; they never bypass the risk engine.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import default_strategy_config
from ..models import MarketRegime, MomentumScore, RelativeStrength, SectorData, Stock
from ..scoring.pipeline import scoring_hash
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1", dependencies=[Authed], tags=["scoring"])
DISCLAIMER = "Rule-based research score from historical data. Not investment advice; every trade passes the risk engine."


def _date_or_latest(db: Session, model, d: date | None) -> date:
    d = d or db.scalar(select(func.max(model.session_date)))
    if d is None:
        raise HTTPException(404, "nothing computed yet; run `python -m app.scoring.cli run`")
    return d


@router.get("/market/regime")
def market_regime(session_date: date | None = Query(None, alias="date"), history: int = Query(0, ge=0, le=500),
                  db: Session = Depends(db_session)) -> dict:
    d = _date_or_latest(db, MarketRegime, session_date)
    r = db.execute(select(MarketRegime).where(MarketRegime.session_date == d)).scalars().first()
    if r is None:
        raise HTTPException(404, f"no regime for {d}")
    out = {"session_date": d.isoformat(), "regime": r.regime, "raw_regime": r.components.get("raw_regime"),
           "confirmation": r.components.get("confirmation"), "score": r.score, "coverage": r.coverage,
           "components": r.components.get("components"), "inputs": r.components.get("inputs"), "policy": r.policy,
           "benchmark_licence": "unofficial (Yahoo Finance) for index levels and India VIX; breadth from NSE data",
           "note": "A regime restricts NEW entries only; it never closes positions by itself."}
    if history:
        rows = db.execute(select(MarketRegime.session_date, MarketRegime.regime, MarketRegime.score)
                          .where(MarketRegime.session_date <= d).order_by(MarketRegime.session_date.desc()).limit(history)).all()
        out["history"] = [{"date": x[0].isoformat(), "regime": x[1], "score": x[2]} for x in reversed(rows)]
    return out


@router.get("/sectors")
def sectors(session_date: date | None = Query(None, alias="date"), db: Session = Depends(db_session)) -> dict:
    d = _date_or_latest(db, SectorData, session_date)
    rows = db.execute(select(SectorData).where(SectorData.session_date == d)).scalars().all()
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0))
    return {"session_date": d.isoformat(),
            "method": "equal-weighted composites of each sector's eligible constituents; Unclassified is not ranked",
            "sectors": [{"sector": r.sector, "rank": r.rank, "rank_change": r.rank_change, "constituents": r.constituents,
                         "flags": r.flags, **r.values} for r in rows]}


def _hash_for(db: Session, d: date, config: str | None) -> str:
    if config == "any":
        h = db.scalar(select(MomentumScore.config_hash).where(MomentumScore.session_date == d).limit(1))
        if h is None:
            raise HTTPException(404, f"no scores for {d}")
        return h
    return scoring_hash(default_strategy_config())


@router.get("/scores")
def scores(session_date: date | None = Query(None, alias="date"), band: str | None = None, min_score: float = 0,
           limit: int = Query(100, le=2000), source: str = "nse_archive", config: str | None = None,
           db: Session = Depends(db_session)) -> dict:
    d = _date_or_latest(db, MomentumScore, session_date)
    h = _hash_for(db, d, config)
    q = (select(MomentumScore, Stock.symbol, RelativeStrength.rs_rank, RelativeStrength.classes)
         .join(Stock, Stock.id == MomentumScore.stock_id)
         .outerjoin(RelativeStrength, (RelativeStrength.stock_id == MomentumScore.stock_id) & (RelativeStrength.session_date == d))
         .where(MomentumScore.session_date == d, MomentumScore.config_hash == h, MomentumScore.total >= min_score,
                Stock.demo.is_(source == "demo")))
    if band:
        q = q.where(MomentumScore.band == band)
    rows = db.execute(q.order_by(MomentumScore.total.desc()).limit(limit)).all()
    return {"session_date": d.isoformat(), "rules_hash": h, "disclaimer": DISCLAIMER,
            "rows": [{"symbol": sym, "total": m.total, "band": m.band, "coverage": m.coverage, "groups": m.groups,
                      "rs_rank": rank, "rs_classes": classes or []} for m, sym, rank, classes in rows]}


@router.get("/stocks/{symbol}/score")
def stock_score(symbol: str, session_date: date | None = Query(None, alias="date"), source: str = "nse_archive",
                db: Session = Depends(db_session)) -> dict:
    stock = db.execute(select(Stock).where(Stock.symbol == symbol.upper(), Stock.demo.is_(source == "demo"))).scalars().first()
    if stock is None:
        raise HTTPException(404, f"unknown symbol {symbol}")
    q = select(MomentumScore).where(MomentumScore.stock_id == stock.id, MomentumScore.breakdown.is_not(None))
    q = q.where(MomentumScore.session_date == session_date) if session_date else q.order_by(MomentumScore.session_date.desc())
    m = db.execute(q.limit(1)).scalars().first()
    if m is None:
        raise HTTPException(404, f"no detailed score for {symbol}{' on ' + session_date.isoformat() if session_date else ''}")
    rs = db.execute(select(RelativeStrength).where(RelativeStrength.stock_id == stock.id,
                                                   RelativeStrength.session_date == m.session_date)).scalars().first()
    return {"symbol": stock.symbol, "session_date": m.session_date.isoformat(), "total": m.total, "band": m.band,
            "coverage": m.coverage, "groups": m.groups, "breakdown": m.breakdown, "disclaimer": DISCLAIMER,
            "relative_strength": None if rs is None else {"rank": rs.rs_rank, "rank_change": rs.rs_rank_change,
                                                          "classes": rs.classes, **rs.values}}
