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


@dataclass(frozen=True, slots=True)
class CoverageBootstrapSummary:
    status: str
    executed: bool
    chunk_seconds: int
    normalized_rows: int = 0
    source_start_utc: datetime | None = None
    source_end_utc: datetime | None = None
    expected_buckets: int = 0
    non_empty_buckets: int = 0
    covered_intervals: int = 0
    discovered_gap_intervals: int = 0
    discovered_gap_seconds: float = 0.0
    discovered_gap_unique_days: int = 0
    superseded_existing_bootstrap: bool = False


@dataclass(frozen=True, slots=True)
class BucketCount:
    start_utc: datetime
    end_utc: datetime
    rows: int


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


def bootstrap_coverage_from_normalized_table(
    client: ClickHouseHttpClient,
    config: CoverageManifestConfig,
    *,
    chunk_seconds: int = 3600,
    force_rebuild: bool = False,
) -> CoverageBootstrapSummary:
    seconds = max(1, int(chunk_seconds))
    row_count = coverage_row_count(client, config)
    existing_bootstrap = load_existing_bootstrap_intervals(client, config) if row_count > 0 else []
    if row_count > 0 and not force_rebuild:
        existing_chunk_seconds = load_existing_bootstrap_chunk_seconds(client, config) if existing_bootstrap else None
        if not existing_bootstrap or existing_chunk_seconds == seconds:
            return CoverageBootstrapSummary(status="already_bootstrapped", executed=False, chunk_seconds=seconds)
    sql = (
        "SELECT min(published_at_utc) AS start_utc, max(published_at_utc) AS end_utc, count() AS rows "
        f"FROM {table_name(config.database, config.normalized_table)} FORMAT JSONEachRow"
    )
    row = first_json_row(client.execute(sql))
    if not row or not row.get("start_utc") or not row.get("end_utc") or int(row.get("rows") or 0) == 0:
        return CoverageBootstrapSummary(status="empty_normalized_table", executed=False, chunk_seconds=seconds)
    source_start = parse_clickhouse_datetime(str(row["start_utc"]))
    source_end = parse_clickhouse_datetime(str(row["end_utc"]))
    normalized_rows = int(row.get("rows") or 0)
    bucket_start = floor_time(source_start, seconds)
    bucket_end = ceil_time(source_end, seconds)
    buckets = load_non_empty_bucket_counts(client, config, bucket_start, bucket_end, seconds)
    bucket_map = {bucket.start_utc: bucket for bucket in buckets}
    expected_buckets = int(max(0, (bucket_end - bucket_start).total_seconds()) // seconds)
    covered_runs: list[list[BucketCount]] = []
    gap_intervals: list[CoverageGap] = []
    current_run: list[BucketCount] = []
    gap_start: datetime | None = None
    cursor = bucket_start
    while cursor < bucket_end:
        bucket = bucket_map.get(cursor)
        next_cursor = cursor + timedelta(seconds=seconds)
        if bucket and bucket.rows > 0:
            if gap_start is not None:
                gap_intervals.append(CoverageGap(gap_start, cursor))
                gap_start = None
            current_run.append(bucket)
        else:
            if current_run:
                covered_runs.append(current_run)
                current_run = []
            if gap_start is None:
                gap_start = cursor
        cursor = next_cursor
    if current_run:
        covered_runs.append(current_run)
    if gap_start is not None:
        gap_intervals.append(CoverageGap(gap_start, bucket_end))
    snapshots = coverage_snapshots_from_bucket_runs(covered_runs, seconds, config)
    if existing_bootstrap:
        snapshots = [supersede_bootstrap_snapshot(interval) for interval in existing_bootstrap] + snapshots
    insert_coverage_snapshots(client, config, snapshots)
    return CoverageBootstrapSummary(
        status="bootstrapped",
        executed=True,
        chunk_seconds=seconds,
        normalized_rows=normalized_rows,
        source_start_utc=source_start,
        source_end_utc=source_end,
        expected_buckets=expected_buckets,
        non_empty_buckets=len(buckets),
        covered_intervals=len(covered_runs),
        discovered_gap_intervals=len(gap_intervals),
        discovered_gap_seconds=sum(gap.seconds for gap in gap_intervals),
        discovered_gap_unique_days=count_unique_utc_days(gap_intervals),
        superseded_existing_bootstrap=bool(existing_bootstrap),
    )


def coverage_row_count(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> int:
    text = client.execute(f"SELECT count() FROM {table_name(config.database, config.coverage_table)} FINAL")
    return int((text.strip() or "0").splitlines()[0])


def load_existing_bootstrap_intervals(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> list[CoverageInterval]:
    sql = (
        "SELECT coverage_id, source, status, coverage_start_utc, coverage_end_utc "
        f"FROM {table_name(config.database, config.coverage_table)} FINAL "
        "WHERE (source = 'bootstrap_existing_news_rows' "
        "OR coverage_id = 'bootstrap_existing_normalized_table') "
        "AND status IN ('running', 'completed') "
        "ORDER BY coverage_start_utc, coverage_id FORMAT JSONEachRow"
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


def load_existing_bootstrap_chunk_seconds(client: ClickHouseHttpClient, config: CoverageManifestConfig) -> int | None:
    sql = (
        "SELECT metadata_json "
        f"FROM {table_name(config.database, config.coverage_table)} FINAL "
        "WHERE source = 'bootstrap_existing_news_rows' "
        "AND status IN ('running', 'completed') "
        "ORDER BY updated_at_utc DESC LIMIT 1 FORMAT JSONEachRow"
    )
    row = first_json_row(client.execute(sql))
    if not row:
        return None
    try:
        metadata = json.loads(str(row.get("metadata_json") or "{}"))
        value = metadata.get("chunk_seconds")
        return int(value) if value is not None else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def load_non_empty_bucket_counts(
    client: ClickHouseHttpClient,
    config: CoverageManifestConfig,
    start_utc: datetime,
    end_utc: datetime,
    chunk_seconds: int,
) -> list[BucketCount]:
    sql = (
        "SELECT "
        f"toStartOfInterval(published_at_utc, INTERVAL {int(chunk_seconds)} SECOND) AS bucket_start, "
        "count() AS rows "
        f"FROM {table_name(config.database, config.normalized_table)} "
        f"WHERE published_at_utc >= {sql_string(clickhouse_datetime64(start_utc))} "
        f"AND published_at_utc < {sql_string(clickhouse_datetime64(end_utc))} "
        "GROUP BY bucket_start ORDER BY bucket_start FORMAT JSONEachRow"
    )
    buckets: list[BucketCount] = []
    for line in client.execute(sql).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        start = parse_clickhouse_datetime(str(row["bucket_start"]))
        buckets.append(BucketCount(start_utc=start, end_utc=start + timedelta(seconds=chunk_seconds), rows=int(row.get("rows") or 0)))
    return buckets


def coverage_snapshots_from_bucket_runs(runs: list[list[BucketCount]], chunk_seconds: int, config: CoverageManifestConfig) -> list[CoverageSnapshot]:
    now = datetime.now(UTC)
    snapshots: list[CoverageSnapshot] = []
    for index, run in enumerate(runs, start=1):
        if not run:
            continue
        start = run[0].start_utc
        end = run[-1].end_utc
        rows = sum(bucket.rows for bucket in run)
        snapshots.append(
            CoverageSnapshot(
                coverage_id=f"bootstrap_existing_news_rows_{index:08d}_{filename_time(start)}_{filename_time(end)}",
                run_id="bootstrap_existing_bucketed_news_rows",
                source="bootstrap_existing_news_rows",
                status="completed",
                coverage_start_utc=start,
                coverage_end_utc=end,
                started_at_utc=now,
                updated_at_utc=now,
                closed_at_utc=now,
                provider_rows=rows,
                processed_rows=rows,
                written_rows=0,
                metadata={
                    "source_table": f"{config.database}.{config.normalized_table}",
                    "bootstrap_mode": "bucketed_existing_news_rows",
                    "chunk_seconds": chunk_seconds,
                    "bucket_count": len(run),
                },
            )
        )
    return snapshots


def supersede_bootstrap_snapshot(interval: CoverageInterval) -> CoverageSnapshot:
    now = datetime.now(UTC)
    return CoverageSnapshot(
        coverage_id=interval.coverage_id,
        run_id="bootstrap_existing_normalized_table",
        source=interval.source,
        status="superseded",
        coverage_start_utc=interval.start_utc,
        coverage_end_utc=interval.end_utc,
        started_at_utc=now,
        updated_at_utc=now,
        closed_at_utc=now,
        metadata={"superseded_by": "bucketed_existing_news_rows"},
    )


def insert_coverage_snapshot(client: ClickHouseHttpClient, config: CoverageManifestConfig, snapshot: CoverageSnapshot) -> None:
    insert_coverage_snapshots(client, config, [snapshot])


def insert_coverage_snapshots(client: ClickHouseHttpClient, config: CoverageManifestConfig, snapshots: list[CoverageSnapshot]) -> None:
    if not snapshots:
        return
    columns = ", ".join(quote_ident(column) for column in COVERAGE_COLUMNS)
    for partition_snapshots in group_snapshots_by_partition(snapshots).values():
        rows = "\n".join(
            json.dumps(coverage_row(snapshot), ensure_ascii=False, separators=(",", ":"), default=str)
            for snapshot in partition_snapshots
        )
        client.execute(f"INSERT INTO {table_name(config.database, config.coverage_table)} ({columns}) FORMAT JSONEachRow\n{rows}")


def group_snapshots_by_partition(snapshots: list[CoverageSnapshot]) -> dict[str, list[CoverageSnapshot]]:
    grouped: dict[str, list[CoverageSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(coverage_partition_key(snapshot), []).append(snapshot)
    return dict(sorted(grouped.items()))


def coverage_partition_key(snapshot: CoverageSnapshot) -> str:
    return snapshot.coverage_start_utc.astimezone(UTC).strftime("%Y%m")


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
    output = [gap for gap in gaps if gap.seconds > max(0, merge_tolerance_seconds)]
    if end_utc > cursor:
        trailing = CoverageGap(cursor, end_utc)
        if trailing.seconds > max(0, trailing_live_lookback_seconds):
            output.append(trailing)
    return output


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


def count_unique_utc_days(gaps: list[CoverageGap]) -> int:
    days: set[str] = set()
    for gap in gaps:
        if gap.end_utc <= gap.start_utc:
            continue
        cursor = gap.start_utc.astimezone(UTC).date()
        end_day = (gap.end_utc - timedelta(microseconds=1)).astimezone(UTC).date()
        while cursor <= end_day:
            days.add(cursor.isoformat())
            cursor += timedelta(days=1)
    return len(days)


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


def floor_time(value: datetime, seconds: int) -> datetime:
    epoch = int(value.astimezone(UTC).timestamp())
    floored = epoch - (epoch % max(1, seconds))
    return datetime.fromtimestamp(floored, tz=UTC)


def ceil_time(value: datetime, seconds: int) -> datetime:
    epoch = int(value.astimezone(UTC).timestamp())
    size = max(1, seconds)
    ceiled = epoch if epoch % size == 0 else epoch + (size - (epoch % size))
    return datetime.fromtimestamp(ceiled, tz=UTC)


def filename_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
