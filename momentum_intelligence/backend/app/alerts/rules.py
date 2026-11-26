"""Alert rules: pure functions over COMPLETED intraday candles (the caller passes only completed bars).

Inputs per symbol: today's completed 15-minute bars (IST), the previous session's raw close, the
20-session average daily volume. Rules:

| rule | applies to | condition | sticky |
|---|---|---|---|
| stop_breached | open position | a completed bar's low <= stop (the paper exit is booked by the end-of-day run) | yes |
| stop_proximity | open position | lowest completed low within stop_proximity_pct above the stop | no |
| rs_lagging | open position | return since previous close lags the Nifty 50's by >= rs_lag_pct | no |
| breakout_trigger | watchlist setup (breakout / continuation) | a completed bar closed above the trigger level | yes |
| approaching_trigger | watchlist setup | last close within approach_pct below the trigger | no |
| volume_pace | position or watchlist | cumulative volume / (avg daily volume x elapsed share) >= volume_pace, after a minimum time | no |
| index_drop | market | Nifty 50 down >= index_drop_pct from the previous close | yes |
| vix_jump | market | India VIX up >= vix_jump_pct from the previous close | yes |
| price_discontinuity | any | price below 65% of the previous close (probable corporate action): other rules skipped | yes |

Sticky alerts stay active for the session once triggered; the others resolve when the condition stops holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import pandas as pd

from ..config import AlertConfig

SESSION_OPEN, SESSION_MINUTES = time(9, 15), 375
STICKY = {"stop_breached", "breakout_trigger", "index_drop", "vix_jump", "price_discontinuity"}
SEVERITY = {"stop_breached": "critical", "index_drop": "critical", "stop_proximity": "warning", "rs_lagging": "warning",
            "vix_jump": "warning", "price_discontinuity": "warning", "breakout_trigger": "info", "approaching_trigger": "info",
            "volume_pace": "info"}


@dataclass
class Candidate:
    rule: str
    symbol: str
    message: str
    bar_end: datetime | None
    values: dict = field(default_factory=dict)
    account: str = ""
    stock_id: int | None = None

    @property
    def severity(self) -> str:
        return SEVERITY[self.rule]


def bar_end(bars: pd.DataFrame, minutes: int = 15) -> datetime | None:
    return None if bars.empty else (bars["ts"].iloc[-1] + timedelta(minutes=minutes)).to_pydatetime()


def elapsed_minutes(end: datetime) -> float:
    start = end.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)
    return max(0.0, min(SESSION_MINUTES, (end - start).total_seconds() / 60))


def discontinuity(sym, bars, prev_close) -> Candidate | None:
    if bars.empty or not prev_close:
        return None
    last = float(bars["close"].iloc[-1])
    if last < 0.65 * prev_close:
        return Candidate("price_discontinuity", sym, f"{sym} at {last:.2f} vs previous close {prev_close:.2f}: probable split/bonus "
                         "or bad tick; other alerts for it are paused until the end-of-day data confirms", bar_end(bars),
                         {"last": last, "prev_close": prev_close})
    return None


def position_rules(sym, bars, *, stop, prev_close, nifty_ret_pct, cfg: AlertConfig, account="", stock_id=None) -> list[Candidate]:
    out: list[Candidate] = []
    if bars.empty:
        return out
    if (d := discontinuity(sym, bars, prev_close)):
        d.account, d.stock_id = account, stock_id
        return [d]
    end = bar_end(bars)
    low, last = float(bars["low"].min()), float(bars["close"].iloc[-1])
    common = {"account": account, "stock_id": stock_id}
    if low <= stop:
        out.append(Candidate("stop_breached", sym, f"{sym}: low {low:.2f} reached the stop {stop:.2f} ({account}); the paper exit is "
                             "booked by the end-of-day run at the stop or the gap price", end, {"low": low, "stop": stop, "last": last}, **common))
    elif (low - stop) / stop * 100 <= cfg.stop_proximity_pct:
        out.append(Candidate("stop_proximity", sym, f"{sym}: low {low:.2f} is {((low - stop) / stop * 100):.2f}% above the stop {stop:.2f} "
                             f"({account})", end, {"low": low, "stop": stop, "last": last}, **common))
    if prev_close and nifty_ret_pct is not None:
        ret = (last / prev_close - 1) * 100
        lag = ret - nifty_ret_pct
        if lag <= -cfg.rs_lag_pct:
            out.append(Candidate("rs_lagging", sym, f"{sym} {ret:+.2f}% today vs Nifty 50 {nifty_ret_pct:+.2f}% ({lag:+.2f} pts, {account})",
                                 end, {"return_pct": round(ret, 3), "nifty_pct": round(nifty_ret_pct, 3)}, **common))
    return out


def watch_rules(sym, bars, *, trigger, strategy, state, prev_close, cfg: AlertConfig, stock_id=None) -> list[Candidate]:
    out: list[Candidate] = []
    if bars.empty or not trigger:
        return out
    if (d := discontinuity(sym, bars, prev_close)):
        d.stock_id = stock_id
        return [d]
    end = bar_end(bars)
    closes = bars["close"]
    last = float(closes.iloc[-1])
    vals = {"trigger": trigger, "last": last, "strategy": strategy, "signal_state": state}
    if strategy in ("breakout", "continuation") and (closes > trigger).any():
        first = bars[closes > trigger]["ts"].iloc[0]
        out.append(Candidate("breakout_trigger", sym, f"{sym}: a completed 15-minute bar closed above the {strategy} trigger {trigger:.2f} "
                             f"(first at {first:%H:%M}). A signal is confirmed only on the daily close.", end, vals, stock_id=stock_id))
    elif 0 <= (trigger - last) / trigger * 100 <= cfg.approach_pct:
        out.append(Candidate("approaching_trigger", sym, f"{sym}: {last:.2f} is {((trigger - last) / trigger * 100):.2f}% below the "
                             f"{strategy} trigger {trigger:.2f}", end, vals, stock_id=stock_id))
    return out


def volume_rule(sym, bars, *, avg_daily_volume, cfg: AlertConfig, stock_id=None) -> Candidate | None:
    if bars.empty or not avg_daily_volume:
        return None
    end = bar_end(bars)
    mins = elapsed_minutes(end)
    if mins < cfg.volume_pace_after_minutes:
        return None
    pace = float(bars["volume"].sum()) / (avg_daily_volume * mins / SESSION_MINUTES)
    if pace >= cfg.volume_pace:
        return Candidate("volume_pace", sym, f"{sym}: volume running at {pace:.1f}x its 20-day pace", end,
                         {"pace": round(pace, 2), "volume": int(bars["volume"].sum()), "minutes": mins}, stock_id=stock_id)
    return None


def market_rules(nifty: pd.DataFrame, nifty_prev: float | None, vix: pd.DataFrame, vix_prev: float | None, cfg: AlertConfig) -> list[Candidate]:
    out = []
    if not nifty.empty and nifty_prev:
        ret = (float(nifty["close"].iloc[-1]) / nifty_prev - 1) * 100
        if ret <= -cfg.index_drop_pct:
            out.append(Candidate("index_drop", "NIFTY 50", f"Nifty 50 {ret:+.2f}% from the previous close (Yahoo, unofficial). "
                                 "The regime is recomputed at the end of the day; a regime only restricts new entries.",
                                 bar_end(nifty), {"return_pct": round(ret, 3)}))
    if not vix.empty and vix_prev:
        ch = (float(vix["close"].iloc[-1]) / vix_prev - 1) * 100
        if ch >= cfg.vix_jump_pct:
            out.append(Candidate("vix_jump", "INDIA VIX", f"India VIX {ch:+.1f}% from the previous close", bar_end(vix),
                                 {"change_pct": round(ch, 2)}))
    return out
