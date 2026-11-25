"""Backtest results (read-only). Historical simulations, not expected returns."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BacktestRun
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1/backtests", dependencies=[Authed], tags=["backtests"])
NOTE = ("Historical simulation on point-in-time data with next-open fills, slippage and Indian delivery charges. "
        "Past results do not predict future returns. Benchmark: Nifty 50 price index (unofficial Yahoo data, dividends excluded).")


@router.get("")
def list_runs(limit: int = Query(50, le=500), db: Session = Depends(db_session)) -> dict:
    rows = db.execute(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).scalars().all()
    keys = ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct", "trades", "expectancy_r", "bench_total_return_pct")
    return {"note": NOTE, "runs": [{"id": r.id, "name": r.name, "kind": r.kind, "start": r.start, "end": r.end, "capital": r.capital,
                                    "created_at": r.created_at, "metrics": {k: (r.metrics or {}).get(k) for k in keys}} for r in rows]}


@router.get("/{run_id}")
def get_run(run_id: int, include_trades: bool = False, db: Session = Depends(db_session)) -> dict:
    r = db.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, f"no backtest {run_id}")
    out = {"id": r.id, "name": r.name, "kind": r.kind, "start": r.start, "end": r.end, "capital": r.capital, "note": NOTE,
           "metrics": r.metrics, "details": r.details, "equity_curve": r.equity_curve, "config": r.config}
    if include_trades:
        out["trades"] = r.trades
    return out
