from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


PUBLICATION_SOURCE_KINDS: tuple[str, ...] = (
    "finra_short_volume",
    "finra_short_interest",
    "sec_fails_to_deliver",
    "reg_sho_threshold",
    "ibkr_borrow_availability",
    "massive_market_snapshot",
    "massive_splits",
    "massive_dividends",
    "massive_ipos",
    "massive_presentation_assets",
    "sec_country_assertions",
)


@dataclass(frozen=True, slots=True)
class PublicationGap:
    coverage_kind: str
    start_date: date
    end_date: date
    missing_days: int
    source_system: str


def ensure_market_publication_schema(
    client: ClickHouseHttpClient,
    *,
    database: str,
    storage_policy: str = "",
) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {qn(database)}")
    settings = mergetree_settings(storage_policy)
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'market_reference_publication_coverage_v1')}
(
    coverage_id String,
    coverage_kind LowCardinality(String),
    source_system LowCardinality(String),
    source_object String,
    coverage_start_date Date,
    coverage_end_date Date,
    status LowCardinality(String),
    rows_read UInt64,
    rows_written UInt64,
    rows_failed UInt64,
    started_at_utc DateTime64(3, 'UTC'),
    finished_at_utc Nullable(DateTime64(3, 'UTC')),
    details_json String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(coverage_start_date)
ORDER BY (coverage_kind, source_system, coverage_start_date, coverage_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'market_fails_to_deliver_v1')}
(
    ftd_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    provider_ticker String,
    settlement_date Date,
    cusip Nullable(String),
    fails_quantity UInt64,
    issuer_name Nullable(String),
    previous_close_price Nullable(Float64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(settlement_date)
ORDER BY (provider_ticker, settlement_date, source_system, ftd_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'market_reg_sho_threshold_v1')}
(
    threshold_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    provider_ticker String,
    threshold_date Date,
    listing_exchange Nullable(String),
    threshold_status LowCardinality(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(threshold_date)
ORDER BY (provider_ticker, threshold_date, source_system, threshold_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'market_security_borrow_v1')}
(
    borrow_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    broker LowCardinality(String),
    provider_ticker String,
    ibkr_conid Nullable(String),
    observed_at_utc DateTime64(3, 'UTC'),
    borrow_status LowCardinality(String),
    shortable_shares Nullable(UInt64),
    lender_count Nullable(UInt32),
    indicative_borrow_rate Nullable(Float64),
    fee_rate Nullable(Float64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(observed_at_utc)
ORDER BY (provider_ticker, observed_at_utc, broker, borrow_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'market_security_country_v1')}
(
    country_assertion_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    issuer_id Nullable(String),
    provider_ticker Nullable(String),
    assertion_date Date,
    listing_country_code Nullable(String),
    issuer_legal_country_code Nullable(String),
    issuer_hq_country_code Nullable(String),
    security_issue_country_code Nullable(String),
    effective_country_code Nullable(String),
    confidence_score Float64,
    source_system LowCardinality(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(assertion_date)
ORDER BY (ifNull(symbol_id, ''), assertion_date, source_system, country_assertion_id)
SETTINGS {settings}
""".strip()
    )
    for table_name, statement in publication_alters(database):
        if not table_exists(client, database, table_name):
            continue
        client.execute(statement)


def publication_alters(database: str) -> list[tuple[str, str]]:
    return [
        ("market_short_interest_v1", f"ALTER TABLE {table(database, 'market_short_interest_v1')} ADD COLUMN IF NOT EXISTS publication_date Nullable(Date) AFTER settlement_date"),
        ("market_short_interest_v1", f"ALTER TABLE {table(database, 'market_short_interest_v1')} ADD COLUMN IF NOT EXISTS published_at_utc Nullable(DateTime64(3, 'UTC')) AFTER publication_date"),
        ("market_short_interest_v1", f"ALTER TABLE {table(database, 'market_short_interest_v1')} ADD COLUMN IF NOT EXISTS source_venue Nullable(String) AFTER source_system"),
        ("market_short_volume_v1", f"ALTER TABLE {table(database, 'market_short_volume_v1')} ADD COLUMN IF NOT EXISTS source_venue Nullable(String) AFTER source_system"),
        ("market_short_volume_v1", f"ALTER TABLE {table(database, 'market_short_volume_v1')} ADD COLUMN IF NOT EXISTS published_at_utc Nullable(DateTime64(3, 'UTC')) AFTER trade_date"),
        ("market_security_float_v1", f"ALTER TABLE {table(database, 'market_security_float_v1')} ADD COLUMN IF NOT EXISTS shares_outstanding Nullable(UInt64) AFTER free_float_percent"),
        ("market_security_float_v1", f"ALTER TABLE {table(database, 'market_security_float_v1')} ADD COLUMN IF NOT EXISTS float_source_tag LowCardinality(String) DEFAULT '' AFTER shares_outstanding"),
    ]


def market_publication_audit(client: ClickHouseHttpClient, *, database: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table_specs = {
        "market_security_market_snapshot_v1": "observed_at_utc",
        "market_security_float_v1": "effective_date",
        "market_short_interest_v1": "settlement_date",
        "market_short_volume_v1": "trade_date",
        "market_stock_split_v1": "execution_date",
        "market_cash_dividend_v1": "ex_dividend_date",
        "market_ipo_v1": "listing_date",
        "market_fails_to_deliver_v1": "settlement_date",
        "market_reg_sho_threshold_v1": "threshold_date",
        "market_security_borrow_v1": "observed_at_utc",
        "market_security_country_v1": "assertion_date",
        "market_reference_publication_coverage_v1": "coverage_start_date",
    }
    for name, date_column in table_specs.items():
        if not table_exists(client, database, name):
            rows.append({"table": name, "status": "missing", "rows": 0, "min": None, "max": None})
            continue
        result = query_one(
            client,
            f"SELECT count() AS rows, min({qn(date_column)}) AS min_value, max({qn(date_column)}) AS max_value "
            f"FROM {table(database, name)} FINAL",
        )
        rows.append(
            {
                "table": name,
                "status": "ok",
                "rows": int(result.get("rows") or 0),
                "min": result.get("min_value"),
                "max": result.get("max_value"),
            }
        )
    return rows


def find_publication_gaps(
    client: ClickHouseHttpClient,
    *,
    database: str,
    coverage_kind: str,
    source_system: str,
    start_date: date,
    end_date: date,
) -> list[PublicationGap]:
    if end_date <= start_date:
        return []
    rows = query_json_each_row(
        client,
        f"""
        SELECT coverage_start_date, coverage_end_date
        FROM {table(database, 'market_reference_publication_coverage_v1')} FINAL
        WHERE coverage_kind = {sql_string(coverage_kind)}
          AND source_system = {sql_string(source_system)}
          AND status IN ('completed', 'covered_empty', 'bootstrap_trusted')
          AND coverage_end_date > toDate({sql_string(start_date.isoformat())})
          AND coverage_start_date < toDate({sql_string(end_date.isoformat())})
        ORDER BY coverage_start_date, coverage_end_date
        """,
    )
    intervals = []
    for row in rows:
        try:
            intervals.append((date.fromisoformat(str(row["coverage_start_date"])[:10]), date.fromisoformat(str(row["coverage_end_date"])[:10])))
        except Exception:
            continue
    merged = merge_date_intervals(intervals)
    cursor = start_date
    gaps: list[PublicationGap] = []
    for left, right in merged:
        left = max(left, start_date)
        right = min(right, end_date)
        if cursor < left:
            gaps.append(PublicationGap(coverage_kind, cursor, left, (left - cursor).days, source_system))
        cursor = max(cursor, right)
    if cursor < end_date:
        gaps.append(PublicationGap(coverage_kind, cursor, end_date, (end_date - cursor).days, source_system))
    return gaps


def insert_publication_coverage(
    client: ClickHouseHttpClient,
    *,
    database: str,
    coverage_id: str,
    coverage_kind: str,
    source_system: str,
    source_object: str,
    start_date: date,
    end_date: date,
    status: str,
    rows_read: int,
    rows_written: int,
    rows_failed: int,
    started_at_utc: datetime,
    finished_at_utc: datetime | None,
    details: dict[str, Any],
    source_run_id: str,
) -> None:
    now = datetime.now(UTC)
    row = {
        "coverage_id": coverage_id,
        "coverage_kind": coverage_kind,
        "source_system": source_system,
        "source_object": source_object,
        "coverage_start_date": start_date.isoformat(),
        "coverage_end_date": end_date.isoformat(),
        "status": status,
        "rows_read": max(0, int(rows_read)),
        "rows_written": max(0, int(rows_written)),
        "rows_failed": max(0, int(rows_failed)),
        "started_at_utc": dt64(started_at_utc),
        "finished_at_utc": dt64(finished_at_utc) if finished_at_utc else None,
        "details_json": json.dumps(details, sort_keys=True, separators=(",", ":"), default=str),
        "source_run_id": source_run_id,
        "inserted_at": dt64(now),
    }
    client.execute(f"INSERT INTO {table(database, 'market_reference_publication_coverage_v1')} FORMAT JSONEachRow\n{json.dumps(row, separators=(',', ':'))}")


def merge_date_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    output: list[tuple[date, date]] = []
    for start, end in sorted((left, right) for left, right in intervals if right > left):
        if not output or start > output[-1][1]:
            output.append((start, end))
        else:
            output[-1] = (output[-1][0], max(output[-1][1], end))
    return output


def table_exists(client: ClickHouseHttpClient, database: str, name: str) -> bool:
    value = client.execute(
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(name)} FORMAT TSV"
    ).strip()
    return int(value or "0") > 0


def query_one(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    rows = query_json_each_row(client, sql)
    return rows[0] if rows else {}


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def mergetree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return ", ".join(settings)


def table(database: str, name: str) -> str:
    return f"{qn(database)}.{qn(name)}"


def qn(value: str) -> str:
    return quote_ident(value)


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def next_day(value: date) -> date:
    return value + timedelta(days=1)
