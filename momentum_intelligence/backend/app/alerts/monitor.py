"""Intraday monitor: watch set from the database, completed candles from Yahoo, rules, alert state machine.

State machine per (session, rule, symbol, account):
* not seen before + condition true            -> active (new)
* active + condition true                      -> active (last_seen, values, checks updated)
* active + condition false, non-sticky rule    -> resolved
* resolved + condition true again              -> active
* acknowledged (by the user)                   -> stays acknowledged; values still updated
* any alert of an earlier session              -> expired (``expire_before``)

Only completed candles are used: the provider drops the bar still forming (``calendar.intraday_bar_complete``).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..market_data.calendar import IST, now_ist
from ..models import Alert, PaperAccount, PaperPosition, TradingSession, utcnow
from . import rules as R

YAHOO = "Yahoo Finance 15-minute candles (unofficial, delayed up to ~15 min), completed candles only"


def watch_set(db: Session, cfg: StrategyConfig, configs: list[StrategyConfig], today: date | None = None) -> dict:
    """Positions (and entries filling at today's open) of active paper accounts, plus open / just-confirmed
    setups from the latest signal builds; reference prices from the last session before ``today``."""
    from ..strategies.pipeline import strategy_hash
    q = select(TradingSession.session_date).where(TradingSession.source == "nse_archive")
    if today is not None:
        q = q.where(TradingSession.session_date < today)
    last = db.scalar(q.order_by(TradingSession.session_date.desc()).limit(1))
    accts = {a.id: a.name for a in db.execute(select(PaperAccount).where(PaperAccount.last_processed == last)).scalars()}
    positions = [{"symbol": p.symbol, "stock_id": p.stock_id, "stop": p.stop, "account": accts[p.account_id]}
                 for p in db.execute(select(PaperPosition).where(PaperPosition.status == "open",
                                                                 PaperPosition.account_id.in_(list(accts) or [-1]))).scalars()]
    # entries queued for today's open: watched against their planned stop too
    from ..models import PaperOrder, TradeProposal
    for o, prop in db.execute(select(PaperOrder, TradeProposal).join(TradeProposal, TradeProposal.id == PaperOrder.proposal_id)
                              .where(PaperOrder.status == "pending", PaperOrder.kind == "entry",
                                     PaperOrder.account_id.in_(list(accts) or [-1]))).all():
        positions.append({"symbol": o.symbol, "stock_id": o.stock_id, "stop": prop.stop, "account": accts[o.account_id] + " (entry at open)"})
    hashes = sorted({strategy_hash(c) for c in configs})
    sigs = db.execute(text("""select distinct on (stock_id) stock_id, symbol, strategy, state, trigger from trade_signals
        where config_hash = any(:h) and trigger is not null and (is_open or confirmed = :d)
        order by stock_id, opened desc"""), {"h": hashes, "d": last}).all()
    watch = [{"symbol": s.symbol, "stock_id": s.stock_id, "strategy": s.strategy, "state": s.state, "trigger": s.trigger} for s in sigs]
    ids = list({x["stock_id"] for x in positions + watch})
    ref = db.execute(text("""select stock_id, (array_agg(close order by session_date desc))[1] as prev_close, avg(volume) as avg_vol
        from (select stock_id, session_date, close, volume, row_number() over (partition by stock_id order by session_date desc) rn
              from market_candles where stock_id = any(:ids) and not is_adjusted and source = 'nse_archive' and session_date <= :d) x
        where rn <= 20 group by stock_id"""), {"ids": ids, "d": last}).all() if ids else []
    # previous close: the official NSE index close, Yahoo only if NSE has none (ADR-018)
    idx = {r[0]: float(r[1]) for r in db.execute(text("""select distinct on (s.symbol) s.symbol, c.close
        from market_candles c join stocks s on s.id = c.stock_id
        where s.key in ('INDEX:NIFTY 50', 'INDEX:INDIA VIX') and c.source in ('nse_index', 'yahoo') and c.session_date = :d
        order by s.symbol, (c.source = 'nse_index') desc"""), {"d": last}).all()}
    return {"last_session": last, "positions": positions, "watch": watch,
            "prev_close": {r.stock_id: float(r.prev_close) for r in ref}, "avg_vol": {r.stock_id: float(r.avg_vol) for r in ref},
            "nifty_prev": idx.get("NIFTY 50"), "vix_prev": idx.get("INDIA VIX")}


def evaluate(ws: dict, bars: dict[str, pd.DataFrame], nifty: pd.DataFrame, vix: pd.DataFrame, cfg: StrategyConfig) -> list[R.Candidate]:
    a = cfg.alerts
    out = R.market_rules(nifty, ws["nifty_prev"], vix, ws["vix_prev"], a)
    nifty_ret = None if nifty.empty or not ws["nifty_prev"] else (float(nifty["close"].iloc[-1]) / ws["nifty_prev"] - 1) * 100
    seen_volume = set()
    for p in ws["positions"]:
        b = bars.get(p["symbol"], pd.DataFrame())
        out += R.position_rules(p["symbol"], b, stop=p["stop"], prev_close=ws["prev_close"].get(p["stock_id"]), nifty_ret_pct=nifty_ret,
                                cfg=a, account=p["account"], stock_id=p["stock_id"])
        if p["symbol"] not in seen_volume and (v := R.volume_rule(p["symbol"], b, avg_daily_volume=ws["avg_vol"].get(p["stock_id"]),
                                                                   cfg=a, stock_id=p["stock_id"])):
            out.append(v)
            seen_volume.add(p["symbol"])
    for w in ws["watch"]:
        b = bars.get(w["symbol"], pd.DataFrame())
        out += R.watch_rules(w["symbol"], b, trigger=w["trigger"], strategy=w["strategy"], state=w["state"],
                             prev_close=ws["prev_close"].get(w["stock_id"]), cfg=a, stock_id=w["stock_id"])
        if w["symbol"] not in seen_volume and (v := R.volume_rule(w["symbol"], b, avg_daily_volume=ws["avg_vol"].get(w["stock_id"]),
                                                                   cfg=a, stock_id=w["stock_id"])):
            out.append(v)
            seen_volume.add(w["symbol"])
    # a probable corporate action pauses the other rules for that symbol
    paused = {c.symbol for c in out if c.rule == "price_discontinuity"}
    return [c for c in out if c.rule == "price_discontinuity" or c.symbol not in paused]


def apply(db: Session, session: date, candidates: list[R.Candidate], *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    existing = {(a.rule, a.symbol, a.account): a for a in db.execute(select(Alert).where(Alert.session_date == session)).scalars()}
    seen, counts = set(), {"new": 0, "updated": 0, "reactivated": 0, "resolved": 0}
    for c in candidates:
        k = (c.rule, c.symbol, c.account)
        seen.add(k)
        a = existing.get(k)
        if a is None:
            db.execute(insert(Alert).values(session_date=session, rule=c.rule, symbol=c.symbol, stock_id=c.stock_id, account=c.account,
                                            severity=c.severity, state="active", message=c.message, values=c.values, source=YAHOO,
                                            bar_end=c.bar_end, first_seen=now, last_seen=now, checks=1).on_conflict_do_nothing())
            counts["new"] += 1
            continue
        a.message, a.values, a.bar_end, a.last_seen, a.checks = c.message, c.values, c.bar_end, now, a.checks + 1
        if a.state == "resolved":
            a.state, a.resolved_at = "active", None
            counts["reactivated"] += 1
        else:
            counts["updated"] += 1
    for k, a in existing.items():
        if k not in seen and a.state == "active" and a.rule not in R.STICKY:
            a.state, a.resolved_at = "resolved", now
            counts["resolved"] += 1
    db.flush()
    return counts


def expire_before(db: Session, session: date) -> int:
    r = db.execute(update(Alert).where(Alert.session_date < session, Alert.state.in_(("active", "resolved")))
                   .values(state="expired"))
    return r.rowcount


def run(db: Session, cfg: StrategyConfig, configs: list[StrategyConfig], *, provider=None, now: datetime | None = None) -> dict:
    """One monitoring pass. ``provider`` defaults to Yahoo; ``now`` (IST) decides which candles are complete."""
    from ..market_data.yahoo import YahooProvider, tidy
    now = (now or now_ist()).astimezone(IST)
    today = now.date()
    ws = watch_set(db, cfg, configs, today)
    # Positions and today's entries first, then setups closest to acting; never drop a position for a watchlist name.
    rank = {"confirmed": 0, "potential_setup": 1, "early_warning": 2}
    ordered = [p["symbol"] for p in ws["positions"]] + [w["symbol"] for w in sorted(ws["watch"], key=lambda w: (rank.get(w["state"], 3), w["symbol"]))]
    symbols = list(dict.fromkeys(ordered))
    cap = max(cfg.alerts.max_symbols, len({p["symbol"] for p in ws["positions"]}))
    dropped = len(symbols) - cap
    symbols = symbols[:cap]
    yp = provider or YahooProvider()
    frames = {}
    if symbols:
        raw = yp._download([f"{s}.NS" for s in symbols], interval="15m", start=today.isoformat(),
                           end=(pd.Timestamp(today) + pd.Timedelta(days=1)).date().isoformat())
        f = tidy(raw, {f"{s}.NS": s for s in symbols}, "15m", now)
        f = f[f["session_date"] == today]
        frames = {s: g.reset_index(drop=True) for s, g in f.groupby("symbol")}

    def index(ticker, name):
        raw = yp._download([ticker], interval="15m", start=today.isoformat(), end=(pd.Timestamp(today) + pd.Timedelta(days=1)).date().isoformat())
        f = tidy(raw, {ticker: name}, "15m", now)
        return f[f["session_date"] == today].reset_index(drop=True)

    nifty, vix = index("^NSEI", "NIFTY 50"), index("^INDIAVIX", "INDIA VIX")
    cands = evaluate(ws, frames, nifty, vix, cfg)
    expired = expire_before(db, today)
    counts = apply(db, today, cands)
    last_bar = max([b["ts"].iloc[-1] for b in frames.values() if not b.empty] + ([nifty["ts"].iloc[-1]] if not nifty.empty else []), default=None)
    return {"done": True, "session": today, "symbols_watched": len(symbols), "symbols_not_watched": max(dropped, 0),
            "symbols_with_bars": len(frames),
            "last_completed_bar_start": None if last_bar is None else last_bar.isoformat(), "candidates": len(cands),
            "expired_previous": expired, **counts}
