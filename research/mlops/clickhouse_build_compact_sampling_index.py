from __future__ import annotations

import argparse
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
from research.mlops.clickhouse_delete_compact_audit_rows import (  # noqa: E402
    default_clickhouse_url_with_network_fallback,
)
from research.mlops.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    env_status_keys,
)
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)


DEFAULT_TRAIN_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_TABLE = "validation_2026"
DEFAULT_TRAIN_START = "2019-01-01"
DEFAULT_TRAIN_END = "2025-12-31"
DEFAULT_VALIDATION_START = "2026-01-01"
DEFAULT_VALIDATION_END = "2099-12-31"
DEFAULT_EVENTS_PER_CHUNK = 128
DEFAULT_CLEAN_MODE = "structural"


@dataclass(frozen=True, slots=True)
class IndexJob:
    table: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class IndexSummary:
    table: str
    start_date: str
    end_date: str
    ticker_count: int
    total_events: int
    min_event_count: int
    max_event_count: int
    first_sip_timestamp_us: int
    last_sip_timestamp_us: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build compact SIP ticker-level sampling index tables for masked-event training."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--train-table", default=DEFAULT_TRAIN_TABLE)
    parser.add_argument("--validation-table", default=DEFAULT_VALIDATION_TABLE)
    parser.add_argument("--train-start-date", default=DEFAULT_TRAIN_START)
    parser.add_argument("--train-end-date", default=DEFAULT_TRAIN_END)
    parser.add_argument("--validation-start-date", default=DEFAULT_VALIDATION_START)
    parser.add_argument("--validation-end-date", default=DEFAULT_VALIDATION_END)
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--min-events", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--clean-mode", choices=("structural", "issue_flags_zero"), default=DEFAULT_CLEAN_MODE)
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=0)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_sampling_index"))
    parser.add_argument("--rebuild", action="store_true", help="Drop and recreate index tables before inserting.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned DDL/DML and do not mutate ClickHouse.")
    return parser.parse_args()


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def mergetree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return "SETTINGS " + ", ".join(settings)


def create_index_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    event_count UInt64,
    first_sip_timestamp_us UInt64,
    last_sip_timestamp_us UInt64,
    min_valid_ordinal UInt64,
    max_valid_ordinal UInt64
)
ENGINE = MergeTree
ORDER BY ticker
{mergetree_settings(storage_policy)}
"""


def clean_predicate(alias: str, clean_mode: str) -> str:
    base = f"{alias}.ticker != '' AND {alias}.sip_timestamp_us > 0 AND {alias}.sequence_number > 0"
    if clean_mode == "issue_flags_zero":
        return base + f" AND {alias}.issue_flags = 0"
    return base


def event_union_sql(args: argparse.Namespace, job: IndexJob) -> str:
    quote_predicate = clean_predicate("q", args.clean_mode)
    trade_predicate = clean_predicate("t", args.clean_mode)
    return f"""
    SELECT q.ticker, q.sip_timestamp_us, q.sequence_number, toUInt8(0) AS event_type
    FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)} AS q
    WHERE q.event_date BETWEEN toDate({sql_string(job.start_date)}) AND toDate({sql_string(job.end_date)})
      AND {quote_predicate}
    UNION ALL
    SELECT t.ticker, t.sip_timestamp_us, t.sequence_number, toUInt8(1) AS event_type
    FROM {quote_ident(args.database)}.{quote_ident(args.trade_table)} AS t
    WHERE t.event_date BETWEEN toDate({sql_string(job.start_date)}) AND toDate({sql_string(job.end_date)})
      AND {trade_predicate}
"""


def insert_index_sql(args: argparse.Namespace, job: IndexJob) -> str:
    min_events = max(int(args.min_events), int(args.events_per_chunk))
    min_valid = int(args.events_per_chunk) - 1
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(job.table)}
SELECT
    ticker,
    event_count,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    toUInt64({min_valid}) AS min_valid_ordinal,
    event_count - 1 AS max_valid_ordinal
FROM
(
    SELECT
        ticker,
        count() AS event_count,
        min(sip_timestamp_us) AS first_sip_timestamp_us,
        max(sip_timestamp_us) AS last_sip_timestamp_us
    FROM
    (
{event_union_sql(args, job)}
    )
    GROUP BY ticker
    HAVING event_count >= {min_events}
)
{query_settings(args)}
"""


def summarize_index(client: ClickHouseHttpClient, database: str, job: IndexJob) -> IndexSummary:
    rows = client.query_tsv(
        f"""
SELECT
    count(),
    if(count() = 0, 0, sum(event_count)),
    if(count() = 0, 0, min(event_count)),
    if(count() = 0, 0, max(event_count)),
    if(count() = 0, 0, min(first_sip_timestamp_us)),
    if(count() = 0, 0, max(last_sip_timestamp_us))
FROM {quote_ident(database)}.{quote_ident(job.table)}
"""
    ).strip()
    parts = rows.split("\t") if rows else ["0", "0", "0", "0", "0", "0"]
    return IndexSummary(
        table=job.table,
        start_date=job.start_date,
        end_date=job.end_date,
        ticker_count=int(parts[0] or 0),
        total_events=int(parts[1] or 0),
        min_event_count=int(parts[2] or 0),
        max_event_count=int(parts[3] or 0),
        first_sip_timestamp_us=int(parts[4] or 0),
        last_sip_timestamp_us=int(parts[5] or 0),
    )


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def print_sql_preview(label: str, sql: str, *, limit: int = 1800) -> None:
    body = sql.strip()
    print(f"--- {label} SQL preview ---", flush=True)
    print(body[:limit] + ("\n..." if len(body) > limit else ""), flush=True)


def execute_job(client: ClickHouseHttpClient, args: argparse.Namespace, job: IndexJob, report_path: Path) -> None:
    table_name = f"{quote_ident(args.database)}.{quote_ident(job.table)}"
    print("=" * 96, flush=True)
    print(f"INDEX START table={table_name} range={job.start_date}->{job.end_date}", flush=True)
    if args.rebuild:
        drop_sql = f"DROP TABLE IF EXISTS {table_name}"
        if args.dry_run:
            print_sql_preview("drop", drop_sql)
        else:
            client.execute(drop_sql)
            print(f"DROPPED {table_name}", flush=True)
    create_sql = create_index_table_sql(args.database, job.table, args.storage_policy)
    insert_sql = insert_index_sql(args, job)
    if args.dry_run:
        print_sql_preview("create", create_sql)
        print_sql_preview("insert", insert_sql)
        return
    client.execute(create_sql)
    client.execute(f"TRUNCATE TABLE {table_name}")
    profile = run_profiled(client, f"build_sampling_index_{job.table}", insert_sql)
    summary = summarize_index(client, args.database, job)
    append_jsonl(report_path, {"type": "profile", "job": asdict(job), "profile": asdict(profile), "summary": asdict(summary)})
    print(
        f"INDEX DONE table={job.table} tickers={summary.ticker_count:,} "
        f"events={summary.total_events:,} min_events={summary.min_event_count:,} "
        f"max_events={summary.max_event_count:,} wall_seconds={profile.wall_seconds:.1f}",
        flush=True,
    )


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    if args.events_per_chunk < 2:
        raise SystemExit("--events-per-chunk must be >= 2")
    output_root = Path(args.output_root_win)
    run_id = "compact_sampling_index_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    jobs = [
        IndexJob(args.train_table, args.train_start_date, args.train_end_date),
        IndexJob(args.validation_table, args.validation_start_date, args.validation_end_date),
    ]

    print("=" * 96, flush=True)
    print("Compact SIP sampling index builder", flush=True)
    print(f"database={args.database} quote_table={args.quote_table} trade_table={args.trade_table}", flush=True)
    print(f"jobs={[asdict(job) for job in jobs]}", flush=True)
    print(
        f"events_per_chunk={args.events_per_chunk} min_events={args.min_events} "
        f"clean_mode={args.clean_mode} rebuild={args.rebuild} dry_run={args.dry_run}",
        flush=True,
    )
    print(f"storage_policy={args.storage_policy or '<default>'} settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    if not args.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
        append_jsonl(
            report_path,
            {
                "type": "run_start",
                "run_id": run_id,
                "args": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
                "jobs": [asdict(job) for job in jobs],
            },
        )

    started = time.perf_counter()
    for job in jobs:
        execute_job(client, args, job, report_path)
    print("=" * 96, flush=True)
    print(f"DONE elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
