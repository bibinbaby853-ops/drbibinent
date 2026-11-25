"""``python -m app.scoring.cli run [--start --end | --date]`` and ``show [--date] [--limit]``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import func, select, text

from ..config import default_strategy_config
from ..database import session_scope
from ..models import MarketRegime, TradingSession
from .pipeline import run_range, scoring_hash


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scoring")
    ap.add_argument("--source", default="nse_archive")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--date")
    r.add_argument("--start")
    r.add_argument("--end")
    r.add_argument("--detail-sessions", type=int, default=5)
    s = sub.add_parser("show")
    s.add_argument("--date")
    s.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    cfg = default_strategy_config()
    if args.cmd == "run":
        with session_scope() as db:
            last = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == args.source))
        start = date.fromisoformat(args.date or args.start) if (args.date or args.start) else last
        end = date.fromisoformat(args.date or args.end) if (args.date or args.end) else last

        def show(d, reg, n):
            print(json.dumps({"date": d.isoformat(), "regime": reg["regime"], "score": reg["score"], "coverage": reg["coverage"],
                              "scored": n}), flush=True)
        with session_scope() as db:
            res = run_range(db, start, end, cfg, detail_sessions=args.detail_sessions, source=args.source, progress=show)
        print(json.dumps({"sessions": res.sessions, "scored_rows": res.scored_rows, "regime_days": res.regimes}))
        return 0
    with session_scope() as db:
        d = date.fromisoformat(args.date) if args.date else db.scalar(select(func.max(MarketRegime.session_date)))
        reg = db.execute(select(MarketRegime).where(MarketRegime.session_date == d)).scalars().first()
        print(f"{d}  regime: {reg.regime} (score {reg.score}, coverage {reg.coverage})  policy: {reg.policy}")
        rows = db.execute(text("""
            select s.symbol, m.total, m.band, m.coverage, r.rs_rank, r.classes, u.sector
            from momentum_scores m join stocks s on s.id = m.stock_id
            join relative_strength r on r.stock_id = m.stock_id and r.session_date = m.session_date
            join stock_universe_snapshots u on u.stock_id = m.stock_id and u.session_date = m.session_date
            where m.session_date = :d and m.config_hash = :h order by m.total desc, r.rs_rank desc limit :n"""),
            {"d": d, "h": scoring_hash(cfg), "n": args.limit}).all()
        for x in rows:
            print(f"  {x.symbol:14s} {x.total:6.1f} {x.band:10s} cov {x.coverage:.2f}  RS {x.rs_rank or 0:5.1f}  {x.sector[:26]:26s} {','.join(x.classes)}")
        sec = db.execute(text("select sector, rank, rank_change, constituents, flags from sector_data where session_date = :d and rank is not null order by rank"), {"d": d}).all()
        print("Sectors:", "; ".join(f"{s.rank}. {s.sector} ({s.constituents}{', ' + ','.join(s.flags) if s.flags else ''})" for s in sec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
