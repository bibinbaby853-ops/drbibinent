"""``python -m app.universe.cli snapshot --date YYYY-MM-DD [--indicators]`` or ``--start/--end``; ``show --date``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import select

from ..config import default_strategy_config
from ..database import session_scope
from ..models import TradingSession, UniverseSnapshot
from .engine import REASONS, build_snapshot, config_hash
from .indicators import compute_for_session


def _sessions(db, start, end, source):
    return db.execute(select(TradingSession.session_date).where(TradingSession.source == source,
                                                                TradingSession.session_date >= start,
                                                                TradingSession.session_date <= end)
                      .order_by(TradingSession.session_date)).scalars().all()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="universe")
    ap.add_argument("--source", default="nse_archive")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--date")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--indicators", action="store_true", help="also compute indicator snapshots for eligible stocks")
    sh = sub.add_parser("show")
    sh.add_argument("--date")
    sh.add_argument("--limit", type=int, default=25)
    args = ap.parse_args(argv)
    cfg = default_strategy_config()

    if args.cmd == "snapshot":
        with session_scope() as db:
            if args.date:
                days = [date.fromisoformat(args.date)]
            elif args.start:
                days = _sessions(db, date.fromisoformat(args.start), date.fromisoformat(args.end or date.today().isoformat()), args.source)
            else:
                days = _sessions(db, date(1990, 1, 1), date.today(), args.source)[-1:]
        for d in days:
            with session_scope() as db:
                r = build_snapshot(db, d, cfg, source=args.source)
                line = {"date": d.isoformat(), "candidates": r.candidates, "eligible": r.eligible, "rules": r.config_hash, "excluded_by": r.reasons}
                if args.indicators and r.eligible:
                    ids = db.execute(select(UniverseSnapshot.stock_id).where(UniverseSnapshot.session_date == d,
                                                                             UniverseSnapshot.config_hash == r.config_hash,
                                                                             UniverseSnapshot.eligible.is_(True))).scalars().all()
                    line["indicator_rows"] = len(compute_for_session(db, d, list(ids), source=args.source))
            print(json.dumps(line), flush=True)
        return 0

    with session_scope() as db:
        d = date.fromisoformat(args.date) if args.date else _sessions(db, date(1990, 1, 1), date.today(), args.source)[-1]
        rows = db.execute(select(UniverseSnapshot).where(UniverseSnapshot.session_date == d, UniverseSnapshot.config_hash == config_hash(cfg),
                                                         UniverseSnapshot.source == args.source, UniverseSnapshot.eligible.is_(True))
                          .order_by(UniverseSnapshot.median_value_cr.desc()).limit(args.limit)).scalars().all()
        print(f"Eligible on {d} (top {args.limit} by median traded value):")
        for r in rows:
            print(f"  {r.symbol:14s} {r.sector[:34]:34s} close {r.close:>10.2f}  median Rs {r.median_value_cr:>9.1f} Cr")
        print("Reason codes:", json.dumps(REASONS, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
