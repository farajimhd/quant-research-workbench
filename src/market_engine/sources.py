from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from src.market_engine.events import MarketEvent


@dataclass(frozen=True, slots=True)
class EventCursor:
    source: str
    token: str = ""
    ts: datetime | None = None


class MarketEventSource(Protocol):
    """Common source contract for live, historical, and replay events."""

    async def health(self) -> dict[str, object]:
        ...

    async def stream(self, cursor: EventCursor | None = None) -> AsyncIteratorLike:
        ...


@dataclass(slots=True)
class EventBatch:
    cursor: EventCursor
    events: list[MarketEvent] = field(default_factory=list)


class AsyncIteratorLike(Protocol):
    def __aiter__(self) -> "AsyncIteratorLike":
        ...

    async def __anext__(self) -> EventBatch:
        ...
