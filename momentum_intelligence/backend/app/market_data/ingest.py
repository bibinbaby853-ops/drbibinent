"""Write provider data to PostgreSQL with lineage, quality checks and adjustments.

Flow for one session (``ingest_session``):

1. fetch the bhavcopy; a 404 on a weekday after publication time = holiday (logged, not a session)
2. run session checks; blocking rows are not stored as candles, every issue is stored
3. resolve identities (ISIN key; symbol changes and split-driven ISIN changes keep the same stock row)
4. upsert raw daily candles (idempotent on stock, interval, ts, adjusted flag, source)
5. log every fetched file in ``data_provider_logs`` (URL, hash, rows, checks)

``refresh_adjustments`` then derives corporate-action factors from the exchange's
PREV_CLOSE and (re)writes adjusted rows. Running any step twice gives the same
database state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import (CorporateAction, DataProviderLog, DataQualityIssue, MarketCandle, Stock, StockSymbolHistory,
                      TradingSession, utcnow)
from .adjustments import derive_factors
from .base import BarFrame, DataNotPublished, Lineage, ProviderError, SymbolChange
from .calendar import bhavcopy_expected, is_weekday, weekdays
from .validation import Issue, blocking_mask, check_session, summarise

log = logging.getLogger(__name__)


@dataclass
class SessionResult:
    session_date: date
    status: str  # ingested | holiday | not_published | weekend | failed
    rows: int = 0
    rejected: int = 0
    new_stocks: int = 0
    symbol_changes: int = 0
    isin_changes: int = 0
    issues: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _py(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v is pd.NaT:
        return None
    return v.item() if hasattr(v, "item") else v


def write_lineage(db: Session, lin: Lineage, *, status: str, rows: int, checks: dict | None = None,
                  error: str | None = None, demo: bool = False) -> int:
    rec = DataProviderLog(provider=lin.provider, licence=lin.licence, dataset=lin.dataset,
                          params={**lin.params, "from_cache": lin.from_cache}, source_url=lin.source_url,
                          started_at=lin.fetched_at, finished_at=utcnow(), status=status, rows=rows, as_of=lin.as_of,
                          content_sha256=lin.content_sha256, checks=checks or {}, error=error, demo=demo)
    db.add(rec)
    db.flush()
    return rec.id


def _stock_key(row, demo: bool) -> str:
    return f"DEMO:{row['symbol']}" if demo else row["isin"]


def resolve_stocks(db: Session, bars: pd.DataFrame, d: date, *, source: str, demo: bool,
                   symbol_changes: list[SymbolChange] | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """Map each row's key to a stock id, creating or re-keying stocks as needed.

    Returns (key -> stock_id, counters). Identity rules, in order:
    1. key (ISIN) already known: same stock; a different symbol is a symbol change.
    2. unknown ISIN, but the same symbol traded on the previous session under an ISIN absent today:
       the ISIN changed (typical on a face-value split); keep the stock row.
    3. unknown ISIN whose symbol is the NEW symbol of a change effective today: re-key that stock.
    4. an ISIN the stock had before (from symbol history): same stock; a back-fill of an older session
       does not re-key it, because only a later session may change the current key.
    5. otherwise a new stock.
    """
    counters = {"new": 0, "symbol_change": 0, "isin_change": 0}
    keys = {_stock_key(r, demo): r for r in bars.to_dict("records")}
    existing = {s.key: s for s in db.scalars(select(Stock).where(Stock.demo == demo, Stock.kind == "equity"))}
    by_symbol = {s.symbol: s for s in existing.values()}
    by_id = {s.id: s for s in existing.values()}
    past_isin = {} if demo else {h.isin: by_id.get(h.stock_id) for h in db.execute(
        select(StockSymbolHistory.isin, StockSymbolHistory.stock_id).where(StockSymbolHistory.isin.is_not(None)))}
    today_keys = set(keys)
    changes_today = {c.new_symbol: c.old_symbol for c in (symbol_changes or []) if c.effective == d}
    out: dict[str, int] = {}

    def history(stock: Stock, reason: str):
        db.execute(insert(StockSymbolHistory).values(stock_id=stock.id, symbol=stock.symbol, isin=stock.isin, valid_from=d,
                                                     reason=reason, source=source).on_conflict_do_nothing())

    for key, r in keys.items():
        stock = existing.get(key) or past_isin.get(key)
        if stock is not None:
            if stock.key == key and stock.symbol != r["symbol"] and (stock.last_seen is None or d >= stock.last_seen):
                stock.symbol = r["symbol"]
                counters["symbol_change"] += 1
                history(stock, "symbol_change")
        elif not demo:
            cand = by_symbol.get(r["symbol"]) or (by_symbol.get(changes_today[r["symbol"]]) if r["symbol"] in changes_today else None)
            if cand is not None and cand.key not in today_keys and cand.last_seen is not None and cand.last_seen < d:
                existing.pop(cand.key, None)
                cand.key, cand.isin, cand.symbol = key, r["isin"], r["symbol"]
                existing[key] = cand
                stock = cand
                counters["isin_change"] += 1
                history(stock, "isin_change")
        if stock is None:
            stock = Stock(key=key, isin=None if demo else r["isin"], symbol=r["symbol"], series=r["series"], kind="equity",
                          status="active", first_seen=d, last_seen=d, demo=demo)
            db.add(stock)
            db.flush()
            existing[key] = stock
            counters["new"] += 1
            history(stock, "first_seen")
        stock.series = r["series"] if (stock.last_seen is None or d >= stock.last_seen) else stock.series
        stock.first_seen = min(stock.first_seen or d, d)
        stock.last_seen = max(stock.last_seen or d, d)
        stock.status = "active"
        by_symbol[stock.symbol] = stock
        out[key] = stock.id
    db.flush()
    return out, counters


def upsert_candles(db: Session, bars: pd.DataFrame, ids: dict[str, int], *, source: str, licence: str,
                   dataset_id: int | None, demo: bool, interval: str = "1d", adjusted: bool = False) -> int:
    cols = ["open", "high", "low", "close", "volume", "turnover_inr", "trades", "delivery_qty", "delivery_pct", "prev_close", "series"]
    now = utcnow()
    rows = []
    for r in bars.to_dict("records"):
        rec = {c: _py(r.get(c)) for c in cols}
        for c in ("volume", "trades", "delivery_qty"):
            if rec[c] is not None:
                rec[c] = int(rec[c])
        rec.update(stock_id=ids[_stock_key(r, demo)], interval=interval, ts=r["ts"], session_date=r["session_date"],
                   is_adjusted=adjusted, source=source, licence=licence, dataset_id=dataset_id, fetched_at=now, demo=demo)
        rows.append(rec)
    if not rows:
        return 0
    for i in range(0, len(rows), 2000):
        stmt = insert(MarketCandle).values(rows[i:i + 2000])
        stmt = stmt.on_conflict_do_update(constraint="uq_candle", set_={c: stmt.excluded[c] for c in cols + ["dataset_id", "fetched_at", "session_date", "licence"]})
        db.execute(stmt)
    return len(rows)


def store_issues(db: Session, issues: list[Issue], *, source: str, d: date, log_id: int | None, ids_by_symbol: dict[str, int]) -> None:
    db.execute(delete(DataQualityIssue).where(DataQualityIssue.source == source, DataQualityIssue.session_date == d))
    rows = [{"log_id": log_id, "source": source, "stock_id": ids_by_symbol.get(i.symbol), "symbol": i.symbol,
             "session_date": i.session_date or d, "check": i.check, "severity": i.severity,
             "details": {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in i.details.items()}}
            for i in issues]
    if rows:
        db.execute(insert(DataQualityIssue).values(rows))


def ingest_session(db: Session, provider, d: date, *, symbol_changes: list[SymbolChange] | None = None,
                   now: datetime | None = None) -> SessionResult:
    demo = provider.licence == "demo"
    if not is_weekday(d):
        return SessionResult(d, "weekend")
    try:
        frame: BarFrame = provider.bhavcopy(d)
    except DataNotPublished as exc:
        status = "holiday" if (demo or bhavcopy_expected(d, now)) else "not_published"
        return SessionResult(d, status, notes=[str(exc)])
    except ProviderError as exc:
        db.rollback()
        return SessionResult(d, "failed", notes=[str(exc)])

    bars = frame.data
    issues = check_session(bars, require_isin=not demo)
    bad = blocking_mask(bars, issues)
    if not demo:
        bad |= bars["isin"].isna()  # cannot key a security without ISIN; recorded as missing_isin
    good = bars[~bad]
    summary = summarise(issues)
    primary, *others = frame.lineage
    log_id = write_lineage(db, primary, status="success" if not bad.any() else "partial", rows=len(good),
                           checks=summary, demo=demo)
    for lin in others:
        write_lineage(db, lin, status="success", rows=len(bars), demo=demo)
    for note in frame.notes:
        issues.append(Issue("source_note", "info", None, d, {"note": note}))

    ids, counters = resolve_stocks(db, good, d, source=provider.name, demo=demo, symbol_changes=symbol_changes)
    n = upsert_candles(db, good, ids, source=provider.name, licence=provider.licence, dataset_id=log_id, demo=demo)
    by_symbol = {r["symbol"]: ids[_stock_key(r, demo)] for r in good.to_dict("records")}
    store_issues(db, issues, source=provider.name, d=d, log_id=log_id, ids_by_symbol=by_symbol)
    db.execute(insert(TradingSession).values(session_date=d, source=provider.name).on_conflict_do_nothing())
    db.flush()
    return SessionResult(d, "ingested", rows=n, rejected=int(bad.sum()), new_stocks=counters["new"],
                         symbol_changes=counters["symbol_change"], isin_changes=counters["isin_change"],
                         issues=summary, notes=frame.notes)


def ingest_range(db_factory, provider, start: date, end: date, *, symbol_changes=None, progress=None) -> list[SessionResult]:
    """Ingest sessions oldest first, one transaction per session (a failure loses only that session)."""
    results = []
    for d in weekdays(start, end):
        with db_factory() as db:
            res = ingest_session(db, provider, d, symbol_changes=symbol_changes)
        results.append(res)
        if progress:
            progress(res)
    return results


# ---------------------------------------------------------------- adjustments

_RAW_SQL = text("""
    select c.stock_id, s.symbol, s.isin, c.session_date, c.open, c.close, c.prev_close
    from market_candles c join stocks s on s.id = c.stock_id
    where c.interval = '1d' and c.is_adjusted = false and c.source = :source
      and (cast(:ids as integer[]) is null or c.stock_id = any(cast(:ids as integer[])))
    order by c.stock_id, c.session_date
""")

_ADJUST_SQL = text("""
    insert into market_candles (stock_id, interval, ts, session_date, open, high, low, close, volume, turnover_inr,
        trades, delivery_qty, delivery_pct, prev_close, series, is_adjusted, source, licence, dataset_id, fetched_at, demo)
    select c.stock_id, c.interval, c.ts, c.session_date, c.open * f.cf, c.high * f.cf, c.low * f.cf, c.close * f.cf,
        round(c.volume / f.cf), c.turnover_inr, c.trades, round(c.delivery_qty / f.cf), c.delivery_pct,
        c.prev_close * f.cf, c.series, true, c.source, c.licence, c.dataset_id, c.fetched_at, c.demo
    from market_candles c
    cross join lateral (
        select coalesce(exp(sum(ln(a.factor))), 1.0) as cf from corporate_actions a
        where a.stock_id = c.stock_id and a.ex_date > c.session_date and a.confidence in ('high', 'medium')
    ) f
    where c.interval = '1d' and c.is_adjusted = false and c.source = :source
      and (cast(:ids as integer[]) is null or c.stock_id = any(cast(:ids as integer[])))
      and (cast(:since as date) is null or c.session_date >= cast(:since as date))
    on conflict on constraint uq_candle do update set
        open = excluded.open, high = excluded.high, low = excluded.low, close = excluded.close,
        volume = excluded.volume, delivery_qty = excluded.delivery_qty, prev_close = excluded.prev_close,
        turnover_inr = excluded.turnover_inr, trades = excluded.trades, delivery_pct = excluded.delivery_pct,
        series = excluded.series, dataset_id = excluded.dataset_id, fetched_at = excluded.fetched_at
""")


def refresh_adjustments(db: Session, *, source: str, stock_ids: list[int] | None = None, since: date | None = None) -> dict:
    """Derive factors from raw bars, store new ones, and (re)write adjusted candles.

    Stocks with a newly found factor get their whole adjusted history rebuilt; others only from ``since``.
    """
    raw = pd.read_sql(_RAW_SQL, db.connection(), params={"source": source, "ids": stock_ids})
    known = {(r.stock_id, r.ex_date) for r in db.execute(select(CorporateAction.stock_id, CorporateAction.ex_date)
                                                           .where(CorporateAction.source.like(f"{source}:%")))}
    new_rows, rebuild = [], set()
    for sid, g in raw.groupby("stock_id", sort=False):
        for ev in derive_factors(g, symbol=g["symbol"].iat[0], isin=g["isin"].iat[0], source=f"{source}:prev_close"):
            if (sid, ev.ex_date) not in known:
                new_rows.append({"stock_id": int(sid), "ex_date": ev.ex_date, "kind": ev.kind, "factor": ev.factor,
                                 "source": ev.source, "confidence": ev.confidence, "details": ev.details})
                rebuild.add(int(sid))
    if new_rows:
        db.execute(insert(CorporateAction).values(new_rows).on_conflict_do_nothing(constraint="uq_corporate_action"))
        inferred = [r for r in new_rows if r["kind"] == "inferred_split_or_bonus"]
        if inferred:
            db.execute(insert(DataQualityIssue).values([
                {"source": f"{source}:adjustments", "stock_id": r["stock_id"], "symbol": None, "session_date": r["ex_date"],
                 "check": "corporate_action_inferred", "severity": "warning",
                 "details": {"factor": r["factor"], **{k: v for k, v in r["details"].items()}}} for r in inferred]))
    touched = 0
    if rebuild:
        touched += db.execute(_ADJUST_SQL, {"source": source, "ids": sorted(rebuild), "since": None}).rowcount
    rest = None if stock_ids is None else sorted(set(stock_ids) - rebuild)
    if rest is None or rest:
        touched += db.execute(_ADJUST_SQL, {"source": source, "ids": rest, "since": since}).rowcount
    db.flush()
    return {"factors_new": len(new_rows), "stocks_rebuilt": len(rebuild), "adjusted_rows_written": touched}


def confirm_inferred(db: Session, splits_lookup, *, source: str, window_days: int = 3, tolerance: float = 0.02) -> dict:
    """Check medium-confidence inferred factors against a second source.

    ``splits_lookup(symbol) -> [(ex_date, shares_multiplier)]`` (Yahoo). Outcomes:
    * matching split within +-window_days and ratio within tolerance -> confidence high
    * a split with a different ratio -> confidence low (factor no longer applied) and a warning
    * no split found -> stays medium, the check is recorded
    Adjusted series are rebuilt for stocks whose applied factors changed.
    """
    rows = db.execute(select(CorporateAction, Stock.symbol).join(Stock, Stock.id == CorporateAction.stock_id)
                      .where(CorporateAction.source == f"{source}:ratio_gap", CorporateAction.confidence == "medium")).all()
    counts = {"checked": 0, "confirmed": 0, "disagreed": 0, "not_found": 0, "errors": 0}
    changed: set[int] = set()
    for ca, symbol in rows:
        if (ca.details or {}).get("confirmed_by") or (ca.details or {}).get("second_source_checked"):
            continue
        counts["checked"] += 1
        try:
            found = splits_lookup(symbol)
        except ProviderError as exc:
            counts["errors"] += 1
            log.warning("split lookup failed for %s: %s", symbol, exc)
            continue
        near = [(d, m) for d, m in found if abs((d - ca.ex_date).days) <= window_days]
        details = dict(ca.details or {}, second_source_checked="yahoo")
        if not near:
            counts["not_found"] += 1
        elif any(abs((1 / m) / ca.factor - 1) <= tolerance for _, m in near):
            ca.confidence, details["confirmed_by"] = "high", "yahoo"
            counts["confirmed"] += 1
        else:
            ca.confidence = "low"
            details["yahoo_multipliers"] = [m for _, m in near]
            counts["disagreed"] += 1
            changed.add(ca.stock_id)
            db.add(DataQualityIssue(source=f"{source}:adjustments", stock_id=ca.stock_id, symbol=symbol, session_date=ca.ex_date,
                                    check="corporate_action_disagreement", severity="warning",
                                    details={"inferred_factor": ca.factor, "yahoo_multipliers": [m for _, m in near]}))
        ca.details = details
    db.flush()
    if changed:
        # The factor is no longer applied: rewrite those stocks' adjusted history.
        db.execute(_ADJUST_SQL, {"source": source, "ids": sorted(changed), "since": None})
    return counts


# ---------------------------------------------------------------- reference data

def ingest_reference(db: Session, provider) -> dict:
    """Names and listing dates from EQUITY_L; sector classification from the Nifty Total Market list."""
    out = {}
    secs, lin = provider.security_master()
    write_lineage(db, lin, status="success", rows=len(secs))
    stocks = {s.isin: s for s in db.scalars(select(Stock).where(Stock.demo.is_(False), Stock.isin.is_not(None)))}
    listed = {s.isin for s in secs if s.isin}
    named = 0
    for sec in secs:
        st = stocks.get(sec.isin)
        if st is not None:
            st.name = sec.name
            named += 1
    out["names_updated"] = named
    try:
        ind, lin = provider.industries()
        write_lineage(db, lin, status="success", rows=len(ind))
        cls = 0
        for isin, sector in ind.items():
            st = stocks.get(isin)
            if st is not None:
                st.sector, st.classification_source = sector, "nse:ind_niftytotalmarket_list"
                cls += 1
        out["sector_classified"] = cls
    except DataNotPublished as exc:
        out["sector_classified"] = 0
        out["sector_note"] = str(exc)
    # Present in bhavcopies but no longer in the listed-equity master: marked inactive (kept for survivorship).
    # Only stocks that also stopped trading are marked, so a lagging master file cannot deactivate a live stock.
    latest = db.scalar(select(TradingSession.session_date).order_by(TradingSession.session_date.desc()).limit(1))
    inactive = 0
    for isin, st in stocks.items():
        if isin not in listed and st.status == "active" and latest is not None and st.last_seen is not None and st.last_seen < latest:
            st.status = "inactive"
            inactive += 1
    out["marked_inactive"] = inactive
    db.flush()
    return out
