from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pipelines.news.benzinga.core.clickhouse_writer import DEFAULT_COVERAGE_TABLE, DEFAULT_DATABASE, DEFAULT_NORMALIZED_TABLE, table_name
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


COVERAGE_COLUMNS = [
    "coverage_id",
    "run_id",
    "source",
    "status",
    "coverage_start_utc",
    "coverage_end_utc",
    "started_at_utc",
    "updated_at_utc",
    "closed_at_utc",
    "poll_runs",
    "provider_rows",
    "processed_rows",
    "written_rows",
    "failed_rows",
    "skipped_existing",
    "last_error",
    "metadata_json",
]


@dataclass(frozen=True, slots=True)
class CoverageManifestConfig:
    database: str = DEFAULT_DATABASE
    coverage_table: str = DEFAULT_COVERAGE_TABLE
    normalized_table: str = DEFAULT_NORMALIZED_TABLE
    storage_policy: str = ""

    @classmethod
    def from_env(cls) -> "CoverageManifestConfig":
        return cls(
            database=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE,
            coverage_table=os.environ.get("NEWS_BENZINGA_COVERAGE_TABLE") or DEFAULT_COVERAGE_TABLE,
            normalized_table=os.environ.get("NEWS_BENZINGA_NORMALIZED_TABLE") or DEFAULT_NORMALIZED_TABLE,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    coverage_id: str
    source: str
    status: str
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class CoverageGap:
    start_utc: datetime
    end_utc: datetime

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end_utc - self.start_utc).total_seconds())


@dataclass(slots=True)
class CoverageSnapshot:
    coverage_id: str
    run_id: str
    source: str
    status: str
    coverage_start_utc: datetime
    coverage_end_utc: datetime
    started_at_utc: datetime
    updated_at_utc: datetime
    closed_at_utc: datetime | None = None
    poll_runs: int = 0
    provider_rows: int = 0
    processed_rows: int = 0
    written_rows: int = 0
    failed_rows: int = 0
    skipped_existing: int = 0
    last_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def new_run_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def ensure_coverage_manifest_table(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> None:
    settings = ["index_granularity = 8192"]
    if config.storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(config.storage_policy.strip())}")
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table_name(config.database, config.coverage_table)}
(
    coverage_id String,
    run_id String,
    source LowCardinality(String),
    status LowCardinality(String),
    coverage_start_utc DateTime64(9, 'UTC'),
    coverage_end_utc DateTime64(9, 'UTC'),
    started_at_utc DateTime64(9, 'UTC'),
    updated_at_utc DateTime64(9, 'UTC'),
    closed_at_utc Nullable(DateTime64(9, 'UTC')),
    poll_runs UInt64,
    provider_rows UInt64,
    processed_rows UInt64,
    written_rows UInt64,
    failed_rows UInt64,
    skipped_existing UInt64,
    last_error String,
    metadata_json String
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(coverage_start_utc)
ORDER BY (source, coverage_start_utc, coverage_id)
SETTINGS {", ".join(settings)}
""".strip()
    )


def bootstrap_coverage_from_normalized_table(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> bool:
    if coverage_row_count(client, config) > 0:
        return False
    sql = (
        "SELECT min(published_at_utc) AS start_utc, max(published_at_utc) AS end_utc, count() AS rows "
        f"FROM {table_name(config.database, config.normalized_table)} FORMAT JSONEachRow"
    )
    row = first_json_row(client.execute(sql))
    if not row or not row.get("start_utc") or not row.get("end_utc") or int(row.get("rows") or 0) == 0:
        return False
    now = datetime.now(UTC)
    snapshot = CoverageSnapshot(
        coverage_id="bootstrap_existing_normalized_table",
        run_id="bootstrap_existing_normalized_table",
        source="bootstrap_existing_news_rows",
        status="completed",
        coverage_start_utc=parse_clickhouse_datetime(str(row["start_utc"])),
        coverage_end_utc=parse_clickhouse_datetime(str(row["end_utc"])),
        started_at_utc=now,
        updated_at_utc=now,
        closed_at_utc=now,
        provider_rows=int(row.get("rows") or 0),
        processed_rows=int(row.get("rows") or 0),
        written_rows=0,
        metadata={"source_table": f"{config.database}.{config.normalized_table}", "bootstrap_mode": "min_max_existing_rows"},
    )
    insert_coverage_snapshot(client, config, snapshot)
    return True


def coverage_row_count(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> int:
    text = client.execute(f"SELECT count() FROM {table_name(config.database, config.coverage_table)} FINAL")
    return int((text.strip() or "0").splitlines()[0])


def insert_coverage_snapshot(client: ClickHouseHttpClient, config: CoverageManifestConfig, snapshot: CoverageSnapshot) -> None:
    row = coverage_row(snapshot)
    body = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str)
    columns = ", ".join(quote_ident(column) for column in COVERAGE_COLUMNS)
    client.execute(f"INSERT INTO {table_name(config.database, config.coverage_table)} ({columns}) FORMAT JSONEachRow\n{body}")


def insert_coverage_snapshots(client: ClickHouseHttpClient, config: CoverageManifestConfig, snapshots: list[CoverageSnapshot]) -> None:
    if not snapshots:
        return
    columns = ", ".join(quote_ident(column) for column in COVERAGE_COLUMNS)
    rows = "\n".join(json.dumps(coverage_row(snapshot), ensure_ascii=False, separators=(",", ":"), default=str) for snapshot in snapshots)
    client.execute(f"INSERT INTO {table_name(config.database, config.coverage_table)} ({columns}) FORMAT JSONEachRow\n{rows}")


def load_coverage_intervals(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> list[CoverageInterval]:
    sql = (
        "SELECT coverage_id, source, status, coverage_start_utc, coverage_end_utc "
        f"FROM {table_name(config.database, config.coverage_table)} FINAL "
        "WHERE status IN ('running', 'completed') "
        "AND coverage_end_utc >= coverage_start_utc "
        "ORDER BY coverage_start_utc, coverage_end_utc FORMAT JSONEachRow"
    )
    intervals: list[CoverageInterval] = []
    for line in client.execute(sql).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        intervals.append(
            CoverageInterval(
                coverage_id=str(row.get("coverage_id") or ""),
                source=str(row.get("source") or ""),
                status=str(row.get("status") or ""),
                start_utc=parse_clickhouse_datetime(str(row["coverage_start_utc"])),
                end_utc=parse_clickhouse_datetime(str(row["coverage_end_utc"])),
            )
        )
    return intervals


def find_coverage_gaps(
    intervals: list[CoverageInterval],
    *,
    end_utc: datetime,
    merge_tolerance_seconds: int,
    trailing_live_lookback_seconds: int,
) -> list[CoverageGap]:
    merged = merge_intervals(intervals, tolerance=timedelta(seconds=max(0, merge_tolerance_seconds)))
    if not merged:
        return []
    gaps: list[CoverageGap] = []
    cursor = merged[0].end_utc
    for interval in merged[1:]:
        if interval.start_utc > cursor:
            gaps.append(CoverageGap(cursor, interval.start_utc))
        if interval.end_utc > cursor:
            cursor = interval.end_utc
    if end_utc > cursor:
        trailing = CoverageGap(cursor, end_utc)
        if trailing.seconds > max(0, trailing_live_lookback_seconds):
            gaps.append(trailing)
    return [gap for gap in gaps if gap.seconds > max(0, merge_tolerance_seconds)]


def merge_intervals(intervals: list[CoverageInterval], *, tolerance: timedelta) -> list[CoverageInterval]:
    ordered = sorted(intervals, key=lambda item: (item.start_utc, item.end_utc))
    merged: list[CoverageInterval] = []
    for interval in ordered:
        if interval.end_utc < interval.start_utc:
            continue
        if not merged:
            merged.append(interval)
            continue
        previous = merged[-1]
        if interval.start_utc <= previous.end_utc + tolerance:
            merged[-1] = CoverageInterval(
                coverage_id=previous.coverage_id,
                source=previous.source,
                status=previous.status,
                start_utc=previous.start_utc,
                end_utc=max(previous.end_utc, interval.end_utc),
            )
        else:
            merged.append(interval)
    return merged


def coverage_row(snapshot: CoverageSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    metadata = payload.pop("metadata")
    payload["metadata_json"] = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, default=str)
    for column in ["coverage_start_utc", "coverage_end_utc", "started_at_utc", "updated_at_utc", "closed_at_utc"]:
        if payload.get(column) is not None:
            payload[column] = clickhouse_datetime64(payload[column])
    return {column: payload.get(column) for column in COVERAGE_COLUMNS}


def first_json_row(text: str) -> dict[str, Any] | None:
    for line in text.splitlines():
        if line.strip():
            return json.loads(line)
    return None


def parse_clickhouse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T") + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clickhouse_datetime64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
