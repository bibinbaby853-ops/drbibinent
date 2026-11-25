"""Signal state machine: one active signal per stock across strategies.

States: early_warning (1) -> potential_setup (2) -> confirmed (3); terminal: invalidated, expired.

Each session, the strongest candidate across enabled strategies is chosen (higher level first,
then priority order Breakout > Continuation > Pullback):

* no active signal: a candidate opens one (unless the stock is in its post-confirmation cooldown)
* active, below confirmed:
  - a potential setup whose close falls below its invalidation level -> invalidated
  - a stronger (or equal, refreshed) candidate -> upgraded / refreshed (the strategy may change)
  - otherwise it ages; after ``early_expiry_sessions`` / ``potential_expiry_sessions`` without a
    candidate at least as strong -> expired
* confirmed: emitted on that session (a trade proposal for the next open); the signal then
  closes, and the stock enters a cooldown so the same move cannot produce duplicate entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..config import StrategyRules
from .rules import CONFIRMED, EARLY, LEVEL_NAME, POTENTIAL


@dataclass
class Signal:
    strategy: str
    state: str
    level: int
    opened: date
    last_progress: date
    sessions_since_progress: int = 0
    entry: float | None = None
    stop: float | None = None
    invalidation: float | None = None
    trigger: float | None = None
    confirmed: date | None = None
    closed: date | None = None
    reasons: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def event(self, d, frm, to, why, **values):
        self.events.append({"session_date": d, "from_state": frm, "to_state": to, "reason": why, "values": values})


def _reasons(res: pd.DataFrame, i) -> dict:
    return {k[4:]: bool(res[k].iat[i]) for k in res.columns if k.startswith("why_")}


def run(dates: list[date], close: pd.Series, results: dict[str, pd.DataFrame], r: StrategyRules,
        state: Signal | None = None, cooldown_until: int | None = None, eligible: pd.Series | None = None) -> list[Signal]:
    """Run the machine over one stock's sessions. Returns every signal touched (closed ones have ``closed`` set).

    ``eligible`` (bool per session) gates new signals; ``state``/``cooldown_until`` resume from a stored state.
    """
    order = [s for s in r.enabled if s in results]
    out: list[Signal] = []
    active = state
    cool = cooldown_until if cooldown_until is not None else -1
    for i, d in enumerate(dates):
        cands = [(results[s]["level"].iat[i], -order.index(s), s) for s in order if results[s]["level"].iat[i] > 0]
        best = max(cands) if cands else None
        c = close.iat[i]
        if active is not None:
            # invalidation (potential setups only; early warnings expire instead)
            if active.level == POTENTIAL and active.invalidation is not None and c < active.invalidation:
                active.event(d, active.state, "invalidated", f"close {c:.2f} below invalidation level {active.invalidation:.2f}")
                active.state, active.closed = "invalidated", d
                out.append(active)
                active = None
            elif best is not None and best[0] >= active.level:
                lvl, _, strat = best
                res = results[strat]
                frm = active.state
                upgraded = lvl > active.level or strat != active.strategy
                active.strategy, active.level, active.state = strat, int(lvl), LEVEL_NAME[int(lvl)]
                active.entry, active.stop = float(c), float(res["stop"].iat[i])
                active.invalidation = None if pd.isna(res["invalidation"].iat[i]) else float(res["invalidation"].iat[i])
                active.trigger = None if pd.isna(res["trigger"].iat[i]) else float(res["trigger"].iat[i])
                active.reasons = _reasons(res, i)
                active.last_progress, active.sessions_since_progress = d, 0
                if upgraded:
                    active.event(d, frm, active.state, f"{strat}: {active.state}", stop=active.stop, entry=active.entry)
            else:
                active.sessions_since_progress += 1
                limit = r.early_expiry_sessions if active.level == EARLY else r.potential_expiry_sessions
                if active.sessions_since_progress >= limit:
                    active.event(d, active.state, "expired", f"no progress for {active.sessions_since_progress} sessions")
                    active.state, active.closed = "expired", d
                    out.append(active)
                    active = None
        if active is None and best is not None and i > cool and (eligible is None or bool(eligible.iat[i])):
            lvl, _, strat = best
            res = results[strat]
            active = Signal(strategy=strat, state=LEVEL_NAME[int(lvl)], level=int(lvl), opened=d, last_progress=d,
                            entry=float(c), stop=float(res["stop"].iat[i]),
                            invalidation=None if pd.isna(res["invalidation"].iat[i]) else float(res["invalidation"].iat[i]),
                            trigger=None if pd.isna(res["trigger"].iat[i]) else float(res["trigger"].iat[i]),
                            reasons=_reasons(res, i))
            active.event(d, None, active.state, f"{strat}: {active.state}", stop=active.stop, entry=active.entry)
        if active is not None and active.level == CONFIRMED:
            if eligible is not None and not bool(eligible.iat[i]):
                active.event(d, active.state, "expired", "confirmed but not eligible in the universe this session")
                active.state, active.closed = "expired", d
            else:
                active.confirmed, active.closed = d, d
                if active.events and active.events[-1]["to_state"] == "confirmed" and active.events[-1]["session_date"] == d:
                    active.events[-1]["reason"] += " -> entry proposal for the next open"
                else:
                    active.event(d, active.state, "confirmed", "entry proposal for the next open", stop=active.stop, entry=active.entry)
                cool = i + r.cooldown_sessions
            out.append(active)
            active = None
    if active is not None:
        out.append(active)  # still open at the end of the range
    return out
