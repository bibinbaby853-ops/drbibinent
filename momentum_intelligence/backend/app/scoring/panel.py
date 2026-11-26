"""Wide panels (sessions x stock_id) for a date range, loaded in a few queries.

Prices are the stored adjusted series (adjusted to the latest session). That is
look-ahead free for everything computed here, because returns and ratios are
unchanged by multiplying a whole series by a constant; factors with ex-date
inside a window are known by the window's end. Absolute price levels (stops,
ATR in rupees) never come from this panel (ADR-014).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..market_data.indices import BENCHMARKS, DEMO_BENCHMARK, benchmark, index_closes
from ..universe.engine import config_hash

_SESSIONS = text("select session_date from trading_sessions where source = :src and session_date <= :end order by 1")
_BARS = text("""
    select c.stock_id, c.session_date, c.open, c.high, c.low, c.close, c.volume
    from market_candles c join stocks s on s.id = c.stock_id
    where c.source = :src and c.interval = '1d' and c.is_adjusted = true and s.kind = 'equity'
      and c.session_date >= :start and c.session_date <= :end and c.demo = :demo
""")
_UNIVERSE = text("""
    select session_date, stock_id, eligible, sector, median_value_cr from stock_universe_snapshots
    where config_hash = :h and source = :src and session_date >= :start and session_date <= :end
""")


@dataclass
class MarketPanel:
    sessions: list[date]
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    eligible: pd.DataFrame          # bool
    median_value: pd.DataFrame      # Rs Cr, NaN where no snapshot
    sector: pd.Series               # stock_id -> sector label (current classification, ADR-013)
    benchmarks: dict[str, pd.Series] = field(default_factory=dict)
    source: str = "nse_archive"
    # per benchmark and session: 'nse' (official), 'yahoo' (fallback), 'carried' (no bar; previous value used)
    benchmark_origin: dict[str, pd.Series] = field(default_factory=dict)

    def bars(self, sid: int) -> pd.DataFrame:
        df = pd.DataFrame({"open": self.open[sid], "high": self.high[sid], "low": self.low[sid],
                           "close": self.close[sid], "volume": self.volume[sid]}).dropna(subset=["close"])
        df.index.name = "session_date"
        return df.reset_index()


CARRY_LIMIT = 3   # sessions a missing index bar may be carried forward


def align_benchmark(close: pd.Series, origin: pd.Series, sessions: list) -> tuple[pd.Series, pd.Series]:
    """Benchmark closes on the NSE session calendar. A session with no bar from any source takes the previous
    close for up to CARRY_LIMIT sessions and is marked 'carried'; beyond that it stays missing."""
    raw = close.reindex(sessions)
    filled = raw.ffill(limit=CARRY_LIMIT)
    o = origin.reindex(sessions).astype(object)
    o[raw.isna() & filled.notna()] = "carried"
    return filled, o


def load_panel(db: Session, start: date, end: date, cfg: StrategyConfig, *, lookback: int = 420,
               source: str = "nse_archive") -> MarketPanel:
    """Panel covering ``lookback`` sessions before ``start`` through ``end``."""
    conn = db.connection()
    all_sessions = pd.read_sql(_SESSIONS, conn, params={"src": source, "end": end})["session_date"].tolist()
    i0 = max(0, next((i for i, d in enumerate(all_sessions) if d >= start), len(all_sessions)) - lookback)
    sessions = all_sessions[i0:]
    first = sessions[0]
    bars = pd.read_sql(_BARS, conn, params={"src": source, "start": first, "end": end, "demo": source == "demo"})
    def wide(col):
        return bars.pivot(index="session_date", columns="stock_id", values=col).reindex(sessions)
    uni = pd.read_sql(_UNIVERSE, conn, params={"h": config_hash(cfg), "src": source, "start": first, "end": end})
    elig = uni.pivot(index="session_date", columns="stock_id", values="eligible").reindex(sessions)
    med = uni.pivot(index="session_date", columns="stock_id", values="median_value_cr").reindex(sessions)
    close = wide("close")
    elig = elig.reindex(columns=close.columns).fillna(False).astype(bool)
    med = med.reindex(columns=close.columns)
    sector = uni.drop_duplicates("stock_id", keep="last").set_index("stock_id")["sector"].reindex(close.columns)
    sector = sector.fillna(cfg.universe_rules.unclassified_sector)
    bench, origin = {}, {}
    if source == "demo":  # demo data is benchmarked against the synthetic demo index only
        s = index_closes(db, DEMO_BENCHMARK, source="demo")
        if not s.empty:
            bench["NIFTY 50"] = s.reindex(sessions).ffill(limit=3)
            origin["NIFTY 50"] = pd.Series("demo", index=sessions, dtype=object)
    else:
        for name in BENCHMARKS:
            s, o = benchmark(db, name)
            if not s.empty:
                bench[name], origin[name] = align_benchmark(s, o, sessions)
    return MarketPanel(sessions, wide("open"), wide("high"), wide("low"), close, wide("volume"), elig, med, sector, bench, source, origin)
