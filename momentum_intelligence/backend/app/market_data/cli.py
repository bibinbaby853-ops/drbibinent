"""Command line: ``python -m app.market_data.cli <command>``.

  ingest      --start YYYY-MM-DD --end YYYY-MM-DD   NSE bhavcopies into PostgreSQL (or --demo)
  reference                                          names, sectors, inactive flags from NSE masters
  adjust      [--rebuild] [--confirm-yahoo]          derive corporate-action factors, write adjusted bars
  status                                             what is in the database
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta

from sqlalchemy import text

from ..config import get_settings
from ..database import session_scope
from .demo import DemoProvider
from .http import HttpClient
from .ingest import confirm_inferred, ingest_range, ingest_reference, refresh_adjustments, reset_derived_factors
from .nse_archive import NSEArchiveProvider


def _provider(args):
    s = get_settings()
    if args.demo or s.data_mode == "demo" and not args.live:
        return DemoProvider()
    http = HttpClient(min_interval_s=s.http_min_interval_s, max_retries=s.http_max_retries, timeout_s=s.http_timeout_s)
    return NSEArchiveProvider(s.data_dir, http=http, offline=args.offline)


def cmd_ingest(args) -> int:
    p = _provider(args)
    start = date.fromisoformat(args.start) if args.start else date.today() - timedelta(days=args.days)
    end = date.fromisoformat(args.end) if args.end else date.today()
    changes = None
    if isinstance(p, NSEArchiveProvider):
        try:
            changes, _ = p.symbol_changes()
        except Exception as exc:  # noqa: BLE001
            print(f"symbol change list unavailable: {exc}", file=sys.stderr)

    def show(r):
        if r.status == "weekend":
            return
        extra = f" rows={r.rows} rejected={r.rejected} new={r.new_stocks} sym_chg={r.symbol_changes} isin_chg={r.isin_changes}" if r.status == "ingested" else ""
        print(f"{r.session_date} {r.status:13s}{extra}{' | ' + '; '.join(r.notes) if r.notes else ''}", flush=True)

    results = ingest_range(session_scope, p, start, end, symbol_changes=changes, progress=show)
    ingested = [r for r in results if r.status == "ingested"]
    with session_scope() as db:
        adj = refresh_adjustments(db, source=p.name, since=min((r.session_date for r in ingested), default=None))
    print(json.dumps({"provider": p.name, "licence": p.licence, "sessions_ingested": len(ingested),
                      "holidays": [str(r.session_date) for r in results if r.status == "holiday"],
                      "failed": [str(r.session_date) for r in results if r.status == "failed"], **adj}, indent=2))
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_reference(args) -> int:
    p = _provider(args)
    if not isinstance(p, NSEArchiveProvider):
        print("reference data comes from NSE; nothing to do in demo mode")
        return 0
    with session_scope() as db:
        print(json.dumps(ingest_reference(db, p), indent=2))
    return 0


def cmd_adjust(args) -> int:
    p = _provider(args)
    with session_scope() as db:
        out = {}
        if args.rebuild:
            out["factors_deleted"] = reset_derived_factors(db, source=p.name)
        out.update(refresh_adjustments(db, source=p.name))
    if args.confirm_yahoo and p.name != "demo":
        from .yahoo import YahooProvider
        with session_scope() as db:
            out["yahoo_confirmation"] = confirm_inferred(db, YahooProvider().splits, source=p.name)
    print(json.dumps(out, indent=2))
    return 0


def cmd_indices(args) -> int:
    """Nifty 50, Nifty 500, India VIX daily history from Yahoo (UNOFFICIAL), then a cross-check against NSE data."""
    from .indices import ingest_indices
    from .yahoo import YahooProvider
    start = date.fromisoformat(args.start) if args.start else date(2020, 1, 1)
    end = date.fromisoformat(args.end) if args.end else date.today()
    with session_scope() as db:
        out = ingest_indices(db, YahooProvider(), start, end)
    print(json.dumps({"licence": "unofficial (Yahoo Finance)", "rows": out}, indent=2))
    return 0


def cmd_status(args) -> int:
    q = {
        "sessions": "select count(*), min(session_date)::text, max(session_date)::text from trading_sessions",
        "stocks": "select demo, status, count(*) from stocks group by 1, 2 order by 1, 2",
        "candles": "select source, is_adjusted, count(*), max(session_date)::text from market_candles group by 1, 2 order by 1, 2",
        "corporate_actions": "select source, kind, count(*) from corporate_actions group by 1, 2 order by 1, 2",
        "quality_issues": "select severity, \"check\", count(*) from data_quality_issues group by 1, 2 order by 1, 3 desc",
    }
    with session_scope() as db:
        for name, sql in q.items():
            print(f"[{name}]")
            for row in db.execute(text(sql)):
                print("  ", *row)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(prog="market_data")
    ap.add_argument("--demo", action="store_true", help="use the labelled synthetic DEMO provider")
    ap.add_argument("--live", action="store_true", help="use NSE archives even if MI_DATA_MODE=demo")
    ap.add_argument("--offline", action="store_true", help="read cached files only, never the network")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--start")
    i.add_argument("--end")
    i.add_argument("--days", type=int, default=10, help="when --start is omitted: calendar days back from today")
    sub.add_parser("reference")
    a = sub.add_parser("adjust")
    a.add_argument("--confirm-yahoo", action="store_true", help="check inferred split/bonus factors against Yahoo (unofficial)")
    a.add_argument("--rebuild", action="store_true", help="delete derived factors and recompute them from raw bars")
    sub.add_parser("status")
    ix = sub.add_parser("indices")
    ix.add_argument("--start")
    ix.add_argument("--end")
    args = ap.parse_args(argv)
    return {"ingest": cmd_ingest, "reference": cmd_reference, "adjust": cmd_adjust, "status": cmd_status,
            "indices": cmd_indices}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
