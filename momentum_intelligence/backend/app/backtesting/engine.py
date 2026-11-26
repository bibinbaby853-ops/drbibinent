"""Event-driven backtest engine: the paper portfolio's daily cycle, in memory.

Per session d (same order and the same pure functions as ``portfolio.service.run_day``):
0. corporate actions with ex-date d on held positions
1. pending orders fill at d's open (entry: cancelled below the stop or above the chase limit; limits
   re-applied at the fill price; expiry when the stock does not trade); exit orders likewise
2. resting stops and partial targets on d's high/low
3. mark at d's close; breakeven / trailing stops; time stops -> exit orders for the next open
4. confirmed signals of d -> risk engine (regime policy, sizing, caps, correlation, gap risk) -> entry orders
5. equity curve and drawdown

``config_for`` may return a different StrategyConfig per session (walk-forward); entries use the config
of their decision session, and open positions are managed with the current session's exit rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from ..config import StrategyConfig
from ..portfolio.costs import charges, fill_price, slippage_bps
from ..portfolio.exits import after_close, intraday
from ..portfolio.ledger import Ledger, Position
from ..risk.engine import Context, OpenExposure, Proposal, evaluate
from .data import BacktestData, Signal


@dataclass
class Order:
    key: str
    stock_id: int
    side: str
    kind: str
    qty: int
    created: date
    expires_after: int
    reference: float | None = None
    stop: float | None = None
    strategy: str | None = None
    sector: str | None = None
    risk_budget: float | None = None
    position_key: str | None = None


@dataclass
class Trade:
    stock_id: int
    symbol: str
    strategy: str
    sector: str
    entry_date: date
    entry_price: float
    qty: int
    initial_risk: float
    exit_date: date | None = None
    pnl: float = 0.0
    charges: float = 0.0
    exits: list = field(default_factory=list)

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.initial_risk if self.initial_risk else 0.0


@dataclass
class BacktestResult:
    equity: pd.DataFrame                  # session, equity, cash, invested, drawdown_pct, open_positions, agg_risk
    trades: list[Trade]
    counts: dict
    config: dict
    start: date
    end: date


class Backtester:
    def __init__(self, data: BacktestData, cfg: StrategyConfig, config_for: Callable[[date], StrategyConfig] | None = None,
                 capital: float | None = None, signals: dict[date, list[Signal]] | None = None):
        self.data, self.cfg = data, cfg
        self.config_for = config_for or (lambda d: cfg)
        self.capital = capital or cfg.simulated_capital_inr
        self._signals = signals
        self.sidx = {d: i for i, d in enumerate(data.sessions)}

    # -------------------------------------------------- helpers (read only row d, or earlier rows)
    def _bar(self, sid, d):
        o = self.data.raw_open.at[d, sid] if sid in self.data.raw_open.columns else np.nan
        if pd.isna(o):
            return None
        return (float(o), float(self.data.raw_high.at[d, sid]), float(self.data.raw_low.at[d, sid]), float(self.data.raw_close.at[d, sid]))

    def _med(self, sid, d):
        v = self.data.median_value.at[d, sid] if sid in self.data.median_value.columns else np.nan
        return None if pd.isna(v) else float(v)

    def _corr(self, sid, held, d, lookback):
        i = self.sidx[d]
        window = self.data.adj_close.iloc[max(0, i - lookback - 1): i + 1]
        cols = [c for c in [sid, *held] if c in window.columns]
        sub = window[cols].dropna(how="all")
        rets = sub.pct_change(fill_method=None).tail(lookback)
        out = {}
        if sid not in rets.columns:
            return out
        for h in held:
            if h in rets.columns:
                pair = rets[[sid, h]].dropna()
                if len(pair) >= 20:
                    v = pair[sid].corr(pair[h])
                    if not pd.isna(v):
                        out[h] = float(v)
        return out

    # -------------------------------------------------- run
    def run(self, start: date, end: date) -> BacktestResult:
        d0 = self.data
        days = [d for d in d0.sessions if start <= d <= end]
        sigs = self._signals if self._signals is not None else d0.signals(self.cfg, d0.anchor or days[0], end)
        L = Ledger.new(self.capital)
        orders: list[Order] = []
        trades: dict[str, Trade] = {}
        closed: list[Trade] = []
        counts: dict[str, int] = {}
        curve = []
        peak = self.capital
        seq = 0

        def bump(k, n=1):
            counts[k] = counts.get(k, 0) + n

        def sell(p: Position, qty, ref, bps, d, kind, cfg):
            px = fill_price("sell", ref, bps)
            ch = charges("sell", qty, px, cfg.execution.costs).total
            pnl = L.sell(p.key, qty, px, ch)
            t = trades[p.key]
            t.pnl += pnl
            t.charges += ch
            t.exits.append((d, kind, qty, px))
            bump(f"exit_{kind}")
            if p.key not in L.positions:
                t.exit_date = d
                closed.append(trades.pop(p.key))

        for d in days:
            cfg = self.config_for(d)
            sess_upto = self.sidx[d]
            # 0. corporate actions
            for p in list(L.positions.values()):
                f = d0.factors.get((p.stock_id, d))
                if f:
                    L.adjust_for_corporate_action(p.stock_id, f)
                    bump("corporate_actions")
            # 1. orders at the open
            still = []
            for o in orders:
                bar = self._bar(o.stock_id, d)
                if bar is None:
                    waited = sum(1 for s in d0.sessions[self.sidx[o.created] + 1: sess_upto + 1])
                    if waited >= o.expires_after:
                        bump("order_expired")
                    else:
                        still.append(o)
                    continue
                op = bar[0]
                bps = slippage_bps(self._med(o.stock_id, d), cfg.execution.slippage)
                if o.kind == "entry":
                    ecfg = self.config_for(o.created)
                    if op <= o.stop:
                        bump("entry_cancelled_below_stop")
                        continue
                    if op > o.reference * (1 + ecfg.execution.max_chase_pct / 100):
                        bump("entry_cancelled_chase")
                        continue
                    px = fill_price("buy", op, bps)
                    reserve = L.equity * ecfg.cash_reserve_pct / 100
                    per_share = px - o.stop
                    limits = [o.qty, math.floor(max(L.cash - reserve, 0) / (px * 1.0012)),
                              math.floor((o.risk_budget or float("inf")) / per_share),
                              math.floor(max(L.equity * ecfg.max_initial_aggregate_risk_pct / 100 - L.aggregate_initial_risk(), 0) / per_share)]
                    qty = min(limits)
                    if qty < 1:
                        bump("entry_cancelled_no_room")
                        continue
                    if qty < o.qty:
                        bump("entry_recapped_at_fill")
                    ch = charges("buy", qty, px, ecfg.execution.costs).total
                    seq += 1
                    key = f"p{seq}"
                    pos = Position(key=key, stock_id=o.stock_id, symbol=d0.symbols.get(o.stock_id, str(o.stock_id)), sector=o.sector,
                                   qty=qty, avg_price=px, initial_stop=o.stop, stop=o.stop, initial_risk=round(qty * (px - o.stop), 2),
                                   entry_date=d, strategy=o.strategy, highest_close=px, entry_qty=qty, buy_charges=0.0)
                    L.buy(pos, px, ch)
                    trades[key] = Trade(o.stock_id, pos.symbol, o.strategy, o.sector, d, px, qty, pos.initial_risk, charges=ch)
                    bump("fills")
                else:
                    p = L.positions.get(o.position_key)
                    if p is None:
                        continue
                    sell(p, min(o.qty, p.qty), op, bps, d, o.kind, cfg)
            orders = still
            # 2. intraday stops / targets
            for p in list(L.positions.values()):
                bar = self._bar(p.stock_id, d)
                if bar is None:
                    continue
                bps = slippage_bps(self._med(p.stock_id, d), cfg.execution.slippage)
                for ex in intraday(p, bar[0], bar[1], bar[2], cfg.exits):
                    sell(p, ex.qty, ex.reference, bps, d, ex.kind, cfg)
                    if ex.kind == "partial_target" and p.key in L.positions:
                        p.partial_done = True
            # 3. after the close
            L.mark({p.stock_id: b[3] for p in L.positions.values() if (b := self._bar(p.stock_id, d)) is not None})
            for p in list(L.positions.values()):
                bar = self._bar(p.stock_id, d)
                if bar is None:
                    continue
                atr = d0.atr_pit.at[d, p.stock_id]
                held = sess_upto - self.sidx[p.entry_date] + 1
                r = after_close(p, bar[3], None if pd.isna(atr) else float(atr), held, cfg.exits)
                if r["new_stop"] is not None:
                    p.stop = r["new_stop"]
                if r["time_exit"] and not any(o.position_key == p.key for o in orders):
                    orders.append(Order(f"time:{p.key}:{d}", p.stock_id, "sell", "time_stop", p.qty, d, 5, position_key=p.key))
            # 4. new entries from confirmed signals
            todays = sigs.get(d, [])
            if todays:
                regime = d0.regime.get(d, "unknown") if d in d0.regime.index else "unknown"
                # policy comes from the configuration under test (the regime classification is data)
                pol = cfg.regime.policies[regime].model_dump() if regime in cfg.regime.policies else None
                scored = []
                for s in todays:
                    sc = d0.scores.at[d, s.stock_id] if (d in d0.scores.index and s.stock_id in d0.scores.columns) else np.nan
                    scored.append((None if pd.isna(sc) else float(sc), s))
                scored.sort(key=lambda x: (-(x[0] or 0), x[1].stock_id))
                entries_today = 0
                for sc, s in scored:
                    exposures = [OpenExposure(p.stock_id, p.symbol, p.sector, p.market_value, p.initial_risk * p.qty / p.entry_qty)
                                 for p in L.positions.values()]
                    exposures += [OpenExposure(o.stock_id, "", o.sector, o.qty * o.reference, o.qty * (o.reference - o.stop), pending=True)
                                  for o in orders if o.kind == "entry"]
                    held_ids = [e.stock_id for e in exposures if e.stock_id != s.stock_id]
                    elig = bool(d0.eligible.at[d, s.stock_id]) if s.stock_id in d0.eligible.columns else False
                    atr = d0.atr_pit.at[d, s.stock_id]
                    gap = d0.gap_p.at[d, s.stock_id]
                    sector = d0.sector.get(s.stock_id, cfg.universe_rules.unclassified_sector)
                    ctx = Context(equity=L.equity, cash=L.cash, exposures=exposures, eligible=elig, median_value_cr=self._med(s.stock_id, d),
                                  regime=regime, policy=pol or {"allow_new_entries": False}, entries_today=entries_today,
                                  atr=None if pd.isna(atr) else float(atr),
                                  correlations=self._corr(s.stock_id, held_ids, d, cfg.risk.correlation_lookback),
                                  adverse_gap_pct=None if pd.isna(gap) else float(gap))
                    dec = evaluate(Proposal(s.stock_id, d0.symbols.get(s.stock_id, ""), sector, s.strategy, d, s.entry, s.stop,
                                            score=sc, source=s.strategy), ctx, cfg)
                    if not dec.approved:
                        bump("rejected_" + dec.reason.split(":")[0])
                        continue
                    orders.append(Order(f"entry:{s.stock_id}:{d}", s.stock_id, "buy", "entry", dec.qty, d,
                                        cfg.execution.order_expiry_sessions, reference=s.entry, stop=s.stop, strategy=s.strategy,
                                        sector=sector, risk_budget=dec.risk_budget))
                    entries_today += 1
                    bump("approved")
            # 5. curve
            eq = L.equity
            peak = max(peak, eq)
            rec = L.reconcile()
            if not rec["ok"]:
                raise RuntimeError(f"backtest accounting mismatch on {d}: {rec}")
            curve.append({"session": d, "equity": eq, "cash": L.cash, "invested": L.invested, "drawdown_pct": (1 - eq / peak) * 100,
                          "open_positions": len(L.positions), "agg_risk": L.aggregate_initial_risk(), "charges": L.total_charges})
        open_trades = list(trades.values())  # still open at the end: marked to market in the equity curve
        return BacktestResult(pd.DataFrame(curve), closed + open_trades, counts, self.cfg.model_dump(mode="json"), days[0], days[-1])
