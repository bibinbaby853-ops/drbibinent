"""Alerts, reports and job status (Phase 9)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Alert, JobRun, Report, utcnow
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1", dependencies=[Authed], tags=["monitoring"])


def _alert(a: Alert) -> dict:
    return {"id": a.id, "session_date": a.session_date, "rule": a.rule, "symbol": a.symbol, "account": a.account or None,
            "severity": a.severity, "state": a.state, "message": a.message, "values": a.values, "source": a.source,
            "bar_end": a.bar_end, "first_seen": a.first_seen, "last_seen": a.last_seen, "checks": a.checks,
            "acknowledged_at": a.acknowledged_at, "resolved_at": a.resolved_at}


@router.get("/alerts")
def alerts(session_date: date | None = Query(None, alias="date"), state: str | None = None, severity: str | None = None,
           limit: int = Query(500, le=5000), db: Session = Depends(db_session)) -> dict:
    d = session_date or db.scalar(select(func.max(Alert.session_date)))
    if d is None:
        return {"session_date": None, "alerts": [], "note": "No alerts yet: the intraday monitor runs every 15 minutes in market hours."}
    q = select(Alert).where(Alert.session_date == d)
    if state:
        q = q.where(Alert.state == state)
    if severity:
        q = q.where(Alert.severity == severity)
    rows = db.execute(q.order_by(Alert.last_seen.desc()).limit(limit)).scalars().all()
    counts = dict(db.execute(select(Alert.state, func.count()).where(Alert.session_date == d).group_by(Alert.state)).all())
    last = db.execute(select(JobRun).where(JobRun.job == "intraday").order_by(JobRun.id.desc()).limit(1)).scalars().first()
    return {"session_date": d, "alerts": [_alert(a) for a in rows], "counts": counts,
            "last_run": None if not last else {"key": last.key, "status": last.status, "finished_at": last.finished_at,
                                               "details": {k: v for k, v in (last.details or {}).items() if k != "traceback"}},
            "note": "Alerts use completed candles only; intraday data is Yahoo's (unofficial, delayed up to ~15 minutes)."}


@router.post("/alerts/{alert_id}/ack")
def acknowledge(alert_id: int, db: Session = Depends(db_session)) -> dict:
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, f"no alert {alert_id}")
    if a.state != "expired":
        a.state, a.acknowledged_at = "acknowledged", utcnow()
        db.commit()
    return _alert(a)


@router.get("/reports")
def reports(kind: str | None = None, limit: int = Query(30, le=500), db: Session = Depends(db_session)) -> dict:
    q = select(Report.id, Report.kind, Report.session_date, Report.generated_at)
    if kind:
        q = q.where(Report.kind == kind)
    rows = db.execute(q.order_by(Report.session_date.desc(), Report.generated_at.desc()).limit(limit)).all()
    return {"reports": [{"id": r.id, "kind": r.kind, "session_date": r.session_date, "generated_at": r.generated_at} for r in rows]}


@router.get("/reports/latest")
def latest_report(kind: str | None = None, db: Session = Depends(db_session)) -> dict:
    q = select(Report)
    if kind:
        q = q.where(Report.kind == kind)
    r = db.execute(q.order_by(Report.generated_at.desc()).limit(1)).scalars().first()
    if r is None:
        raise HTTPException(404, f"no {kind or ''} report yet; reports are written by the scheduler (pre-market 08:30, after the end-of-day run)")
    return report(r.id, db)


@router.get("/reports/{report_id}")
def report(report_id: int, db: Session = Depends(db_session)) -> dict:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, f"no report {report_id}")
    return {"id": r.id, "kind": r.kind, "session_date": r.session_date, "generated_at": r.generated_at, "data_as_of": r.data_as_of,
            "sources": r.sources, "markdown": r.content_md, "data": r.data}


@router.get("/jobs")
def jobs(limit: int = Query(50, le=500), db: Session = Depends(db_session)) -> dict:
    rows = db.execute(select(JobRun).order_by(JobRun.id.desc()).limit(limit)).scalars().all()
    return {"runs": [{"id": r.id, "job": r.job, "key": r.key, "status": r.status, "started_at": r.started_at,
                      "finished_at": r.finished_at, "error": r.error} for r in rows]}
