"""Walk-forward backtest of the chart-pattern signals.

For every stock and every trading day, the pattern detectors are run on the
data available up to that day's close (no look-ahead: all indicators are
causal and each call only sees ``df.iloc[:t+1]``). When a setup appears, the
same trade plan the live report would print is executed with simple,
mechanical rules:

* READY setups (confirmed breakout, bullish retest, pullback bounce): buy at
  the next session's open if it opens inside the plan's entry range.
* BREAKOUT WATCH setups: wait up to ``trigger_days`` sessions for a daily
  close above the pivot on at least 1.5x volume, then buy the next open.
* PULLBACK WATCH setups: rest a limit order at the top of the pullback zone
  for up to ``pullback_days`` sessions.

Exits: the plan's stop, target 1 (usually 2R) or a time stop after
``max_hold`` sessions, whichever comes first. If the stop and target are both
inside one daily bar, the stop is assumed to have hit first (conservative).
Gaps through the stop exit at the open. A round-trip cost is deducted.

Only one signal per stock is open at a time. Two baselines use the same exit
rules so the pattern results can be judged against chance.

Known limits: the universe is today's index membership (survivorship bias),
prices are Yahoo's adjusted series, and only the pattern logic is tested,
not the full 100-point score (RS rank, sector, delivery and fundamentals have
no point-in-time history here).
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd

from .indicators import enrich_momentum
from .patterns import detect_patterns
from .tradeplan import BREAKOUT, PULLBACK, READY, plan_trade

log = logging.getLogger(__name__)


@dataclass
class BTConfig:
    start: str = ""  # first signal date (YYYY-MM-DD); default = 5 years before the last bar
    max_hold: int = 20
    trigger_days: int = 5
    pullback_days: int = 10
    cost_pct: float = 0.30  # round trip: brokerage, STT, stamp duty, slippage
    min_turnover_cr: float = 5.0
    baseline_every: int = 10


@dataclass
class Trade:
    symbol: str
    signal_date: str
    pattern: str
    stage: str
    path: str  # READY / BREAKOUT WATCH / PULLBACK WATCH / BASELINE...
    entry_date: str
    entry: float
    stop: float
    target1: float
    target2: float
    exit_date: str
    exit: float
    exit_reason: str
    r_t1: float  # R multiple, exiting at target 1
    hit_t1: bool
    hit_t2: bool  # reached target 2 before the stop (with a trailing exit not modelled)
    days: int
    net_pct: float  # % return after costs, exiting at target 1
    above_200: bool
    rvol: float
    rs_3m: float


def _extended(r: pd.Series, df: pd.DataFrame, pats) -> bool:
    atr = r.ATR14
    ext_atr = (r.Close - r.EMA20) / atr if atr else np.nan
    ret5 = (r.Close / df["Close"].iloc[-6] - 1) * 100 if len(df) > 6 else np.nan
    best = pats[0] if pats else None
    ext_pivot = (r.Close / best.pivot - 1) * 100 if best else np.nan
    return bool((ext_atr > 3.0) or (ret5 > 15) or (best is not None and best.stage == "confirmed" and ext_pivot > 5))


def _simulate(df: pd.DataFrame, i_entry: int, entry: float, stop: float, t1: float, t2: float, max_hold: int):
    """Walk forward from the entry bar. Returns (exit_idx, exit_price, reason, hit_t1, hit_t2)."""
    o, h, l, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    hit_t2 = False
    last = min(len(df) - 1, i_entry + max_hold - 1)
    for j in range(i_entry, last + 1):
        if j > i_entry and o[j] <= stop:
            return j, o[j], "stop (gap)", False, hit_t2
        if l[j] <= stop:
            return j, stop, "stop", False, hit_t2
        if h[j] >= t2:
            hit_t2 = True
        if h[j] >= t1:
            px = o[j] if (j > i_entry and o[j] > t1) else t1
            # keep scanning for target 2 information only
            for k in range(j, min(len(df) - 1, i_entry + max_hold - 1) + 1):
                if l[k] <= stop:
                    break
                if h[k] >= t2:
                    hit_t2 = True
                    break
            return j, px, "target 1", True, hit_t2
    return last, c[last], "time", False, hit_t2


def backtest_stock(args) -> list[dict]:
    symbol, raw, bench, cfg = args
    if raw is None or len(raw) < 320:
        return []
    df = enrich_momentum(raw)
    b = bench["Close"].reindex(df.index).ffill() if bench is not None else None
    start = pd.Timestamp(cfg.start) if cfg.start else df.index[-1] - pd.DateOffset(years=5)
    first = max(260, int(df.index.searchsorted(start)))
    o, h, l, c, rv = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close", "RVOL"))
    hi60 = df["High"].rolling(60, min_periods=20).max().to_numpy()
    ema50 = df["EMA50"].to_numpy()
    turnover = (df["Turnover20"] / 1e7).to_numpy()
    trades: list[dict] = []
    busy_until = -1
    n = len(df)

    def ctx(t):
        rs = np.nan
        if b is not None and t >= 63:
            rs = ((c[t] / c[t - 63]) - (b.iloc[t] / b.iloc[t - 63])) * 100
        return bool(c[t] > df["EMA200"].iat[t]) if not np.isnan(df["EMA200"].iat[t]) else False, float(rv[t]), float(rs)

    def record(t_sig, pat, stage, path, i_e, entry, stop, t1, t2):
        nonlocal busy_until
        if entry <= stop or i_e >= n:
            return
        j, px, why, ht1, ht2 = _simulate(df, i_e, entry, stop, t1, t2, cfg.max_hold)
        risk = entry - stop
        a200, rvol, rs3 = ctx(t_sig)
        trades.append(asdict(Trade(
            symbol, str(df.index[t_sig].date()), pat, stage, path, str(df.index[i_e].date()), round(entry, 2),
            round(stop, 2), round(t1, 2), round(t2, 2), str(df.index[j].date()), round(float(px), 2), why,
            round((px - entry) / risk, 3), ht1, ht2, int(j - i_e + 1),
            round((px / entry - 1) * 100 - cfg.cost_pct, 3), a200, round(rvol, 2), round(rs3, 2))))
        busy_until = max(busy_until, j)

    t = first
    while t < n - 1:
        if t <= busy_until or np.isnan(turnover[t]) or turnover[t] < cfg.min_turnover_cr:
            t += 1
            continue
        # every setup family needs price in an uptrend and near recent highs
        if not (c[t] > ema50[t] and c[t] >= 0.85 * hi60[t]):
            t += 1
            continue
        view = df.iloc[: t + 1]
        pats = detect_patterns(view)
        if not pats:
            t += 1
            continue
        a = SimpleNamespace(patterns=pats, extended=_extended(view.iloc[-1], view, pats))
        try:
            plan = plan_trade(a, view, 1_000_000, 1.0)
        except Exception:  # noqa: BLE001
            t += 1
            continue
        best = pats[0]
        if plan.watchlist == READY:
            i_e = t + 1
            if plan.entry_low <= o[i_e] <= plan.entry_high:
                record(t, best.name, best.stage, READY, i_e, o[i_e], plan.stop, plan.target1, plan.target2)
        elif plan.watchlist == BREAKOUT:
            pivot = best.pivot
            for k in range(t + 1, min(n - 1, t + cfg.trigger_days) + 1):
                if c[k] > pivot and rv[k] >= 1.5:
                    i_e = k + 1
                    if i_e < n:
                        entry = o[i_e]
                        plan_risk = plan.entry - plan.stop
                        if entry <= pivot * 1.05 and entry > plan.stop and plan_risk > 0:
                            # keep the plan's reward-to-risk multiples around the actual fill
                            r1 = (plan.target1 - plan.entry) / plan_risk
                            r2 = (plan.target2 - plan.entry) / plan_risk
                            risk = entry - plan.stop
                            record(t, best.name, best.stage, BREAKOUT, i_e, entry, plan.stop,
                                   entry + r1 * risk, entry + r2 * risk)
                    break
                if c[k] < plan.stop:
                    break
            busy_until = max(busy_until, t + cfg.trigger_days)
        else:  # pullback watch: resting limit order at the top of the zone
            lim = plan.entry_high
            for k in range(t + 1, min(n - 1, t + cfg.pullback_days) + 1):
                if l[k] <= lim:
                    fill = min(o[k], lim)
                    if fill > plan.stop:
                        record(t, best.name, best.stage, PULLBACK, k, fill, plan.stop, plan.target1, plan.target2)
                    break
            busy_until = max(busy_until, t + cfg.pullback_days)
        t += 1

    # baselines with the same exit geometry: stop 2 ATR, target 2R
    atr = df["ATR14"].to_numpy()
    for t in range(first, n - 1, cfg.baseline_every):
        if np.isnan(atr[t]) or np.isnan(turnover[t]) or turnover[t] < cfg.min_turnover_cr:
            continue
        entry = o[t + 1]
        stop = entry - 2 * atr[t]
        for path, ok in (("BASELINE: any day", True), ("BASELINE: uptrend day", c[t] > ema50[t] and ema50[t] > df["EMA200"].iat[t])):
            if not ok:
                continue
            j, px, why, ht1, ht2 = _simulate(df, t + 1, entry, stop, entry + 4 * atr[t], entry + 6 * atr[t], cfg.max_hold)
            a200, rvol, rs3 = ctx(t)
            trades.append(asdict(Trade(symbol, str(df.index[t].date()), path, "-", path, str(df.index[t + 1].date()),
                                       round(entry, 2), round(stop, 2), round(entry + 4 * atr[t], 2), round(entry + 6 * atr[t], 2),
                                       str(df.index[j].date()), round(float(px), 2), why, round((px - entry) / (2 * atr[t]), 3),
                                       ht1, ht2, int(j - t), round((px / entry - 1) * 100 - cfg.cost_pct, 3), a200,
                                       round(float(rv[t]), 2), round(rs3, 2))))
    return trades


def run_backtest(data: dict[str, pd.DataFrame], bench: pd.DataFrame | None, cfg: BTConfig, workers: int = 4) -> pd.DataFrame:
    jobs = [(t[:-3] if t.endswith(".NS") else t, df, bench, cfg) for t, df in data.items()]
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, res in enumerate(ex.map(backtest_stock, jobs, chunksize=4), 1):
            rows.extend(res)
            if k % 50 == 0:
                log.info("Backtested %d of %d stocks", k, len(jobs))
    return pd.DataFrame(rows)


def summarise(trades: pd.DataFrame, by: str | list[str]) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(by)
    out = pd.DataFrame({
        "Trades": g.size(),
        "Win%": g["hit_t1"].mean() * 100,
        "Stopped%": g["exit_reason"].apply(lambda s: s.str.startswith("stop").mean() * 100),
        "TimeExit%": g["exit_reason"].apply(lambda s: (s == "time").mean() * 100),
        "AvgR": g["r_t1"].mean(),
        "MedianR": g["r_t1"].median(),
        "AvgNet%": g["net_pct"].mean(),
        "T2Reach%": g["hit_t2"].mean() * 100,
        "AvgDays": g["days"].mean(),
    })
    gains = g["net_pct"].apply(lambda s: s[s > 0].sum())
    losses = g["net_pct"].apply(lambda s: -s[s < 0].sum())
    out["ProfitFactor"] = (gains / losses.replace(0, np.nan))
    return out.round(2).sort_values("AvgR", ascending=False)
