from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.benchmarks.clickhouse_compact_schema_codec_benchmark import (  # noqa: E402
    QUOTE_SCHEMA_STRING,
    TRADE_SCHEMA_STRING,
    clamp_int32_sql,
    price_int_sql,
    price_precision_clipped_sql,
    scale_code_sql,
    tape_code_sql,
)
from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    DEFAULT_CONTINUITY_TABLE,
    DEFAULT_EVENTS_TABLE,
    DEFAULT_MANIFEST_TABLE,
    condition_code_expr,
    create_continuity_table_sql,
    create_events_table_sql,
    create_manifest_table_sql,
    delete_day_sql,
    insert_day_manifest,
    latest_day_status,
    mutation_settings,
    query_settings,
    quote_condition_pack_expr,
    trade_condition_pack_expr,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import (  # noqa: E402
    DEFAULT_AWS_REGION,
    DEFAULT_AWS_SERVICE,
    DEFAULT_CHUNK_BYTES,
    DEFAULT_DISCOVERY,
    DownloadConfig,
    DownloadJob,
    build_remote_jobs,
    download_one,
    env_value,
    parse_kinds,
)
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    env_status_keys,
)
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
    windows_path_to_clickhouse_path,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_START_DATE = "2025-01-01"
DEFAULT_END_DATE = "2026-12-31"
DEFAULT_DOWNLOAD_WORKERS = 8
DEFAULT_MAX_THREADS = 32


@dataclass(frozen=True, slots=True)
class DayFiles:
    source_date: str
    quote_job: DownloadJob
    trade_job: DownloadJob


@dataclass(frozen=True, slots=True)
class DayJob:
    source_date: str
    build_step: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Massive SIP flatfiles and update market_sip_compact.events directly. "
            "Quotes/trades are kept as flatfiles on disk and are not persisted as ClickHouse quote/trade tables."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or default_storage_policy())
    parser.add_argument("--partition-mode", choices=("month", "ticker_hash", "none"), default="month")
    parser.add_argument("--partition-buckets", type=int, default=256)
    parser.add_argument("--download-workers", type=int, default=DEFAULT_DOWNLOAD_WORKERS)
    parser.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS)
    parser.add_argument("--max-partitions-per-insert-block", type=int, default=1024)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "flatfile_event_update"))
    parser.add_argument("--discovery", choices=("remote",), default=DEFAULT_DISCOVERY)
    parser.add_argument("--aws-region", default=DEFAULT_AWS_REGION)
    parser.add_argument("--aws-service", default=DEFAULT_AWS_SERVICE)
    parser.add_argument("--s3-endpoint-url", default="")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--aws-access-key-id", default="")
    parser.add_argument("--aws-secret-access-key", default="")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument("--overwrite-incomplete", action="store_true", default=True)
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--day-offset", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--force-day-delete", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def download_config(args: argparse.Namespace) -> DownloadConfig:
    return DownloadConfig(
        endpoint_url=env_value(args.s3_endpoint_url, "S3_ENDPOINT_URL"),
        bucket=env_value(args.bucket, "BUCKET"),
        access_key=env_value(args.aws_access_key_id, "AWS_ACCESS_KEY_ID"),
        secret_key=env_value(args.aws_secret_access_key, "AWS_SECRET_ACCESS_KEY"),
        region=args.aws_region,
        service=args.aws_service,
        timeout_seconds=float(args.timeout_seconds),
        chunk_bytes=int(args.chunk_bytes),
        verify_tls=not bool(args.no_verify_tls),
        overwrite_incomplete=bool(args.overwrite_incomplete),
        dry_run=bool(args.dry_run),
        progress_interval_seconds=5.0,
    )


def source_days(args: argparse.Namespace, config: DownloadConfig) -> list[DayFiles]:
    flatfiles_root = Path(args.flatfiles_root_win)
    jobs = build_remote_jobs(flatfiles_root, args.start_date, args.end_date, parse_kinds("quotes,trades"), config)
    by_day: dict[str, dict[str, DownloadJob]] = {}
    for job in jobs:
        by_day.setdefault(job.session_date, {})[job.kind] = job
    days = [
        DayFiles(source_date=source_date, quote_job=kinds["quotes"], trade_job=kinds["trades"])
        for source_date, kinds in sorted(by_day.items())
        if "quotes" in kinds and "trades" in kinds
    ]
    if args.day_offset:
        days = days[int(args.day_offset) :]
    if args.limit_days:
        days = days[: int(args.limit_days)]
    return days


def ensure_day_files(day: DayFiles, config: DownloadConfig) -> dict[str, Any]:
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(download_one, config, day.quote_job, 0, None),
            executor.submit(download_one, config, day.trade_job, 1, None),
        ]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    statuses = {f"{result.kind}:{result.session_date}": result.status for result in results}
    ok = all(result.status in {"downloaded", "skipped_complete"} for result in results)
    return {"source_date": day.source_date, "ok": ok, "statuses": statuses, "seconds": time.time() - t0}


def event_date_expr_from_us(expr: str) -> str:
    return f"toDate(fromUnixTimestamp64Micro(toInt64({expr}), 'UTC'))"


def quote_clean_predicate() -> str:
    bid_price = "toFloat64OrZero(bid_price)"
    ask_price = "toFloat64OrZero(ask_price)"
    delta_us = "intDiv(toInt64OrZero(participant_timestamp) - toInt64OrZero(sip_timestamp), 1000)"
    issue_flags = (
        f"toUInt16(if({bid_price} <= 0, 1, 0) + "
        f"if({ask_price} <= 0, 2, 0) + "
        "if(toFloat64OrZero(bid_size) <= 0, 4, 0) + "
        "if(toFloat64OrZero(ask_size) <= 0, 8, 0) + "
        f"if({delta_us} < -2147483648 OR {delta_us} > 2147483647, 16, 0) + "
        f"if({price_precision_clipped_sql(bid_price)}, 32, 0) + "
        f"if({price_precision_clipped_sql(ask_price)}, 64, 0))"
    )
    bid_scale = scale_code_sql(bid_price)
    ask_scale = scale_code_sql(ask_price)
    return f"""
ticker != ''
AND toUInt64OrZero(sip_timestamp) > 0
AND toUInt32OrZero(sequence_number) > 0
AND {price_int_sql(bid_price)} > 0
AND {price_int_sql(ask_price)} > 0
AND toFloat64OrZero(bid_size) > 0
AND toFloat64OrZero(ask_size) > 0
AND if({bid_scale} = 1, {price_int_sql(bid_price)} / 10000.0, {price_int_sql(bid_price)} / 100.0)
    <= if({ask_scale} = 1, {price_int_sql(ask_price)} / 10000.0, {price_int_sql(ask_price)} / 100.0)
AND {issue_flags} = 0
""".strip()


def trade_clean_predicate() -> str:
    price = "toFloat64OrZero(price)"
    delta_us = "intDiv(toInt64OrZero(participant_timestamp) - toInt64OrZero(sip_timestamp), 1000)"
    issue_flags = (
        f"toUInt16(if({price} <= 0, 1, 0) + "
        "if(toFloat64OrZero(size) <= 0, 2, 0) + "
        f"if({delta_us} < -2147483648 OR {delta_us} > 2147483647, 4, 0) + "
        f"if({price_precision_clipped_sql(price)}, 8, 0))"
    )
    return f"""
ticker != ''
AND toUInt64OrZero(sip_timestamp) > 0
AND toUInt32OrZero(sequence_number) > 0
AND {price_int_sql(price)} > 0
AND toFloat64OrZero(size) > 0
AND {issue_flags} = 0
""".strip()


def raw_event_union_sql(args: argparse.Namespace, day: DayFiles) -> str:
    quote_path = windows_path_to_clickhouse_path(Path(day.quote_job.destination), Path(args.flatfiles_root_win), args.flatfiles_root_ch)
    trade_path = windows_path_to_clickhouse_path(Path(day.trade_job.destination), Path(args.flatfiles_root_win), args.flatfiles_root_ch)
    bid_price = "toFloat64OrZero(bid_price)"
    ask_price = "toFloat64OrZero(ask_price)"
    trade_price = "toFloat64OrZero(price)"
    bid_scale = scale_code_sql(bid_price)
    ask_scale = scale_code_sql(ask_price)
    trade_scale = scale_code_sql(trade_price)
    quote_flags = f"toUInt8({bid_scale} + ({ask_scale} * 2) + ({tape_code_sql('tape')} * 4))"
    trade_flags = f"toUInt8({trade_scale} + ({tape_code_sql('tape')} * 2) + (toUInt8(greatest(0, least(15, toInt16OrZero(correction)))) * 8))"
    return f"""
    SELECT
        q.ticker AS ticker,
        toUInt8(0) AS event_type,
        q.sip_timestamp_us AS sip_timestamp_us,
        q.sequence_number AS sequence_number,
        q.ask_price_int AS price_primary_int,
        q.bid_price_int AS price_secondary_int,
        toFloat32(q.ask_size) AS size_primary,
        toFloat32(q.bid_size) AS size_secondary,
        q.ask_exchange AS exchange_primary,
        q.bid_exchange AS exchange_secondary,
        toUInt8(
            bitOr(
                bitOr(bitAnd(bitShiftRight(q.quote_flags, 1), 1), bitShiftLeft(bitAnd(q.quote_flags, 1), 1)),
                bitShiftLeft(bitAnd(bitShiftRight(q.quote_flags, 2), 7), 2)
            )
        ) AS event_flags,
        {quote_condition_pack_expr()} AS conditions_packed,
        q.event_date AS event_date
    FROM
    (
        SELECT
            ticker,
            toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)) AS sip_timestamp_us,
            toUInt32OrZero(sequence_number) AS sequence_number,
            {price_int_sql(bid_price)} AS bid_price_int,
            {price_int_sql(ask_price)} AS ask_price_int,
            toUInt32(toFloat64OrZero(bid_size)) AS bid_size,
            toUInt32(toFloat64OrZero(ask_size)) AS ask_size,
            toUInt8OrZero(bid_exchange) AS bid_exchange,
            toUInt8OrZero(ask_exchange) AS ask_exchange,
            conditions,
            indicators,
            {quote_flags} AS quote_flags,
            {event_date_expr_from_us("intDiv(toUInt64OrZero(sip_timestamp), 1000)")} AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4
        FROM file({sql_string(quote_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
        WHERE {quote_clean_predicate()}
    ) AS q
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc1 ON qc1.modifier_int = q.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc2 ON qc2.modifier_int = q.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc3 ON qc3.modifier_int = q.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc4 ON qc4.modifier_int = q.condition_code_4

    UNION ALL

    SELECT
        t.ticker AS ticker,
        toUInt8(1) AS event_type,
        t.sip_timestamp_us AS sip_timestamp_us,
        t.sequence_number AS sequence_number,
        t.price_int AS price_primary_int,
        toUInt32(0) AS price_secondary_int,
        t.size AS size_primary,
        toFloat32(0) AS size_secondary,
        t.exchange AS exchange_primary,
        toUInt8(0) AS exchange_secondary,
        toUInt8(
            bitOr(
                bitAnd(t.trade_flags, 1),
                bitShiftLeft(bitAnd(bitShiftRight(t.trade_flags, 1), 7), 2)
            )
        ) AS event_flags,
        {trade_condition_pack_expr()} AS conditions_packed,
        t.event_date AS event_date
    FROM
    (
        SELECT
            ticker,
            toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)) AS sip_timestamp_us,
            toUInt32OrZero(sequence_number) AS sequence_number,
            {price_int_sql(trade_price)} AS price_int,
            toFloat32OrZero(size) AS size,
            toUInt8OrZero(exchange) AS exchange,
            conditions,
            {trade_flags} AS trade_flags,
            {event_date_expr_from_us("intDiv(toUInt64OrZero(sip_timestamp), 1000)")} AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            {condition_code_expr(5)} AS condition_code_5
        FROM file({sql_string(trade_path)}, 'CSVWithNames', {sql_string(TRADE_SCHEMA_STRING)})
        WHERE {trade_clean_predicate()}
    ) AS t
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc1 ON tc1.modifier_int = t.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc2 ON tc2.modifier_int = t.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc3 ON tc3.modifier_int = t.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc4 ON tc4.modifier_int = t.condition_code_4
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc5 ON tc5.modifier_int = t.condition_code_5
"""


def condition_reference_subquery(args: argparse.Namespace, table: str) -> str:
    return (
        f"(SELECT modifier_int, min(dense_id) AS dense_id "
        f"FROM {quote_ident(args.database)}.{quote_ident(table)} "
        "GROUP BY modifier_int)"
    )


def insert_direct_day_sql(args: argparse.Namespace, day: DayFiles, build_step: int) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{table}
(
    ticker,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed,
    event_date
)
SELECT
    e.ticker,
    coalesce(c.ordinal_offset, toUInt64(0))
        + toUInt64(row_number() OVER (PARTITION BY e.ticker ORDER BY e.sip_timestamp_us, e.sequence_number, e.event_type) - 1) AS ordinal,
    e.event_type,
    e.sip_timestamp_us,
    e.price_primary_int,
    e.price_secondary_int,
    e.size_primary,
    e.size_secondary,
    e.exchange_primary,
    e.exchange_secondary,
    e.event_flags,
    e.conditions_packed,
    e.event_date
FROM
(
{raw_event_union_sql(args, day)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
ORDER BY e.ticker, ordinal
{query_settings(args)}
"""


def insert_direct_day_continuity_sql(args: argparse.Namespace, day: DayFiles, build_step: int) -> str:
    db = quote_ident(args.database)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{continuity_table}
(
    ticker,
    build_step,
    source_date,
    event_count,
    next_ordinal,
    last_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
)
SELECT
    e.ticker,
    toUInt32({int(build_step)}) AS build_step,
    toDate({sql_string(day.source_date)}) AS source_date,
    count() AS event_count,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() AS next_ordinal,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() - 1 AS last_ordinal,
    min(e.sip_timestamp_us) AS first_sip_timestamp_us,
    max(e.sip_timestamp_us) AS last_sip_timestamp_us
FROM
(
{raw_event_union_sql(args, day)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
GROUP BY e.ticker, c.ordinal_offset
{query_settings(args)}
"""


def delete_day_continuity_sql(args: argparse.Namespace, day: DayFiles) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
DELETE WHERE source_date = toDate({sql_string(day.source_date)})
{mutation_settings(args)}
"""


def build_step_for_date(value: str) -> int:
    return date.fromisoformat(value).toordinal()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def ensure_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_events_table_sql(args))
    client.execute(create_manifest_table_sql(args))
    client.execute(create_continuity_table_sql(args))


def run_day(client: ClickHouseHttpClient, args: argparse.Namespace, day: DayFiles, run_id: str, report_path: Path) -> str:
    job = DayJob(source_date=day.source_date, build_step=build_step_for_date(day.source_date))
    status = latest_day_status(client, args, job)
    if status == "ok":
        print(f"DAY SKIP {day.source_date} status=ok", flush=True)
        return "skipped"
    if status in {"failed", "started", "interrupted"} and not (args.retry_failed or args.retry_started):
        print(f"DAY SKIP {day.source_date} status={status}; pass retry flags to revisit", flush=True)
        return "skipped"
    if status in {"failed", "started", "interrupted"}:
        if not args.force_day_delete:
            raise RuntimeError(f"day={day.source_date} status={status}; rerun with --force-day-delete to avoid duplicate rows")
        run_profiled(client, f"delete_events_day_{day.source_date}", delete_day_sql(args, job))
        run_profiled(client, f"delete_continuity_day_{day.source_date}", delete_day_continuity_sql(args, day))

    insert_day_manifest(client, args, job, status="started", run_id=run_id)
    try:
        profile = run_profiled(client, f"insert_events_from_flatfiles_{day.source_date}", insert_direct_day_sql(args, day, job.build_step))
        continuity_profile = run_profiled(
            client,
            f"insert_events_continuity_from_flatfiles_{day.source_date}",
            insert_direct_day_continuity_sql(args, day, job.build_step),
        )
        insert_day_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
        append_jsonl(
            report_path,
            {
                "type": "day",
                "source_date": day.source_date,
                "status": "ok",
                "event_profile": asdict(profile),
                "continuity_profile": asdict(continuity_profile),
            },
        )
        print(
            f"DAY OK {day.source_date} events_seconds={profile.wall_seconds:.1f} "
            f"written_rows={profile.written_rows:,}",
            flush=True,
        )
        return "ok"
    except KeyboardInterrupt:
        profile = QueryProfile(label=f"insert_events_from_flatfiles_{day.source_date}", query_id="", wall_seconds=0.0, exception="KeyboardInterrupt")
        insert_day_manifest(client, args, job, status="interrupted", run_id=run_id, profile=profile, exception="KeyboardInterrupt")
        raise
    except Exception as exc:
        profile = QueryProfile(label=f"insert_events_from_flatfiles_{day.source_date}", query_id="", wall_seconds=0.0, exception=repr(exc))
        insert_day_manifest(client, args, job, status="failed", run_id=run_id, profile=profile, exception=repr(exc))
        append_jsonl(report_path, {"type": "day", "source_date": day.source_date, "status": "failed", "exception": repr(exc)})
        raise


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"flatfile_event_update_{run_id}.jsonl"
    print("=" * 100, flush=True)
    print("Massive SIP flatfile download + direct event update", flush=True)
    print(f"database={args.database} events_table={args.events_table}", flush=True)
    print(f"date_range={args.start_date} -> {args.end_date}", flush=True)
    print(f"flatfiles_root_win={args.flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={args.flatfiles_root_ch}", flush=True)
    print(f"download_workers={args.download_workers} max_threads={args.max_threads}", flush=True)
    print(f"storage_policy={args.storage_policy}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys() + ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'S3_ENDPOINT_URL', 'BUCKET'])}", flush=True)
    print(f"loaded_env_files={loaded_env_files}", flush=True)
    print("=" * 100, flush=True)

    config = download_config(args)
    days = source_days(args, config)
    if not days:
        print("No complete quote/trade day pairs discovered.", flush=True)
        return
    print(f"Discovered {len(days):,} complete quote/trade day pairs", flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    ensure_tables(client, args)

    download_results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.download_workers))) as executor:
        futures = {executor.submit(ensure_day_files, day, config): day for day in days}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            day = futures[future]
            result = future.result()
            download_results[day.source_date] = result
            append_jsonl(report_path, {"type": "download", **result})
            print(
                f"DOWNLOAD [{index:,}/{len(days):,}] {day.source_date} "
                f"ok={result['ok']} seconds={result['seconds']:.1f} statuses={result['statuses']}",
                flush=True,
            )

    completed = 0
    started = time.time()
    for day in sorted(days, key=lambda item: item.source_date):
        if not download_results.get(day.source_date, {}).get("ok"):
            print(f"DAY SKIP {day.source_date} because download was not complete", flush=True)
            continue
        completed += 1
        elapsed = time.time() - started
        rate = completed / elapsed if elapsed > 0 else 0.0
        eta = (len(days) - completed) / rate if rate > 0 else 0.0
        print(
            f"DAY START [{completed:,}/{len(days):,}] {day.source_date} "
            f"elapsed_hours={elapsed / 3600:.2f} eta_hours={eta / 3600:.2f}",
            flush=True,
        )
        if args.dry_run:
            continue
        run_day(client, args, day, run_id, report_path)

    print(f"DONE report={report_path}", flush=True)


if __name__ == "__main__":
    main()
