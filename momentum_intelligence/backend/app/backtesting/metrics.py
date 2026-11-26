"""Backtest statistics. Formulas (all from the daily equity curve unless noted):

* daily return r_t = E_t / E_{t-1} - 1 (the first against the starting capital)
* CAGR = (E_end / E_start) ^ (1 / years) - 1, years = calendar days / 365.25
* volatility = stdev(r, sample) x sqrt(252)
* Sharpe = (mean(r) - rf/252) / stdev(r) x sqrt(252); reported at rf = 0 and at ``rf`` (default 6.5%, Indian T-bills)
* Sortino = (mean(r) - rf/252) / sqrt(mean(min(r - rf/252, 0)^2)) x sqrt(252)
* max drawdown = max over t of 1 - E_t / max(E_<=t); duration = longest run of sessions below a prior peak
* Calmar = CAGR / max drawdown
* trades: win rate, average win / loss in R (P&L after all charges / initial risk), expectancy (mean R),
  profit factor (gross rupee wins / gross rupee losses), average holding sessions
* exposure = mean(invested / equity); time in market = share of sessions with an open position
* benchmark (Nifty 50 price index, dividends excluded): return, CAGR, max drawdown, beta = cov(r, b) / var(b),
  annualised alpha = (mean(r) - beta x mean(b)) x 252, correlation, information ratio = mean(r - b) / stdev(r - b) x sqrt(252)
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd


def _f(x, nd=4):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), nd)


def drawdown(equity: pd.Series) -> tuple[float, int]:
    peak = equity.cummax()
    dd = 1 - equity / peak
    below = (dd > 1e-12).astype(int).to_numpy()
    longest = run = 0
    for b in below:
        run = run + 1 if b else 0
        longest = max(longest, run)
    return float(dd.max()) if len(dd) else 0.0, int(longest)


def curve_stats(equity: pd.Series, start_capital: float, start: date, end: date, rf: float = 0.065) -> dict:
    e = equity.astype(float)
    r = pd.concat([pd.Series([e.iloc[0] / start_capital - 1]), e.pct_change().iloc[1:]], ignore_index=True)
    years = max((end - start).days, 1) / 365.25
    total = e.iloc[-1] / start_capital - 1
    cagr = (e.iloc[-1] / start_capital) ** (1 / years) - 1
    sd = r.std(ddof=1)
    vol = sd * math.sqrt(252)

    def sharpe(rate):  # undefined for a (numerically) riskless curve
        return None if not sd or sd < 1e-12 else (r.mean() - rate / 252) / sd * math.sqrt(252)

    down = np.minimum(r - rf / 252, 0)
    dd_sd = math.sqrt((down ** 2).mean())
    sortino = None if dd_sd < 1e-12 else (r.mean() - rf / 252) / dd_sd * math.sqrt(252)
    mdd, dur = drawdown(pd.concat([pd.Series([start_capital]), e], ignore_index=True))
    return {"total_return_pct": _f(total * 100), "cagr_pct": _f(cagr * 100), "volatility_pct": _f(vol * 100),
            "sharpe_rf0": _f(sharpe(0.0)), f"sharpe_rf{rf * 100:g}": _f(sharpe(rf)), "sortino": _f(sortino),
            "max_drawdown_pct": _f(mdd * 100), "max_drawdown_sessions": dur,
            "calmar": _f(None if mdd == 0 else cagr / mdd), "sessions": int(len(e)), "years": _f(years, 3)}, r


def trade_stats(trades) -> dict:
    done = [t for t in trades if t.exit_date is not None]
    if not done:
        return {"trades": 0}
    R = np.array([t.r_multiple for t in done])
    pnl = np.array([t.pnl for t in done])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    hold = [np.busday_count(t.entry_date, t.exit_date) + 1 for t in done]
    by = {}
    for t in done:
        b = by.setdefault(t.strategy, [])
        b.append(t.r_multiple)
    return {"trades": len(done), "open_at_end": sum(1 for t in trades if t.exit_date is None),
            "win_rate_pct": _f((pnl > 0).mean() * 100), "avg_win_r": _f(R[pnl > 0].mean() if (pnl > 0).any() else None),
            "avg_loss_r": _f(R[pnl <= 0].mean() if (pnl <= 0).any() else None), "expectancy_r": _f(R.mean()),
            "median_r": _f(float(np.median(R))), "best_r": _f(R.max()), "worst_r": _f(R.min()),
            "profit_factor": _f(None if losses.sum() == 0 else wins.sum() / abs(losses.sum())),
            "avg_holding_sessions": _f(float(np.mean(hold)), 1), "net_pnl": _f(pnl.sum(), 2),
            "charges": _f(sum(t.charges for t in done), 2),
            "by_strategy": {k: {"trades": len(v), "expectancy_r": _f(np.mean(v)), "win_rate_pct": _f((np.array(v) > 0).mean() * 100)}
                            for k, v in sorted(by.items())}}


def benchmark_stats(r: pd.Series, sessions: list[date], bench: pd.Series, start: date, end: date) -> dict:
    b = bench.reindex(sessions).ffill()
    if b.isna().all():
        return {"benchmark": None}
    br = b.pct_change()
    br.iloc[0] = 0.0
    br = br.fillna(0.0).reset_index(drop=True)
    years = max((end - start).days, 1) / 365.25
    tot = b.iloc[-1] / b.iloc[0] - 1
    var = br.var(ddof=1)
    beta = None if not var else r.cov(br) / var
    diff = r - br
    mdd, _ = drawdown(b.reset_index(drop=True))
    return {"benchmark": "NIFTY 50 (price index; NSE official closes, Yahoo where missing)", "bench_total_return_pct": _f(tot * 100),
            "bench_cagr_pct": _f(((1 + tot) ** (1 / years) - 1) * 100), "bench_max_drawdown_pct": _f(mdd * 100),
            "beta": _f(beta), "alpha_annual_pct": _f(None if beta is None else (r.mean() - beta * br.mean()) * 252 * 100),
            "correlation": _f(r.corr(br)), "information_ratio": _f(None if diff.std() == 0 else diff.mean() / diff.std() * math.sqrt(252))}


def summarize(result, start_capital: float, bench: pd.Series | None = None, rf: float = 0.065) -> dict:
    eq = result.equity
    if eq.empty:
        return {"sessions": 0}
    cs, r = curve_stats(eq["equity"], start_capital, result.start, result.end, rf)
    out = {**cs, **trade_stats(result.trades)}
    out["exposure_pct"] = _f((eq["invested"] / eq["equity"]).mean() * 100)
    out["time_in_market_pct"] = _f((eq["open_positions"] > 0).mean() * 100)
    out["max_open_positions"] = int(eq["open_positions"].max())
    out["max_aggregate_initial_risk_pct"] = _f((eq["agg_risk"] / eq["equity"]).max() * 100)
    out["charges_total"] = _f(eq["charges"].iloc[-1], 2)
    out["charges_pct_of_capital"] = _f(eq["charges"].iloc[-1] / start_capital * 100)
    out["final_equity"] = _f(eq["equity"].iloc[-1], 2)
    if bench is not None and len(bench):
        out.update(benchmark_stats(r, list(eq["session"]), bench, result.start, result.end))
    out["counts"] = result.counts
    return out
