"""Scheduler process (ADR-007): APScheduler, Asia/Kolkata time, every job through ``jobs.run_job``.

| job | trigger | key (idempotency) | done when |
|---|---|---|---|
| pre_market | Mon-Fri 08:30 | date | report generated, earlier alerts expired |
| intraday | Mon-Fri every 15 min 09:30-15:45 | date + time slot | one monitoring pass (completed candles only) |
| eod | every day, every 20 min 18:00-23:40 | date | the day's NSE session is stored and processed, post-market report written |

``eod`` records ``skipped`` (not done) until NSE publishes, so it retries at the next trigger; on weekdays
that never publish (holidays) it simply keeps skipping until 23:40. Weekend special sessions are caught
because the job also runs on weekends.

    python -m app.scheduler.service                 # run the scheduler (foreground)
    python -m app.scheduler.service once eod        # run one job now (e.g. from cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, time

from ..config import default_strategy_config
from ..database import session_scope
from ..market_data.calendar import IST, now_ist
from .jobs import run_job

log = logging.getLogger("scheduler")
INTRADAY_FROM, INTRADAY_TO = time(9, 30), time(15, 45)


def _configs():
    from sqlalchemy import select
    from ..models import PaperAccount
    from ..portfolio.service import account_config
    cfg = default_strategy_config()
    with session_scope() as db:
        accts = db.execute(select(PaperAccount)).scalars().all()
        return cfg, [cfg, *[account_config(a, cfg) for a in accts]]


def pre_market(today: date | None = None) -> dict:
    today = today or now_ist().date()

    def fn():
        from ..alerts.monitor import expire_before
        from ..reports.daily import generate
        cfg, _ = _configs()
        with session_scope() as db:
            expired = expire_before(db, today)
            r = generate(db, "pre_market", cfg)
            return {"done": True, "report_id": r.id, "session": r.session_date, "alerts_expired": expired}
    return run_job("pre_market", today.isoformat(), fn)


def intraday(now: datetime | None = None, provider=None) -> dict:
    now = (now or now_ist()).astimezone(IST)
    if not (INTRADAY_FROM <= now.time() <= INTRADAY_TO) or now.weekday() >= 5:
        return {"job": "intraday", "status": "outside_market_hours"}
    slot = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)

    def fn():
        from ..alerts.monitor import run
        cfg, configs = _configs()
        with session_scope() as db:
            return run(db, cfg, configs, provider=provider, now=now)
    return run_job("intraday", slot.strftime("%Y-%m-%d %H:%M"), fn)


def eod(today: date | None = None) -> dict:
    today = today or now_ist().date()

    def fn():
        from . import eod as pipeline
        from ..reports.daily import generate
        out = pipeline.run(today)
        latest = out.get("latest_session")
        if latest is None or latest < today:
            return {"done": False, "reason": f"no NSE session stored for {today} yet (latest {latest})", **out}
        cfg, _ = _configs()
        with session_scope() as db:
            r = generate(db, "post_market", cfg, latest)
            rid = r.id
        ai = ai_report(rid)  # optional; never fails the end-of-day job
        return {"done": True, "report_id": rid, "ai": ai.get("status"), **out}
    return run_job("eod", today.isoformat(), fn)


def ai_report(report_id: int | None = None) -> dict:
    """Optional Claude narrative for the latest post-market report (no call at all when AI is disabled)."""
    from sqlalchemy import select
    from ..ai.research import enabled, narrate_report
    from ..models import Report
    with session_scope() as db:
        q = select(Report).where(Report.kind == "post_market").order_by(Report.session_date.desc()).limit(1) if report_id is None \
            else select(Report).where(Report.id == report_id)
        rep = db.execute(q).scalars().first()
        if rep is None:
            return {"job": "ai_report", "status": "no_report"}
        rid = rep.id
    if not enabled():
        return {"job": "ai_report", "status": "disabled", "report_id": rid}

    def fn():
        with session_scope() as db:
            row = narrate_report(db, db.get(Report, rid))
            return {"done": True, "report_id": rid, "narrative_status": row.status, "detail": row.detail}
    return run_job("ai_report", f"report-{rid}", fn)


JOBS = {"pre_market": pre_market, "intraday": intraday, "eod": eod, "ai_report": ai_report}


def build_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    s = BlockingScheduler(timezone=IST)
    s.add_job(pre_market, CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=IST), id="pre_market",
              coalesce=True, max_instances=1, misfire_grace_time=1800)
    s.add_job(intraday, CronTrigger(day_of_week="mon-fri", hour="9-15", minute="0,15,30,45", second=30, timezone=IST), id="intraday",
              coalesce=True, max_instances=1, misfire_grace_time=600)
    s.add_job(eod, CronTrigger(hour="18-23", minute="0,20,40", timezone=IST), id="eod", coalesce=True, max_instances=1,
              misfire_grace_time=1800)
    return s


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="scheduler")
    sub = ap.add_subparsers(dest="cmd")
    o = sub.add_parser("once")
    o.add_argument("job", choices=list(JOBS))
    a = ap.parse_args(argv)
    if a.cmd == "once":
        print(json.dumps(JOBS[a.job](), default=str, indent=2))
        return 0
    s = build_scheduler()
    for j in s.get_jobs():
        log.info("job %s: %s", j.id, j.trigger)
    s.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
