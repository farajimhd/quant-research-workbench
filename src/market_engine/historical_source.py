from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime
from typing import Any

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
        self.source_revision: dict[str, Any] | None = None

    async def health(self) -> dict[str, object]:
        import asyncio
        import urllib.request

        def read() -> dict[str, object]:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        return _validate_health(await asyncio.to_thread(read))

    async def stream(self, cursor: EventCursor | None = None):
        if cursor and cursor.token:
            raise ValueError("Reconnect historical events using a source-owned page boundary")
        page_cursor: dict[str, Any] | None = None
        pinned_revision: dict[str, Any] | None = None
        while True:
            payload = await asyncio.to_thread(
                self._read_page, page_cursor, pinned_revision
            )
            revision = _source_revision(payload)
            if pinned_revision is None:
                pinned_revision = revision
                self.source_revision = dict(revision)
            elif revision != pinned_revision:
                raise RuntimeError(
                    "QMD historical source revision changed during paged read; "
                    "restart from the first page"
                )
            events = [
                event_from_qmd_payload(dict(item))
                for item in payload.get("events") or []
            ]
            if events:
                yield _batch(events)
            if payload.get("complete") or not events:
                return
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, dict):
                raise RuntimeError("QMD historical event page omitted its continuation cursor")
            page_cursor = next_cursor

    def _read_page(
        self,
        cursor: dict[str, Any] | None,
        pinned_revision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "tickers": ",".join(self.tickers),
            "limit": self.batch_size,
        }
        if cursor:
            parameters.update(
                {
                    "cursor_sip_timestamp_us": int(cursor["sip_timestamp_us"]),
                    "cursor_ticker": str(cursor["ticker"]),
                    "cursor_ordinal": int(cursor["ordinal"]),
                }
            )
        if pinned_revision:
            parameters.update(
                {
                    "expected_source_plan_hash": str(
                        pinned_revision["source_plan_hash"]
                    ),
                    "expected_revision_token": str(
                        pinned_revision["revision_token"]
                    ),
                }
            )
        query = urllib.parse.urlencode(parameters)
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/snapshot/events?{query}",
                timeout=60,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(detail)
            except json.JSONDecodeError:
                error_payload = {"error": detail or str(exc)}
            raise RuntimeError(
                f"QMD historical event page failed ({exc.code}): "
                f"{error_payload.get('error') or str(exc)}"
            ) from exc
        if payload.get("error"):
            raise RuntimeError(
                f"QMD historical event page failed ({payload.get('source', 'unknown')}): {payload['error']}"
            )
        return payload


def _source_revision(payload: dict[str, Any]) -> dict[str, Any]:
    revision = payload.get("source_revision")
    if not isinstance(revision, dict):
        raise RuntimeError("QMD historical event page omitted source_revision")
    plan_hash = str(revision.get("source_plan_hash") or "").strip()
    revision_token = str(revision.get("token") or "").strip()
    if not plan_hash or not revision_token:
        raise RuntimeError(
            "QMD historical event page returned an incomplete source_revision"
        )
    return {
        "source_plan_hash": plan_hash,
        "revision_token": revision_token,
    }


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
