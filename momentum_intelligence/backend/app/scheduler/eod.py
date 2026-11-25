"""End-of-day pipeline: everything that follows a new NSE bhavcopy, in order, idempotently.

1. ingest new sessions (NSE archives) and refresh adjustments; confirm inferred splits (Yahoo)
2. reference data (names, sectors, price bands / GSM) and benchmark indices (Yahoo, unofficial)
3. universe snapshot, scores, RS, sectors, regime for each new session
4. strategy signals for every configuration in use (effective settings + each account's profile)
5. every paper account processes the new sessions; accounts with a strategy profile take that
   profile's confirmed signals as proposals (forward testing)

Running it twice does nothing new. Phase 9 schedules it; until then run it by hand or from cron:
    python -m app.scheduler.eod
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta

from sqlalchemy import func, select

from ..config import default_strategy_config, get_settings
from ..database import session_scope
from ..models import PaperAccount, TradingSession
from ..universe.engine import build_snapshot


def log(step, **kw):
    print(json.dumps({"step": step, **kw}, default=str), flush=True)


def run(today: date | None = None, *, skip_network: bool = False) -> dict:
    t0 = time.time()
    cfg = default_strategy_config()
    s = get_settings()
    today = today or date.today()
    with session_scope() as db:
        last = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == "nse_archive"))
    new_sessions: list[date] = []
    if not skip_network:
        from ..market_data.http import HttpClient
        from ..market_data.indices import ingest_indices
        from ..market_data.ingest import confirm_inferred, ingest_range, ingest_reference, refresh_adjustments
        from ..market_data.nse_archive import NSEArchiveProvider
        from ..market_data.yahoo import YahooProvider
        p = NSEArchiveProvider(s.data_dir, http=HttpClient(min_interval_s=s.http_min_interval_s, max_retries=s.http_max_retries))
        changes = None
        try:
            changes, _ = p.symbol_changes()
        except Exception as exc:  # noqa: BLE001
            log("symbol_changes_unavailable", error=str(exc))
        res = ingest_range(session_scope, p, (last or today - timedelta(days=7)) + timedelta(days=1), today, symbol_changes=changes)
        new_sessions = [r.session_date for r in res if r.status == "ingested"]
        log("ingest", new_sessions=new_sessions, statuses={str(r.session_date): r.status for r in res})
        if new_sessions:
            with session_scope() as db:
                log("adjust", **refresh_adjustments(db, source="nse_archive", since=min(new_sessions)))
            with session_scope() as db:
                log("confirm_splits", **confirm_inferred(db, YahooProvider().splits, source="nse_archive"))
            with session_scope() as db:
                log("reference", **ingest_reference(db, p))
            with session_scope() as db:
                log("indices", **ingest_indices(db, YahooProvider(), min(new_sessions) - timedelta(days=10), today))
    if not new_sessions:
        log("no_new_sessions", latest=last)
    # Resumable: work out what is missing for recent sessions instead of trusting "new sessions" alone.
    from ..models import MarketRegime, UniverseSnapshot
    from ..universe.engine import config_hash
    with session_scope() as db:
        recent = db.execute(select(TradingSession.session_date).where(TradingSession.source == "nse_archive")
                            .order_by(TradingSession.session_date.desc()).limit(10)).scalars().all()[::-1]
        have_uni = set(db.execute(select(UniverseSnapshot.session_date).where(UniverseSnapshot.session_date.in_(recent),
                                                                            UniverseSnapshot.config_hash == config_hash(cfg)).distinct()).scalars())
        have_reg = set(db.execute(select(MarketRegime.session_date).where(MarketRegime.session_date.in_(recent))).scalars())
    for d in [d for d in recent if d not in have_uni]:
        with session_scope() as db:
            r = build_snapshot(db, d, cfg)
        log("universe", date=d, eligible=r.eligible, candidates=r.candidates)
    todo = [d for d in recent if d not in have_reg]
    if todo:
        from ..scoring.pipeline import run_range
        with session_scope() as db:
            rr = run_range(db, min(todo), max(todo), cfg)
        log("scoring", sessions=rr.sessions, scored=rr.scored_rows, regimes=rr.regimes)
    # signals for every configuration in use
    from ..portfolio import service as svc
    from ..strategies.pipeline import build, built_through, strategy_hash
    with session_scope() as db:
        accounts = db.execute(select(PaperAccount).order_by(PaperAccount.id)).scalars().all()
        acct_cfgs = {a.name: svc.account_config(a, cfg) for a in accounts}
        latest = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == "nse_archive"))
    built = set()
    for c in [cfg, *acct_cfgs.values()]:
        h = strategy_hash(c)
        if h in built:
            continue
        with session_scope() as db:
            done = built_through(db, c)
        if done is not None and done >= latest:
            built.add(h)
            continue
        with session_scope() as db:
            log("signals", hash=h, **build(db, latest, c))
        built.add(h)
    # paper accounts
    for a in accounts:
        if a.source != "nse_archive" or a.last_processed is None:
            continue
        with session_scope() as db:
            todo = [d for d in svc.sessions(db, "nse_archive") if d > a.last_processed]
        for d in todo:
            with session_scope() as db:
                acct = svc.account_by_name(db, a.name)
                out = svc.run_day(db, acct, d, acct_cfgs[a.name], auto_signals=bool(a.profile))
            log("paper", account=a.name, profile=a.profile, session=d, equity=out["equity"], fills=len(out["fills"]),
                exits=len(out["exits"]), proposals=len(out["proposals"]))
    summary = {"new_sessions": new_sessions, "latest_session": latest, "seconds": round(time.time() - t0)}
    log("done", **summary)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eod")
    ap.add_argument("--date", help="treat this as today (default: today)")
    ap.add_argument("--skip-network", action="store_true", help="no downloads; only process what is already stored")
    a = ap.parse_args(argv)
    run(date.fromisoformat(a.date) if a.date else None, skip_network=a.skip_network)
    return 0


if __name__ == "__main__":
    sys.exit(main())
