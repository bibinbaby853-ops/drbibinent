"""Paper portfolio service: proposals, the daily cycle, and persistence.

``run_day(account, d)`` processes one exchange session, in this order:

0. corporate actions with ex-date d for held stocks (quantity and levels adjusted)
1. pending orders fill at d's OPEN (entries are cancelled if the open is below the stop
   or more than ``max_chase_pct`` above the planned entry; they expire if the stock
   does not trade)
2. resting stops and partial targets act on d's high/low
3. after the close: mark to market, trailing stops, time stops (exit orders for the next open)
4. pending proposals dated d go through the risk engine; approved ones become entry
   orders for the next open
5. snapshot, drawdown alerts, reconciliation (a mismatch aborts the day)

A session is processed once (``last_processed``); every order has a unique idempotency
key, so a retry cannot fill anything twice. All of it is paper: there is no broker.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..indicators import core
from ..indicators.panel import asof_adjust
from ..models import (MarketRegime, PaperAccount, PaperOrder, PaperPosition, PortfolioSnapshot, RiskEvent, Stock,
                      TradeJournal, TradeProposal, TradingSession, UniverseSnapshot, MomentumScore)
from ..risk.engine import Context, OpenExposure, Proposal, evaluate
from ..universe.engine import config_hash
from .costs import charges, fill_price, slippage_bps
from .exits import after_close, intraday
from .ledger import Ledger, Position


class AlreadyProcessed(Exception):
    pass


# ---------------------------------------------------------------- accounts and ledger persistence

def create_account(db: Session, name: str, capital: float, source: str = "nse_archive", profile: str | None = None) -> PaperAccount:
    if profile:
        from ..backtesting.presets import profile_config
        profile_config(profile, StrategyConfig())  # validates the name
    acct = PaperAccount(name=name, initial_capital=capital, cash=capital, peak_equity=capital, source=source, mode="paper",
                        profile=profile)
    db.add(acct)
    db.flush()
    return acct


def account_by_name(db: Session, name: str) -> PaperAccount:
    acct = db.execute(select(PaperAccount).where(PaperAccount.name == name)).scalars().first()
    if acct is None:
        raise LookupError(f"no paper account named {name!r}; create one with `portfolio.cli init`")
    return acct


def load_ledger(db: Session, acct: PaperAccount) -> Ledger:
    L = Ledger(initial_capital=acct.initial_capital, cash=acct.cash, realised_pnl=acct.realised_pnl,
               total_charges=acct.total_charges, gross_bought=acct.gross_bought, gross_sold=acct.gross_sold,
               buy_charges_total=acct.buy_charges_total, sell_charges_total=acct.sell_charges_total)
    for p in db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id, PaperPosition.status == "open")).scalars():
        L.positions[str(p.id)] = Position(key=str(p.id), stock_id=p.stock_id, symbol=p.symbol, sector=p.sector, qty=p.qty,
                                          avg_price=p.avg_price, initial_stop=p.initial_stop, stop=p.stop,
                                          initial_risk=p.initial_risk, entry_date=p.entry_date, strategy=p.strategy,
                                          highest_close=p.highest_close, entry_qty=p.entry_qty, buy_charges=p.buy_charges,
                                          partial_done=p.partial_done, target=p.target, mark=p.mark)
    return L


def save_ledger(db: Session, acct: PaperAccount, L: Ledger, d: date) -> None:
    for f in ("cash", "realised_pnl", "total_charges", "gross_bought", "gross_sold", "buy_charges_total", "sell_charges_total"):
        setattr(acct, f, round(getattr(L, f), 6))
    for row in db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id, PaperPosition.status == "open")).scalars():
        p = L.positions.get(str(row.id))
        if p is None:
            row.status, row.qty, row.closed_date = "closed", 0, d
            continue
        for f in ("qty", "entry_qty", "avg_price", "initial_stop", "stop", "target", "initial_risk", "buy_charges", "highest_close",
                  "partial_done", "mark"):
            setattr(row, f, getattr(p, f))


def journal(db, acct, d, action, reason, *, position_id=None, order_id=None, data_as_of=None, **details):
    db.add(TradeJournal(account_id=acct.id, session_date=d, position_id=position_id, order_id=order_id, action=action,
                        reason=reason, data_as_of=data_as_of or d, details=details))


def reset_account(db, acct: PaperAccount, start_after: date, reason: str) -> dict:
    """Clear an account's activity and restart it after ``start_after`` with its original capital and profile.

    Used when the data an account traded on was corrected (ADR-018). The account keeps its id, name and profile;
    what was discarded is recorded in the journal entry that starts the new history."""
    from sqlalchemy import delete
    orders = db.execute(select(PaperOrder).where(PaperOrder.account_id == acct.id).order_by(PaperOrder.id)).scalars().all()
    discarded = {
        "orders": [{"symbol": o.symbol, "side": o.side, "kind": o.kind, "qty": o.qty, "status": o.status,
                    "created_session": str(o.created_session)} for o in orders],
        "positions": db.scalar(select(func.count()).select_from(PaperPosition).where(PaperPosition.account_id == acct.id)),
        "proposals": db.scalar(select(func.count()).select_from(TradeProposal).where(TradeProposal.account_id == acct.id)),
        "previous_last_processed": str(acct.last_processed),
    }
    for model in (TradeJournal, RiskEvent, PaperOrder, PaperPosition, TradeProposal, PortfolioSnapshot):
        db.execute(delete(model).where(model.account_id == acct.id))
    acct.cash = acct.peak_equity = acct.initial_capital
    acct.realised_pnl = acct.total_charges = acct.gross_bought = acct.gross_sold = 0.0
    acct.buy_charges_total = acct.sell_charges_total = 0.0
    acct.last_processed = start_after
    db.flush()
    journal(db, acct, start_after, "account_reset", reason, discarded=discarded)
    return discarded


# ---------------------------------------------------------------- market data helpers

_BARS_ON = text("""select stock_id, open, high, low, close from market_candles
    where session_date = :d and interval = '1d' and is_adjusted = false and source = :src and stock_id = any(cast(:ids as integer[]))""")
_SERIES = text("""select stock_id, session_date, open, high, low, close, volume from market_candles
    where stock_id = any(cast(:ids as integer[])) and session_date <= :d and session_date >= :start
      and interval = '1d' and is_adjusted = :adj and source = :src order by stock_id, session_date""")
_FACTORS = text("""select stock_id, ex_date, factor from corporate_actions
    where stock_id = any(cast(:ids as integer[])) and confidence in ('high', 'medium') and source like :src""")


def sessions(db: Session, source: str, upto: date | None = None) -> list[date]:
    q = select(TradingSession.session_date).where(TradingSession.source == source)
    if upto:
        q = q.where(TradingSession.session_date <= upto)
    return db.execute(q.order_by(TradingSession.session_date)).scalars().all()


def bars_on(db: Session, ids, d: date, source: str) -> dict[int, tuple]:
    if not ids:
        return {}
    return {r.stock_id: (r.open, r.high, r.low, r.close) for r in db.execute(_BARS_ON, {"d": d, "src": source, "ids": list(ids)})}


def _series(db, ids, d, start, source, adjusted):
    return pd.read_sql(_SERIES, db.connection(), params={"ids": list(ids), "d": d, "start": start, "adj": adjusted, "src": source})


def pit_atr(db: Session, ids, d: date, source: str, sess: list[date]) -> dict[int, float]:
    """ATR(14) in rupees at d on prices adjusted only for corporate actions known by d (ADR-014)."""
    if not ids:
        return {}
    start = sess[0]  # full history: Wilder smoothing seeded at the first bar, identical to the backtester and indicators
    raw = _series(db, ids, d, start, source, False)
    fac = pd.read_sql(_FACTORS, db.connection(), params={"ids": list(ids), "src": f"{source}:%"})
    out = {}
    for sid, g in raw.groupby("stock_id"):
        f = fac[fac.stock_id == sid]
        b = asof_adjust(g, list(zip(f.ex_date, f.factor)), d)
        a = core.atr(b.high, b.low, b.close, 14)
        if len(a) and not np.isnan(a.iat[-1]):
            out[int(sid)] = float(a.iat[-1])
    return out


def risk_inputs(db: Session, sid: int, held: list[int], d: date, source: str, sess: list[date], cfg: StrategyConfig) -> tuple[dict, float | None]:
    """Correlations of daily returns with held stocks, and the percentile adverse overnight gap (adjusted prices)."""
    lb = max(cfg.risk.correlation_lookback, cfg.risk.gap_lookback) + 1
    start = sess[max(0, len(sess) - lb)]
    s = _series(db, [sid, *held], d, start, source, True)
    if s.empty:
        return {}, None
    close = s.pivot(index="session_date", columns="stock_id", values="close")
    rets = close.pct_change(fill_method=None).tail(cfg.risk.correlation_lookback)
    corr = {}
    if sid in rets.columns:
        for h in held:
            if h in rets.columns:
                pair = rets[[sid, h]].dropna()
                if len(pair) >= 20:
                    v = pair[sid].corr(pair[h])
                    if not pd.isna(v):  # undefined for a constant series: no evidence either way
                        corr[h] = float(v)
    me = s[s.stock_id == sid].tail(cfg.risk.gap_lookback + 1)
    gap = None
    if len(me) > 30:
        g = ((me["close"].shift(1) - me["open"]) / me["close"].shift(1) * 100).clip(lower=0).dropna()
        gap = float(np.percentile(g, cfg.risk.gap_percentile))
    return corr, gap


# ---------------------------------------------------------------- proposals

def propose(db: Session, acct: PaperAccount, symbol: str, entry: float, stop: float, *, target: float | None = None,
            strategy: str = "manual", session_date: date | None = None, note: str | None = None, cfg: StrategyConfig) -> TradeProposal:
    """Queue an entry proposal for the risk engine. Same account/strategy/stock/session twice returns the first one."""
    stock = db.execute(select(Stock).where(Stock.symbol == symbol.upper(), Stock.kind == "equity",
                                           Stock.demo.is_(acct.source == "demo"))).scalars().first()
    if stock is None:
        raise LookupError(f"unknown equity symbol {symbol}")
    # default: the session whose close the account last processed (the data the proposal is based on)
    d = session_date or acct.last_processed or sessions(db, acct.source)[-1]
    if acct.last_processed is not None and d < acct.last_processed:
        raise ValueError(f"session {d} is before the last processed session {acct.last_processed}; propose for "
                         f"{acct.last_processed} or later")
    key = f"acct{acct.id}:{strategy}:{stock.id}:{d.isoformat()}"
    existing = db.execute(select(TradeProposal).where(TradeProposal.idempotency_key == key)).scalars().first()
    if existing:
        return existing
    from ..scoring.pipeline import scoring_hash
    score = db.scalar(select(MomentumScore.total).where(MomentumScore.stock_id == stock.id, MomentumScore.session_date == d,
                                                        MomentumScore.config_hash == scoring_hash(cfg)))
    p = TradeProposal(account_id=acct.id, stock_id=stock.id, symbol=stock.symbol, strategy=strategy, source=strategy,
                      session_date=d, entry=entry, stop=stop, target=target, score=score, note=note, idempotency_key=key)
    db.add(p)
    db.flush()
    if acct.last_processed is not None and d == acct.last_processed:
        # its session's run is over: risk-check it now so it can be ordered for the next open
        evaluate_proposals(db, acct, d, load_ledger(db, acct), cfg, sessions(db, acct.source, upto=d))
    return p


def evaluate_proposals(db: Session, acct: PaperAccount, d: date, L: Ledger, cfg: StrategyConfig, sess: list[date]) -> list[dict]:
    """Risk-check every pending proposal dated d (best score first); approved ones become entry orders."""
    pend = db.execute(select(TradeProposal).where(TradeProposal.account_id == acct.id, TradeProposal.session_date == d,
                                                  TradeProposal.status == "pending")).scalars().all()
    pend.sort(key=lambda p: (-(p.score or 0), p.id))
    out = []
    if not pend:
        return out
    h = config_hash(cfg)
    reg = db.execute(select(MarketRegime).where(MarketRegime.session_date == d).order_by(MarketRegime.version.desc())).scalars().first()
    for p in pend:
        uni = db.execute(select(UniverseSnapshot).where(UniverseSnapshot.session_date == d, UniverseSnapshot.stock_id == p.stock_id,
                                                        UniverseSnapshot.config_hash == h)).scalars().first()
        orders = db.execute(select(PaperOrder).where(PaperOrder.account_id == acct.id, PaperOrder.status == "pending",
                                                     PaperOrder.kind == "entry")).scalars().all()
        exposures = [OpenExposure(x.stock_id, x.symbol, x.sector, x.market_value, x.initial_risk * x.qty / x.entry_qty)
                     for x in L.positions.values()]
        for o in orders:
            prop = db.get(TradeProposal, o.proposal_id)
            sector = db.scalar(select(UniverseSnapshot.sector).where(UniverseSnapshot.stock_id == o.stock_id,
                                                                     UniverseSnapshot.session_date == prop.session_date).limit(1))
            exposures.append(OpenExposure(o.stock_id, o.symbol, sector or cfg.universe_rules.unclassified_sector,
                                          o.qty * o.reference_price, o.qty * (o.reference_price - prop.stop), pending=True))
        entries_today = db.scalar(select(func.count()).select_from(PaperOrder).where(
            PaperOrder.account_id == acct.id, PaperOrder.kind == "entry", PaperOrder.created_session == d)) or 0
        held = [e.stock_id for e in exposures if e.stock_id != p.stock_id]
        corr, gap = risk_inputs(db, p.stock_id, held, d, acct.source, sess, cfg)
        atr = pit_atr(db, [p.stock_id], d, acct.source, sess).get(p.stock_id)
        sector = uni.sector if uni else cfg.universe_rules.unclassified_sector
        ctx = Context(equity=L.equity, cash=L.cash, exposures=exposures, eligible=bool(uni and uni.eligible),
                      median_value_cr=uni.median_value_cr if uni else None, regime=reg.regime if reg else "unknown",
                      policy=(cfg.regime.policies[reg.regime].model_dump() if reg and reg.regime in cfg.regime.policies
                              else {"allow_new_entries": False}), entries_today=entries_today, atr=atr,
                      correlations=corr, adverse_gap_pct=gap)
        prop = Proposal(stock_id=p.stock_id, symbol=p.symbol, sector=sector, strategy=p.strategy, session_date=d, entry=p.entry,
                        stop=p.stop, target=p.target, score=p.score, source=p.source)
        dec = evaluate(prop, ctx, cfg)
        for c in dec.checks:
            db.add(RiskEvent(account_id=acct.id, session_date=d, proposal_id=p.id, check=c["check"], outcome=c["outcome"],
                             details={**c["details"], "qty_after": c["qty_after"]}))
        p.decision = {"approved": dec.approved, "qty": dec.qty, "reason": dec.reason, "expected_fill": dec.expected_fill,
                      "risk_budget": dec.risk_budget,
                      "initial_risk": dec.initial_risk, "estimated_charges": dec.estimated_charges, "regime": ctx.regime,
                      "equity": round(ctx.equity, 2)}
        if not dec.approved:
            p.status = "rejected"
            journal(db, acct, d, "proposal_rejected", dec.reason, symbol=p.symbol, proposal_id=p.id)
        else:
            p.status = "ordered"
            o = PaperOrder(account_id=acct.id, idempotency_key=f"acct{acct.id}:entry:proposal{p.id}", proposal_id=p.id,
                           stock_id=p.stock_id, symbol=p.symbol, side="buy", kind="entry", qty=dec.qty, reference_price=p.entry,
                           created_session=d, expires_after=cfg.execution.order_expiry_sessions,
                           reason=f"risk-approved {p.strategy} entry: {dec.qty} shares, initial risk Rs {dec.initial_risk}")
            db.add(o)
            db.flush()
            journal(db, acct, d, "entry_order", o.reason, order_id=o.id, proposal_id=p.id, symbol=p.symbol)
        out.append({"proposal_id": p.id, "symbol": p.symbol, **p.decision})
    return out


def manual_exit(db: Session, acct: PaperAccount, position_id: int, reason: str, d: date | None = None) -> PaperOrder:
    pos = db.get(PaperPosition, position_id)
    if pos is None or pos.account_id != acct.id or pos.status != "open":
        raise LookupError(f"no open position {position_id}")
    d = d or acct.last_processed or sessions(db, acct.source)[-1]
    key = f"acct{acct.id}:exit:pos{pos.id}:manual_exit:{d.isoformat()}"
    o = db.execute(select(PaperOrder).where(PaperOrder.idempotency_key == key)).scalars().first()
    if o:
        return o
    o = PaperOrder(account_id=acct.id, idempotency_key=key, position_id=pos.id, stock_id=pos.stock_id, symbol=pos.symbol,
                   side="sell", kind="manual_exit", qty=pos.qty, created_session=d, expires_after=5, reason=f"manual exit: {reason}")
    db.add(o)
    db.flush()
    journal(db, acct, d, "exit_order", o.reason, position_id=pos.id, order_id=o.id)
    return o


# ---------------------------------------------------------------- the daily cycle

def _sell(db, acct, L, d, o_or_kind, p: Position, qty, ref, bps, cfg, reason, order: PaperOrder | None = None):
    px = fill_price("sell", ref, bps)
    ch = charges("sell", qty, px, cfg.execution.costs)
    pnl = L.sell(p.key, qty, px, ch.total)
    row = db.get(PaperPosition, int(p.key))
    row.realised_pnl = round((row.realised_pnl or 0) + pnl, 2)
    if order is None:
        order = PaperOrder(account_id=acct.id, idempotency_key=f"acct{acct.id}:exit:pos{p.key}:{o_or_kind}:{d.isoformat()}",
                           position_id=int(p.key), stock_id=p.stock_id, symbol=p.symbol, side="sell", kind=o_or_kind, qty=qty,
                           reference_price=ref, created_session=d, reason=reason)
        db.add(order)
    order.status, order.filled_session, order.fill_price, order.slippage_bps = "filled", d, px, bps
    order.charges, order.qty = ch.to_dict(), qty
    db.flush()
    journal(db, acct, d, f"sell_{order.kind}", reason, position_id=int(p.key), order_id=order.id, qty=qty, price=px,
            pnl=round(pnl, 2), charges=ch.total)
    return pnl


def propose_from_signals(db: Session, acct: PaperAccount, d: date, cfg: StrategyConfig) -> list[int]:
    """Turn the strategy signals confirmed on ``d`` into proposals (same interface as manual proposals)."""
    from ..strategies.pipeline import confirmed_on
    ids = []
    for s in confirmed_on(db, d, cfg):
        if s.entry is None or s.stop is None or s.stop >= s.entry:
            continue
        p = propose(db, acct, s.symbol, s.entry, s.stop, strategy=s.strategy, session_date=d,
                    note=f"signal {s.key}: {s.strategy} confirmed {d}", cfg=cfg)
        ids.append(p.id)
    return ids


def run_day(db: Session, acct: PaperAccount, d: date, cfg: StrategyConfig, *, auto_signals: bool = False) -> dict:
    if acct.mode != "paper":
        raise RuntimeError("only paper accounts exist")
    if acct.last_processed is not None and d <= acct.last_processed:
        raise AlreadyProcessed(f"{acct.name}: {d} already processed (last {acct.last_processed})")
    sess = sessions(db, acct.source, upto=d)
    if not sess or sess[-1] != d:
        raise ValueError(f"{d} is not an ingested {acct.source} session")
    L = load_ledger(db, acct)
    h = config_hash(cfg)
    summary = {"session": d.isoformat(), "fills": [], "exits": [], "cancelled": [], "proposals": []}
    pending = db.execute(select(PaperOrder).where(PaperOrder.account_id == acct.id, PaperOrder.status == "pending",
                                                  PaperOrder.created_session < d)).scalars().all()
    ids = {p.stock_id for p in L.positions.values()} | {o.stock_id for o in pending}
    bars = bars_on(db, ids, d, acct.source)
    med = dict(db.execute(select(UniverseSnapshot.stock_id, UniverseSnapshot.median_value_cr).where(
        UniverseSnapshot.session_date == d, UniverseSnapshot.config_hash == h, UniverseSnapshot.stock_id.in_(ids or [-1]))).all())

    # 0. corporate actions effective today
    if L.positions:
        acts = db.execute(text("""select stock_id, factor from corporate_actions where ex_date = :d and confidence in ('high','medium')
                                  and source like :src and stock_id = any(cast(:ids as integer[]))"""),
                          {"d": d, "src": f"{acct.source}:%", "ids": [p.stock_id for p in L.positions.values()]}).all()
        for sid, factor in acts:
            for key in L.adjust_for_corporate_action(sid, factor):
                journal(db, acct, d, "corporate_action", f"price factor {factor:.4f} applied (qty / factor, levels x factor)",
                        position_id=int(key), factor=factor)

    # 1. orders at the open
    for o in pending:
        bar = bars.get(o.stock_id)
        if bar is None:
            waited = len([s for s in sess if s > o.created_session])
            if waited >= o.expires_after:
                o.status, o.reason = "expired", (o.reason or "") + f" | expired: no trade for {waited} session(s)"
                summary["cancelled"].append({"symbol": o.symbol, "why": "expired"})
                journal(db, acct, d, "order_expired", "stock did not trade", order_id=o.id)
            continue
        op = bar[0]
        bps = slippage_bps(med.get(o.stock_id), cfg.execution.slippage)
        if o.kind == "entry":
            prop = db.get(TradeProposal, o.proposal_id)
            if op <= prop.stop:
                o.status, o.reason = "cancelled", f"open {op} at or below stop {prop.stop}: entry cancelled"
            elif op > o.reference_price * (1 + cfg.execution.max_chase_pct / 100):
                o.status, o.reason = "cancelled", f"open {op} more than {cfg.execution.max_chase_pct}% above planned entry {o.reference_price}"
            if o.status == "cancelled":
                summary["cancelled"].append({"symbol": o.symbol, "why": o.reason})
                journal(db, acct, d, "entry_cancelled", o.reason, order_id=o.id)
                continue
            px = fill_price("buy", op, bps)
            # Re-apply the limits at the actual fill: a higher open than planned means more risk per share.
            reserve = L.equity * cfg.cash_reserve_pct / 100
            affordable = math.floor(max(L.cash - reserve, 0) / (px * 1.0012))
            per_share = px - prop.stop
            budget = (prop.decision or {}).get("risk_budget") or float("inf")
            agg_room = L.equity * cfg.max_initial_aggregate_risk_pct / 100 - L.aggregate_initial_risk()
            limits = {"order": o.qty, "cash_reserve": affordable, "position_risk": math.floor(budget / per_share),
                      "aggregate_risk": math.floor(max(agg_room, 0) / per_share)}
            qty = min(limits.values())
            binding = min(limits, key=limits.get)
            if qty < 1:
                o.status, o.reason = "cancelled", f"no room at fill price {px} ({binding} limit)"
                journal(db, acct, d, "entry_cancelled", o.reason, order_id=o.id)
                continue
            ch = charges("buy", qty, px, cfg.execution.costs)
            sector = db.scalar(select(UniverseSnapshot.sector).where(UniverseSnapshot.stock_id == o.stock_id,
                                                                     UniverseSnapshot.session_date == prop.session_date).limit(1))
            row = PaperPosition(account_id=acct.id, stock_id=o.stock_id, symbol=o.symbol, sector=sector or "Unclassified",
                                strategy=prop.strategy, proposal_id=prop.id, qty=qty, entry_qty=qty, avg_price=px,
                                initial_stop=prop.stop, stop=prop.stop, target=prop.target, initial_risk=round(qty * (px - prop.stop), 2),
                                buy_charges=ch.total, highest_close=px, entry_date=d, mark=px)
            db.add(row)
            db.flush()
            L.buy(Position(key=str(row.id), stock_id=o.stock_id, symbol=o.symbol, sector=row.sector, qty=qty, avg_price=px,
                           initial_stop=prop.stop, stop=prop.stop, initial_risk=row.initial_risk, entry_date=d, strategy=prop.strategy,
                           highest_close=px, entry_qty=qty, buy_charges=0.0, target=prop.target), px, ch.total)
            o.status, o.filled_session, o.fill_price, o.slippage_bps, o.charges, o.position_id = "filled", d, px, bps, ch.to_dict(), row.id
            if qty < o.qty:
                o.reason = (o.reason or "") + f" | filled {qty} of {o.qty} ({binding} limit at fill price {px})"
                o.qty = qty
            journal(db, acct, d, "buy_filled", f"entry filled at open {op} + {bps} bps", position_id=row.id, order_id=o.id,
                    qty=qty, price=px, charges=ch.total)
            summary["fills"].append({"symbol": o.symbol, "qty": qty, "price": px})
        else:  # exit orders placed after a previous close (time stop, manual)
            p = L.positions.get(str(o.position_id))
            if p is None:
                o.status, o.reason = "cancelled", "position already closed"
                continue
            pnl = _sell(db, acct, L, d, o.kind, p, min(o.qty, p.qty), op, bps, cfg, o.reason or o.kind, order=o)
            summary["exits"].append({"symbol": o.symbol, "kind": o.kind, "pnl": round(pnl, 2)})

    # 2. resting stops / targets during the session
    bars = bars_on(db, {p.stock_id for p in L.positions.values()}, d, acct.source)
    for p in list(L.positions.values()):
        bar = bars.get(p.stock_id)
        if bar is None:
            continue
        bps = slippage_bps(med.get(p.stock_id), cfg.execution.slippage)
        for ex in intraday(p, bar[0], bar[1], bar[2], cfg.exits):
            pnl = _sell(db, acct, L, d, ex.kind, p, ex.qty, ex.reference, bps, cfg, ex.reason)
            summary["exits"].append({"symbol": p.symbol, "kind": ex.kind, "qty": ex.qty, "pnl": round(pnl, 2)})
            if ex.kind == "partial_target" and p.key in L.positions:
                p.partial_done = True

    # 3. after the close
    L.mark({sid: b[3] for sid, b in bars.items()})
    atrs = pit_atr(db, [p.stock_id for p in L.positions.values()], d, acct.source, sess)
    for p in list(L.positions.values()):
        if p.stock_id not in bars:
            continue
        held = len([s for s in sess if s >= p.entry_date])
        r = after_close(p, bars[p.stock_id][3], atrs.get(p.stock_id), held, cfg.exits)
        if r["new_stop"] is not None:
            old, p.stop = p.stop, r["new_stop"]
            journal(db, acct, d, "stop_raised", "; ".join(r["reasons"]), position_id=int(p.key), old=old, new=p.stop)
        if r["time_exit"]:
            key = f"acct{acct.id}:exit:pos{p.key}:time_stop:{d.isoformat()}"
            already = db.execute(select(PaperOrder.id).where(PaperOrder.position_id == int(p.key), PaperOrder.side == "sell",
                                                             PaperOrder.status == "pending")).first()
            if already is None and db.execute(select(PaperOrder.id).where(PaperOrder.idempotency_key == key)).first() is None:
                o = PaperOrder(account_id=acct.id, idempotency_key=key, position_id=int(p.key), stock_id=p.stock_id, symbol=p.symbol,
                               side="sell", kind="time_stop", qty=p.qty, created_session=d, expires_after=5, reason="; ".join(r["reasons"]))
                db.add(o)
                db.flush()
                journal(db, acct, d, "exit_order", o.reason, position_id=int(p.key), order_id=o.id)

    # 4. new entries (anything still pending from an earlier session is expired, never executed on stale data)
    if auto_signals:
        summary["signal_proposals"] = propose_from_signals(db, acct, d, cfg)
    for stale in db.execute(select(TradeProposal).where(TradeProposal.account_id == acct.id, TradeProposal.status == "pending",
                                                        TradeProposal.session_date < d)).scalars():
        stale.status = "expired"
        stale.decision = {"approved": False, "reason": f"not evaluated on its session {stale.session_date}; expired on {d}"}
        journal(db, acct, d, "proposal_expired", stale.decision["reason"], symbol=stale.symbol, proposal_id=stale.id)
    summary["proposals"] = evaluate_proposals(db, acct, d, L, cfg, sess)

    # 5. snapshot, alerts, reconciliation
    rec = L.reconcile()
    if not rec["ok"]:
        raise RuntimeError(f"accounting does not reconcile on {d}: {rec}")
    eq = L.equity
    prev_dd = db.scalar(select(PortfolioSnapshot.drawdown_pct).where(PortfolioSnapshot.account_id == acct.id)
                        .order_by(PortfolioSnapshot.session_date.desc()).limit(1)) or 0.0
    acct.peak_equity = max(acct.peak_equity, eq)
    dd = (1 - eq / acct.peak_equity) * 100
    for level in sorted(cfg.drawdown_alerts_pct):
        if dd >= level > prev_dd:
            db.add(RiskEvent(account_id=acct.id, session_date=d, check="drawdown_alert", outcome="alert",
                             details={"level_pct": level, "drawdown_pct": round(dd, 2), "equity": round(eq, 2), "peak": round(acct.peak_equity, 2)}))
            journal(db, acct, d, "drawdown_alert", f"drawdown {dd:.2f}% crossed {level}%")
    save_ledger(db, acct, L, d)
    db.add(PortfolioSnapshot(account_id=acct.id, session_date=d, equity=round(eq, 2), cash=round(L.cash, 2), invested=round(L.invested, 2),
                             unrealised_pnl=round(L.unrealised_pnl, 2), realised_pnl=round(L.realised_pnl, 2),
                             charges=round(L.total_charges, 2), peak_equity=round(acct.peak_equity, 2), drawdown_pct=round(dd, 3),
                             open_positions=len(L.positions), aggregate_initial_risk=round(L.aggregate_initial_risk(), 2),
                             exposure={k: round(v, 2) for k, v in L.sector_exposure().items()}, reconciliation=rec))
    acct.last_processed = d
    db.flush()
    summary.update(equity=round(eq, 2), cash=round(L.cash, 2), drawdown_pct=round(dd, 3), open_positions=len(L.positions))
    return summary


def evaluate_now(db: Session, acct: PaperAccount, cfg: StrategyConfig) -> list[dict]:
    """Evaluate proposals dated the last processed session (for proposals added after that day's run)."""
    if acct.last_processed is None:
        return []
    L = load_ledger(db, acct)
    return evaluate_proposals(db, acct, acct.last_processed, L, cfg, sessions(db, acct.source, upto=acct.last_processed))


def preview(db: Session, acct: PaperAccount, proposal: TradeProposal, cfg: StrategyConfig) -> dict:
    """Run the risk engine on a proposal without storing anything (savepoint rolled back)."""
    nested = db.begin_nested()
    try:
        L = load_ledger(db, acct)
        out = evaluate_proposals(db, acct, proposal.session_date, L, cfg,
                                 sessions(db, acct.source, upto=proposal.session_date))
        risk = [{"check": e.check, "outcome": e.outcome, "details": e.details} for e in db.execute(
            select(RiskEvent).where(RiskEvent.proposal_id == proposal.id).order_by(RiskEvent.id)).scalars()]
        return {"decision": next((x for x in out if x["proposal_id"] == proposal.id), None), "checks": risk}
    finally:
        nested.rollback()



def account_config(acct: PaperAccount, base: StrategyConfig) -> StrategyConfig:
    from ..backtesting.presets import profile_config
    return profile_config(acct.profile, base)
