"""Point-in-time universe snapshots.

For a session d, every stock that traded on d gets one row with ``eligible``
and the reasons it is not. Only information available at d is used:

* liquidity: median daily traded value over the last N exchange sessions
  ending d; a session the stock did not trade counts as zero (conservative)
* history: number of sessions the stock has traded up to d
* instrument: company shares only (ISIN INE...); ETFs (INF...) trade in EQ too but are excluded
* series: EQ by default (BE / BZ are trade-to-trade or non-compliant segments)
* data quality: no blocking issue for the stock in the last M sessions, and no
  market-wide data gap in that window
* corporate actions (ADR-011): no inferred, unconfirmed or disputed factor with
  ex-date in the last 252 sessions up to d
* surveillance (ADR-015): GSM stage from the latest NSE flag file on or before d

Survivorship: the candidate list is "traded on d" from that day's exchange
file, so stocks delisted later are included on the days they traded.

Rows are keyed by a hash of the rules, so the same data and rules always give
the same snapshot, and changing a rule never overwrites an older snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..models import TradingSession, UniverseSnapshot

REASONS = {
    "not_equity": "not company shares (ETF or other instrument)",
    "series": "series not eligible (trade-to-trade or non-compliant segment)",
    "illiquid": "median traded value below the minimum",
    "insufficient_history": "fewer traded sessions than required",
    "data_quality": "blocking data-quality issue in the look-back window",
    "market_data_gap": "market-wide data gap in the look-back window",
    "unconfirmed_corporate_action": "corporate-action factor not confirmed by a second source (ADR-011)",
    "surveillance_gsm": "under graded surveillance (GSM)",
}


def rules_of(cfg: StrategyConfig) -> dict:
    return {"min_median_traded_value_cr": cfg.min_median_traded_value_cr,
            "liquidity_lookback_sessions": cfg.liquidity_lookback_sessions,
            **cfg.universe_rules.model_dump(mode="json")}


def config_hash(cfg: StrategyConfig) -> str:
    return hashlib.sha256(json.dumps(rules_of(cfg), sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class SnapshotResult:
    session_date: date
    config_hash: str
    candidates: int
    eligible: int
    reasons: dict


def sessions_upto(db: Session, d: date, n: int, source: str) -> list[date]:
    rows = db.execute(select(TradingSession.session_date).where(TradingSession.source == source, TradingSession.session_date <= d)
                      .order_by(TradingSession.session_date.desc()).limit(n)).scalars().all()
    return sorted(rows)


def evaluate(cfg: StrategyConfig, today: pd.DataFrame, values: pd.DataFrame, history: pd.Series, window: list[date],
             blocked: set[int], market_gap: bool, quarantined: set[int], gsm: dict[int, str]) -> pd.DataFrame:
    """Pure eligibility rules (unit-tested without a database).

    today: stock_id, symbol, series, sector, close; values: stock_id, session_date, turnover_inr (window only).
    """
    rules = cfg.universe_rules
    v = values[values["session_date"].isin(window)]
    piv = v.pivot_table(index="stock_id", columns="session_date", values="turnover_inr", aggfunc="sum")
    piv = piv.reindex(index=today["stock_id"], columns=window).fillna(0.0)  # not traded = zero value
    median_cr = (piv.median(axis=1) / 1e7) if len(window) else pd.Series(0.0, index=today["stock_id"])
    out = today.copy()
    out["median_value_cr"] = out["stock_id"].map(median_cr).round(4)
    out["history_sessions"] = out["stock_id"].map(history).fillna(0).astype(int)
    reasons = []
    for r in out.itertuples(index=False):
        why = []
        if getattr(r, "kind", "equity") != "equity":
            why.append("not_equity")
        if r.series not in rules.eligible_series:
            why.append("series")
        if r.median_value_cr < cfg.min_median_traded_value_cr:
            why.append("illiquid")
        if r.history_sessions < rules.min_history_sessions:
            why.append("insufficient_history")
        if r.stock_id in blocked:
            why.append("data_quality")
        if market_gap:
            why.append("market_data_gap")
        if r.stock_id in quarantined:
            why.append("unconfirmed_corporate_action")
        if rules.exclude_gsm and r.stock_id in gsm:
            why.append("surveillance_gsm")
        reasons.append(why)
    out["reasons"] = reasons
    out["eligible"] = out["reasons"].map(len) == 0
    return out


_TODAY = text("""
    select c.stock_id, s.symbol, s.kind, c.series, coalesce(s.sector, :unclassified) as sector, c.close
    from market_candles c join stocks s on s.id = c.stock_id
    where c.session_date = :d and c.interval = '1d' and c.is_adjusted = false and c.source = :source
""")
_VALUES = text("""
    select stock_id, session_date, turnover_inr from market_candles
    where session_date >= :start and session_date <= :d and interval = '1d' and is_adjusted = false and source = :source
""")
_HISTORY = text("""
    select stock_id, count(*) as n from market_candles
    where session_date <= :d and interval = '1d' and is_adjusted = false and source = :source
    group by stock_id
""")
_BLOCKED = text("""
    select distinct stock_id from data_quality_issues
    where severity = 'blocking' and stock_id is not null and session_date >= :start and session_date <= :d
""")
_MARKET_GAP = text("""
    select count(*) from data_quality_issues
    where "check" = 'market_wide_prev_close_gap' and session_date >= :start and session_date <= :d and source like :src
""")
_QUARANTINE = text("""
    select distinct stock_id from corporate_actions
    where ex_date > :start and ex_date <= :d and source like :src and confidence = 'medium'
      and coalesce(details->>'confirmed_by', '') = ''
""")
_GSM = text("""
    select distinct on (stock_id) stock_id, gsm_stage from surveillance_flags
    where as_of <= :d order by stock_id, as_of desc
""")


def build_snapshot(db: Session, d: date, cfg: StrategyConfig, *, source: str = "nse_archive") -> SnapshotResult:
    """Compute and store the universe for session ``d`` (replaces an existing snapshot with the same rules)."""
    rules = cfg.universe_rules
    h = config_hash(cfg)
    conn = db.connection()
    today = pd.read_sql(_TODAY, conn, params={"d": d, "source": source, "unclassified": rules.unclassified_sector})
    if today.empty:
        return SnapshotResult(d, h, 0, 0, {})
    liq_window = sessions_upto(db, d, cfg.liquidity_lookback_sessions, source)
    q_window = sessions_upto(db, d, rules.quality_lookback_sessions, source)
    ca_window = sessions_upto(db, d, rules.corporate_action_quarantine_sessions + 1, source)
    values = pd.read_sql(_VALUES, conn, params={"start": liq_window[0], "d": d, "source": source})
    # counted for all stocks then filtered: much faster than an ANY(<3,000 ids>) predicate
    hist = pd.read_sql(_HISTORY, conn, params={"d": d, "source": source})
    history = hist.set_index("stock_id")["n"]
    blocked = {r[0] for r in db.execute(_BLOCKED, {"start": q_window[0], "d": d})}
    market_gap = db.execute(_MARKET_GAP, {"start": q_window[0], "d": d, "src": f"{source}%"}).scalar_one() > 0
    quarantined = set()
    if rules.corporate_action_quarantine_sessions > 0:
        quarantined = {r[0] for r in db.execute(_QUARANTINE, {"start": ca_window[0], "d": d, "src": f"{source}:%"})}
    gsm = {r.stock_id: r.gsm_stage for r in db.execute(_GSM, {"d": d}) if r.gsm_stage}

    snap = evaluate(cfg, today, values, history, liq_window, blocked, market_gap, quarantined, gsm)
    db.execute(delete(UniverseSnapshot).where(UniverseSnapshot.session_date == d, UniverseSnapshot.config_hash == h,
                                              UniverseSnapshot.source == source))
    rows = [{"session_date": d, "stock_id": int(r.stock_id), "eligible": bool(r.eligible), "reasons": list(r.reasons),
             "symbol": r.symbol, "series": r.series, "sector": r.sector, "close": float(r.close),
             "median_value_cr": float(r.median_value_cr), "history_sessions": int(r.history_sessions),
             "config_hash": h, "source": source, "demo": source == "demo"} for r in snap.itertuples(index=False)]
    db.execute(insert(UniverseSnapshot), rows)  # executemany: compiled once
    db.flush()
    counts: dict[str, int] = {}
    for why in snap["reasons"]:
        for w in why:
            counts[w] = counts.get(w, 0) + 1
    return SnapshotResult(d, h, len(snap), int(snap["eligible"].sum()), counts)
