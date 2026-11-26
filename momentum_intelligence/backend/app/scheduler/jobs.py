"""Job runner: Postgres advisory lock per job, idempotent per key, every run recorded in ``job_runs``.

* a job that already succeeded for a key is skipped (status ``skipped`` is NOT recorded again; returned)
* a job whose function reports ``done=False`` (e.g. bhavcopy not published yet) records ``skipped``, so it
  is retried at the next trigger
* exceptions record ``failed`` with the error, and the lock is released
* a second process trying the same job at the same time gets ``locked`` and does nothing
"""

from __future__ import annotations

import traceback
import zlib
from typing import Callable

from sqlalchemy import select, text

from ..database import get_engine, session_scope
from ..models import JobRun, utcnow


def _lock_id(job: str) -> int:
    return zlib.crc32(f"momentum-intelligence:{job}".encode()) & 0x7FFFFFFF


def already_done(job: str, key: str) -> bool:
    with session_scope() as db:
        return db.execute(select(JobRun.id).where(JobRun.job == job, JobRun.key == key, JobRun.status == "success")).first() is not None


def run_job(job: str, key: str, fn: Callable[[], dict]) -> dict:
    """Run ``fn`` once per (job, key). ``fn`` returns a dict; ``{"done": False, ...}`` means 'try again later'."""
    if already_done(job, key):
        return {"job": job, "key": key, "status": "already_done"}
    with get_engine().connect() as lock_conn:
        got = lock_conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": _lock_id(job)}).scalar()
        if not got:
            return {"job": job, "key": key, "status": "locked"}
        try:
            if already_done(job, key):  # re-check under the lock
                return {"job": job, "key": key, "status": "already_done"}
            with session_scope() as db:
                row = JobRun(job=job, key=key, status="running")
                db.add(row)
                db.flush()
                rid = row.id
            try:
                out = fn() or {}
                status = "success" if out.get("done", True) else "skipped"
                err = None
            except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
                out, status, err = {}, "failed", "".join(traceback.format_exception_only(type(exc), exc)).strip()
                out["traceback"] = traceback.format_exc()[-4000:]
            with session_scope() as db:
                r = db.get(JobRun, rid)
                r.status, r.finished_at, r.details, r.error = status, utcnow(), _jsonable(out), err
            return {"job": job, "key": key, "status": status, **({"error": err} if err else {})}
        finally:
            lock_conn.execute(text("select pg_advisory_unlock(:k)"), {"k": _lock_id(job)})
            lock_conn.commit()


def _jsonable(v):
    import datetime as dt
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v
