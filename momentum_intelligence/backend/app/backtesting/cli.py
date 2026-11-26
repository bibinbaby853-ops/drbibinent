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


def _store(name, kind, res, metrics, cfg_dump, capital, details=None, start=None, end=None):
    curve = [[r.session.isoformat(), round(r.equity, 2), round(r.drawdown_pct, 3)] for r in res.equity.itertuples()] if res is not None else []
    trades = [] if res is None else [{"symbol": t.symbol, "strategy": t.strategy, "sector": t.sector, "entry": t.entry_date.isoformat(),
                                      "exit": t.exit_date.isoformat() if t.exit_date else None, "qty": t.qty, "entry_price": t.entry_price,
                                      "pnl": round(t.pnl, 2), "r": round(t.r_multiple, 3), "charges": round(t.charges, 2)} for t in res.trades]
    with session_scope() as db:
        start = res.start if res is not None else start
        end = res.end if res is not None else end
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
        sp.add_argument("--variants", help="comma-separated subset of sensitivity variants")
    rd = sub.add_parser("redesign", help="evaluate the pre-registered designs on the design period only")
    rd.add_argument("--name", default="redesign")
    ho = sub.add_parser("holdout", help="ONE final evaluation of a chosen design on the locked holdout period")
    ho.add_argument("--design", required=True)
    ho.add_argument("--confirm", action="store_true", help="required: acknowledges this consumes the holdout")
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
    if a.cmd == "redesign":
        return _redesign(a, cfg)
    if a.cmd == "holdout":
        return _holdout(a, cfg)
    with session_scope() as db:
        first = date.fromisoformat(a.start) if a.start else db.scalar(select(func.min(RelativeStrength.session_date)))
        last = date.fromisoformat(a.end) if a.end else db.scalar(select(func.max(TradingSession.session_date)))
        t = time.time()
        data = load(db, first, last, cfg)
    print(f"data loaded in {time.time() - t:.0f}s: {len(data.sessions)} sessions, {len(data.frames)} stocks", flush=True)
    if last >= presets.HOLDOUT_START:
        print(f"NOTE: this range reaches into the holdout period (from {presets.HOLDOUT_START}); research runs for design "
              "decisions must end before it (use `redesign`).", flush=True)
    capital = cfg.simulated_capital_inr
    if a.cmd in ("run", "all"):
        res = Backtester(data, cfg).run(first, last)
        m = summarize(res, capital, data.nifty)
        rid = _store(a.name or "default", "single", res, m, cfg.model_dump(mode="json"), capital)
        print(json.dumps({"id": rid, **{k: m[k] for k in ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct", "trades",
                                                          "expectancy_r", "bench_total_return_pct")}}), flush=True)
    if a.cmd in ("sensitivity", "all"):
        variants = presets.sensitivity_variants(cfg)
        if a.variants:
            variants = {k: v for k, v in variants.items() if k in a.variants.split(",")}
        grid = sensitivity(data, variants, first, last, capital,
                           progress=lambda n, m: print(json.dumps({"variant": n, **m}), flush=True))
        rid = _store(a.name or "sensitivity", "sensitivity", None, {}, cfg.model_dump(mode="json"), capital, {"variants": grid},
                     start=first, end=last)
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


KEYS = ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct", "trades", "win_rate_pct", "expectancy_r", "profit_factor",
        "exposure_pct", "charges_total", "bench_total_return_pct", "by_strategy")


def _redesign(a, cfg) -> int:
    """Design period only: data is loaded up to the day before the holdout, so nothing later can leak in."""
    from datetime import timedelta
    end = presets.HOLDOUT_START - timedelta(days=1)
    with session_scope() as db:
        data = load(db, presets.DESIGN_START, end, cfg)
    assert data.sessions[-1] < presets.HOLDOUT_START
    print(f"design data: {data.sessions[0]}..{data.sessions[-1]} ({len(data.frames)} stocks)", flush=True)
    cache, results = {}, {}
    periods = {"full": (presets.DESIGN_START, end), "h1": (presets.DESIGN_START, presets.DESIGN_SPLIT - timedelta(days=1)),
               "h2": (presets.DESIGN_SPLIT, end)}
    from .walkforward import run_config
    for name, (why, dcfg) in presets.designs(cfg).items():
        results[name] = {"rationale": why}
        for pname, (s, e) in periods.items():
            m = run_config(data, dcfg, s, e, cache, dcfg.simulated_capital_inr)["metrics"]
            results[name][pname] = {k: m.get(k) for k in KEYS}
        f, h1, h2 = results[name]["full"], results[name]["h1"], results[name]["h2"]
        print(json.dumps({"design": name, "full_ret": f["total_return_pct"], "full_sharpe": f["sharpe_rf0"], "maxdd": f["max_drawdown_pct"],
                          "trades": f["trades"], "expR": f["expectancy_r"], "exposure": f["exposure_pct"],
                          "h1_ret": h1["total_return_pct"], "h2_ret": h2["total_return_pct"]}), flush=True)
    ok = {n: r for n, r in results.items() if n != "D0_baseline" and (r["h1"]["total_return_pct"] or 0) > 0
          and (r["h2"]["total_return_pct"] or 0) > 0 and r["full"]["sharpe_rf0"] is not None}
    order = list(presets.designs(cfg))
    chosen = max(ok, key=lambda n: (round(ok[n]["full"]["sharpe_rf0"], 2), -order.index(n))) if ok else None
    _store(a.name, "redesign", None, {}, {"designs": order, "rule": "best design-period Sharpe among designs positive in both halves"},
           cfg.simulated_capital_inr, {"results": results, "chosen": chosen, "holdout_start": presets.HOLDOUT_START.isoformat()},
           start=presets.DESIGN_START, end=end)
    print(json.dumps({"chosen": chosen, "eligible": sorted(ok)}), flush=True)
    return 0


def _holdout(a, cfg) -> int:
    designs = presets.designs(cfg)
    if a.design not in designs:
        print(f"unknown design {a.design}; choose from {list(designs)}")
        return 2
    with session_scope() as db:
        used = db.execute(select(BacktestRun).where(BacktestRun.kind == "holdout")).scalars().all()
    if used and not a.confirm:
        print(f"the holdout has already been evaluated ({[u.name for u in used]}); a second look biases it. Re-run with --confirm "
              "only if you accept that, and report it.")
        return 3
    if not a.confirm:
        print("holdout evaluation consumes the untouched period; re-run with --confirm")
        return 3
    _, dcfg = designs[a.design]
    with session_scope() as db:
        last = db.scalar(select(func.max(TradingSession.session_date)))
        data = load(db, presets.DESIGN_START, last, dcfg)
    res = Backtester(data, dcfg, capital=dcfg.simulated_capital_inr).run(presets.HOLDOUT_START, last)
    m = summarize(res, dcfg.simulated_capital_inr, data.nifty)
    rid = _store(f"holdout:{a.design}", "holdout", res, m, dcfg.model_dump(mode="json"), dcfg.simulated_capital_inr,
                 {"previous_holdout_runs": [u.name for u in used]})
    print(json.dumps({"holdout_id": rid, **{k: m.get(k) for k in KEYS}}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
