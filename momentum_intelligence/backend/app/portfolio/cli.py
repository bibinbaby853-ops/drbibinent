"""Paper portfolio CLI (PAPER ONLY).

  init     --name main --capital 100000
  propose  --name main --symbol X --entry 100 --stop 94 [--target 120] [--date D] [--note ...]
  run      --name main [--date D | --start D --end D]     process sessions (fills, exits, risk checks)
  exit     --name main --position ID --reason "..."       manual exit at the next open
  show     --name main
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import select

from ..config import default_strategy_config
from ..database import session_scope
from ..models import PaperOrder, PaperPosition, PortfolioSnapshot
from . import service as svc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="portfolio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "propose", "run", "exit", "show"):
        sp = sub.add_parser(name)
        sp.add_argument("--name", default="main")
        if name == "init":
            sp.add_argument("--capital", type=float, default=None)
            sp.add_argument("--source", default="nse_archive")
        if name == "propose":
            sp.add_argument("--symbol", required=True)
            sp.add_argument("--entry", type=float, required=True)
            sp.add_argument("--stop", type=float, required=True)
            sp.add_argument("--target", type=float)
            sp.add_argument("--date")
            sp.add_argument("--note")
        if name == "run":
            sp.add_argument("--date")
            sp.add_argument("--start")
            sp.add_argument("--end")
            sp.add_argument("--auto-signals", action="store_true", help="propose each session's confirmed strategy signals")
        if name == "exit":
            sp.add_argument("--position", type=int, required=True)
            sp.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    cfg = default_strategy_config()
    with session_scope() as db:
        if a.cmd == "init":
            acct = svc.create_account(db, a.name, a.capital or cfg.simulated_capital_inr, a.source)
            print(f"paper account {acct.name!r}: Rs {acct.initial_capital:,.0f} ({acct.source})")
            return 0
        acct = svc.account_by_name(db, a.name)
        if a.cmd == "propose":
            p = svc.propose(db, acct, a.symbol, a.entry, a.stop, target=a.target,
                            session_date=date.fromisoformat(a.date) if a.date else None, note=a.note, cfg=cfg)
            out = {"proposal_id": p.id, "symbol": p.symbol, "session": p.session_date.isoformat(), "status": p.status,
                   "decision": p.decision}
            print(json.dumps(out, indent=2, default=str))
            return 0
        if a.cmd == "exit":
            o = svc.manual_exit(db, acct, a.position, a.reason)
            print(f"exit order {o.id} for {o.symbol} at the next open")
            return 0
        if a.cmd == "show":
            snap = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == acct.id)
                              .order_by(PortfolioSnapshot.session_date.desc()).limit(1)).scalars().first()
            print(f"{acct.name} (PAPER)  last processed {acct.last_processed}")
            if snap:
                print(f"  equity Rs {snap.equity:,.2f}  cash Rs {snap.cash:,.2f}  realised Rs {snap.realised_pnl:,.2f}  "
                      f"charges Rs {snap.charges:,.2f}  drawdown {snap.drawdown_pct:.2f}%  reconciled {snap.reconciliation.get('ok')}")
            for p in db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id, PaperPosition.status == "open")).scalars():
                print(f"  #{p.id} {p.symbol:12s} {p.qty:>6d} @ {p.avg_price:>9.2f}  stop {p.stop:>9.2f}  mark {p.mark or 0:>9.2f}  {p.sector}")
            for o in db.execute(select(PaperOrder).where(PaperOrder.account_id == acct.id, PaperOrder.status == "pending")).scalars():
                print(f"  pending {o.side} {o.kind} {o.symbol} x{o.qty} (created {o.created_session})")
            return 0
    # run: one transaction per session
    with session_scope() as db:
        acct = svc.account_by_name(db, a.name)
        all_s = svc.sessions(db, acct.source)
    if a.date:
        days = [date.fromisoformat(a.date)]
    else:
        start = date.fromisoformat(a.start) if a.start else (acct.last_processed or all_s[-1])
        end = date.fromisoformat(a.end) if a.end else all_s[-1]
        days = [s for s in all_s if start <= s <= end and (acct.last_processed is None or s > acct.last_processed)]
    for d in days:
        with session_scope() as db:
            acct = svc.account_by_name(db, a.name)
            print(json.dumps(svc.run_day(db, acct, d, cfg, auto_signals=a.auto_signals), default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
