from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402


DEFAULT_SOURCE_DATABASE = "q_live"
DEFAULT_TARGET_DATABASE = "market_sip_compact"
DEFAULT_FILING_TABLE = "sec_filing_context"
DEFAULT_TEXT_TABLE = "sec_filing_text_context"
DEFAULT_XBRL_TABLE = "sec_xbrl_context"
DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT_WIN / "sec_context"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize q_live SEC filings, SEC text, and XBRL rows into compact market_sip_compact "
            "training context tables. The migration pays FINAL and CIK/accession mapping costs once "
            "so training can query compact tables directly."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE)
    parser.add_argument("--target-database", default=DEFAULT_TARGET_DATABASE)
    parser.add_argument("--filing-table", default=DEFAULT_FILING_TABLE)
    parser.add_argument("--text-table", default=DEFAULT_TEXT_TABLE)
    parser.add_argument("--xbrl-table", default=DEFAULT_XBRL_TABLE)
    parser.add_argument("--start-date", default="2019-01-01", help="Inclusive UTC accepted_at date.")
    parser.add_argument("--end-date", default=datetime.now(UTC).date().isoformat(), help="Inclusive UTC accepted_at date.")
    parser.add_argument("--storage-policy", default=default_storage_policy(), help="Defaults to CLICKHOUSE_HISTORICAL_STORAGE_POLICY.")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--max-memory-usage", default="300G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait-mutations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mutation-timeout-seconds", type=int, default=7200)
    parser.add_argument("--text-prefix-chars", type=int, default=0, help="Deprecated no-op. SEC text context now stores full text.")
    parser.add_argument("--max-text-rows-per-filing", type=int, default=0, help="Deprecated no-op. SEC text context now stores every text row.")
    parser.add_argument("--sec-text-buckets", type=int, default=64, help="Process SEC text by cityHash64(cik) buckets to match q_live.sec_filing_text_v2 partitioning.")
    parser.add_argument("--skip-text", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--drop-target-tables", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    report_path = Path(args.output_root_win) / f"sec_context_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    start_date = parse_date(args.start_date)
    end_date_exclusive = parse_date(args.end_date) + timedelta(days=1)

    print("=" * 96, flush=True)
    print("SEC context migration for training", flush=True)
    print(f"source_database={args.source_database} target_database={args.target_database}", flush=True)
    print(f"tables={args.filing_table},{args.text_table},{args.xbrl_table}", flush=True)
    print(f"accepted_at_range=[{start_date.isoformat()}, {end_date_exclusive.isoformat()})", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"replace_range={args.replace_range} wait_mutations={args.wait_mutations} dry_run={args.dry_run}", flush=True)
    print(
        "sec_text_context=full_text_all_rows "
        f"sec_text_buckets={args.sec_text_buckets} skip_text={args.skip_text}",
        flush=True,
    )
    print(f"skip_xbrl={args.skip_xbrl} drop_target_tables={args.drop_target_tables}", flush=True)
    print(f"report={report_path}", flush=True)
    print(
        "secret_status="
        f"{secret_status(['CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD', 'CLICKHOUSE_HISTORICAL_STORAGE_POLICY'])}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    try:
        run_migration(client, args, start_date=start_date, end_date_exclusive=end_date_exclusive, report_path=report_path)
    except KeyboardInterrupt:
        append_jsonl(
            report_path,
            {
                "operation": "migration",
                "status": "interrupted",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "interrupted_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        print("=" * 96, flush=True)
        print(f"INTERRUPTED elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
        print("If interruption happened during DELETE, check system.mutations before rerunning.", flush=True)
        print("=" * 96, flush=True)
        return 130

    print("=" * 96, flush=True)
    print(f"DONE elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
    print("=" * 96, flush=True)
    return 0


def run_migration(client: ClickHouseHttpClient, args: argparse.Namespace, *, start_date: date, end_date_exclusive: date, report_path: Path) -> None:
    statements = [
        f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.target_database)}",
        create_filing_context_table_sql(args.target_database, args.filing_table, args.storage_policy),
        create_text_context_table_sql(args.target_database, args.text_table, args.storage_policy),
        create_xbrl_context_table_sql(args.target_database, args.xbrl_table, args.storage_policy),
    ]
    if args.drop_target_tables:
        statements = [
            f"DROP TABLE IF EXISTS {quote_ident(args.target_database)}.{quote_ident(args.filing_table)}",
            f"DROP TABLE IF EXISTS {quote_ident(args.target_database)}.{quote_ident(args.text_table)}",
            f"DROP TABLE IF EXISTS {quote_ident(args.target_database)}.{quote_ident(args.xbrl_table)}",
            *statements,
        ]
    for index, statement in enumerate(statements, 1):
        run_sql(client, f"schema_{index}", statement, report_path, dry_run=bool(args.dry_run))

    active_tables = [args.filing_table]
    if not args.skip_text:
        active_tables.append(args.text_table)
    if not args.skip_xbrl:
        active_tables.append(args.xbrl_table)

    if args.replace_range:
        for table in active_tables:
            sql = delete_range_sql(args.target_database, table, start_date=start_date, end_date_exclusive=end_date_exclusive)
            run_sql(client, f"delete_{table}", sql, report_path, dry_run=bool(args.dry_run))
            if args.wait_mutations and not args.dry_run:
                wait_for_mutations(
                    client,
                    database=args.target_database,
                    table=table,
                    timeout_seconds=int(args.mutation_timeout_seconds),
                    report_path=report_path,
                )

    run_sql(
        client,
        f"insert_{args.filing_table}",
        insert_filing_context_sql(args, start_date=start_date, end_date_exclusive=end_date_exclusive),
        report_path,
        dry_run=bool(args.dry_run),
    )
    if not args.skip_text:
        for bucket in range(max(1, int(args.sec_text_buckets))):
            run_sql(
                client,
                f"insert_{args.text_table}_bucket_{bucket:02d}",
                insert_text_context_sql(args, start_date=start_date, end_date_exclusive=end_date_exclusive, bucket=bucket),
                report_path,
                dry_run=bool(args.dry_run),
            )
    if not args.skip_xbrl:
        run_sql(
            client,
            f"insert_{args.xbrl_table}",
            insert_xbrl_context_sql(args, start_date=start_date, end_date_exclusive=end_date_exclusive),
            report_path,
            dry_run=bool(args.dry_run),
        )

    if not args.dry_run:
        for table in active_tables:
            summarize_table(client, args.target_database, table, start_date=start_date, end_date_exclusive=end_date_exclusive, report_path=report_path)


def create_filing_context_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    accepted_at_utc DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    cik String,
    accession_number String,
    form_type LowCardinality(String),
    accepted_at_source LowCardinality(String),
    mapping_confidence Float32,
    bridge_id String,
    security_id String,
    listing_id String,
    symbol_id String,
    filing_id String,
    company_name String,
    primary_document String,
    primary_document_url String,
    filing_detail_url String,
    items String,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ticker, timestamp_us, accession_number, cik)
{mergetree_settings_sql(storage_policy)}
"""


def create_text_context_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    accepted_at_utc DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    cik String,
    accession_number String,
    form_type LowCardinality(String),
    text_rank UInt8,
    document_id String,
    text_kind LowCardinality(String),
    text String CODEC(ZSTD(3)),
    text_char_count UInt32,
    quality_flags String,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ticker, timestamp_us, accession_number, text_rank, document_id)
{mergetree_settings_sql(storage_policy)}
"""


def create_xbrl_context_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    accepted_at_utc DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    cik String,
    accession_number String,
    source_id String,
    issuer_id String,
    xbrl_row_kind LowCardinality(String),
    taxonomy LowCardinality(String),
    tag LowCardinality(String),
    unit_code LowCardinality(String),
    fiscal_year UInt16,
    fiscal_period LowCardinality(String),
    form_type LowCardinality(String),
    accepted_at_source LowCardinality(String),
    period_end_date Date,
    value Float64,
    calendar_period_code LowCardinality(String),
    location_code LowCardinality(String),
    mapping_confidence Float32,
    bridge_id String,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ticker, timestamp_us, accession_number, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date, source_id)
{mergetree_settings_sql(storage_policy)}
"""


def bridge_cte_sql(args: argparse.Namespace) -> str:
    source_db = quote_ident(args.source_database)
    return f"""
bridge AS
(
    SELECT
        ifNull(ticker, '') AS ticker,
        cik,
        ifNull(accession_number, '') AS accession_number,
        valid_from_date,
        valid_to_date_exclusive,
        any(bridge_id) AS bridge_id,
        any(ifNull(security_id, '')) AS security_id,
        any(ifNull(listing_id, '')) AS listing_id,
        any(ifNull(symbol_id, '')) AS symbol_id,
        max(confidence_score) AS confidence_score
    FROM {source_db}.id_sec_market_bridge_v1
    WHERE ifNull(ticker, '') != ''
      AND mapping_status IN ('active', 'mapped', 'accepted', '')
    GROUP BY ticker, cik, accession_number, valid_from_date, valid_to_date_exclusive
)
"""


def insert_filing_context_sql(args: argparse.Namespace, *, start_date: date, end_date_exclusive: date) -> str:
    source_db = quote_ident(args.source_database)
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.filing_table)}"
    return f"""
INSERT INTO {target}
WITH {bridge_cte_sql(args)}
SELECT
    b.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(f.accepted_at_utc)) AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    ifNull(f.form_type, '') AS form_type,
    ifNull(f.accepted_at_source, '') AS accepted_at_source,
    toFloat32(b.confidence_score) AS mapping_confidence,
    b.bridge_id AS bridge_id,
    b.security_id AS security_id,
    b.listing_id AS listing_id,
    b.symbol_id AS symbol_id,
    toString(f.filing_id) AS filing_id,
    ifNull(f.company_name, '') AS company_name,
    ifNull(f.primary_document, '') AS primary_document,
    ifNull(f.primary_document_url, '') AS primary_document_url,
    ifNull(f.filing_detail_url, '') AS filing_detail_url,
    ifNull(f.items, '') AS items,
    now64(3, 'UTC') AS updated_at
FROM {source_db}.sec_filing_v2 AS f FINAL
INNER JOIN bridge AS b
    ON b.cik = f.cik
WHERE f.accepted_at_utc IS NOT NULL
  AND (b.accession_number = '' OR b.accession_number = f.accession_number)
  AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
  AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
  AND f.accepted_at_utc >= {date_time64_sql(start_date)}
  AND f.accepted_at_utc < {date_time64_sql(end_date_exclusive)}
{query_settings(args)}
"""


def insert_text_context_sql(args: argparse.Namespace, *, start_date: date, end_date_exclusive: date, bucket: int) -> str:
    source_db = quote_ident(args.source_database)
    filing_context = f"{quote_ident(args.target_database)}.{quote_ident(args.filing_table)}"
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.text_table)}"
    buckets = max(1, int(args.sec_text_buckets))
    return f"""
INSERT INTO {target}
SELECT
    f.ticker AS ticker,
    f.timestamp_us AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    f.form_type AS form_type,
    toUInt8(0) AS text_rank,
    ifNull(t.document_id, '') AS document_id,
    ifNull(t.text_kind, '') AS text_kind,
    ifNull(t.text, '') AS text,
    toUInt32(least(ifNull(t.text_char_count, 0), 4294967295)) AS text_char_count,
    arrayStringConcat(t.quality_flags, ',') AS quality_flags,
    now64(3, 'UTC') AS updated_at
FROM {filing_context} AS f
INNER JOIN {source_db}.sec_filing_text_v2 AS t
    ON t.cik = f.cik
   AND t.accession_number = f.accession_number
WHERE f.accepted_at_utc >= {date_time64_sql(start_date)}
  AND f.accepted_at_utc < {date_time64_sql(end_date_exclusive)}
  AND cityHash64(t.cik) % {buckets} = {int(bucket)}
ORDER BY f.ticker, f.accepted_at_utc, f.accession_number, t.text_kind, t.document_id
{query_settings(args)}
"""


def insert_xbrl_context_sql(args: argparse.Namespace, *, start_date: date, end_date_exclusive: date) -> str:
    source_db = quote_ident(args.source_database)
    filing_context = f"{quote_ident(args.target_database)}.{quote_ident(args.filing_table)}"
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.xbrl_table)}"
    return f"""
INSERT INTO {target}
SELECT *
FROM
(
    SELECT
        f.ticker AS ticker,
        f.timestamp_us AS timestamp_us,
        f.accepted_at_utc AS accepted_at_utc,
        x.cik AS cik,
        ifNull(x.accession_number, '') AS accession_number,
        toString(x.company_fact_id) AS source_id,
        ifNull(x.issuer_id, '') AS issuer_id,
        'company_fact' AS xbrl_row_kind,
        ifNull(x.taxonomy, '') AS taxonomy,
        ifNull(x.tag, '') AS tag,
        ifNull(x.unit_code, '') AS unit_code,
        toUInt16(ifNull(x.fiscal_year, 0)) AS fiscal_year,
        ifNull(x.fiscal_period, '') AS fiscal_period,
        ifNull(x.form_type, '') AS form_type,
        ifNull(f.accepted_at_source, '') AS accepted_at_source,
        ifNull(x.period_end_date, toDate('1970-01-01')) AS period_end_date,
        toFloat64(x.value) AS value,
        '' AS calendar_period_code,
        '' AS location_code,
        f.mapping_confidence AS mapping_confidence,
        f.bridge_id AS bridge_id,
        now64(3, 'UTC') AS updated_at
    FROM {source_db}.sec_xbrl_company_fact_v1 AS x
    INNER JOIN {filing_context} AS f
        ON f.cik = x.cik
       AND f.accession_number = x.accession_number
    WHERE x.accession_number IS NOT NULL
      AND x.accession_number != ''
      AND f.accepted_at_utc >= {date_time64_sql(start_date)}
      AND f.accepted_at_utc < {date_time64_sql(end_date_exclusive)}
    UNION ALL
    SELECT
        f.ticker AS ticker,
        f.timestamp_us AS timestamp_us,
        f.accepted_at_utc AS accepted_at_utc,
        o.cik AS cik,
        o.accession_number AS accession_number,
        toString(o.frame_observation_id) AS source_id,
        ifNull(o.issuer_id, '') AS issuer_id,
        'frame_observation' AS xbrl_row_kind,
        ifNull(o.taxonomy, '') AS taxonomy,
        ifNull(o.tag, '') AS tag,
        ifNull(o.unit_code, '') AS unit_code,
        toUInt16(0) AS fiscal_year,
        '' AS fiscal_period,
        '' AS form_type,
        ifNull(f.accepted_at_source, '') AS accepted_at_source,
        ifNull(o.period_end_date, toDate('1970-01-01')) AS period_end_date,
        toFloat64(o.value) AS value,
        ifNull(o.calendar_period_code, '') AS calendar_period_code,
        ifNull(o.location_code, '') AS location_code,
        f.mapping_confidence AS mapping_confidence,
        f.bridge_id AS bridge_id,
        now64(3, 'UTC') AS updated_at
    FROM {source_db}.sec_xbrl_frame_observation_v1 AS o
    INNER JOIN {filing_context} AS f
        ON f.cik = o.cik
       AND f.accession_number = o.accession_number
    WHERE f.accepted_at_utc >= {date_time64_sql(start_date)}
      AND f.accepted_at_utc < {date_time64_sql(end_date_exclusive)}
)
{query_settings(args)}
"""


def delete_range_sql(database: str, table: str, *, start_date: date, end_date_exclusive: date) -> str:
    return f"""
ALTER TABLE {quote_ident(database)}.{quote_ident(table)}
DELETE WHERE accepted_at_utc >= {date_time64_sql(start_date)}
  AND accepted_at_utc < {date_time64_sql(end_date_exclusive)}
"""


def summarize_table(client: ClickHouseHttpClient, database: str, table: str, *, start_date: date, end_date_exclusive: date, report_path: Path) -> None:
    sql = f"""
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    min(accepted_at_utc) AS min_accepted_at_utc,
    max(accepted_at_utc) AS max_accepted_at_utc
FROM {quote_ident(database)}.{quote_ident(table)}
WHERE accepted_at_utc >= {date_time64_sql(start_date)}
  AND accepted_at_utc < {date_time64_sql(end_date_exclusive)}
FORMAT JSONEachRow
"""
    started = time.perf_counter()
    raw = client.execute(sql).strip()
    seconds = time.perf_counter() - started
    summary = json.loads(raw) if raw else {}
    payload = {"operation": "summary", "table": table, "seconds": round(seconds, 3), **summary}
    append_jsonl(report_path, payload)
    print(
        f"SUMMARY {table} rows={int(summary.get('rows', 0)):,} tickers={int(summary.get('tickers', 0)):,} "
        f"min={summary.get('min_accepted_at_utc')} max={summary.get('max_accepted_at_utc')} seconds={seconds:.1f}",
        flush=True,
    )


def wait_for_mutations(client: ClickHouseHttpClient, *, database: str, table: str, timeout_seconds: int, report_path: Path) -> None:
    deadline = time.perf_counter() + float(timeout_seconds)
    while True:
        sql = f"""
SELECT count()
FROM system.mutations
WHERE database = {sql_string(database)}
  AND table = {sql_string(table)}
  AND is_done = 0
FORMAT TSV
"""
        pending = int((client.execute(sql).strip() or "0").splitlines()[0])
        if pending == 0:
            print(f"MUTATIONS DONE table={table}", flush=True)
            append_jsonl(report_path, {"operation": "wait_mutations", "table": table, "pending": pending, "status": "done"})
            return
        if time.perf_counter() >= deadline:
            raise TimeoutError(f"Timed out waiting for mutations on {database}.{table}; pending={pending}")
        print(f"MUTATIONS WAIT table={table} pending={pending}", flush=True)
        time.sleep(5.0)


def run_sql(client: ClickHouseHttpClient, label: str, sql: str, report_path: Path, *, dry_run: bool) -> None:
    compact = " ".join(line.strip() for line in sql.strip().splitlines() if line.strip())
    print(f"QUERY START {label}", flush=True)
    if dry_run:
        print(f"DRY RUN {label}: {compact[:1000]}", flush=True)
        append_jsonl(report_path, {"operation": label, "status": "dry_run", "sql_preview": compact[:4000]})
        return
    started = time.perf_counter()
    try:
        client.execute(sql)
    except Exception as exc:
        seconds = time.perf_counter() - started
        append_jsonl(report_path, {"operation": label, "status": "failed", "seconds": round(seconds, 3), "error": repr(exc)})
        print(f"QUERY FAILED {label}: {exc!r}", flush=True)
        raise
    seconds = time.perf_counter() - started
    append_jsonl(report_path, {"operation": label, "status": "ok", "seconds": round(seconds, 3)})
    print(f"QUERY DONE {label} seconds={seconds:.1f}", flush=True)


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage).strip():
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def date_time64_sql(value: date) -> str:
    return f"toDateTime64({sql_string(value.isoformat() + ' 00:00:00')}, 9, 'UTC')"


def parse_date(text: str) -> date:
    return date.fromisoformat(text.strip()[:10])


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
