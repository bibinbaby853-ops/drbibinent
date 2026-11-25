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
