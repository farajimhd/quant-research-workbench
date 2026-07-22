from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import mergetree_settings


SCHEDULE_TABLE = "market_reference_source_schedule_v1"
SCHEDULE_QUERY_MAX_ATTEMPTS = 4
SCHEDULE_QUERY_RETRY_BASE_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class SourceScheduleDecision:
    source_name: str
    should_run: bool
    reason: str
    last_finished_at_utc: str
    next_due_at_utc: str
    frequency_seconds: int


def ensure_source_schedule_schema(client: ClickHouseHttpClient, *, database: str, storage_policy: str = "") -> None:
    execute_schedule_query(client, f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    settings = mergetree_settings(storage_policy)
    execute_schedule_query(
        client,
        f"""
CREATE TABLE IF NOT EXISTS {table(database, SCHEDULE_TABLE)}
(
    source_name LowCardinality(String),
    schedule_scope LowCardinality(String),
    frequency_seconds UInt64,
    last_started_at_utc Nullable(DateTime64(3, 'UTC')),
    last_finished_at_utc Nullable(DateTime64(3, 'UTC')),
    last_status LowCardinality(String),
    rows_written UInt64,
    details_json String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (source_name, schedule_scope)
SETTINGS {settings}
""".strip()
    )


def schedule_decision(
    client: ClickHouseHttpClient,
    config: ReferenceGatewayConfig,
    *,
    source_name: str,
    frequency_seconds: int,
    scope: str = "default",
    force: bool = False,
) -> SourceScheduleDecision:
    if force:
        return SourceScheduleDecision(source_name, True, "forced", "", "", max(0, int(frequency_seconds)))
    if frequency_seconds <= 0:
        return SourceScheduleDecision(source_name, True, "frequency_disabled", "", "", 0)
    if not schedule_table_exists(client, config.clickhouse_write_database):
        return SourceScheduleDecision(source_name, True, "schedule_table_missing", "", "", int(frequency_seconds))
    rows = query_json_each_row(
        client,
        f"""
        SELECT
            last_finished_at_utc,
            last_status,
            inserted_at
        FROM {table(config.clickhouse_write_database, SCHEDULE_TABLE)} FINAL
        WHERE source_name = {sql_string(source_name)}
          AND schedule_scope = {sql_string(scope)}
        ORDER BY inserted_at DESC
        LIMIT 1
        """,
    )
    if not rows:
        return SourceScheduleDecision(source_name, True, "no_previous_run", "", "", int(frequency_seconds))
    row = rows[0]
    last_text = str(row.get("last_finished_at_utc") or "")
    last_finished = parse_clickhouse_datetime(last_text)
    if last_finished is None:
        return SourceScheduleDecision(source_name, True, "previous_run_unfinished", last_text, "", int(frequency_seconds))
    next_due = last_finished + timedelta(seconds=int(frequency_seconds))
    now = datetime.now(UTC)
    if next_due <= now:
        return SourceScheduleDecision(source_name, True, "due", last_text, dt64(next_due), int(frequency_seconds))
    return SourceScheduleDecision(source_name, False, "not_due", last_text, dt64(next_due), int(frequency_seconds))


def record_source_schedule(
    client: ClickHouseHttpClient,
    config: ReferenceGatewayConfig,
    *,
    source_name: str,
    status: str,
    rows_written: int,
    details: dict[str, Any],
    source_run_id: str = "",
    scope: str = "default",
    frequency_seconds: int = 0,
    started_at_utc: datetime | None = None,
    finished_at_utc: datetime | None = None,
) -> None:
    now = datetime.now(UTC)
    row = {
        "source_name": source_name,
        "schedule_scope": scope,
        "frequency_seconds": max(0, int(frequency_seconds)),
        "last_started_at_utc": dt64(started_at_utc or now),
        "last_finished_at_utc": dt64(finished_at_utc or now),
        "last_status": status,
        "rows_written": max(0, int(rows_written)),
        "details_json": json.dumps(details, sort_keys=True, separators=(",", ":"), default=str),
        "source_run_id": source_run_id,
        "inserted_at": dt64(now),
    }
    body = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
    execute_schedule_query(
        client,
        f"INSERT INTO {table(config.clickhouse_write_database, SCHEDULE_TABLE)} FORMAT JSONEachRow\n{body}",
    )


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = execute_schedule_query(client, sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def execute_schedule_query(client: ClickHouseHttpClient, sql: str) -> str:
    """Retry schedule control-plane queries that are idempotent by contract."""
    for attempt in range(1, SCHEDULE_QUERY_MAX_ATTEMPTS + 1):
        try:
            return client.execute(sql)
        except (ConnectionError, TimeoutError, OSError):
            if attempt >= SCHEDULE_QUERY_MAX_ATTEMPTS:
                raise
            time.sleep(SCHEDULE_QUERY_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


def schedule_table_exists(client: ClickHouseHttpClient, database: str) -> bool:
    value = execute_schedule_query(
        client,
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(SCHEDULE_TABLE)} FORMAT TSV",
    ).strip()
    return int(value or "0") > 0


def parse_clickhouse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for suffix in ("Z", "+00:00"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = text.replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
