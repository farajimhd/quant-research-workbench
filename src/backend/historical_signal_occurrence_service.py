from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)


MAX_HISTORICAL_SIGNAL_OCCURRENCES = 1_000_000
SUPPORTED_NATIVE_OCCURRENCE_SOURCES = {"qmd_squeeze_episode"}


def historical_source_native_signal_occurrences(
    stream: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    client: ClickHouseHttpClient | None = None,
) -> dict[str, Any]:
    """Load immutable QMD-owned occurrences at their original availability clocks."""

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Historical Signal Stream bounds must be timezone-aware")
    if end <= start:
        raise ValueError("Historical Signal Stream end must follow start")
    stream_id = str(stream.get("signal_stream_id") or "").strip()
    source = str(stream.get("occurrence_source") or "").strip()
    if not stream_id:
        raise ValueError("Historical source-native Signal Stream requires an id")
    if source not in SUPPORTED_NATIVE_OCCURRENCE_SOURCES:
        raise ValueError(
            f"Historical source-native Signal Stream source is unsupported: {source or 'missing'}"
        )

    _load_repository_env()
    active = client or ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=60,
    )
    database = os.environ.get("QMD_CLICKHOUSE_DATABASE", "q_live").strip() or "q_live"
    table = (
        os.environ.get("QMD_SIGNAL_STREAM_TABLE", "signal_stream_occurrence_v1").strip()
        or "signal_stream_occurrence_v1"
    )
    target = f"{quote_ident(database)}.{quote_ident(table)}"
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    where = (
        f"signal_stream_id={sql_string(stream_id)} "
        f"AND event_time>=parseDateTime64BestEffort({sql_string(start_utc.isoformat())},6,'UTC') "
        f"AND event_time<parseDateTime64BestEffort({sql_string(end_utc.isoformat())},6,'UTC')"
    )
    count_rows = _json_rows(
        active.execute(
            f"SELECT count() AS row_count FROM {target} FINAL WHERE {where} FORMAT JSONEachRow"
        )
    )
    row_count = int((count_rows[0] if count_rows else {}).get("row_count") or 0)
    if row_count > MAX_HISTORICAL_SIGNAL_OCCURRENCES:
        raise RuntimeError(
            "Historical Signal Stream occurrence count exceeds bounded loader capacity: "
            f"{row_count:,} > {MAX_HISTORICAL_SIGNAL_OCCURRENCES:,}"
        )
    rows = _json_rows(
        active.execute(
            "SELECT event_id,sequence,event_time,configuration_revision,definition_revision,"
            f"payload_json FROM {target} FINAL WHERE {where} "
            "ORDER BY event_time,sequence,event_id FORMAT JSONEachRow"
        )
    )
    if len(rows) != row_count:
        raise RuntimeError(
            f"Historical Signal Stream row count changed during read: {row_count} -> {len(rows)}"
        )

    occurrences: list[dict[str, Any]] = []
    hash_rows: list[dict[str, Any]] = []
    definition_revisions: set[str] = set()
    configuration_revisions: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Historical Signal Stream occurrence payload is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Historical Signal Stream occurrence payload must be an object")
        event_id = str(row.get("event_id") or payload.get("event_id") or "").strip()
        event_time = _clock(row.get("event_time"), "event_time")
        available_at = _clock(
            payload.get("available_at")
            or payload.get("effective_at")
            or payload.get("event_time")
            or event_time,
            "available_at",
        )
        if not event_id:
            raise RuntimeError("Historical Signal Stream occurrence omitted event_id")
        if available_at < event_time:
            raise RuntimeError(
                f"Historical Signal Stream occurrence {event_id} is available before its event clock"
            )
        if not (start_utc <= available_at < end_utc):
            raise RuntimeError(
                f"Historical Signal Stream occurrence {event_id} escaped the requested window"
            )
        payload["event_id"] = event_id
        payload["signal_stream_id"] = stream_id
        payload["event_time"] = event_time.isoformat()
        payload["effective_at"] = available_at.isoformat()
        payload["available_at"] = available_at.isoformat()
        occurrences.append(payload)
        definition_revisions.add(str(row.get("definition_revision") or ""))
        configuration_revisions.add(str(row.get("configuration_revision") or ""))
        hash_rows.append(
            {
                "event_id": event_id,
                "sequence": int(row.get("sequence") or 0),
                "event_time": event_time.isoformat(),
                "payload": payload,
            }
        )
    content_hash = hashlib.sha256(
        json.dumps(hash_rows, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "occurrences": occurrences,
        "authority": {
            "authority": "qmd_persisted_signal_stream_occurrences",
            "database": database,
            "table": table,
            "signal_stream_id": stream_id,
            "occurrence_source": source,
            "row_count": len(occurrences),
            "available_start": start_utc.isoformat(),
            "available_end": end_utc.isoformat(),
            "configuration_revisions": sorted(configuration_revisions),
            "definition_revisions": sorted(definition_revisions),
            "content_hash": content_hash,
        },
    }


def _clock(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(
                f"Historical Signal Stream occurrence has invalid {label}: {text or 'missing'}"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_rows(payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("ClickHouse JSONEachRow response must contain objects")
        rows.append(value)
    return rows


def _load_repository_env() -> None:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
