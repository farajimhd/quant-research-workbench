from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from market_ai.types import CompactEvent


def compact_event_from_mapping(payload: dict[str, Any]) -> CompactEvent | None:
    if "warning" in payload or "error" in payload:
        return None
    try:
        return CompactEvent(
            ticker=str(payload["ticker"]).upper(),
            sip_timestamp_us=int(payload["sip_timestamp_us"]),
            event_type=int(payload["event_type"]),
            price_primary_int=int(payload["price_primary_int"]),
            price_secondary_int=int(payload.get("price_secondary_int", 0)),
            size_primary=float(payload.get("size_primary", 0.0)),
            size_secondary=float(payload.get("size_secondary", 0.0)),
            exchange_primary=int(payload.get("exchange_primary", 0)),
            exchange_secondary=int(payload.get("exchange_secondary", 0)),
            condition_tokens_packed=int(payload.get("condition_tokens_packed", 0)),
            source_sequence=int(payload.get("source_sequence", 0)),
            arrival_sequence=int(payload.get("arrival_sequence", 0)),
            ordinal=int(payload["ordinal"]) if payload.get("ordinal") not in (None, "") else None,
            issue_flags=int(payload.get("issue_flags", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def iter_qmd_compact_events(url: str, *, stop_event: asyncio.Event) -> AsyncIterator[CompactEvent]:
    try:
        import websockets
    except Exception as error:  # pragma: no cover - environment dependent.
        raise RuntimeError("Install the 'websockets' package to consume qmd compact-event streams.") from error

    async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=4096) as websocket:
        while not stop_event.is_set():
            try:
                text = await asyncio.wait_for(websocket.recv(), timeout=1.0)
            except TimeoutError:
                continue
            if isinstance(text, bytes):
                text = text.decode("utf-8")
            payload = json.loads(text)
            if isinstance(payload, dict):
                event = compact_event_from_mapping(payload)
                if event is not None:
                    yield event


async def iter_synthetic_events(
    *,
    tickers: tuple[str, ...],
    events_per_second: float,
    max_events: int,
    stop_event: asyncio.Event,
) -> AsyncIterator[CompactEvent]:
    tickers = tickers or ("AAPL",)
    interval = 1.0 / max(1.0, float(events_per_second))
    emitted = 0
    ordinal_by_ticker = {ticker: 0 for ticker in tickers}
    while not stop_event.is_set() and (max_events <= 0 or emitted < max_events):
        ticker = tickers[emitted % len(tickers)]
        ordinal = ordinal_by_ticker[ticker]
        ordinal_by_ticker[ticker] += 1
        yield CompactEvent(
            ticker=ticker,
            sip_timestamp_us=1_800_000_000_000_000 + emitted,
            event_type=emitted % 2,
            price_primary_int=10_000 + (ordinal % 100),
            price_secondary_int=9_995 + (ordinal % 100),
            size_primary=100.0 + float(ordinal % 50),
            size_secondary=100.0,
            exchange_primary=1,
            exchange_secondary=2,
            condition_tokens_packed=0,
            source_sequence=ordinal,
            arrival_sequence=emitted,
            ordinal=ordinal,
        )
        emitted += 1
        if interval >= 0.001:
            await asyncio.sleep(interval)
        elif emitted % 1024 == 0:
            await asyncio.sleep(0)
