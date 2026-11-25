"""SQLAlchemy ORM models.

Phase 2 covers the data layer: securities, symbol history, trading sessions,
candles (raw and adjusted), corporate actions, provider logs (lineage) and
data-quality issues. Later phases add scoring, signals, portfolio and audit
tables in new migrations.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Stock(Base):
    """One security, identified by ISIN so history survives symbol changes."""

    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable external key: current ISIN for equities, "INDEX:<name>" for indices, "DEMO:<symbol>" for demo data.
    # When a split changes the ISIN the row is kept (history continuity) and the key/isin are updated.
    key: Mapped[str] = mapped_column(String(60), unique=True)
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    series: Mapped[str | None] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(10), default="equity")  # equity | index | etf
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(150))
    classification_source: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | inactive | delisted
    listing_date: Mapped[date | None] = mapped_column(Date)
    face_value: Mapped[float | None] = mapped_column(Float)
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    symbols: Mapped[list["StockSymbolHistory"]] = relationship(back_populates="stock")


class StockSymbolHistory(Base):
    __tablename__ = "stock_symbol_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    isin: Mapped[str | None] = mapped_column(String(12))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(30), default="first_seen")  # first_seen | symbol_change | isin_change
    source: Mapped[str] = mapped_column(String(60))

    stock: Mapped[Stock] = relationship(back_populates="symbols")
    __table_args__ = (UniqueConstraint("stock_id", "symbol", "isin", "valid_from", name="uq_symbol_history"),)


class TradingSession(Base):
    """A date is a trading session only when an official source shows trading on it."""

    __tablename__ = "trading_sessions"

    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(60), primary_key=True)  # demo sessions never mix with exchange sessions
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataProviderLog(Base):
    """Lineage for every dataset fetched: where, when, what, and what the checks found."""

    __tablename__ = "data_provider_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    licence: Mapped[str] = mapped_column(String(30))
    dataset: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_url: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))  # success | partial | failed | skipped
    rows: Mapped[int] = mapped_column(Integer, default=0)
    as_of: Mapped[date | None] = mapped_column(Date)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    checks: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_provider_logs_dataset_asof", "dataset", "as_of"),)


class MarketCandle(Base):
    """OHLCV bar. Raw exchange prices (is_adjusted=False) and derived adjusted prices are separate rows."""

    __tablename__ = "market_candles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    interval: Mapped[str] = mapped_column(String(4))  # 1d | 60m | 15m
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # bar start (daily: 09:15 IST)
    session_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)
    turnover_inr: Mapped[float | None] = mapped_column(Float)
    trades: Mapped[int | None] = mapped_column(Integer)
    delivery_qty: Mapped[int | None] = mapped_column(BigInteger)
    delivery_pct: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)  # as published by the source
    series: Mapped[str | None] = mapped_column(String(4))
    is_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40))
    licence: Mapped[str] = mapped_column(String(30))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("data_provider_logs.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("stock_id", "interval", "ts", "is_adjusted", "source", name="uq_candle"),
        Index("ix_candles_stock_interval_date", "stock_id", "interval", "session_date"),
        Index("ix_candles_session", "session_date"),
    )


class CorporateAction(Base):
    """Price-adjustment factor effective on ex_date: prices before ex_date are multiplied by factor."""

    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    ex_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(30))  # price_adjustment | split_or_bonus | inferred_split_or_bonus
    factor: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(10), default="high")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("stock_id", "ex_date", "source", name="uq_corporate_action"),)


class DataQualityIssue(Base):
    """A failed data check. Blocking issues quarantine the stock for that session."""

    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    log_id: Mapped[int | None] = mapped_column(ForeignKey("data_provider_logs.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(40))
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    symbol: Mapped[str | None] = mapped_column(String(40))
    session_date: Mapped[date | None] = mapped_column(Date)
    check: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # info | warning | blocking
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_quality_session_check", "session_date", "check"), Index("ix_quality_source_session", "source", "session_date"))


class SurveillanceFlag(Base):
    """Price band and GSM stage as published in NSE sec_list.csv on ``as_of`` (current-only source, ADR-015)."""

    __tablename__ = "surveillance_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    price_band_pct: Mapped[float | None] = mapped_column(Float)  # None = no band
    gsm_stage: Mapped[str | None] = mapped_column(String(20))
    series: Mapped[str | None] = mapped_column(String(4))
    source: Mapped[str] = mapped_column(String(40))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("data_provider_logs.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_surveillance_stock_asof"),)


class UniverseSnapshot(Base):
    """Point-in-time eligibility of every stock that traded on a session, with the reasons."""

    __tablename__ = "stock_universe_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    eligible: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)  # empty when eligible
    symbol: Mapped[str] = mapped_column(String(40))
    series: Mapped[str | None] = mapped_column(String(4))
    sector: Mapped[str] = mapped_column(String(100))
    close: Mapped[float] = mapped_column(Float)
    median_value_cr: Mapped[float | None] = mapped_column(Float)
    history_sessions: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(40))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_date", "stock_id", "config_hash", name="uq_universe_snapshot"),
        Index("ix_universe_session_eligible", "session_date", "eligible"),
    )


class IndicatorSnapshot(Base):
    """Indicator values for one stock and session (point-in-time adjusted), stored as one JSON document."""

    __tablename__ = "technical_indicators"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(10))
    values: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_date", "stock_id", "version", "source", name="uq_indicator_snapshot"),
        Index("ix_indicators_session", "session_date"),
    )


class RelativeStrength(Base):
    """Per-stock relative strength on a session: excess returns, combined rank and classifications."""

    __tablename__ = "relative_strength"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    rs_rank: Mapped[float | None] = mapped_column(Float)          # 1-99 percentile within the eligible universe
    rs_rank_change: Mapped[float | None] = mapped_column(Float)   # vs `change_lookback` sessions earlier
    classes: Mapped[list] = mapped_column(JSONB, default=list)    # leading, improving, weakening_leader, ...
    values: Mapped[dict] = mapped_column(JSONB, default=dict)     # excess returns per window and benchmark
    version: Mapped[str] = mapped_column(String(10))

    __table_args__ = (UniqueConstraint("session_date", "stock_id", "version", name="uq_relative_strength"),
                      Index("ix_rs_session_rank", "session_date", "rs_rank"))


class SectorData(Base):
    """Sector rotation metrics from an equal-weighted composite of the sector's eligible constituents."""

    __tablename__ = "sector_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    sector: Mapped[str] = mapped_column(String(100))
    rank: Mapped[int | None] = mapped_column(Integer)
    rank_change: Mapped[int | None] = mapped_column(Integer)
    constituents: Mapped[int] = mapped_column(Integer)
    flags: Mapped[list] = mapped_column(JSONB, default=list)      # emerging_leadership, thin, ...
    values: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[str] = mapped_column(String(10))

    __table_args__ = (UniqueConstraint("session_date", "sector", "version", name="uq_sector_data"),)


class MarketRegime(Base):
    __tablename__ = "market_regime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    regime: Mapped[str] = mapped_column(String(24))
    score: Mapped[float] = mapped_column(Float)                   # -100..+100
    coverage: Mapped[float] = mapped_column(Float)                # share of component weight with data
    components: Mapped[dict] = mapped_column(JSONB, default=dict)
    policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[str] = mapped_column(String(10))

    __table_args__ = (UniqueConstraint("session_date", "version", name="uq_market_regime"),)


class MomentumScore(Base):
    """100-point score. ``breakdown`` holds every sub-check (stored for recent sessions; history keeps totals)."""

    __tablename__ = "momentum_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    total: Mapped[float] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    band: Mapped[str] = mapped_column(String(12))                 # priority | strong | developing | watchlist
    groups: Mapped[dict] = mapped_column(JSONB, default=dict)     # points per group
    breakdown: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))  # SQL NULL, not JSON null, when absent
    version: Mapped[str] = mapped_column(String(10))
    config_hash: Mapped[str] = mapped_column(String(16))

    __table_args__ = (UniqueConstraint("session_date", "stock_id", "version", "config_hash", name="uq_momentum_score"),
                      Index("ix_scores_session_total", "session_date", "total"))


# ---------------------------------------------------------------- Phase 6: paper portfolio (PAPER ONLY)

class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    mode: Mapped[str] = mapped_column(String(10), default="paper")  # only "paper" exists
    initial_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    realised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_charges: Mapped[float] = mapped_column(Float, default=0.0)
    gross_bought: Mapped[float] = mapped_column(Float, default=0.0)
    gross_sold: Mapped[float] = mapped_column(Float, default=0.0)
    buy_charges_total: Mapped[float] = mapped_column(Float, default=0.0)
    sell_charges_total: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float)
    last_processed: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(40), default="nse_archive")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TradeProposal(Base):
    """A proposed entry (manual now; Phase 5 strategies later). Evaluated by the risk engine after the close."""

    __tablename__ = "trade_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    symbol: Mapped[str] = mapped_column(String(40))
    strategy: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(30), default="manual")
    session_date: Mapped[date] = mapped_column(Date)
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | ordered | rejected | expired
    decision: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    symbol: Mapped[str] = mapped_column(String(40))
    sector: Mapped[str] = mapped_column(String(100))
    strategy: Mapped[str] = mapped_column(String(30))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("trade_proposals.id"))
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    qty: Mapped[int] = mapped_column(Integer)
    entry_qty: Mapped[int] = mapped_column(Integer)
    avg_price: Mapped[float] = mapped_column(Float)
    initial_stop: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    initial_risk: Mapped[float] = mapped_column(Float)
    buy_charges: Mapped[float] = mapped_column(Float, default=0.0)
    highest_close: Mapped[float] = mapped_column(Float)
    partial_done: Mapped[bool] = mapped_column(Boolean, default=False)
    mark: Mapped[float | None] = mapped_column(Float)
    realised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    entry_date: Mapped[date] = mapped_column(Date)
    closed_date: Mapped[date | None] = mapped_column(Date)


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("trade_proposals.id"))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    symbol: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(4))        # buy | sell
    kind: Mapped[str] = mapped_column(String(16))       # entry | stop | partial_target | time_stop | manual_exit
    qty: Mapped[int] = mapped_column(Integer)
    reference_price: Mapped[float | None] = mapped_column(Float)
    created_session: Mapped[date] = mapped_column(Date)
    expires_after: Mapped[int] = mapped_column(Integer, default=1)  # sessions with no bar before expiry
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | filled | cancelled | expired
    filled_session: Mapped[date | None] = mapped_column(Date)
    fill_price: Mapped[float | None] = mapped_column(Float)
    slippage_bps: Mapped[float | None] = mapped_column(Float)
    charges: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"))
    session_date: Mapped[date] = mapped_column(Date)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    invested: Mapped[float] = mapped_column(Float)
    unrealised_pnl: Mapped[float] = mapped_column(Float)
    realised_pnl: Mapped[float] = mapped_column(Float)
    charges: Mapped[float] = mapped_column(Float)
    peak_equity: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)
    open_positions: Mapped[int] = mapped_column(Integer)
    aggregate_initial_risk: Mapped[float] = mapped_column(Float)
    exposure: Mapped[dict] = mapped_column(JSONB, default=dict)
    reconciliation: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("account_id", "session_date", name="uq_portfolio_snapshot"),)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    session_date: Mapped[date] = mapped_column(Date)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("trade_proposals.id"))
    check: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(10))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TradeJournal(Base):
    """Audit trail: every state change with its reason and the data session it relied on."""

    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    session_date: Mapped[date] = mapped_column(Date)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"))
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    data_as_of: Mapped[date | None] = mapped_column(Date)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- Phase 5: strategy signals

class TradeSignal(Base):
    """One signal lifecycle for a stock (derived data: rebuilt deterministically from a fixed anchor)."""

    __tablename__ = "trade_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)       # strategy-independent: stock:opened
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    strategy: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))                   # early_warning | potential_setup | confirmed | invalidated | expired
    is_open: Mapped[bool] = mapped_column(Boolean)
    opened: Mapped[date] = mapped_column(Date)
    closed: Mapped[date | None] = mapped_column(Date)
    confirmed: Mapped[date | None] = mapped_column(Date, index=True)
    entry: Mapped[float | None] = mapped_column(Float)               # traded (raw) price of the decision session
    stop: Mapped[float | None] = mapped_column(Float)                # traded-price terms
    invalidation: Mapped[float | None] = mapped_column(Float)
    trigger: Mapped[float | None] = mapped_column(Float)
    reasons: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[str] = mapped_column(String(10))
    config_hash: Mapped[str] = mapped_column(String(16))

    __table_args__ = (Index("ix_signals_state_open", "state", "is_open"),)


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("trade_signals.id", ondelete="CASCADE"), index=True)
    session_date: Mapped[date] = mapped_column(Date)
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    values: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------- Phase 7: backtests

class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))          # single | walk_forward | sensitivity
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    capital: Mapped[float] = mapped_column(Float)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    equity_curve: Mapped[list] = mapped_column(JSONB, default=list)   # [[date, equity, drawdown_pct], ...]
    trades: Mapped[list] = mapped_column(JSONB, default=list)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)        # windows, selections, grid results
    data_as_of: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
