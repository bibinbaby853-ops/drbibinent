"""Pre-trade risk engine. Ten checks in a fixed order; every check is recorded.

A check either passes, adjusts the quantity downward ("capped"), or rejects the
trade. Quantity is never increased by a later check. The first rejection stops
evaluation (later checks are recorded as "skipped").

1. data_quality     eligible in that session's universe snapshot (liquidity + data quality gate)
2. regime           regime policy allows new entries, today's entry count, minimum score
3. sizing           qty = floor(equity x risk% x regime multiplier / effective risk per share)
                    effective risk = max(entry - stop, min_stop_atr x ATR) at the expected fill price
4. position_cap     position value <= max_position_exposure_pct of equity
5. sector_cap       sector value after the trade <= max_sector_exposure_pct of equity
6. position_count   open positions + pending entries < max_positions; no duplicate symbol
7. aggregate_risk   sum of initial risk (open + pending + new) <= max_initial_aggregate_risk_pct
8. cash_reserve     cash after the purchase and charges >= cash_reserve_pct of equity
9. correlation      60-session return correlation with every open position <= limit
10. gap_risk        if the p95 adverse overnight gap exceeds the stop distance, size to the gap
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..config import StrategyConfig
from ..portfolio.costs import charges, fill_price, slippage_bps

CHECKS = ("data_quality", "regime", "sizing", "position_cap", "sector_cap", "position_count", "aggregate_risk",
          "cash_reserve", "correlation", "gap_risk")


@dataclass
class Proposal:
    stock_id: int
    symbol: str
    sector: str
    strategy: str
    session_date: date        # decision session (after its close)
    entry: float              # planned entry reference price
    stop: float
    target: float | None = None
    score: float | None = None
    source: str = "manual"


@dataclass
class OpenExposure:
    stock_id: int
    symbol: str
    sector: str
    value: float              # market value (or planned value for a pending entry)
    initial_risk: float
    pending: bool = False


@dataclass
class Context:
    equity: float
    cash: float
    exposures: list[OpenExposure]
    eligible: bool
    median_value_cr: float | None
    regime: str
    policy: dict                          # RegimePolicy dict
    entries_today: int
    atr: float | None                     # ATR(14) in rupees at the decision session (point-in-time)
    correlations: dict[int, float] = field(default_factory=dict)  # stock_id of open position -> correlation
    adverse_gap_pct: float | None = None  # percentile adverse overnight gap, percent


@dataclass
class Decision:
    approved: bool
    qty: int
    checks: list[dict]
    expected_fill: float | None = None
    initial_risk: float | None = None
    estimated_charges: float | None = None
    reason: str | None = None


def evaluate(p: Proposal, ctx: Context, cfg: StrategyConfig) -> Decision:
    log: list[dict] = []
    state = {"qty": None}

    def rec(check, outcome, **details):
        log.append({"check": check, "outcome": outcome, "qty_after": state["qty"], "details": details})

    def reject(check, why, **details):
        rec(check, "reject", reason=why, **details)
        done = {c["check"] for c in log}
        for c in CHECKS:
            if c not in done:
                log.append({"check": c, "outcome": "skipped", "qty_after": None, "details": {}})
        return Decision(False, 0, log, reason=f"{check}: {why}")

    eq = ctx.equity
    # 1
    if not ctx.eligible:
        return reject("data_quality", "not eligible in the universe snapshot for this session")
    rec("data_quality", "pass", median_value_cr=ctx.median_value_cr)
    # 2
    pol = ctx.policy
    if not pol.get("allow_new_entries"):
        return reject("regime", f"regime {ctx.regime} does not allow new entries")
    if ctx.entries_today >= pol.get("max_new_entries_per_day", 0):
        return reject("regime", f"daily new-entry limit reached ({ctx.entries_today}/{pol.get('max_new_entries_per_day')})")
    if p.score is not None and p.score < pol.get("min_score", 0):
        return reject("regime", f"score {p.score} below the {ctx.regime} minimum {pol.get('min_score')}")
    rec("regime", "pass", regime=ctx.regime, policy=pol)
    # 3
    if p.stop <= 0 or p.stop >= p.entry:
        return reject("sizing", "stop must be below the entry price")
    stop_pct = (p.entry - p.stop) / p.entry * 100
    if stop_pct > cfg.risk.max_stop_pct:
        return reject("sizing", f"stop {stop_pct:.1f}% below entry exceeds {cfg.risk.max_stop_pct}%")
    bps = slippage_bps(ctx.median_value_cr, cfg.execution.slippage)
    exp_fill = fill_price("buy", p.entry, bps)
    raw_risk = exp_fill - p.stop
    eff_risk = max(raw_risk, (cfg.risk.min_stop_atr * ctx.atr) if ctx.atr else 0.0)
    budget = eq * cfg.risk_per_position_pct / 100 * pol.get("risk_multiplier", 1.0)
    qty = math.floor(budget / eff_risk) if eff_risk > 0 else 0
    state["qty"] = qty
    if qty < 1:
        return reject("sizing", "risk budget buys less than one share", budget=round(budget, 2), risk_per_share=round(eff_risk, 2))
    rec("sizing", "pass", budget=round(budget, 2), expected_fill=exp_fill, slippage_bps=bps, stop_distance=round(raw_risk, 2),
        effective_risk_per_share=round(eff_risk, 2), atr=ctx.atr, risk_multiplier=pol.get("risk_multiplier"))

    def cap(check, limit_qty, **details):
        if limit_qty < state["qty"]:
            state["qty"] = max(0, int(limit_qty))
            if state["qty"] < 1:
                return reject(check, "limit leaves no room for one share", **details)
            rec(check, "capped", **details)
        else:
            rec(check, "pass", **details)
        return None

    # 4
    max_val = eq * cfg.max_position_exposure_pct / 100
    if (r := cap("position_cap", math.floor(max_val / exp_fill), limit_value=round(max_val, 2))):
        return r
    # 5
    sector_val = sum(e.value for e in ctx.exposures if e.sector == p.sector)
    room = eq * cfg.max_sector_exposure_pct / 100 - sector_val
    if (r := cap("sector_cap", math.floor(max(room, 0) / exp_fill), sector=p.sector, sector_value=round(sector_val, 2),
                 room=round(room, 2))):
        return r
    # 6
    if any(e.stock_id == p.stock_id for e in ctx.exposures):
        return reject("position_count", f"already holding or pending in {p.symbol}")
    if len(ctx.exposures) >= cfg.max_positions:
        return reject("position_count", f"{len(ctx.exposures)} open/pending positions (max {cfg.max_positions})")
    rec("position_count", "pass", open_or_pending=len(ctx.exposures))
    # 7
    agg = sum(e.initial_risk for e in ctx.exposures)
    risk_room = eq * cfg.max_initial_aggregate_risk_pct / 100 - agg
    if (r := cap("aggregate_risk", math.floor(max(risk_room, 0) / eff_risk), aggregate_initial_risk=round(agg, 2),
                 room=round(risk_room, 2))):
        return r
    # 8
    reserve = eq * cfg.cash_reserve_pct / 100
    per_share_cash = exp_fill * (1 + (cfg.execution.costs.stt_pct + cfg.execution.costs.stamp_duty_buy_pct + 0.01) / 100)
    if (r := cap("cash_reserve", math.floor(max(ctx.cash - reserve, 0) / per_share_cash), cash=round(ctx.cash, 2),
                 reserve=round(reserve, 2))):
        return r
    # 9
    worst = max(ctx.correlations.items(), key=lambda kv: kv[1], default=(None, None))
    if worst[1] is not None and worst[1] > cfg.risk.max_correlation:
        sym = next((e.symbol for e in ctx.exposures if e.stock_id == worst[0]), worst[0])
        return reject("correlation", f"correlation {worst[1]:.2f} with {sym} exceeds {cfg.risk.max_correlation}")
    rec("correlation", "pass", max_correlation=None if worst[1] is None else round(worst[1], 3))
    # 10
    if ctx.adverse_gap_pct is not None:
        gap_risk = exp_fill * ctx.adverse_gap_pct / 100
        if gap_risk > eff_risk:
            if (r := cap("gap_risk", math.floor(budget / gap_risk), adverse_gap_pct=round(ctx.adverse_gap_pct, 2),
                         stop_distance_pct=round(raw_risk / exp_fill * 100, 2))):
                return r
        else:
            rec("gap_risk", "pass", adverse_gap_pct=round(ctx.adverse_gap_pct, 2))
    else:
        rec("gap_risk", "pass", note="no gap history")
    qty = state["qty"]
    est = charges("buy", qty, exp_fill, cfg.execution.costs).total
    return Decision(True, qty, log, expected_fill=exp_fill, initial_risk=round(qty * raw_risk, 2), estimated_charges=est)
