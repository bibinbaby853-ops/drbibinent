"""Data for backtests, loaded once per process.

Look-ahead discipline: the engine only reads row ``d`` of a panel while processing session ``d``
(and the next session's open when filling at that session). Panels are causal by construction:
* fills and stops use RAW exchange prices (what traded that day)
* correlation and gap statistics use the ADJUSTED series (ratios; factors inside a window are known)
* ATR in rupees at d = adjusted ATR(d) x raw close(d) / adjusted close(d), i.e. on prices adjusted only
  for corporate actions with ex-date <= d (ADR-014)
* eligibility, sector and median traded value come from the point-in-time universe snapshots
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..indicators import core
from ..scoring.panel import MarketPanel, load_panel
from ..scoring.pipeline import scoring_hash
from ..strategies import machine
from ..strategies.pipeline import load_rs, stock_frame
from ..strategies.rules import evaluate


@dataclass
class Signal:
    stock_id: int
    strategy: str
    entry: float   # raw close of the decision session
    stop: float    # raw terms


@dataclass
class BacktestData:
    sessions: list[date]
    raw_open: pd.DataFrame
    raw_high: pd.DataFrame
    raw_low: pd.DataFrame
    raw_close: pd.DataFrame
    adj_open: pd.DataFrame
    adj_close: pd.DataFrame
    atr_pit: pd.DataFrame
    gap_p: pd.DataFrame           # percentile adverse overnight gap, percent (risk.gap_lookback / gap_percentile)
    eligible: pd.DataFrame
    median_value: pd.DataFrame
    sector: pd.Series
    regime: pd.Series             # session -> regime name
    policy: pd.Series             # session -> policy dict
    scores: pd.DataFrame          # session x stock -> score total
    factors: dict[tuple[int, date], float]
    nifty: pd.Series
    symbols: dict[int, str]
    anchor: date | None = None    # first session with RS: signals are always built from here (as in production)
    panel: MarketPanel | None = None
    frames: dict[int, pd.DataFrame] = field(default_factory=dict)  # signal factory cache

    # -------------------------------------------------- signals
    def signals(self, cfg: StrategyConfig, start: date, end: date) -> dict[date, list[Signal]]:
        """Confirmed signals per session for ``cfg.strategies`` (same rules and state machine as production)."""
        dates = [d for d in self.sessions if start <= d <= end]
        out: dict[date, list[Signal]] = {}
        for sid, f in self.frames.items():
            fr = f.reindex(dates)
            res = evaluate(fr, cfg.strategies)
            sigs = machine.run(dates, fr["close"], res, cfg.strategies, eligible=self.eligible[sid].reindex(dates).fillna(False))
            for s in sigs:
                if s.confirmed is None:
                    continue
                k = self.raw_close.at[s.confirmed, sid] / fr.at[s.confirmed, "close"]
                entry, stop = round(s.entry * k, 2), round(s.stop * k, 2)
                if stop < entry:
                    out.setdefault(s.confirmed, []).append(Signal(int(sid), s.strategy, entry, stop))
        for d in out:
            out[d].sort(key=lambda x: x.stock_id)
        return out


_RAW = text("""select c.stock_id, c.session_date, c.open, c.high, c.low, c.close from market_candles c join stocks s on s.id = c.stock_id
    where c.source = :src and c.interval = '1d' and not c.is_adjusted and s.kind = 'equity' and c.session_date >= :start and c.session_date <= :end""")
_REG = text("select session_date, regime, policy from market_regime where session_date >= :start and session_date <= :end")
_SC = text("select session_date, stock_id, total from momentum_scores where config_hash = :h and session_date >= :start and session_date <= :end")
_FAC = text("select stock_id, ex_date, factor from corporate_actions where confidence in ('high','medium') and source like :src")


def load(db: Session, start: date, end: date, cfg: StrategyConfig, *, source: str = "nse_archive", with_frames: bool = True) -> BacktestData:
    panel = load_panel(db, start, end, cfg, lookback=10_000, source=source)
    sess = panel.sessions
    raw = pd.read_sql(_RAW, db.connection(), params={"src": source, "start": sess[0], "end": end})
    wide = lambda c: raw.pivot(index="session_date", columns="stock_id", values=c).reindex(index=sess, columns=panel.close.columns)  # noqa: E731
    r_open, r_high, r_low, r_close = wide("open"), wide("high"), wide("low"), wide("close")
    atr_adj = pd.DataFrame({sid: core.atr(panel.high[sid], panel.low[sid], panel.close[sid], 14) for sid in panel.close.columns})
    atr_pit = atr_adj * (r_close / panel.close)
    gaps = ((panel.close.shift(1) - panel.open) / panel.close.shift(1) * 100).clip(lower=0)
    gap_p = gaps.rolling(cfg.risk.gap_lookback, min_periods=30).quantile(cfg.risk.gap_percentile / 100)
    reg = pd.read_sql(_REG, db.connection(), params={"start": sess[0], "end": end}).set_index("session_date")
    sc = pd.read_sql(_SC, db.connection(), params={"h": scoring_hash(cfg), "start": sess[0], "end": end})
    fac = pd.read_sql(_FAC, db.connection(), params={"src": f"{source}:%"})
    sym = dict(db.execute(text("select id, symbol from stocks where kind = 'equity'")).all())
    data = BacktestData(sessions=sess, raw_open=r_open, raw_high=r_high, raw_low=r_low, raw_close=r_close, adj_open=panel.open,
                        adj_close=panel.close, atr_pit=atr_pit, gap_p=gap_p, eligible=panel.eligible, median_value=panel.median_value,
                        sector=panel.sector, regime=reg["regime"], policy=reg["policy"],
                        scores=sc.pivot(index="session_date", columns="stock_id", values="total"),
                        factors={(int(r.stock_id), r.ex_date): float(r.factor) for r in fac.itertuples()},
                        nifty=panel.benchmarks.get("NIFTY 50", pd.Series(dtype=float)), symbols=sym, panel=panel)
    data.anchor = db.execute(text("select min(session_date) from relative_strength")).scalar()
    if with_frames:
        rs_rank, rs_ex = load_rs(db, end)
        dates = [d for d in sess if d >= start]
        for sid in [s for s in panel.eligible.columns if panel.eligible.loc[dates, s].any()]:
            f = stock_frame(panel, sid, rs_rank, rs_ex)
            if f is not None:
                data.frames[int(sid)] = f
    return data
