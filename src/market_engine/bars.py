from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from src.market_engine.events import MarketEvent, TradeEvent


@dataclass(frozen=True, slots=True)
class BarSpec:
    timeframe: str = "1m"
    source: str = "event_engine"

    @property
    def duration(self) -> timedelta:
        if self.timeframe.endswith("s"):
            return timedelta(seconds=int(self.timeframe[:-1]))
        if self.timeframe.endswith("m"):
            return timedelta(minutes=int(self.timeframe[:-1]))
        if self.timeframe.endswith("h"):
            return timedelta(hours=int(self.timeframe[:-1]))
        raise ValueError(f"Unsupported event-derived timeframe: {self.timeframe}")


@dataclass(frozen=True, slots=True)
class Bar:
    bar_end: datetime
    bar_start: datetime
    close: float
    dollar_volume: float
    high: float
    low: float
    open: float
    source: str
    ticker: str
    timeframe: str
    trade_count: int
    volume: float
    vwap: float


class BarBuilder(Protocol):
    def update(self, event: MarketEvent) -> list[Bar]:
        """Consume one event and return finalized bars."""

    def snapshot(self) -> list[Bar]:
        """Return currently open bars without finalizing them."""


@dataclass(slots=True)
class _MutableBar:
    bar_start: datetime
    dollar_volume: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    trade_count: int = 0
    volume: float = 0.0

    def update_trade(self, trade: TradeEvent) -> None:
        if trade.price <= 0:
            return
        if self.open <= 0:
            self.open = trade.price
            self.high = trade.price
            self.low = trade.price
        self.close = trade.price
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        size = max(0.0, trade.size)
        self.volume += size
        self.dollar_volume += size * trade.price
        self.trade_count += 1


class TimeBarBuilder:
    """Streaming time-bar builder from canonical trade events.

    Quotes update quote state elsewhere. Trade-derived bars are the canonical
    candle source for charts, scanner presets, replay, and event backtests.
    """

    def __init__(self, spec: BarSpec) -> None:
        self.spec = spec
        self._bars: dict[str, _MutableBar] = {}

    def update(self, event: MarketEvent) -> list[Bar]:
        if not isinstance(event, TradeEvent) or not event.ticker:
            return []
        start = floor_time(event.ts, self.spec.duration)
        current = self._bars.get(event.ticker)
        finalized: list[Bar] = []
        if current is not None and start > current.bar_start:
            finalized.append(self._freeze(event.ticker, current))
            current = None
        if current is None:
            current = _MutableBar(bar_start=start)
            self._bars[event.ticker] = current
        current.update_trade(event)
        return finalized

    def snapshot(self) -> list[Bar]:
        return [self._freeze(ticker, bar) for ticker, bar in sorted(self._bars.items())]

    def _freeze(self, ticker: str, bar: _MutableBar) -> Bar:
        duration = self.spec.duration
        vwap = bar.dollar_volume / bar.volume if bar.volume > 0 else 0.0
        return Bar(
            bar_end=bar.bar_start + duration,
            bar_start=bar.bar_start,
            close=bar.close,
            dollar_volume=bar.dollar_volume,
            high=bar.high,
            low=bar.low,
            open=bar.open,
            source=self.spec.source,
            ticker=ticker,
            timeframe=self.spec.timeframe,
            trade_count=bar.trade_count,
            volume=bar.volume,
            vwap=vwap,
        )


def floor_time(value: datetime, duration: timedelta) -> datetime:
    utc_value = value.astimezone(timezone.utc)
    seconds = int(duration.total_seconds())
    if seconds <= 0:
        raise ValueError("Bar duration must be positive.")
    epoch = int(utc_value.timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)
