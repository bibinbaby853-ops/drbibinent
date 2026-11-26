"""Daily reports (pre-market and post-market), built only from stored data. Deterministic: no estimates,
no narrative beyond what the numbers say. Every report states when it was generated, which completed
session it describes, and each source with its licence. (Phase 10 may add an AI narrative on top; it
must not introduce numbers that are not in ``data``.)
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..market_data.calendar import now_ist
from ..models import Alert, MarketRegime, PaperAccount, PaperOrder, PaperPosition, PortfolioSnapshot, Report, SectorData, TradingSession
from ..strategies.pipeline import strategy_hash

SOURCES = [
    {"name": "NSE bhavcopy (UDiFF / legacy) and full bhavcopy", "licence": "exchange official, end of day", "used_for": "prices, volumes, delivery, universe"},
    {"name": "NSE EQUITY_L, sec_list, symbolchange, Nifty Total Market list", "licence": "exchange official", "used_for": "names, price bands, GSM, sectors"},
    {"name": "NSE official index closes (ind_close_all)", "licence": "exchange official", "used_for": "Nifty 50 / Nifty 500 / India VIX daily levels"},
    {"name": "Yahoo Finance", "licence": "unofficial, delayed", "used_for": "index levels only where NSE has none, split confirmation, intraday candles"},
    {"name": "Momentum Intelligence computations", "licence": "derived", "used_for": "scores, RS, regime, signals, paper portfolio"},
]
DISCLAIMER = ("Paper trading and research only; not investment advice. Backtests found no out-of-sample edge for these rules; "
              "the forward paper accounts are the ongoing test.")


def _fmt(v, nd=2, pct=False):
    if v is None:
        return "n/a"
    return f"{v:+.{nd}f}%" if pct else f"{v:,.{nd}f}"


def gather(db: Session, cfg: StrategyConfig, session: date) -> dict:
    reg = db.execute(select(MarketRegime).where(MarketRegime.session_date == session)).scalars().first()
    prev = db.execute(select(MarketRegime).where(MarketRegime.session_date < session).order_by(MarketRegime.session_date.desc()).limit(1)).scalars().first()
    secs = db.execute(select(SectorData).where(SectorData.session_date == session, SectorData.rank.is_not(None)).order_by(SectorData.rank)).scalars().all()
    confirmed = db.execute(text("""select t.symbol, t.strategy, t.entry, t.stop, m.total from trade_signals t
        left join momentum_scores m on m.stock_id = t.stock_id and m.session_date = t.confirmed
        where t.confirmed = :d and t.config_hash = :h order by m.total desc nulls last"""), {"d": session, "h": strategy_hash(cfg)}).all()
    top = db.execute(text("""select s.symbol, m.total, m.band from momentum_scores m join stocks s on s.id = m.stock_id
        where m.session_date = :d order by m.total desc limit 10"""), {"d": session}).all()
    accounts = []
    for a in db.execute(select(PaperAccount).where(PaperAccount.name.like("fwd-%")).order_by(PaperAccount.name)).scalars():
        snap = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.account_id == a.id, PortfolioSnapshot.session_date == session)).scalars().first()
        fills = db.execute(select(PaperOrder).where(PaperOrder.account_id == a.id, PaperOrder.filled_session == session)).scalars().all()
        pend = db.execute(select(PaperOrder).where(PaperOrder.account_id == a.id, PaperOrder.status == "pending")).scalars().all()
        pos = db.execute(select(PaperPosition).where(PaperPosition.account_id == a.id, PaperPosition.status == "open")).scalars().all()
        accounts.append({"name": a.name, "profile": a.profile, "equity": snap.equity if snap else None,
                         "return_pct": None if not snap else (snap.equity / a.initial_capital - 1) * 100,
                         "drawdown_pct": snap.drawdown_pct if snap else None, "reconciled": snap.reconciliation.get("ok") if snap else None,
                         "fills": [{"symbol": o.symbol, "side": o.side, "kind": o.kind, "qty": o.qty, "price": o.fill_price} for o in fills],
                         "pending": [{"symbol": o.symbol, "side": o.side, "kind": o.kind, "qty": o.qty, "reference": o.reference_price} for o in pend],
                         "positions": [{"symbol": p.symbol, "qty": p.qty, "avg": p.avg_price, "stop": p.stop, "mark": p.mark} for p in pos]})
    quality = db.execute(text("""select "check", severity, count(*) from data_quality_issues where session_date = :d group by 1, 2 order by 3 desc"""),
                         {"d": session}).all()
    alerts = db.execute(select(Alert.rule, func.count()).where(Alert.session_date == session).group_by(Alert.rule)).all()
    universe = db.execute(text("select count(*) filter (where eligible), count(*) from stock_universe_snapshots where session_date = :d"),
                          {"d": session}).one()
    return {"session_date": session.isoformat(),
            "regime": None if not reg else {"name": reg.regime, "score": reg.score, "policy": reg.policy,
                                            "previous": prev.regime if prev else None, "inputs": reg.components.get("inputs"),
                                            "benchmark_sources": reg.components.get("benchmark_sources") or {}},
            "universe": {"eligible": universe[0], "traded": universe[1]},
            "sectors_top": [{"sector": s.sector, "rank": s.rank, "rank_change": s.rank_change, "flags": s.flags} for s in secs[:5]],
            "sectors_bottom": [{"sector": s.sector, "rank": s.rank} for s in secs[-3:]],
            "confirmed_setups": [{"symbol": c.symbol, "strategy": c.strategy, "entry": c.entry, "stop": c.stop, "score": c.total} for c in confirmed],
            "top_scores": [{"symbol": t.symbol, "score": t.total, "band": t.band} for t in top],
            "accounts": accounts, "data_quality": [{"check": q[0], "severity": q[1], "count": q[2]} for q in quality],
            "intraday_alerts": {r: n for r, n in alerts}}


def render(kind: str, d: dict, generated: str) -> str:
    title = "Pre-market report" if kind == "pre_market" else "Post-market report"
    reg = d["regime"] or {}
    L = [f"# {title}: session {d['session_date']}", "",
         f"Generated {generated} IST from data for the completed session {d['session_date']} (NSE close 15:30 IST).", "",
         f"> {DISCLAIMER}", "", "## Market"]
    if reg:
        changed = f" (was {reg['previous'].replace('_', ' ')})" if reg.get("previous") and reg["previous"] != reg["name"] else ""
        pol = reg["policy"]
        L += [f"- Regime: **{reg['name'].replace('_', ' ')}**{changed}, score {reg['score']:.0f} (-100 to 100).",
              f"- New entries: {'allowed, up to ' + str(pol['max_new_entries_per_day']) + ' per day at ' + str(pol['risk_multiplier']) + 'x risk, minimum score ' + str(pol['min_score']) if pol['allow_new_entries'] else 'not allowed'}.",
              f"- Breadth: {_fmt((reg.get('inputs') or {}).get('breadth50_pct'), 1)}% above the 50-day average, "
              f"{_fmt((reg.get('inputs') or {}).get('breadth200_pct'), 1)}% above the 200-day; India VIX {_fmt((reg.get('inputs') or {}).get('vix'))}."]
        src = reg.get("benchmark_sources") or {}
        carried = sorted(n for n, o in src.items() if o == "carried")
        fallback = sorted(n for n, o in src.items() if o == "yahoo")
        if carried:
            L.append(f"- Warning: no {', '.join(carried)} close was published for this session; the previous close was "
                     "carried forward, so regime and relative strength use a stale benchmark value.")
        if fallback:
            L.append(f"- {', '.join(fallback)}: NSE's official close was unavailable; Yahoo's (unofficial) value was used.")
    L += [f"- Universe: {d['universe']['eligible']} eligible of {d['universe']['traded']} traded.", "", "## Sectors",
          "Strongest: " + ", ".join(f"{s['rank']}. {s['sector']}" + (f" ({', '.join(f.replace('_', ' ') for f in s['flags'])})" if s['flags'] else "")
                                    for s in d["sectors_top"]),
          "Weakest: " + ", ".join(f"{s['rank']}. {s['sector']}" for s in d["sectors_bottom"]), "",
          f"## Confirmed setups ({len(d['confirmed_setups'])})"]
    L += [f"- {c['symbol']}: {c['strategy']}, entry {_fmt(c['entry'])}, stop {_fmt(c['stop'])}, score {_fmt(c['score'], 1)}"
          for c in d["confirmed_setups"][:15]] or ["- none"]
    L += ["", "## Forward-test paper accounts"]
    for a in d["accounts"]:
        L.append(f"- **{a['name']}** ({(a['profile'] or '').replace('_', ' ')}): equity {_fmt(a['equity'], 0)}, "
                 f"return {_fmt(a['return_pct'], 2, True)}, drawdown {_fmt(a['drawdown_pct'])}%, books reconciled: {a['reconciled']}")
        for f in a["fills"]:
            L.append(f"  - filled: {f['side']} {f['qty']} {f['symbol']} at {_fmt(f['price'])} ({f['kind'].replace('_', ' ')})")
        for p in a["pending"]:
            L.append(f"  - {'for the next open' if kind == 'post_market' else 'at today' + chr(39) + 's open'}: {p['side']} {p['qty']} {p['symbol']} ({p['kind'].replace('_', ' ')})")
        for p in a["positions"]:
            L.append(f"  - holding {p['qty']} {p['symbol']} at {_fmt(p['avg'])}, stop {_fmt(p['stop'])}, last {_fmt(p['mark'])}")
    if d["intraday_alerts"]:
        L += ["", "## Intraday alerts (session)", ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in d["intraday_alerts"].items())]
    if d["data_quality"]:
        L += ["", "## Data quality", ", ".join(f"{q['check'].replace('_', ' ')} ({q['severity']}): {q['count']}" for q in d["data_quality"])]
    L += ["", "## Sources"] + [f"- {s['name']}: {s['licence']}; {s['used_for']}" for s in SOURCES]
    return "\n".join(L) + "\n"


def generate(db: Session, kind: str, cfg: StrategyConfig, session: date | None = None) -> Report:
    if kind not in ("pre_market", "post_market"):
        raise ValueError(kind)
    session = session or db.scalar(select(func.max(MarketRegime.session_date)))
    if session is None:
        raise RuntimeError("no scored session yet")
    generated = now_ist().strftime("%Y-%m-%d %H:%M")
    data = gather(db, cfg, session)
    md = render(kind, data, generated)
    latest_data = db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == "nse_archive"))
    stmt = insert(Report).values(kind=kind, session_date=session, data_as_of=f"{session} 15:30 IST close (latest stored {latest_data})",
                                 sources=SOURCES, content_md=md, data=data, version="1")
    db.execute(stmt.on_conflict_do_update(constraint="uq_report", set_={"content_md": stmt.excluded.content_md, "data": stmt.excluded.data,
                                                                         "generated_at": func.now(), "data_as_of": stmt.excluded.data_as_of,
                                                                         "sources": stmt.excluded.sources}))
    db.flush()
    return db.execute(select(Report).where(Report.kind == kind, Report.session_date == session, Report.version == "1")).scalars().one()
