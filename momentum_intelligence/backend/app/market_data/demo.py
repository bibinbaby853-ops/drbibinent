"""DEMO provider: deterministic synthetic data, always labelled.

For development, tests and the dashboard when no live data is wanted. Prices
are a seeded random walk with drift regimes; one stock has a 1:2 split so the
corporate-action path is exercised. Every symbol starts with ``DEMO`` and every
frame carries ``licence="demo"``; ingest stores it with ``demo=True`` so it can
never be mistaken for, or mixed with, market data.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cached_property

import numpy as np
import pandas as pd

from .base import BarFrame, DataNotPublished, Lineage, Security, utcnow
from .calendar import session_bar_start, weekdays

DEMO_SECTORS = ["Information Technology", "Financial Services", "Capital Goods", "Healthcare",
                "Fast Moving Consumer Goods", "Automobile and Auto Components", "Metals & Mining", "Chemicals"]
SPLIT_SYMBOL = "DEMO03"


class DemoProvider:
    name = "demo"
    licence = "demo"

    def __init__(self, n_stocks: int = 24, start: date = date(2023, 1, 2), end: date | None = None, seed: int = 7):
        self.n = n_stocks
        self.start = start
        self.end = end or date.today()
        self.seed = seed

    @cached_property
    def sessions(self) -> list[date]:
        return weekdays(self.start, self.end)

    @cached_property
    def securities(self) -> list[Security]:
        return [Security(symbol=f"DEMO{i:02d}", isin=None, name=f"Demo Company {i:02d} (synthetic)", series="EQ",
                         industry=DEMO_SECTORS[i % len(DEMO_SECTORS)]) for i in range(1, self.n + 1)]

    @property
    def split_date(self) -> date:
        return self.sessions[len(self.sessions) // 2]

    @cached_property
    def _panel(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        n_days = len(self.sessions)
        market = rng.normal(0.0004, 0.009, n_days)
        frames = []
        for k, sec in enumerate(self.securities):
            beta = rng.uniform(0.6, 1.4)
            drift = rng.choice([-0.0006, 0.0, 0.0008, 0.0015])
            ret = drift + beta * market + rng.normal(0, rng.uniform(0.010, 0.022), n_days)
            close = rng.uniform(80, 2500) * np.exp(np.cumsum(ret))
            gap = rng.normal(0, 0.004, n_days)
            open_ = np.r_[close[0], close[:-1]] * np.exp(gap)
            span = np.abs(rng.normal(0, 0.012, n_days)) * close
            high = np.maximum(open_, close) + span * rng.uniform(0.2, 1.0, n_days)
            low = np.minimum(open_, close) - span * rng.uniform(0.2, 1.0, n_days)
            vol = (rng.lognormal(12.5, 0.6, n_days) * (1 + 3 * np.abs(ret) / 0.02)).round()
            df = pd.DataFrame({"symbol": sec.symbol, "isin": None, "series": "EQ", "session_date": self.sessions,
                               "open": open_, "high": high, "low": low, "close": close, "volume": vol})
            df["prev_close"] = df["close"].shift(1)
            if sec.symbol == SPLIT_SYMBOL:
                ex = df["session_date"] >= self.split_date
                for c in ("open", "high", "low", "close"):
                    df.loc[ex, c] = df.loc[ex, c] / 2
                df.loc[ex, "volume"] = df.loc[ex, "volume"] * 2
                df["prev_close"] = df["close"].shift(1)
                df.loc[df["session_date"] == self.split_date, "prev_close"] /= 2  # exchange-style adjusted prev close
            frames.append(df)
        p = pd.concat(frames, ignore_index=True)
        for c in ("open", "high", "low", "close", "prev_close"):
            p[c] = (p[c] / 0.05).round() * 0.05  # NSE tick
        p["turnover_inr"] = p["volume"] * (p["open"] + p["close"]) / 2
        p["trades"] = (p["volume"] / 150).round()
        p["delivery_qty"] = (p["volume"] * 0.45).round()
        p["delivery_pct"] = 45.0
        p["ts"] = p["session_date"].map(session_bar_start)
        return p

    def _lineage(self, dataset: str, as_of: date | None) -> Lineage:
        return Lineage(self.name, self.licence, dataset, None, utcnow(), as_of, None, params={"seed": self.seed})

    def bhavcopy(self, d: date) -> BarFrame:
        if d not in set(self.sessions):
            raise DataNotPublished(f"demo calendar has no session on {d}")
        df = self._panel[self._panel["session_date"] == d].reset_index(drop=True)
        return BarFrame(df, [self._lineage("demo_bhavcopy", d)])

    def daily_bars(self, symbols, start, end) -> BarFrame:
        p = self._panel
        m = (p["session_date"] >= start) & (p["session_date"] <= end)
        if symbols:
            m &= p["symbol"].isin(symbols)
        return BarFrame(p[m].reset_index(drop=True), [self._lineage("demo_daily", end)])

    def intraday_bars(self, symbols, interval, start, end) -> BarFrame:
        """Hourly/15m bars bridged from the daily bar (open -> close path); illustrative only."""
        steps = {"60m": 7, "15m": 25}[interval]
        minutes = {"60m": 60, "15m": 15}[interval]
        rng = np.random.default_rng(self.seed + 1)
        rows = []
        for r in self.daily_bars(symbols, start, end).data.itertuples():
            path = np.linspace(r.open, r.close, steps + 1) + rng.normal(0, (r.high - r.low) / 8, steps + 1)
            path[0], path[-1] = r.open, r.close
            path = np.clip(path, r.low, r.high)
            for j in range(steps):
                o, c = path[j], path[j + 1]
                rows.append({"symbol": r.symbol, "ts": r.ts + timedelta(minutes=minutes * j), "session_date": r.session_date,
                             "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": round(r.volume / steps)})
        return BarFrame(pd.DataFrame(rows), [self._lineage(f"demo_{interval}", end)], interval=interval)

    def index_bars(self, index, start, end) -> BarFrame:
        p = self.daily_bars(None, start, end).data
        rets = p.pivot(index="session_date", columns="symbol", values="close").pct_change().mean(axis=1).fillna(0)
        level = 10000 * np.exp(np.log1p(rets).cumsum())
        df = pd.DataFrame({"symbol": "DEMO INDEX", "session_date": level.index, "close": level.to_numpy()})
        df["open"] = df["close"].shift(1).fillna(df["close"])
        df["high"] = df[["open", "close"]].max(axis=1) * 1.003
        df["low"] = df[["open", "close"]].min(axis=1) * 0.997
        df["volume"] = 0
        df["ts"] = df["session_date"].map(session_bar_start)
        return BarFrame(df, [self._lineage("demo_index", end)])

    def security_master(self) -> list[Security]:
        return list(self.securities)
