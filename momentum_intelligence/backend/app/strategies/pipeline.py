"""Build strategy signals for all eligible stocks and store them.

Signals are derived data rebuilt from a fixed anchor session (the first session with RS ranks)
up to ``end``; the rules and the state machine are causal, so a session's signals never change
when later data arrives (tested). Levels are converted to the traded price of each decision
session (adjusted level x raw close / adjusted close), so they can be used for orders directly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..indicators.panel import compute as compute_indicators
from ..indicators.panel import weekly_asof
from ..models import RelativeStrength, SignalEvent, Stock, TradeSignal
from ..scoring.panel import load_panel
from . import machine
from .rules import evaluate

VERSION = "1"


def strategy_hash(cfg: StrategyConfig) -> str:
    from ..universe.engine import rules_of
    blob = {"rules": cfg.strategies.model_dump(mode="json"), "universe": rules_of(cfg), "version": VERSION}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()[:16]


_RS = text("""select session_date, stock_id, rs_rank, (values->>'excess_63')::float as excess_63
              from relative_strength where session_date <= :end and version = '1'""")
_RAW = text("""select stock_id, session_date, close from market_candles
               where source = :src and interval = '1d' and is_adjusted = false and session_date >= :start and session_date <= :end
                 and stock_id = any(cast(:ids as integer[]))""")


def stock_frame(panel, sid, rs_rank: pd.DataFrame, rs_ex: pd.DataFrame) -> pd.DataFrame | None:
    """Indicators + weekly trend + OHLCV + RS for one stock over the panel's sessions (shared with the backtester)."""
    b = panel.bars(sid)
    if len(b) < 60:
        return None
    f = compute_indicators(b).set_index("session_date").join(weekly_asof(b).set_index("session_date"))
    f = f.join(b.set_index("session_date")[["open", "high", "low", "volume"]])
    f["rs_rank"] = rs_rank[sid] if sid in rs_rank else np.nan
    f["excess_63"] = rs_ex[sid] if sid in rs_ex else np.nan
    return f


def load_rs(db: Session, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    rs = pd.read_sql(_RS, db.connection(), params={"end": end})
    return (rs.pivot(index="session_date", columns="stock_id", values="rs_rank"),
            rs.pivot(index="session_date", columns="stock_id", values="excess_63"))


def anchor(db: Session) -> date | None:
    return db.scalar(select(func.min(RelativeStrength.session_date)))


def build(db: Session, end: date, cfg: StrategyConfig, *, source: str = "nse_archive", start: date | None = None) -> dict:
    start = start or anchor(db)
    if start is None:
        raise RuntimeError("no relative strength computed yet; run the scoring pipeline first")
    panel = load_panel(db, start, end, cfg, lookback=10_000, source=source)
    dates = [d for d in panel.sessions if start <= d <= end]
    if not dates:
        return {"signals": 0}
    rs_rank, rs_ex = load_rs(db, end)
    elig = panel.eligible.loc[dates]
    sids = [s for s in elig.columns if elig[s].any()]
    raw = pd.read_sql(_RAW, db.connection(), params={"src": source, "start": start, "end": end, "ids": sids})
    raw_close = raw.pivot(index="session_date", columns="stock_id", values="close").reindex(dates)
    symbols = dict(db.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(sids))).all())
    h = strategy_hash(cfg)
    rows, events, counts = [], [], {}
    for sid in sids:
        f = stock_frame(panel, sid, rs_rank, rs_ex)
        if f is None:
            continue
        f = f.reindex(dates)
        results = evaluate(f, cfg.strategies)
        sigs = machine.run(dates, f["close"], results, cfg.strategies, eligible=elig[sid])
        ratio = (raw_close[sid] / f["close"]) if sid in raw_close else pd.Series(1.0, index=dates)
        for s in sigs:
            d_ref = s.confirmed or s.closed or s.last_progress
            k = ratio.get(d_ref, np.nan)
            k = 1.0 if pd.isna(k) else float(k)
            conv = lambda x: None if x is None else round(x * k, 2)  # noqa: E731
            key = f"{sid}:{s.opened.isoformat()}"
            rows.append({"key": key, "stock_id": int(sid), "symbol": symbols.get(sid, str(sid)), "strategy": s.strategy, "state": s.state,
                         "is_open": s.closed is None, "opened": s.opened, "closed": s.closed, "confirmed": s.confirmed,
                         "entry": conv(s.entry), "stop": conv(s.stop), "invalidation": conv(s.invalidation), "trigger": conv(s.trigger),
                         "reasons": s.reasons, "version": VERSION, "config_hash": h})
            events.append((key, s.events, k))
            counts[s.state] = counts.get(s.state, 0) + 1
    db.execute(delete(TradeSignal).where(TradeSignal.version == VERSION, TradeSignal.config_hash == h))
    if rows:
        db.execute(insert(TradeSignal), rows)
        ids = dict(db.execute(select(TradeSignal.key, TradeSignal.id).where(TradeSignal.config_hash == h)).all())
        ev_rows = []
        for key, evs, k in events:
            for e in evs:
                vals = {kk: (round(v * k, 2) if isinstance(v, float) else v) for kk, v in e["values"].items()}
                ev_rows.append({"signal_id": ids[key], "session_date": e["session_date"], "from_state": e["from_state"],
                                "to_state": e["to_state"], "reason": e["reason"], "values": vals})
        for i in range(0, len(ev_rows), 20_000):
            db.execute(insert(SignalEvent), ev_rows[i:i + 20_000])
    db.flush()
    return {"start": start.isoformat(), "end": end.isoformat(), "stocks": len(sids), "signals": len(rows), "by_state": counts,
            "confirmed_on_end": sum(1 for r in rows if r["confirmed"] == end)}


def confirmed_on(db: Session, d: date, cfg: StrategyConfig) -> list[TradeSignal]:
    return db.execute(select(TradeSignal).where(TradeSignal.confirmed == d, TradeSignal.config_hash == strategy_hash(cfg))
                      .order_by(TradeSignal.id)).scalars().all()
