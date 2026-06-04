from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


EventKind = Literal["trade", "quote"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Canonical tick trade event used by live, replay, and backtest paths."""

    conditions: tuple[int, ...]
    event_id: str
    exchange: int
    ingest_ts: datetime
    participant_ts: datetime | None
    price: float
    raw: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    size: float = 0.0
    source: str = "unknown"
    tape: int = 0
    ticker: str = ""
    trf_id: int = 0
    trf_ts: datetime | None = None
    ts: datetime = field(default_factory=utc_now)

    @property
    def kind(self) -> EventKind:
        return "trade"


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    """Canonical NBBO quote event used by live, replay, and backtest paths."""

    ask_exchange: int
    ask_price: float
    ask_size: float
    bid_exchange: int
    bid_price: float
    bid_size: float
    conditions: tuple[int, ...]
    indicators: tuple[int, ...]
    ingest_ts: datetime
    raw: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    source: str = "unknown"
    tape: int = 0
    ticker: str = ""
    ts: datetime = field(default_factory=utc_now)

    @property
    def kind(self) -> EventKind:
        return "quote"

    @property
    def midpoint(self) -> float:
        if self.bid_price <= 0 or self.ask_price <= 0:
            return 0.0
        return (self.bid_price + self.ask_price) / 2

    @property
    def spread(self) -> float:
        if self.bid_price <= 0 or self.ask_price <= 0:
            return 0.0
        return max(0.0, self.ask_price - self.bid_price)


MarketEvent = TradeEvent | QuoteEvent
