from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from typing import Any

import websockets

from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
from src.market_engine.sources import EventBatch, EventCursor


class QmdHistoricalEventSource:
    """Python consumer for the Rust QMD historical event stream."""

    def __init__(
        self,
        base_url: str,
        *,
        start: datetime,
        end: datetime,
        tickers: list[str] | None = None,
        batch_size: int = 10_000,
    ) -> None:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Historical event boundaries must be timezone-aware")
        if end <= start:
            raise ValueError("end must be later than start")
        if not 1 <= batch_size <= 100_000:
            raise ValueError("batch_size must be between 1 and 100000")
        self.base_url = base_url.rstrip("/")
        self.start = start
        self.end = end
        self.tickers = list(tickers or [])
        self.batch_size = batch_size

    async def health(self) -> dict[str, object]:
        import asyncio
        import urllib.request

        def read() -> dict[str, object]:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        return _validate_health(await asyncio.to_thread(read))

    async def stream(self, cursor: EventCursor | None = None):
        if cursor and cursor.token:
            raise ValueError("The Rust gateway owns historical cursor pagination; reconnect using the run checkpoint window")
        query = urllib.parse.urlencode(
            {
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "tickers": ",".join(self.tickers),
                "batch_size": self.batch_size,
            }
        )
        events: list[MarketEvent] = []
        async with websockets.connect(f"{_websocket_base(self.base_url)}/stream/events?{query}", max_size=16 * 1024 * 1024) as socket:
            async for message in socket:
                event = event_from_qmd_payload(json.loads(message))
                events.append(event)
                if len(events) >= self.batch_size:
                    yield _batch(events)
                    events = []
        if events:
            yield _batch(events)


def event_from_qmd_payload(payload: dict[str, Any]) -> MarketEvent:
    if payload.get("error"):
        raise RuntimeError(
            f"QMD historical stream failed ({payload.get('source', 'unknown')}): {payload['error']}"
        )
    kind = str(payload.get("kind") or "").lower()
    common = {
        "conditions": tuple(int(value) for value in payload.get("conditions") or []),
        "ingest_ts": _timestamp(payload.get("ingest_ts")),
        "raw": dict(payload.get("raw") or {}),
        "sequence": int(payload.get("sequence") or 0),
        "source": "qmd_history_gateway",
        "tape": int(payload.get("tape") or 0),
        "ticker": str(payload.get("ticker") or "").upper(),
        "ts": _timestamp(payload.get("ts")),
    }
    if kind == "trade":
        return TradeEvent(
            event_id=str(payload.get("trade_id") or f"compact-{common['sequence']}"),
            exchange=int(payload.get("exchange") or 0),
            participant_ts=_optional_timestamp(payload.get("participant_ts")),
            price=float(payload.get("price") or 0),
            size=float(payload.get("size") or 0),
            trf_id=int(payload.get("trf_id") or 0),
            trf_ts=_optional_timestamp(payload.get("trf_ts")),
            **common,
        )
    if kind == "quote":
        return QuoteEvent(
            ask_exchange=int(payload.get("ask_exchange") or 0),
            ask_price=float(payload.get("ask_price") or 0),
            ask_size=float(payload.get("ask_size") or 0),
            bid_exchange=int(payload.get("bid_exchange") or 0),
            bid_price=float(payload.get("bid_price") or 0),
            bid_size=float(payload.get("bid_size") or 0),
            indicators=tuple(int(value) for value in payload.get("indicators") or []),
            **common,
        )
    raise ValueError(f"Unsupported QMD market event kind: {kind or '<missing>'}")


def _batch(events: list[MarketEvent]) -> EventBatch:
    last = events[-1]
    return EventBatch(
        cursor=EventCursor(source="qmd_history_gateway", token=f"{last.ts.isoformat()}|{last.sequence}|{last.kind}", ts=last.ts),
        events=events,
    )


def _websocket_base(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _validate_health(payload: dict[str, object]) -> dict[str, object]:
    if payload.get("service") != "qmd_history_gateway" or payload.get("host_role") != "historical":
        raise RuntimeError("Configured historical gateway URL returned a different service")
    if payload.get("status") != "ready" or payload.get("running") is not True:
        raise RuntimeError(f"QMD historical gateway is not ready: {payload}")
    return payload


def _timestamp(value: Any) -> datetime:
    if not value:
        raise ValueError("QMD event timestamp is required")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("QMD event timestamp must include a timezone")
    return parsed


def _optional_timestamp(value: Any) -> datetime | None:
    return _timestamp(value) if value else None
