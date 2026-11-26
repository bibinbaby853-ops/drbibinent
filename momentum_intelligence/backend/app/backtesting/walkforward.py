"""Walk-forward validation and sensitivity analysis.

Walk-forward: rolling in-sample (IS) windows of ``is_months`` followed by out-of-sample (OOS) windows of
``oos_months``, stepping by ``oos_months``. For each window every candidate configuration is backtested
on the IS period from flat; the one with the best IS Sharpe (at least ``min_trades`` trades; ties and
too-few-trade cases keep the default) is used for the following OOS period. The OOS periods are then
simulated as ONE continuous account (positions carry across window boundaries; new entries follow the
configuration chosen for the window of their decision session). Only the stitched OOS result is evidence;
IS figures are reported for transparency, never as performance.

Sensitivity: one parameter at a time from the default, full period, same data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from ..config import StrategyConfig
from .data import BacktestData
from .engine import Backtester
from .metrics import summarize


def months_add(d: date, m: int) -> date:
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, 1)


@dataclass
class Window:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date


def windows(first: date, last: date, is_months: int = 12, oos_months: int = 6) -> list[Window]:
    out, s = [], date(first.year, first.month, 1)
    while True:
        is_end = months_add(s, is_months)
        oos_end = months_add(is_end, oos_months)
        if is_end > last:
            break
        out.append(Window(s, is_end, is_end, min(oos_end, last)))
        if oos_end > last:
            break
        s = months_add(s, oos_months)
    return out


def _sessions(data: BacktestData, a: date, b: date, inclusive_end=False):
    return [d for d in data.sessions if a <= d and (d <= b if inclusive_end else d < b)]


def run_config(data: BacktestData, cfg: StrategyConfig, start: date, end: date, signals_cache: dict, capital: float) -> dict:
    key = cfg.strategies.model_dump_json()
    if key not in signals_cache:
        signals_cache[key] = data.signals(cfg, data.anchor or data.sessions[0], data.sessions[-1])
    res = Backtester(data, cfg, signals=signals_cache[key], capital=capital).run(start, end)
    return {"result": res, "metrics": summarize(res, capital, data.nifty)}


def walk_forward(data: BacktestData, candidates: dict[str, StrategyConfig], default: str, *, capital: float,
                 is_months: int = 12, oos_months: int = 6, min_trades: int = 10, first: date | None = None, last: date | None = None,
                 progress: Callable | None = None) -> dict:
    first = first or data.anchor or data.sessions[0]
    last = last or data.sessions[-1]
    wins = windows(first, last, is_months, oos_months)
    cache: dict = {}
    chosen: list[tuple[Window, str, dict]] = []
    for w in wins:
        days = _sessions(data, w.is_start, w.is_end)
        if not days:
            continue
        scores = {}
        for name, cfg in candidates.items():
            m = run_config(data, cfg, days[0], days[-1], cache, capital)["metrics"]
            scores[name] = {"sharpe": m.get("sharpe_rf0"), "trades": m.get("trades", 0), "return_pct": m.get("total_return_pct"),
                            "max_dd_pct": m.get("max_drawdown_pct")}
        eligible = {n: s for n, s in scores.items() if s["trades"] >= min_trades and s["sharpe"] is not None}
        pick = default
        if eligible:
            best = max(eligible, key=lambda n: (eligible[n]["sharpe"], n == default))
            if eligible.get(default) is None or eligible[best]["sharpe"] > eligible[default]["sharpe"]:
                pick = best
        chosen.append((w, pick, scores))
        if progress:
            progress(w, pick, scores)
    # one continuous OOS account
    oos_first = chosen[0][0].oos_start
    oos_days = _sessions(data, oos_first, last, inclusive_end=True)

    def cfg_for(d):
        for w, name, _ in chosen:
            if w.oos_start <= d < w.oos_end or (d == last and w.oos_end == last):
                return candidates[name]
        return candidates[chosen[-1][1]]

    for name, cfg in candidates.items():  # make sure every candidate's signals exist
        key = cfg.strategies.model_dump_json()
        if key not in cache:
            cache[key] = data.signals(cfg, data.anchor or data.sessions[0], data.sessions[-1])
    merged = {}
    for d in oos_days:
        merged[d] = cache[cfg_for(d).strategies.model_dump_json()].get(d, [])
    res = Backtester(data, candidates[default], config_for=cfg_for, signals=merged, capital=capital).run(oos_days[0], oos_days[-1])
    return {"oos": summarize(res, capital, data.nifty), "result": res,
            "windows": [{"is": [w.is_start.isoformat(), w.is_end.isoformat()], "oos": [w.oos_start.isoformat(), w.oos_end.isoformat()],
                         "chosen": n, "is_scores": s} for w, n, s in chosen]}


def sensitivity(data: BacktestData, variants: dict[str, StrategyConfig], start: date, end: date, capital: float,
                progress: Callable | None = None) -> dict:
    cache: dict = {}
    out = {}
    for name, cfg in variants.items():
        m = run_config(data, cfg, start, end, cache, cfg.simulated_capital_inr)["metrics"]  # each variant's own capital
        out[name] = {k: m.get(k) for k in ("total_return_pct", "cagr_pct", "sharpe_rf0", "max_drawdown_pct", "trades", "win_rate_pct",
                                           "expectancy_r", "profit_factor", "exposure_pct", "charges_total", "bench_total_return_pct")}
        if progress:
            progress(name, out[name])
    return out
