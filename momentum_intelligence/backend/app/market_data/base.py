"""Provider protocol, data frames and lineage.

Every frame a provider returns travels with a ``Lineage`` record: which source,
under which licence, when it was fetched, which session it describes and a
SHA-256 of the raw bytes. Nothing downstream may use a frame without lineage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Protocol, runtime_checkable

import pandas as pd

Licence = Literal["exchange_official", "licensed", "unofficial", "demo"]
Interval = Literal["1d", "60m", "15m"]

# Canonical daily-bar columns. Prices are raw (as traded) unless a frame says otherwise.
BAR_COLUMNS = [
    "symbol", "isin", "series", "session_date", "ts", "open", "high", "low", "close",
    "prev_close", "volume", "turnover_inr", "trades", "delivery_qty", "delivery_pct",
]


class ProviderError(RuntimeError):
    """A provider failed to return data."""


class ProviderUnavailable(ProviderError):
    """The provider cannot be used here (blocked host, missing credentials, not configured)."""


class DataNotPublished(ProviderError):
    """The requested file does not exist yet (holiday, weekend, or not yet published)."""


class DataMismatch(ProviderError):
    """A file was served but describes a different session than requested (e.g. a holiday placeholder)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Lineage:
    provider: str
    licence: Licence
    dataset: str
    source_url: str | None
    fetched_at: datetime
    as_of: date | None
    content_sha256: str | None
    from_cache: bool = False
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "licence": self.licence, "dataset": self.dataset,
            "source_url": self.source_url, "fetched_at": self.fetched_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "content_sha256": self.content_sha256, "from_cache": self.from_cache, "params": self.params,
        }


@dataclass
class BarFrame:
    """OHLCV bars plus where they came from. ``adjusted`` is False for exchange-raw prices."""

    data: pd.DataFrame
    lineage: list[Lineage]
    interval: Interval = "1d"
    adjusted: bool = False
    notes: list[str] = field(default_factory=list)  # non-fatal source issues, recorded by ingest

    @property
    def empty(self) -> bool:
        return self.data.empty


@dataclass(frozen=True)
class Security:
    symbol: str
    isin: str | None
    name: str | None
    series: str | None
    listing_date: date | None = None
    face_value: float | None = None
    industry: str | None = None


@dataclass(frozen=True)
class SymbolChange:
    old_symbol: str
    new_symbol: str
    effective: date
    company: str | None = None


@dataclass(frozen=True)
class CorporateActionEvent:
    symbol: str
    isin: str | None
    ex_date: date
    factor: float  # multiply prices before ex_date by this factor
    kind: str
    source: str
    confidence: Literal["high", "medium", "low"] = "high"
    details: dict = field(default_factory=dict)


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    licence: Licence

    def daily_bars(self, symbols: list[str] | None, start: date, end: date) -> BarFrame: ...

    def intraday_bars(self, symbols: list[str], interval: Interval, start: date, end: date) -> BarFrame: ...

    def index_bars(self, index: str, start: date, end: date) -> BarFrame: ...

    def security_master(self) -> list[Security]: ...
