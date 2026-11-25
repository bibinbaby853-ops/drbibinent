"""Read-only endpoints shaped for the dashboard (Phase 8)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import default_strategy_config
from ..indicators import core
from ..models import BacktestRun, MarketRegime, PaperAccount, PortfolioSnapshot, RiskEvent, Stock, TradingSession
from ..scoring.pipeline import scoring_hash
from ..strategies.pipeline import strategy_hash
from ..universe.engine import config_hash
from .deps import Authed, db_session

router = APIRouter(prefix="/api/v1", dependencies=[Authed], tags=["dashboard"])
SOURCE = "NSE bhavcopy (exchange official, end of day)"
EDGE_NOTE = ("Research and paper trading only. Backtests found no out-of-sample edge for these rules "
             "(see Backtesting Lab); forward paper accounts are the ongoing test.")


def _latest(db: Session) -> date:
    d = db.scalar(select(func.max(MarketRegime.session_date)))
    if d is None:
        raise HTTPException(404, "nothing computed yet; run `python -m app.scheduler.eod`")
    return d


def _trend(points: float | None) -> str | None:
    if points is None:
        return None
    return "strong uptrend" if points >= 20 else "uptrend" if points >= 13 else "mixed" if points >= 7 else "downtrend"


_SCAN = text("""
with rv as (
  select stock_id, volume::float / nullif(avg(volume) over (partition by stock_id order by session_date
         rows between 20 preceding and 1 preceding), 0) as rvol, session_date
  from market_candles where not is_adjusted and source = 'nse_archive' and interval = '1d'
    and session_date between cast(:d as date) - 45 and :d
)
select u.stock_id, u.symbol, u.sector, u.series, u.close, u.median_value_cr, u.eligible, u.reasons as exclusions,
       m.total as score, m.band, m.coverage, m.groups, m.breakdown,
       r.rs_rank, r.rs_rank_change, r.classes as rs_classes,
       rv.rvol,
       s.strategy, s.state, s.entry, s.stop, s.invalidation, s.reasons as setup_conditions, s.confirmed
from stock_universe_snapshots u
left join momentum_scores m on m.stock_id = u.stock_id and m.session_date = u.session_date and m.config_hash = :sh
left join relative_strength r on r.stock_id = u.stock_id and r.session_date = u.session_date
left join rv on rv.stock_id = u.stock_id and rv.session_date = u.session_date
left join lateral (
  select * from trade_signals t where t.stock_id = u.stock_id and t.config_hash = :gh and t.opened <= u.session_date
    and (t.confirmed = u.session_date or t.closed is null or t.closed >= u.session_date)
  order by t.opened desc limit 1
) s on true
where u.session_date = :d and u.config_hash = :uh and (:all or u.eligible)
""")


SHORT = {"t_above_sma50": "above 50-day average", "t_above_sma200": "above 200-day average", "t_sma50_above_sma200": "50-day above 200-day",
         "t_sma200_rising": "200-day average rising", "t_sma50_rising": "50-day average rising", "t_adx_trend": "trending (ADX >= 20)",
         "t_weekly_trend": "weekly uptrend", "rs_vs_nifty_63": "beat Nifty 50 over 3 months", "rs_vs_sector_63": "beat its sector over 3 months",
         "rs_not_falling": "RS rank not falling", "v_expansion": "volume expanding", "v_obv_rising": "OBV rising",
         "v_updown": "more volume on up days", "v_demand_today": "up today on heavy volume", "p_off_low": "30%+ above 52-week low",
         "p_contraction": "volatility contracting", "p_at_55d_high": "at a 55-day high", "p_not_extended": "not extended",
         "s_trend": "sector in an uptrend", "s_breadth": "sector breadth above 50%", "m_macd": "MACD positive and rising",
         "m_roc_63": "positive 3-month momentum", "r_atr_band": "volatility suits a 2-8 week stop", "r_near_support": "near the 50-day average"}


def reason_text(b: dict) -> str:
    v = (b.get("value") or [None])[0]
    if b["id"] == "rs_rank":
        return f"RS rank {v:.0f}" if v is not None else "RS rank"
    if b["id"] == "p_near_high":
        return f"{abs(v):.0f}% below 52-week high" if v is not None else "near 52-week high"
    if b["id"] == "s_rank":
        return "sector ranked in the top third"
    if b["id"] == "m_rsi_zone":
        return f"RSI {v:.0f}" if v is not None else "RSI in range"
    return SHORT.get(b["id"], b["rule"])


def card(r) -> dict:
    groups = r.groups or {}
    reasons = []
    if r.breakdown:
        reasons = [reason_text(b) for b in sorted(r.breakdown, key=lambda b: -b["points"]) if b["status"] == "pass"][:6]
    risk_pct = None if not (r.entry and r.stop) else round((r.entry - r.stop) / r.entry * 100, 2)
    return {"symbol": r.symbol, "sector": r.sector, "series": r.series, "price": r.close, "data": {"source": SOURCE},
            "median_value_cr": r.median_value_cr, "eligible": r.eligible, "exclusions": r.exclusions,
            "score": r.score, "band": r.band, "coverage": r.coverage, "groups": groups,
            "rs_rank": r.rs_rank, "rs_change_20": r.rs_rank_change, "rs_classes": r.rs_classes or [],
            "rvol": None if r.rvol is None or np.isnan(r.rvol) else round(float(r.rvol), 2),
            "trend": _trend(groups.get("trend")),
            "setup": None if r.strategy is None else {"strategy": r.strategy, "state": r.state, "entry": r.entry, "stop": r.stop,
                                                       "invalidation": r.invalidation, "risk_pct": risk_pct,
                                                       "target_2r": None if risk_pct is None else round(r.entry + 2 * (r.entry - r.stop), 2),
                                                       "confirmed": r.confirmed, "conditions": r.setup_conditions},
            "reasons": reasons}


@router.get("/scanner")
def scanner(session_date: date | None = Query(None, alias="date"), include_ineligible: bool = False, min_score: float = 0,
            sector: str | None = None, strategy: str | None = None, state: str | None = None, sort: str = "score",
            limit: int = Query(300, le=5000), db: Session = Depends(db_session)) -> dict:
    cfg = default_strategy_config()
    d = session_date or _latest(db)
    rows = db.execute(_SCAN, {"d": d, "sh": scoring_hash(cfg), "gh": strategy_hash(cfg), "uh": config_hash(cfg),
                              "all": include_ineligible}).all()
    cards = [card(r) for r in rows]
    cards = [c for c in cards if (c["score"] or 0) >= min_score and (not sector or c["sector"] == sector)
             and (not strategy or (c["setup"] and c["setup"]["strategy"] == strategy))
             and (not state or (c["setup"] and c["setup"]["state"] == state))]
    key = {"score": lambda c: -(c["score"] or -1), "rs": lambda c: -(c["rs_rank"] or -1), "rvol": lambda c: -(c["rvol"] or -1),
           "symbol": lambda c: c["symbol"]}.get(sort, lambda c: -(c["score"] or -1))
    cards.sort(key=key)
    return {"session_date": d, "data_as_of": f"{d} 15:30 IST close", "source": SOURCE, "count": len(cards), "note": EDGE_NOTE,
            "sectors": sorted({c["sector"] for c in cards}), "stocks": cards[:limit]}


@router.get("/stocks/{symbol}/candles")
def candles(symbol: str, days: int = Query(250, ge=20, le=2000), db: Session = Depends(db_session)) -> dict:
    stock = db.execute(select(Stock).where(Stock.symbol == symbol.upper(), Stock.kind.in_(("equity", "index")))).scalars().first()
    if stock is None:
        raise HTTPException(404, f"unknown symbol {symbol}")
    src = "demo" if stock.demo else ("yahoo" if stock.kind == "index" else "nse_archive")
    df = pd.read_sql(text("""select session_date, open, high, low, close, volume from market_candles where stock_id = :s
        and source = :src and interval = '1d' and is_adjusted = :adj order by session_date desc limit :n"""), db.connection(),
        params={"s": stock.id, "src": src, "adj": stock.kind != "index", "n": days + 200}).sort_values("session_date")
    if df.empty:
        raise HTTPException(404, f"no candles for {symbol}")
    e20, e50 = core.ema(df.close, 20), core.ema(df.close, 50)
    df = df.assign(ema20=e20, ema50=e50).tail(days)
    rows = [{"time": r.session_date.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": int(r.volume or 0),
             "ema20": None if np.isnan(r.ema20) else round(r.ema20, 2), "ema50": None if np.isnan(r.ema50) else round(r.ema50, 2)}
            for r in df.itertuples()]
    sigs = db.execute(text("""select confirmed, strategy, entry, stop from trade_signals where stock_id = :s and config_hash = :h
        and confirmed is not null and confirmed >= :f order by confirmed"""), {"s": stock.id, "h": strategy_hash(default_strategy_config()),
                                                                             "f": rows[0]["time"]}).all()
    return {"symbol": stock.symbol, "name": stock.name, "sector": stock.sector, "kind": stock.kind,
            "prices": "adjusted for splits and bonuses" if stock.kind != "index" else "index level (Yahoo, unofficial)",
            "source": ("DEMO (synthetic)" if stock.demo else SOURCE if stock.kind != "index" else "Yahoo Finance (unofficial)"),
            "demo": stock.demo, "candles": rows,
            "signals": [{"time": s.confirmed.isoformat(), "strategy": s.strategy, "entry": s.entry, "stop": s.stop} for s in sigs]}


@router.get("/market/overview")
def market_overview(days: int = Query(250, ge=20, le=2000), db: Session = Depends(db_session)) -> dict:
    rows = db.execute(select(MarketRegime).order_by(MarketRegime.session_date.desc()).limit(days)).scalars().all()[::-1]
    if not rows:
        raise HTTPException(404, "no regime computed yet")
    nifty = db.execute(text("""select c.session_date, c.close from market_candles c join stocks s on s.id = c.stock_id
        where s.key = 'INDEX:NIFTY 50' and c.source = 'yahoo' and c.session_date >= :f order by 1"""), {"f": rows[0].session_date}).all()
    last = rows[-1]
    return {"latest": {"session_date": last.session_date, "regime": last.regime, "raw_regime": last.components.get("raw_regime"),
                       "score": last.score, "policy": last.policy, "components": last.components.get("components"),
                       "inputs": last.components.get("inputs")},
            "history": [{"time": r.session_date.isoformat(), "regime": r.regime, "score": r.score,
                         "breadth50": (r.components.get("inputs") or {}).get("breadth50_pct"),
                         "breadth200": (r.components.get("inputs") or {}).get("breadth200_pct"),
                         "vix": (r.components.get("inputs") or {}).get("vix"),
                         "median_ret_63": (r.components.get("inputs") or {}).get("median_ret_63_pct")} for r in rows],
            "nifty": [{"time": n.session_date.isoformat(), "value": n.close} for n in nifty],
            "sources": {"index_levels": "Yahoo Finance (unofficial)", "breadth": SOURCE}}


@router.get("/accounts")
def accounts(curves: bool = False, db: Session = Depends(db_session)) -> dict:
    out = []
    for a in db.execute(select(PaperAccount).order_by(PaperAccount.id)).scalars():
        snap = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == a.id)
                          .order_by(PortfolioSnapshot.session_date.desc()).limit(1)).scalars().first()
        first = db.scalar(select(func.min(PortfolioSnapshot.session_date)).where(PortfolioSnapshot.account_id == a.id))
        trades = db.scalar(text("select count(*) from paper_positions where account_id = :a"), {"a": a.id})
        row = {"name": a.name, "profile": a.profile, "forward_test": a.name.startswith("fwd-"), "initial_capital": a.initial_capital,
               "last_processed": a.last_processed, "first_session": first, "positions_opened": trades,
               "equity": snap.equity if snap else a.initial_capital, "return_pct": round(((snap.equity if snap else a.initial_capital)
                                                                                         / a.initial_capital - 1) * 100, 3),
               "drawdown_pct": snap.drawdown_pct if snap else 0.0, "open_positions": snap.open_positions if snap else 0}
        if curves:
            row["curve"] = [{"time": s.session_date.isoformat(), "value": s.equity} for s in db.execute(
                select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == a.id).order_by(PortfolioSnapshot.session_date)).scalars()]
        out.append(row)
    return {"accounts": out, "mode": "paper"}


@router.get("/dashboard/summary")
def summary(db: Session = Depends(db_session)) -> dict:
    cfg = default_strategy_config()
    d = _latest(db)
    latest_data = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == "nse_archive"))
    reg = db.execute(select(MarketRegime).where(MarketRegime.session_date == d)).scalars().first()
    conf = db.execute(text("select strategy, count(*) from trade_signals where confirmed = :d and config_hash = :h group by 1"),
                      {"d": d, "h": strategy_hash(cfg)}).all()
    top = scanner(d, False, 0, None, None, None, "score", 5, db)["stocks"]
    fwd = {a.id: a.name for a in db.execute(select(PaperAccount).where(PaperAccount.name.like("fwd-%"))).scalars()}
    alerts = db.execute(select(RiskEvent).where(RiskEvent.check == "drawdown_alert", RiskEvent.account_id.in_(list(fwd) or [-1]))
                        .order_by(RiskEvent.id.desc()).limit(5)).scalars().all()
    holdout = db.execute(select(BacktestRun).where(BacktestRun.kind == "holdout").order_by(BacktestRun.id.desc()).limit(1)).scalars().first()
    return {"session_date": d, "latest_data": latest_data, "data_age_days": (date.today() - latest_data).days if latest_data else None,
            "regime": {"name": reg.regime, "score": reg.score, "policy": reg.policy} if reg else None,
            "confirmed_signals": {s: n for s, n in conf}, "top": top, "accounts": accounts(False, db)["accounts"],
            "drawdown_alerts": [{"date": a.session_date, "account": fwd.get(a.account_id), **a.details} for a in alerts],
            "evidence": None if holdout is None else {"run": holdout.name, "return_pct": holdout.metrics.get("total_return_pct"),
                                                     "bench_pct": holdout.metrics.get("bench_total_return_pct"),
                                                     "period": [holdout.start, holdout.end]},
            "note": EDGE_NOTE}
