"""``python -m app.strategies.cli build [--end D]`` rebuilds all signals; ``show [--date D]`` lists a session's signals."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import func, select, text

from ..config import default_strategy_config
from ..database import session_scope
from ..models import TradingSession
from .pipeline import build, strategy_hash


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="strategies")
    ap.add_argument("--source", default="nse_archive")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--end")
    s = sub.add_parser("show")
    s.add_argument("--date")
    a = ap.parse_args(argv)
    cfg = default_strategy_config()
    with session_scope() as db:
        last = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == a.source))
        if a.cmd == "build":
            print(json.dumps(build(db, date.fromisoformat(a.end) if a.end else last, cfg, source=a.source), indent=2))
            return 0
        d = date.fromisoformat(a.date) if a.date else last
        rows = db.execute(text("""select s.symbol, s.strategy, s.state, s.entry, s.stop, s.invalidation, m.total from trade_signals s
            left join momentum_scores m on m.stock_id = s.stock_id and m.session_date = :d
            where s.config_hash = :h and (s.confirmed = :d or (s.is_open and s.opened <= :d)) order by s.state, m.total desc nulls last"""),
            {"d": d, "h": strategy_hash(cfg)}).all()
        print(f"Signals on {d}: research output, every entry still passes the risk engine")
        for r in rows:
            print(f"  {r.state:16s} {r.strategy:13s} {r.symbol:12s} entry {r.entry or 0:>10.2f} stop {r.stop or 0:>10.2f}  score {r.total or 0:5.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
