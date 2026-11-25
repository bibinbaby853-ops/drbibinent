"""Backtests (research only; results are historical simulations, not expected returns).

  python -m app.backtesting.cli run [--start D] [--end D] [--name N]       default configuration, one account
  python -m app.backtesting.cli sensitivity [--start D] [--end D]         one parameter at a time
  python -m app.backtesting.cli walkforward [--is-months 12] [--oos-months 6]
  python -m app.backtesting.cli all                                       all three, data loaded once
  python -m app.backtesting.cli show [--id N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date

from sqlalchemy import func, select

from ..config import default_strategy_config
from ..database import session_scope
from ..models import BacktestRun, RelativeStrength, TradingSession
from . import presets
from .data import load
from .engine import Backtester
from .metrics import summarize
from .walkforward import sensitivity, walk_forward


def _store(name, kind, res, metrics, cfg_dump, capital, details=None):
    curve = [[r.session.isoformat(), round(r.equity, 2), round(r.drawdown_pct, 3)] for r in res.equity.itertuples()] if res is not None else []
    trades = [] if res is None else [{"symbol": t.symbol, "strategy": t.strategy, "sector": t.sector, "entry": t.entry_date.isoformat(),
                                      "exit": t.exit_date.isoformat() if t.exit_date else None, "qty": t.qty, "entry_price": t.entry_price,
                                      "pnl": round(t.pnl, 2), "r": round(t.r_multiple, 3), "charges": round(t.charges, 2)} for t in res.trades]
    with session_scope() as db:
        start = res.start if res is not None else None
        end = res.end if res is not None else None
        row = BacktestRun(name=name, kind=kind, start=start, end=end, capital=capital, config=cfg_dump, metrics=metrics,
                          equity_curve=curve, trades=trades, details=details or {}, data_as_of=end)
        db.add(row)
        db.flush()
        return row.id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backtest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("run", "sensitivity", "walkforward", "all"):
        sp = sub.add_parser(n)
        sp.add_argument("--start")
        sp.add_argument("--end")
        sp.add_argument("--name", default=None)
        sp.add_argument("--is-months", type=int, default=12)
        sp.add_argument("--oos-months", type=int, default=6)
    sh = sub.add_parser("show")
    sh.add_argument("--id", type=int)
    a = ap.parse_args(argv)
    cfg = default_strategy_config()
    if a.cmd == "show":
        with session_scope() as db:
            q = select(BacktestRun).order_by(BacktestRun.id.desc())
            rows = [db.get(BacktestRun, a.id)] if a.id else db.execute(q.limit(20)).scalars().all()
            for r in rows:
                m = r.metrics or {}
                print(f"#{r.id} {r.kind:12s} {r.name:28s} {r.start}..{r.end} return {m.get('total_return_pct')}% "
                      f"sharpe {m.get('sharpe_rf0')} maxDD {m.get('max_drawdown_pct')}% trades {m.get('trades')} "
                      f"(Nifty {m.get('bench_total_return_pct')}%)")
        return 0
    with session_scope() as db:
        first = date.fromisoformat(a.start) if a.start else db.scalar(select(func.min(RelativeStrength.session_date)))
        last = date.fromisoformat(a.end) if a.end else db.scalar(select(func.max(TradingSession.session_date)))
        t = time.time()
        data = load(db, first, last, cfg)
    print(f"data loaded in {time.time() - t:.0f}s: {len(data.sessions)} sessions, {len(data.frames)} stocks", flush=True)
    capital = cfg.simulated_capital_inr
    if a.cmd in ("run", "all"):
        res = Backtester(data, cfg).run(first, last)
        m = summarize(res, capital, data.nifty)
        rid = _store(a.name or "default", "single", res, m, cfg.model_dump(mode="json"), capital)
        print(json.dumps({"id": rid, **{k: m[k] for k in ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct", "trades",
                                                          "expectancy_r", "bench_total_return_pct")}}), flush=True)
    if a.cmd in ("sensitivity", "all"):
        grid = sensitivity(data, presets.sensitivity_variants(cfg), first, last, capital,
                           progress=lambda n, m: print(json.dumps({"variant": n, **m}), flush=True))
        rid = _store(a.name or "sensitivity", "sensitivity", None, {}, cfg.model_dump(mode="json"), capital, {"variants": grid})
        with session_scope() as db:
            r = db.get(BacktestRun, rid)
            r.start, r.end = first, last
        print(json.dumps({"sensitivity_id": rid}), flush=True)
    if a.cmd in ("walkforward", "all"):
        cands = presets.candidates(cfg)
        wf = walk_forward(data, cands, "all|trail3|gate", capital=capital, is_months=a.is_months, oos_months=a.oos_months,
                          first=first, last=last,
                          progress=lambda w, p, s: print(json.dumps({"window_oos": [str(w.oos_start), str(w.oos_end)], "chosen": p,
                                                                    "is_sharpe": {k: v["sharpe"] for k, v in s.items()}}), flush=True))
        rid = _store(a.name or "walk-forward", "walk_forward", wf["result"], wf["oos"], {"candidates": list(cands), "default": "all|trail3|gate",
                                                                                         "is_months": a.is_months, "oos_months": a.oos_months},
                     capital, {"windows": wf["windows"]})
        print(json.dumps({"walkforward_id": rid, **{k: wf["oos"][k] for k in ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct",
                                                                               "trades", "expectancy_r", "bench_total_return_pct")}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
