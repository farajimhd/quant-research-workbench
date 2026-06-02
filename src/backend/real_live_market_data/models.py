from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def millis_to_utc(value: Any) -> datetime:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return utc_now()
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


@dataclass(slots=True)
class UniverseRecord:
    ticker: str
    conid: int
    avg_daily_volume: float = 0.0
    currency: str = "USD"
    float_shares: float = 0.0
    last_price: float = 0.0
    primary_exchange: str = ""
    sec_type: str = "STK"
    short_interest: float = 0.0
    short_interest_date: str = ""
    short_volume: float = 0.0
    short_volume_date: str = ""


@dataclass(slots=True)
class TradeEvent:
    conditions: list[int]
    exchange: int
    ingest_ts: datetime
    participant_ts: datetime
    price: float
    raw: dict[str, Any]
    seq: int
    size: float
    sym: str
    tape: int
    trade_id: str
    trf_id: int
    trf_ts: datetime
    ts: datetime


@dataclass(slots=True)
class QuoteEvent:
    ask_exchange: int
    ask_price: float
    ask_size: int
    bid_exchange: int
    bid_price: float
    bid_size: int
    conditions: list[int]
    indicators: list[int]
    ingest_ts: datetime
    raw: dict[str, Any]
    seq: int
    sym: str
    tape: int
    ts: datetime


@dataclass(slots=True)
class BarState:
    close: float = 0.0
    dollar_volume: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    trade_count: int = 0
    volume: float = 0.0

    def update(self, price: float, size: float) -> None:
        if price <= 0:
            return
        if self.open <= 0:
            self.open = price
            self.high = price
            self.low = price
        self.close = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.volume += max(0.0, size)
        self.dollar_volume += max(0.0, size) * price
        self.trade_count += 1

    @property
    def vwap(self) -> float:
        return self.dollar_volume / self.volume if self.volume > 0 else 0.0


@dataclass(slots=True)
class SymbolState:
    bar_1m: BarState = field(default_factory=BarState)
    buy_notional_10s: float = 0.0
    day_dollar_volume: float = 0.0
    day_trade_count: int = 0
    day_volume: float = 0.0
    last_price: float = 0.0
    last_quote: QuoteEvent | None = None
    last_trade: TradeEvent | None = None
    market_state: str = "watch"
    recent_trades: deque[TradeEvent] = field(default_factory=lambda: deque(maxlen=800))
    sell_notional_10s: float = 0.0
    signal_last_ts: datetime | None = None
