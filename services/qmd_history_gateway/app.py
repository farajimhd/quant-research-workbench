from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from services.qmd_history_gateway.store import HistoricalCursor, HistoricalEventStore, HistoricalStoreConfig, historical_to_live_compact, row_to_market_event
from src.market_engine.bars import BarSpec, TimeBarBuilder


def _config() -> HistoricalStoreConfig:
    return HistoricalStoreConfig(
        endpoint_url=os.environ.get("QMD_HISTORY_CLICKHOUSE_URL", os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")),
        database=os.environ.get("QMD_HISTORY_DATABASE", "market_sip_compact"),
        table_prefix=os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_"),
        user=os.environ.get("QMD_HISTORY_CLICKHOUSE_USER", os.environ.get("CLICKHOUSE_USER", "default")),
        password=os.environ.get("QMD_HISTORY_CLICKHOUSE_PASSWORD", os.environ.get("CLICKHOUSE_PASSWORD", "")),
    )


STORE = HistoricalEventStore(_config())
app = FastAPI(title="QMD Historical Gateway", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    result = await asyncio.to_thread(STORE.health)
    return {**result, "running": True, "host_role": "historical", "status": "ready" if result["ready"] else "blocked"}


@app.get("/config")
async def config() -> dict[str, Any]:
    value = asdict(STORE.config)
    value["password"] = "configured" if value["password"] else "missing"
    return value


@app.get("/snapshot/compact-events/{ticker}")
async def compact_event_snapshot(
    ticker: str,
    start: datetime,
    end: datetime,
    limit: int = Query(default=1000, ge=1, le=100_000),
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(STORE.compact_events, ticker=ticker, start=_aware(start), end=_aware(end), limit=limit)


@app.get("/snapshot/bars/{ticker}")
async def bar_snapshot(
    ticker: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    limit: int = Query(default=1000, ge=1, le=100_000),
) -> dict[str, Any]:
    rows, _ = await asyncio.to_thread(STORE.fetch_batch, start=_aware(start), end=_aware(end), tickers=[ticker], limit=limit)
    builder = TimeBarBuilder(BarSpec(timeframe=timeframe, source="qmd_history_gateway"))
    bars = []
    for row in rows:
        bars.extend(builder.update(row_to_market_event(row)))
    bars.extend(builder.snapshot())
    return {"ticker": ticker.upper(), "timeframe": timeframe, "bars": [asdict(bar) for bar in bars], "source": "historical_events"}


@app.websocket("/stream/compact-events")
async def compact_event_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        request = await websocket.receive_json()
        start = _aware(datetime.fromisoformat(str(request["start"]).replace("Z", "+00:00")))
        end = _aware(datetime.fromisoformat(str(request["end"]).replace("Z", "+00:00")))
        tickers = [str(value) for value in request.get("tickers", [])]
        batch_size = max(1, min(int(request.get("batch_size", 10_000)), 100_000))
        cursor = HistoricalCursor()
        while True:
            rows, next_cursor = await asyncio.to_thread(
                STORE.fetch_batch, start=start, end=end, tickers=tickers or None, cursor=cursor, limit=batch_size
            )
            for row in rows:
                await websocket.send_json(historical_to_live_compact(row))
            if not rows or next_cursor is None or len(rows) < batch_size:
                if next_cursor is not None:
                    cursor = next_cursor
                break
            cursor = next_cursor
        await websocket.send_json({"type": "end", "cursor": cursor.token()})
    except WebSocketDisconnect:
        return


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)
