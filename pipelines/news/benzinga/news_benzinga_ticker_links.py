from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_CLICKHOUSE_URL,
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_SOURCE_TABLE = "benzinga_news_normalized_v1"
DEFAULT_TARGET_TABLE = "benzinga_news_ticker_v1"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_ticker_links")


@dataclass(frozen=True, slots=True)
class SourceStats:
    rows: int
    rows_with_tickers: int
    expected_ticker_links: int
    max_distinct_tickers: int
    min_published_at_utc: str
    max_published_at_utc: str


@dataclass(frozen=True, slots=True)
class TargetStats:
    rows: int
    unique_news: int
    duplicate_news_ticker_links: int
    min_published_at_utc: str
    max_published_at_utc: str


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    execute: bool
    rebuild: bool
    source: str
    target: str
    source_stats: SourceStats
    target_before: TargetStats | None
    target_after: TargetStats | None
    inserted_delta: int
    wall_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create and backfill q_live.benzinga_news_ticker_v1 from the loaded "
            "legacy Benzinga normalized news table. The source table remains the "
            "text/source-of-truth table; this table is only the ticker-time join index."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--source-table", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_TABLE") or DEFAULT_SOURCE_TABLE)
    parser.add_argument("--target-table", default=os.environ.get("NEWS_BENZINGA_TICKER_TABLE") or DEFAULT_TARGET_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_TICKER_LINKS_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--max-threads", type=int, default=int(os.environ.get("NEWS_BENZINGA_TICKER_LINKS_MAX_THREADS", "24")))
    parser.add_argument("--max-memory-usage", default=os.environ.get("NEWS_BENZINGA_TICKER_LINKS_MAX_MEMORY", "0"))
    parser.add_argument("--execute", action="store_true", help="Create/backfill the target table. Without this, only print stats and planned SQL.")
    parser.add_argument("--audit-only", action="store_true", help="Only audit source/target counts. Does not create or insert.")
    parser.add_argument("--create-only", action="store_true", help="Create the target table and exit.")
    parser.add_argument("--rebuild", action="store_true", help="Truncate the target table before inserting. Use this for reruns.")
    parser.add_argument("--force", action="store_true", help="Allow insert into a non-empty target without truncating. Usually not recommended.")
    parser.add_argument("--no-final", action="store_true", help="Read the source table without FINAL. Faster, but less defensive for ReplacingMergeTree.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    print("=" * 96, flush=True)
    print("Benzinga news ticker link build", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"source={args.database}.{args.source_table}", flush=True)
    print(f"target={args.database}.{args.target_table}", flush=True)
    print(f"execute={args.execute} audit_only={args.audit_only} create_only={args.create_only} rebuild={args.rebuild}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_keys_status(), sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    started = time.perf_counter()
    ensure_source_exists(client, args)
    source_stats = load_source_stats(client, args)
    target_before = load_target_stats_if_exists(client, args)

    print("source_stats=" + json.dumps(asdict(source_stats), sort_keys=True), flush=True)
    if target_before is None:
        print("target_before=missing", flush=True)
    else:
        print("target_before=" + json.dumps(asdict(target_before), sort_keys=True), flush=True)

    if args.audit_only:
        write_summary(run_root, RunSummary(run_id, False, False, table_name(args, args.source_table), table_name(args, args.target_table), source_stats, target_before, target_before, 0, time.perf_counter() - started))
        return

    create_sql = create_target_table_sql(args)
    insert_sql_text = insert_ticker_links_sql(args)
    if not args.execute:
        print("dry_run_create_sql=", flush=True)
        print(create_sql, flush=True)
        print("dry_run_insert_sql=", flush=True)
        print(insert_sql_text, flush=True)
        write_summary(run_root, RunSummary(run_id, False, False, table_name(args, args.source_table), table_name(args, args.target_table), source_stats, target_before, target_before, 0, time.perf_counter() - started))
        print("dry_run=done; pass --execute to create/backfill", flush=True)
        return

    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_sql)
    if args.create_only:
        target_after = load_target_stats(client, args)
        write_summary(run_root, RunSummary(run_id, True, False, table_name(args, args.source_table), table_name(args, args.target_table), source_stats, target_before, target_after, 0, time.perf_counter() - started))
        print("create_only=done", flush=True)
        return

    target_before = load_target_stats(client, args)
    if args.rebuild:
        print("target_action=truncate", flush=True)
        client.execute(f"TRUNCATE TABLE {table_name(args, args.target_table)}")
        target_before = load_target_stats(client, args)
    elif target_before.rows > 0 and not args.force:
        raise SystemExit(
            f"target table is not empty ({target_before.rows:,} rows). "
            "Use --rebuild to truncate and refill, or --force if duplicate physical rows are intentional."
        )

    print("insert=start", flush=True)
    insert_started = time.perf_counter()
    client.execute(insert_sql_text + settings_sql(args))
    insert_elapsed = time.perf_counter() - insert_started
    target_after = load_target_stats(client, args)
    inserted_delta = target_after.rows - target_before.rows
    print(f"insert=done inserted_delta={inserted_delta:,} elapsed_seconds={insert_elapsed:.1f}", flush=True)

    if inserted_delta != source_stats.expected_ticker_links:
        raise RuntimeError(
            "ticker link count mismatch after insert: "
            f"expected_delta={source_stats.expected_ticker_links:,} actual_delta={inserted_delta:,}"
        )
    if target_after.duplicate_news_ticker_links:
        raise RuntimeError(f"target contains duplicate canonical_news_id/ticker links: {target_after.duplicate_news_ticker_links:,}")

    summary = RunSummary(
        run_id=run_id,
        execute=True,
        rebuild=args.rebuild,
        source=table_name(args, args.source_table),
        target=table_name(args, args.target_table),
        source_stats=source_stats,
        target_before=target_before,
        target_after=target_after,
        inserted_delta=inserted_delta,
        wall_seconds=time.perf_counter() - started,
    )
    write_summary(run_root, summary)
    print("target_after=" + json.dumps(asdict(target_after), sort_keys=True), flush=True)
    print(f"summary_json={run_root / 'benzinga_news_ticker_links_summary.json'}", flush=True)


def source_table_expr(args: argparse.Namespace) -> str:
    suffix = "" if args.no_final else " FINAL"
    return table_name(args, args.source_table) + suffix


def table_name(args: argparse.Namespace, table: str) -> str:
    return f"{quote_ident(args.database)}.{quote_ident(table)}"


def ticker_array_expr() -> str:
    return (
        "arrayDistinct(arrayFilter(x -> x != '', "
        "arrayMap(x -> upperUTF8(trimBoth(toString(x))), tickers)))"
    )


def create_target_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table_name(args, args.target_table)}
(
    canonical_news_id String,
    provider LowCardinality(String),
    provider_article_id String,
    published_date Date,
    published_at_utc DateTime64(9, 'UTC'),
    ticker LowCardinality(String),
    ticker_index UInt16,
    ticker_count UInt16,
    text_hash String,
    content_quality_flags Array(String),
    normalizer_version LowCardinality(String),
    updated_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (ticker, published_at_utc, canonical_news_id)
{merge_tree_settings(args.storage_policy)}
"""


def insert_ticker_links_sql(args: argparse.Namespace) -> str:
    source = source_table_expr(args)
    ticker_expr = ticker_array_expr()
    return f"""
INSERT INTO {table_name(args, args.target_table)}
(
    canonical_news_id,
    provider,
    provider_article_id,
    published_date,
    published_at_utc,
    ticker,
    ticker_index,
    ticker_count,
    text_hash,
    content_quality_flags,
    normalizer_version,
    updated_at_utc
)
SELECT
    canonical_news_id,
    provider,
    provider_article_id,
    published_date,
    published_at_utc,
    ticker,
    toUInt16(ticker_index) AS ticker_index,
    toUInt16(length(tickers_clean)) AS ticker_count,
    text_hash,
    content_quality_flags,
    normalizer_version,
    updated_at_utc
FROM
(
    SELECT
        canonical_news_id,
        provider,
        provider_article_id,
        published_date,
        published_at_utc,
        {ticker_expr} AS tickers_clean,
        text_hash,
        content_quality_flags,
        normalizer_version,
        updated_at_utc
    FROM {source}
    WHERE length(tickers) > 0
)
ARRAY JOIN
    tickers_clean AS ticker,
    arrayEnumerate(tickers_clean) AS ticker_index
"""


def load_source_stats(client: ClickHouseHttpClient, args: argparse.Namespace) -> SourceStats:
    ticker_expr = ticker_array_expr()
    sql = f"""
SELECT
    count() AS rows,
    countIf(length(tickers_clean) > 0) AS rows_with_tickers,
    sum(length(tickers_clean)) AS expected_ticker_links,
    max(length(tickers_clean)) AS max_distinct_tickers,
    toString(min(published_at_utc)) AS min_published_at_utc,
    toString(max(published_at_utc)) AS max_published_at_utc
FROM
(
    SELECT
        published_at_utc,
        {ticker_expr} AS tickers_clean
    FROM {source_table_expr(args)}
)
FORMAT JSONEachRow
"""
    row = query_one_json(client, sql)
    return SourceStats(
        rows=int(row["rows"]),
        rows_with_tickers=int(row["rows_with_tickers"]),
        expected_ticker_links=int(row["expected_ticker_links"]),
        max_distinct_tickers=int(row["max_distinct_tickers"]),
        min_published_at_utc=str(row["min_published_at_utc"]),
        max_published_at_utc=str(row["max_published_at_utc"]),
    )


def load_target_stats_if_exists(client: ClickHouseHttpClient, args: argparse.Namespace) -> TargetStats | None:
    if not table_exists(client, args.database, args.target_table):
        return None
    return load_target_stats(client, args)


def load_target_stats(client: ClickHouseHttpClient, args: argparse.Namespace) -> TargetStats:
    sql = f"""
SELECT
    count() AS rows,
    uniqExact(canonical_news_id) AS unique_news,
    count() - uniqExact(tuple(canonical_news_id, ticker)) AS duplicate_news_ticker_links,
    toString(min(published_at_utc)) AS min_published_at_utc,
    toString(max(published_at_utc)) AS max_published_at_utc
FROM {table_name(args, args.target_table)}
FORMAT JSONEachRow
"""
    row = query_one_json(client, sql)
    return TargetStats(
        rows=int(row["rows"]),
        unique_news=int(row["unique_news"]),
        duplicate_news_ticker_links=int(row["duplicate_news_ticker_links"]),
        min_published_at_utc=str(row["min_published_at_utc"]),
        max_published_at_utc=str(row["max_published_at_utc"]),
    )


def ensure_source_exists(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if not table_exists(client, args.database, args.source_table):
        raise SystemExit(f"source table does not exist: {args.database}.{args.source_table}")


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    sql = f"""
SELECT count()
FROM system.tables
WHERE database = {sql_string(database)}
  AND name = {sql_string(table)}
"""
    return int((client.execute(sql).strip() or "0").splitlines()[0]) > 0


def query_one_json(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    text = client.execute(sql)
    for line in text.splitlines():
        if line.strip():
            return json.loads(line)
    raise RuntimeError("query returned no rows")


def write_summary(run_root: Path, summary: RunSummary) -> None:
    path = run_root / "benzinga_news_ticker_links_summary.json"
    path.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")


def settings_sql(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "" if not settings else "\nSETTINGS " + ", ".join(settings)


def merge_tree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return "SETTINGS " + ", ".join(settings)


def default_clickhouse_url() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or DEFAULT_CLICKHOUSE_URL
    )


def default_clickhouse_user() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.environ.get(CLICKHOUSE_WORKSTATION_USER_ENV)
        or os.environ.get(CLICKHOUSE_USER_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_USER_ENV)
        or "default"
    )


def default_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get(CLICKHOUSE_WORKSTATION_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_ENV)
        or ""
    )


def secret_keys_status() -> dict[str, str]:
    return secret_status(
        [
            "QLIVE_MIGRATION_CLICKHOUSE_URL",
            "QLIVE_MIGRATION_CLICKHOUSE_USER",
            "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
            "QMD_CLICKHOUSE_URL",
            "QMD_CLICKHOUSE_USER",
            "QMD_CLICKHOUSE_PASSWORD",
            "REAL_LIVE_CLICKHOUSE_WRITE_URL",
            "REAL_LIVE_CLICKHOUSE_WRITE_USER",
            "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            "CLICKHOUSE_LIVE_STORAGE_POLICY",
        ]
    )


def parse_size_bytes(value: str) -> int:
    text = value.strip().upper()
    if text.isdigit():
        return int(text)
    multipliers = {
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }
    for suffix, multiplier in sorted(multipliers.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)].strip()) * multiplier)
    raise ValueError(f"invalid size: {value}")


if __name__ == "__main__":
    main()
