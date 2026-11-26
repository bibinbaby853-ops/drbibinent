"""Exit rules on completed daily bars (pure).

During a session (resting orders placed before it):
* stop: low <= stop -> sell all at min(open, stop) (a gap below the stop fills at the open)
* partial target: high >= target -> sell the configured fraction at max(open, target)

After the close (orders for the next open):
* trailing stop: once the close reaches trail_after_r x initial risk above entry, the stop rises to
  highest close since entry - trail_atr x ATR; it never falls
* after a partial exit the stop moves to at least the entry price (breakeven), if configured
* time stop: held for time_stop_sessions sessions -> sell all at the next open
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ExitConfig
from .ledger import Position


@dataclass
class IntradayExit:
    kind: str           # stop | partial_target
    qty: int
    reference: float    # price before slippage
    reason: str


def intraday(p: Position, o: float, h: float, l: float, cfg: ExitConfig) -> list[IntradayExit]:
    out: list[IntradayExit] = []
    # Conservative ordering on the same bar: if both stop and target are touched, assume the stop came first.
    if l <= p.stop:
        ref = min(o, p.stop)
        out.append(IntradayExit("stop", p.qty, ref, f"low {l} <= stop {p.stop:.2f}" + (" (gap below stop, filled at open)" if o < p.stop else "")))
        return out
    target = p.target if p.target is not None else (
        p.avg_price + cfg.partial_exit_r * p.risk_per_share if cfg.partial_exit_r else None)
    if target is not None and not p.partial_done and h >= target:
        q = max(1, int(p.qty * cfg.partial_exit_fraction))
        if q < p.qty:
            out.append(IntradayExit("partial_target", q, max(o, target), f"high {h} >= target {target:.2f}"))
    return out


def after_close(p: Position, close: float, atr: float | None, sessions_held: int, cfg: ExitConfig) -> dict:
    """Returns {"new_stop": float | None, "time_exit": bool, "reasons": [...]}; never lowers the stop."""
    reasons = []
    p.highest_close = max(p.highest_close, close)
    new_stop = p.stop
    if p.partial_done and cfg.breakeven_after_partial and new_stop < p.avg_price:
        new_stop = p.avg_price
        reasons.append("breakeven after partial exit")
    r = p.risk_per_share
    if atr and r > 0 and close >= p.avg_price + cfg.trail_after_r * r:
        trail = p.highest_close - cfg.trail_atr * atr
        if trail > new_stop:
            new_stop = trail
            reasons.append(f"trailing stop: highest close {p.highest_close:.2f} - {cfg.trail_atr} x ATR {atr:.2f}")
    new_stop = round(round(new_stop / 0.05) * 0.05, 2)
    if new_stop <= p.stop + 1e-9:
        new_stop = None
    time_exit = sessions_held >= cfg.time_stop_sessions
    if time_exit:
        reasons.append(f"time stop: held {sessions_held} sessions")
    return {"new_stop": new_stop, "time_exit": time_exit, "reasons": reasons}
