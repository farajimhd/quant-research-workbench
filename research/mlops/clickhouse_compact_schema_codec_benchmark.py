from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_FILE_ROOT_ENV,
    CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_STORAGE_POLICY_ENV,
    CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
    CLICKHOUSE_URL_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    HISTORICAL_CLICKHOUSE_DATABASE_ENV,
    QUOTE_SCHEMA_STRING,
    TRADE_SCHEMA_STRING,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    discover_source_files,
    format_optional_int,
    mergetree_settings_sql,
    normalize_clickhouse_file_path,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)


DEFAULT_DATABASE = "market_sip_compact_benchmark"
DEFAULT_QUOTE_DATE = "2026-05-15"
DEFAULT_TRADE_DATE = "2026-05-15"
DEFAULT_START_DATE = "2026-05-15"
DEFAULT_END_DATE = "2026-06-03"
DEFAULT_MAX_FILES_PER_KIND = 15


@dataclass(frozen=True, slots=True)
class BenchmarkTable:
    kind: str
    variant: str
    table: str


@dataclass(frozen=True, slots=True)
class BenchmarkSource:
    kind: str
    date: str
    source_path: str
    bytes: int


@dataclass(frozen=True, slots=True)
class InsertJob:
    database: str
    table: BenchmarkTable
    source: BenchmarkSource
    sql: str
    settings: str
    clickhouse_url: str
    user: str
    password: str


@dataclass(frozen=True, slots=True)
class InsertResult:
    table: BenchmarkTable
    source: BenchmarkSource
    profile: QueryProfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark compact SIP quote/trade canonical inserts with and without explicit codecs. "
            "The script creates isolated benchmark tables and does not alter production tables."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--quote-date", default="", help="Backward-compatible single quote date. Overrides --start-date/--end-date for quotes when set.")
    parser.add_argument("--trade-date", default="", help="Backward-compatible single trade date. Overrides --start-date/--end-date for trades when set.")
    parser.add_argument("--max-files-per-kind", type=int, default=DEFAULT_MAX_FILES_PER_KIND, help="Maximum quote files and maximum trade files to insert. 0 means all discovered files.")
    parser.add_argument("--insert-concurrency", type=int, default=1, help="Concurrent file inserts per benchmark table. Variants still run sequentially for fair comparison.")
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_schema_codec_benchmark"))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--cleanup-before", action="store_true", help="Drop benchmark tables for this run id before creating them.")
    parser.add_argument("--skip-insert", action="store_true", help="Skip inserts and run only optimize/storage/retrieval against existing benchmark tables for --run-id.")
    parser.add_argument("--optimize-final", action="store_true", help="Run OPTIMIZE FINAL before retrieval/storage metrics.")
    return parser.parse_args()


def env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        HISTORICAL_CLICKHOUSE_DATABASE_ENV,
        CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
        CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        CLICKHOUSE_FILE_ROOT_ENV,
        "CLICKHOUSE_FLATFILES_ROOT",
        CLICKHOUSE_STORAGE_POLICY_ENV,
    ]


def query_settings(args: argparse.Namespace) -> str:
    settings = [
        "input_format_csv_empty_as_default = 1",
        "input_format_skip_unknown_fields = 1",
        "date_time_input_format = 'best_effort'",
    ]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def codec_suffix(sql_type: str, *, enabled: bool) -> str:
    if not enabled:
        return sql_type
    if sql_type.startswith("UInt64"):
        return f"{sql_type} CODEC(DoubleDelta, ZSTD(1))"
    if sql_type.startswith("UInt32"):
        return f"{sql_type} CODEC(T64, ZSTD(1))"
    if sql_type.startswith("Int32"):
        return f"{sql_type} CODEC(T64, ZSTD(1))"
    return sql_type


def quote_table_sql(database: str, table: str, *, codecs: bool, storage_policy: str) -> str:
    db = quote_ident(database)
    table_name = quote_ident(table)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table_name}
(
    ticker LowCardinality(String),
    sip_timestamp_us {codec_suffix("UInt64", enabled=codecs)},
    participant_delta_us {codec_suffix("Int32", enabled=codecs)},
    sequence_number {codec_suffix("UInt32", enabled=codecs)},
    bid_price_int {codec_suffix("UInt32", enabled=codecs)},
    ask_price_int {codec_suffix("UInt32", enabled=codecs)},
    bid_size {codec_suffix("UInt32", enabled=codecs)},
    ask_size {codec_suffix("UInt32", enabled=codecs)},
    bid_exchange UInt8,
    ask_exchange UInt8,
    conditions LowCardinality(String),
    indicators LowCardinality(String),
    quote_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
{mergetree_settings_sql(storage_policy)}
"""


def trade_table_sql(database: str, table: str, *, codecs: bool, storage_policy: str) -> str:
    db = quote_ident(database)
    table_name = quote_ident(table)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table_name}
(
    ticker LowCardinality(String),
    sip_timestamp_us {codec_suffix("UInt64", enabled=codecs)},
    participant_delta_us {codec_suffix("Int32", enabled=codecs)},
    sequence_number {codec_suffix("UInt32", enabled=codecs)},
    price_int {codec_suffix("UInt32", enabled=codecs)},
    size Float32,
    exchange UInt8,
    conditions LowCardinality(String),
    trade_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
{mergetree_settings_sql(storage_policy)}
"""


def clamp_int32_sql(expr: str) -> str:
    return f"toInt32(greatest(-2147483648, least(2147483647, {expr})))"


def scale_code_sql(price_expr: str) -> str:
    cent_fraction = sub_cent_precision_sql(price_expr)
    return f"if({price_expr} > 0 AND ({price_expr} < 1 OR ({cent_fraction} AND {price_expr} <= 429496.7295)), 1, 0)"


def sub_cent_precision_sql(price_expr: str) -> str:
    return f"abs(({price_expr} * 100) - round({price_expr} * 100)) > 0.0000001"


def price_precision_clipped_sql(price_expr: str) -> str:
    return f"({price_expr} > 429496.7295 AND {sub_cent_precision_sql(price_expr)})"


def price_int_sql(price_expr: str) -> str:
    scale = scale_code_sql(price_expr)
    return f"toUInt32(round(if({scale} = 1, {price_expr} * 10000, {price_expr} * 100)))"


def tape_code_sql(tape_expr: str) -> str:
    return f"toUInt8(greatest(0, least(3, toInt16OrZero({tape_expr}) - 1)))"


def insert_quote_sql(database: str, table: str, source_path: str) -> str:
    db = quote_ident(database)
    table_name = quote_ident(table)
    bid_price = "toFloat64OrZero(bid_price)"
    ask_price = "toFloat64OrZero(ask_price)"
    bid_scale = scale_code_sql(bid_price)
    ask_scale = scale_code_sql(ask_price)
    delta_us = "intDiv(toInt64OrZero(participant_timestamp) - toInt64OrZero(sip_timestamp), 1000)"
    flags = f"toUInt8({bid_scale} + ({ask_scale} * 2) + ({tape_code_sql('tape')} * 4))"
    issue_flags = (
        f"toUInt16(if({bid_price} <= 0, 1, 0) + "
        f"if({ask_price} <= 0, 2, 0) + "
        "if(toFloat64OrZero(bid_size) <= 0, 4, 0) + "
        "if(toFloat64OrZero(ask_size) <= 0, 8, 0) + "
        f"if({delta_us} < -2147483648 OR {delta_us} > 2147483647, 16, 0) + "
        f"if({price_precision_clipped_sql(bid_price)}, 32, 0) + "
        f"if({price_precision_clipped_sql(ask_price)}, 64, 0))"
    )
    return f"""
INSERT INTO {db}.{table_name}
(
    ticker,
    sip_timestamp_us,
    participant_delta_us,
    sequence_number,
    bid_price_int,
    ask_price_int,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    conditions,
    indicators,
    quote_flags,
    issue_flags
)
SELECT
    ticker,
    toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)),
    {clamp_int32_sql(delta_us)},
    toUInt32OrZero(sequence_number),
    {price_int_sql(bid_price)},
    {price_int_sql(ask_price)},
    toUInt32(toFloat64OrZero(bid_size)),
    toUInt32(toFloat64OrZero(ask_size)),
    toUInt8OrZero(bid_exchange),
    toUInt8OrZero(ask_exchange),
    conditions,
    indicators,
    {flags},
    {issue_flags}
FROM file({sql_string(source_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
"""


def insert_trade_sql(database: str, table: str, source_path: str) -> str:
    db = quote_ident(database)
    table_name = quote_ident(table)
    price = "toFloat64OrZero(price)"
    price_scale = scale_code_sql(price)
    delta_us = "intDiv(toInt64OrZero(participant_timestamp) - toInt64OrZero(sip_timestamp), 1000)"
    correction_code = "toUInt8(greatest(0, least(15, toInt16OrZero(correction))))"
    flags = f"toUInt8({price_scale} + ({tape_code_sql('tape')} * 2) + ({correction_code} * 8))"
    issue_flags = (
        f"toUInt16(if({price} <= 0, 1, 0) + "
        "if(toFloat64OrZero(size) <= 0, 2, 0) + "
        f"if({delta_us} < -2147483648 OR {delta_us} > 2147483647, 4, 0) + "
        f"if({price_precision_clipped_sql(price)}, 8, 0))"
    )
    return f"""
INSERT INTO {db}.{table_name}
(
    ticker,
    sip_timestamp_us,
    participant_delta_us,
    sequence_number,
    price_int,
    size,
    exchange,
    conditions,
    trade_flags,
    issue_flags
)
SELECT
    ticker,
    toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)),
    {clamp_int32_sql(delta_us)},
    toUInt32OrZero(sequence_number),
    {price_int_sql(price)},
    toFloat32OrZero(size),
    toUInt8OrZero(exchange),
    conditions,
    {flags},
    {issue_flags}
FROM file({sql_string(source_path)}, 'CSVWithNames', {sql_string(TRADE_SCHEMA_STRING)})
"""


def retrieval_queries(database: str, table: str, kind: str) -> list[tuple[str, str]]:
    db_table = f"{quote_ident(database)}.{quote_ident(table)}"
    if kind == "quotes":
        return [
            (
                "scan_group_ticker",
                f"SELECT ticker, count(), sum(bid_size), sum(ask_size) FROM {db_table} GROUP BY ticker ORDER BY count() DESC LIMIT 100",
            ),
            (
                "ordered_aapl_window",
                f"SELECT * FROM {db_table} WHERE ticker = 'AAPL' ORDER BY sip_timestamp_us, sequence_number LIMIT 100000",
            ),
        ]
    return [
        (
            "scan_group_ticker",
            f"SELECT ticker, count(), sum(size) FROM {db_table} GROUP BY ticker ORDER BY count() DESC LIMIT 100",
        ),
        (
            "ordered_aapl_window",
            f"SELECT * FROM {db_table} WHERE ticker = 'AAPL' ORDER BY sip_timestamp_us, sequence_number LIMIT 100000",
        ),
    ]


def table_storage_query(database: str, table: str) -> str:
    return (
        "SELECT rows, "
        "bytes_on_disk, formatReadableSize(bytes_on_disk) AS bytes_on_disk_readable, "
        "compressed_bytes, formatReadableSize(compressed_bytes) AS compressed_readable, "
        "uncompressed_bytes, formatReadableSize(uncompressed_bytes) AS uncompressed_readable, "
        "active_parts "
        "FROM ("
        "SELECT "
        "sum(rows) AS rows, "
        "sum(bytes_on_disk) AS bytes_on_disk, "
        "sum(data_compressed_bytes) AS compressed_bytes, "
        "sum(data_uncompressed_bytes) AS uncompressed_bytes, "
        "count() AS active_parts "
        "FROM system.parts "
        f"WHERE database = {sql_string(database)} AND table = {sql_string(table)} AND active"
        ") "
        "FORMAT JSONEachRow"
    )


def run_query_timed(client: ClickHouseHttpClient, label: str, sql: str, settings: str) -> QueryProfile:
    return run_profiled(client, label, sql, settings)


def run_insert_job(job: InsertJob) -> InsertResult:
    client = ClickHouseHttpClient(job.clickhouse_url, job.user, job.password)
    profile = run_query_timed(client, f"insert_{job.table.table}_{job.source.date}", job.sql, job.settings)
    return InsertResult(table=job.table, source=job.source, profile=profile)


def parse_json_each_row(text: str) -> dict[str, object]:
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return rows[0] if rows else {}


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    rows = client.query_tsv(
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(table)}"
    ).strip()
    return bool(rows and int(rows) > 0)


def find_sources(root_win: Path, root_ch: str, kind: str, start_date: str, end_date: str, max_files: int) -> list[BenchmarkSource]:
    sources = discover_source_files(root_win, root_ch, [kind], start_date, end_date)
    if not sources:
        raise RuntimeError(f"No {kind} flatfiles found for date range {start_date}..{end_date} under {root_win}")
    if max_files > 0:
        sources = sources[:max_files]
    return [
        BenchmarkSource(kind=source.kind, date=source.date, source_path=source.clickhouse_path, bytes=source.bytes)
        for source in sources
    ]


def source_summary(sources: list[BenchmarkSource]) -> dict[str, object]:
    return {
        "count": len(sources),
        "start_date": min((source.date for source in sources), default=""),
        "end_date": max((source.date for source in sources), default=""),
        "total_gib": round(sum(source.bytes for source in sources) / (1024**3), 3),
        "sources": [asdict(source) for source in sources],
    }


def print_profile(profile: QueryProfile) -> None:
    memory_gib = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024**3)
    rows_per_second = None
    if profile.written_rows and profile.wall_seconds > 0:
        rows_per_second = round(profile.written_rows / profile.wall_seconds)
    print(
        f"{profile.label}: wall={profile.wall_seconds:.2f}s query_ms={profile.query_duration_ms} "
        f"memory_gib={None if memory_gib is None else round(memory_gib, 3)} "
        f"read_rows={format_optional_int(profile.read_rows)} written_rows={format_optional_int(profile.written_rows)} "
        f"rows_per_sec={format_optional_int(rows_per_second)}",
        flush=True,
    )


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    flatfiles_root_win = Path(args.flatfiles_root_win)
    flatfiles_root_ch = normalize_clickhouse_file_path(args.flatfiles_root_ch)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"compact_schema_codec_benchmark_{args.run_id}.jsonl"

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    settings = query_settings(args)
    database = args.database.strip()
    run_suffix = "".join(ch if ch.isalnum() else "_" for ch in args.run_id.lower())
    if args.skip_insert and args.cleanup_before:
        raise ValueError("--skip-insert cannot be combined with --cleanup-before because it would drop the tables you want to reuse.")

    quote_start = args.quote_date or args.start_date
    quote_end = args.quote_date or args.end_date
    trade_start = args.trade_date or args.start_date
    trade_end = args.trade_date or args.end_date
    quote_sources = find_sources(flatfiles_root_win, flatfiles_root_ch, "quotes", quote_start, quote_end, args.max_files_per_kind)
    trade_sources = find_sources(flatfiles_root_win, flatfiles_root_ch, "trades", trade_start, trade_end, args.max_files_per_kind)

    tables = [
        BenchmarkTable("quotes", "plain", f"quotes_plain_{run_suffix}"),
        BenchmarkTable("quotes", "codec", f"quotes_codec_{run_suffix}"),
        BenchmarkTable("trades", "plain", f"trades_plain_{run_suffix}"),
        BenchmarkTable("trades", "codec", f"trades_codec_{run_suffix}"),
    ]
    sources_by_kind = {"quotes": quote_sources, "trades": trade_sources}

    print("=" * 96, flush=True)
    print("Compact SIP canonical schema codec benchmark", flush=True)
    print(f"database={database}", flush=True)
    print(f"quote_sources={source_summary(quote_sources)}", flush=True)
    print(f"trade_sources={source_summary(trade_sources)}", flush=True)
    print(f"flatfiles_root_win={flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={flatfiles_root_ch}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"settings={settings.strip()}", flush=True)
    print(f"insert_concurrency={max(1, int(args.insert_concurrency))}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    if args.skip_insert:
        missing_tables = [table.table for table in tables if not table_exists(client, database, table.table)]
        if missing_tables:
            raise RuntimeError(
                "Cannot use --skip-insert because these benchmark tables do not exist: "
                + ", ".join(missing_tables)
                + ". Pass the original --run-id from the insert run."
            )
    else:
        client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
        for table in tables:
            if args.cleanup_before:
                client.execute(f"DROP TABLE IF EXISTS {quote_ident(database)}.{quote_ident(table.table)}")
            create_sql = (
                quote_table_sql(database, table.table, codecs=table.variant == "codec", storage_policy=args.storage_policy)
                if table.kind == "quotes"
                else trade_table_sql(database, table.table, codecs=table.variant == "codec", storage_policy=args.storage_policy)
            )
            client.execute(create_sql)

    with report_path.open("a", encoding="utf-8") as report:
        report.write(
            json.dumps(
                {
                    "type": "config",
                    "run_id": args.run_id,
                    "database": database,
                    "quote_sources": source_summary(quote_sources),
                    "trade_sources": source_summary(trade_sources),
                    "storage_policy": args.storage_policy,
                    "settings": settings.strip(),
                    "insert_concurrency": max(1, int(args.insert_concurrency)),
                },
                sort_keys=True,
            )
            + "\n"
        )

        if args.skip_insert:
            print("-" * 96, flush=True)
            print("SKIP INSERT requested; running optimize/storage/retrieval against existing tables.", flush=True)
            report.write(json.dumps({"type": "skip_insert", "tables": [asdict(table) for table in tables]}, sort_keys=True) + "\n")
        else:
            for table in tables:
                print("-" * 96, flush=True)
                table_sources = sources_by_kind[table.kind]
                insert_concurrency = max(1, int(args.insert_concurrency))
                print(
                    f"INSERT {table.kind}:{table.variant} table={table.table} "
                    f"files={len(table_sources)} insert_concurrency={insert_concurrency}",
                    flush=True,
                )
                aggregate_rows = 0
                aggregate_query_seconds = 0.0
                insert_started = time.perf_counter()
                jobs: list[InsertJob] = []
                for source in table_sources:
                    sql = (
                        insert_quote_sql(database, table.table, source.source_path)
                        if table.kind == "quotes"
                        else insert_trade_sql(database, table.table, source.source_path)
                    )
                    jobs.append(
                        InsertJob(
                            database=database,
                            table=table,
                            source=source,
                            sql=sql,
                            settings=settings,
                            clickhouse_url=args.clickhouse_url,
                            user=args.user,
                            password=args.password,
                        )
                    )
                for source_index, source in enumerate(table_sources, start=1):
                    print(
                        f"INSERT-QUEUE [{source_index:,}/{len(table_sources):,}] {table.table} "
                        f"{source.date} size_gib={source.bytes / (1024**3):.2f}",
                        flush=True,
                    )
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(insert_concurrency, len(jobs))) as executor:
                    future_to_job = {executor.submit(run_insert_job, job): job for job in jobs}
                    completed = 0
                    for future in concurrent.futures.as_completed(future_to_job):
                        job = future_to_job[future]
                        result = future.result()
                        completed += 1
                        print(
                            f"INSERT-DONE [{completed:,}/{len(jobs):,}] {table.table} {job.source.date}",
                            flush=True,
                        )
                        print_profile(result.profile)
                        aggregate_rows += int(result.profile.written_rows or 0)
                        aggregate_query_seconds += result.profile.wall_seconds
                        report.write(
                            json.dumps(
                                {
                                    "type": "insert_profile",
                                    "table": asdict(result.table),
                                    "source": asdict(result.source),
                                    "profile": asdict(result.profile),
                                },
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        report.flush()
                elapsed_wall_seconds = time.perf_counter() - insert_started
                rows_per_second_query_sum = round(aggregate_rows / aggregate_query_seconds) if aggregate_query_seconds > 0 else 0
                rows_per_second_elapsed = round(aggregate_rows / elapsed_wall_seconds) if elapsed_wall_seconds > 0 else 0
                aggregate = {
                    "files": len(table_sources),
                    "insert_concurrency": insert_concurrency,
                    "query_wall_seconds_sum": aggregate_query_seconds,
                    "elapsed_wall_seconds": elapsed_wall_seconds,
                    "written_rows_sum": aggregate_rows,
                    "rows_per_second_query_sum": rows_per_second_query_sum,
                    "rows_per_second_elapsed": rows_per_second_elapsed,
                }
                print(f"INSERT-SUMMARY {table.table}: {aggregate}", flush=True)
                report.write(json.dumps({"type": "insert_summary", "table": asdict(table), "aggregate": aggregate}, sort_keys=True) + "\n")
                report.flush()

        if args.optimize_final:
            for table in tables:
                profile = run_query_timed(client, f"optimize_{table.table}", f"OPTIMIZE TABLE {quote_ident(database)}.{quote_ident(table.table)} FINAL", settings)
                print_profile(profile)
                report.write(json.dumps({"type": "optimize_profile", "table": asdict(table), "profile": asdict(profile)}, sort_keys=True) + "\n")

        print("-" * 96, flush=True)
        print("STORAGE AND RETRIEVAL", flush=True)
        for table in tables:
            storage = parse_json_each_row(client.execute(table_storage_query(database, table.table)))
            print(f"STORAGE {table.table}: {storage}", flush=True)
            report.write(json.dumps({"type": "storage", "table": asdict(table), "storage": storage}, sort_keys=True) + "\n")
            for query_name, sql in retrieval_queries(database, table.table, table.kind):
                profile = run_query_timed(client, f"{query_name}_{table.table}", sql, settings)
                print_profile(profile)
                report.write(
                    json.dumps(
                        {
                            "type": "retrieval_profile",
                            "table": asdict(table),
                            "query_name": query_name,
                            "profile": asdict(profile),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

    print("=" * 96, flush=True)
    print(f"DONE report={report_path}", flush=True)
    print("Benchmark tables are intentionally left in place for inspection. Use a new --run-id or DROP them manually when done.", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
