from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import qi, sql_string


KIND_LIVE_FEED = "sec_live_feed"
KIND_DAILY_ARCHIVE = "sec_daily_archive"
KIND_BULK_SUBMISSIONS = "sec_bulk_submissions"
KIND_BULK_COMPANYFACTS = "sec_bulk_companyfacts"
KIND_TEXT_EXTRACTION = "sec_text_extraction"
KIND_INTEGRITY_AUDIT = "sec_integrity_audit"


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    coverage_kind: str
    start_utc: datetime
    end_utc: datetime
    status: str
    row_count: int


@dataclass(frozen=True, slots=True)
class SecGap:
    coverage_kind: str
    start_utc: datetime
    end_utc: datetime
    reason: str

    @property
    def days(self) -> float:
        return max(0.0, (self.end_utc - self.start_utc).total_seconds() / 86400.0)


@dataclass(frozen=True, slots=True)
class SecCoverageConfig:
    database: str
    coverage_table: str
    storage_policy: str = ""


def new_coverage_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def ensure_coverage_table(client: ClickHouseHttpClient, config: SecCoverageConfig) -> None:
    storage = f", storage_policy = {sql_string(config.storage_policy)}" if config.storage_policy else ""
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qi(config.database)}.{qi(config.coverage_table)}
        (
            coverage_id String,
            source LowCardinality(String),
            coverage_kind LowCardinality(String),
            coverage_start_utc DateTime64(3, 'UTC'),
            coverage_end_utc DateTime64(3, 'UTC'),
            status LowCardinality(String),
            row_count UInt64,
            file_count UInt64,
            error_count UInt64,
            run_id String,
            host_role LowCardinality(String),
            metadata_json String,
            started_at_utc DateTime64(3, 'UTC'),
            updated_at_utc DateTime64(3, 'UTC'),
            completed_at_utc Nullable(DateTime64(3, 'UTC'))
        )
        ENGINE = ReplacingMergeTree(updated_at_utc)
        PARTITION BY toYYYYMM(coverage_start_utc)
        ORDER BY (coverage_kind, coverage_start_utc, coverage_id)
        SETTINGS index_granularity = 8192{storage}
        """
    )


def insert_coverage(
    client: ClickHouseHttpClient,
    config: SecCoverageConfig,
    *,
    coverage_id: str,
    coverage_kind: str,
    start_utc: datetime,
    end_utc: datetime,
    status: str,
    row_count: int = 0,
    file_count: int = 0,
    error_count: int = 0,
    run_id: str = "",
    host_role: str = "",
    metadata: dict[str, Any] | None = None,
    completed: bool = False,
) -> None:
    now = datetime.now(UTC)
    row = {
        "coverage_id": coverage_id,
        "source": "sec",
        "coverage_kind": coverage_kind,
        "coverage_start_utc": dt_text(start_utc),
        "coverage_end_utc": dt_text(end_utc),
        "status": status,
        "row_count": int(row_count),
        "file_count": int(file_count),
        "error_count": int(error_count),
        "run_id": run_id,
        "host_role": host_role,
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"), default=str),
        "started_at_utc": dt_text(now),
        "updated_at_utc": dt_text(now),
        "completed_at_utc": dt_text(now) if completed else None,
    }
    client.execute(f"INSERT INTO {qi(config.database)}.{qi(config.coverage_table)} FORMAT JSONEachRow\n{json.dumps(row, default=str)}")


def load_intervals(client: ClickHouseHttpClient, config: SecCoverageConfig) -> list[CoverageInterval]:
    out = client.execute(
        f"""
        SELECT coverage_kind, coverage_start_utc, coverage_end_utc, status, row_count
        FROM {qi(config.database)}.{qi(config.coverage_table)} FINAL
        WHERE source = 'sec' AND status IN ('running', 'completed', 'covered_empty', 'coverage_bootstrap')
        ORDER BY coverage_kind, coverage_start_utc
        FORMAT JSONEachRow
        """
    )
    return [
        CoverageInterval(
            coverage_kind=row["coverage_kind"],
            start_utc=parse_dt(row["coverage_start_utc"]),
            end_utc=parse_dt(row["coverage_end_utc"]),
            status=row["status"],
            row_count=int(row.get("row_count") or 0),
        )
        for row in (json.loads(line) for line in out.splitlines() if line.strip())
    ]


def bootstrap_from_existing_tables(client: ClickHouseHttpClient, config: SecCoverageConfig, *, run_id: str, host_role: str) -> list[CoverageInterval]:
    if load_intervals(client, config):
        return []
    database = qi(config.database)
    specs = [
        (KIND_LIVE_FEED, f"SELECT min(accepted_at_utc), max(accepted_at_utc), count() FROM {database}.sec_filing_v2 FINAL"),
        (
            KIND_TEXT_EXTRACTION,
            f"SELECT min(toDateTime64(source_archive_date, 3, 'UTC')), max(toDateTime64(source_archive_date, 3, 'UTC')), count() FROM {database}.sec_filing_text_v2 FINAL",
        ),
        (KIND_BULK_COMPANYFACTS, f"SELECT min(filed_at_utc), max(filed_at_utc), count() FROM {database}.sec_xbrl_company_fact_v1 FINAL"),
        (KIND_BULK_SUBMISSIONS, "SELECT min(accepted_at_utc), max(accepted_at_utc), count() FROM sec_core.sec_bulk_mirror_filing_acceptance_v1 FINAL"),
    ]
    inserted: list[CoverageInterval] = []
    for kind, sql in specs:
        try:
            raw = client.execute(sql + " FORMAT TSV").strip().split("\t")
        except Exception:
            continue
        if len(raw) != 3 or not raw[0] or not raw[1] or raw[0].startswith("\\N"):
            continue
        start = parse_dt(raw[0])
        end = parse_dt(raw[1])
        rows = int(raw[2] or "0")
        coverage_id = new_coverage_id(f"{kind}_bootstrap")
        insert_coverage(
            client,
            config,
            coverage_id=coverage_id,
            coverage_kind=kind,
            start_utc=start,
            end_utc=end,
            status="coverage_bootstrap",
            row_count=rows,
            run_id=run_id,
            host_role=host_role,
            metadata={"bootstrap_source": "existing_tables"},
            completed=True,
        )
        inserted.append(CoverageInterval(kind, start, end, "coverage_bootstrap", rows))
    return inserted


def plan_freshness_gaps(client: ClickHouseHttpClient, *, database: str, now_utc: datetime) -> list[SecGap]:
    gaps: list[SecGap] = []
    latest_filing = scalar_dt(client, f"SELECT max(accepted_at_utc) FROM {qi(database)}.sec_filing_v2 FINAL")
    latest_text = scalar_dt(client, f"SELECT max(toDateTime64(source_archive_date, 3, 'UTC')) FROM {qi(database)}.sec_filing_text_v2 FINAL")
    latest_xbrl = scalar_dt(client, f"SELECT max(filed_at_utc) FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL")
    if latest_filing and now_utc - latest_filing > timedelta(hours=12):
        gaps.append(SecGap(KIND_LIVE_FEED, latest_filing, now_utc, "latest filing parent is stale"))
    if latest_text and now_utc - latest_text > timedelta(days=2):
        gaps.append(SecGap(KIND_TEXT_EXTRACTION, latest_text, now_utc, "latest filing text archive date is stale"))
    if latest_xbrl and now_utc - latest_xbrl > timedelta(days=2):
        gaps.append(SecGap(KIND_BULK_COMPANYFACTS, latest_xbrl, now_utc, "latest XBRL companyfacts filed date is stale"))
    return gaps


def scalar_dt(client: ClickHouseHttpClient, sql: str) -> datetime | None:
    out = client.execute(sql + " FORMAT TSV").strip()
    if not out or out == "\\N":
        return None
    return parse_dt(out)


def parse_dt(value: str) -> datetime:
    text = value.replace("Z", "").replace("T", " ")
    if "." in text:
        fmt = "%Y-%m-%d %H:%M:%S.%f"
    elif len(text.strip()) == 10:
        fmt = "%Y-%m-%d"
    else:
        fmt = "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(text[:26], fmt).replace(tzinfo=UTC)


def dt_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
