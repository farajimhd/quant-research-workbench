from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.benchmarks.clickhouse_compact_schema_codec_benchmark import (  # noqa: E402
    EVENT_DATE_TIMEZONE,
    event_date_expr,
    quote_table_sql,
    trade_table_sql,
)
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    env_status_keys,
    validate_event_date_expression,
    validate_target_schema,
    REQUIRED_QUOTE_COLUMN_TYPES,
    REQUIRED_TRADE_COLUMN_TYPES,
)
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_FIX_MANIFEST_TABLE = "event_date_fix_manifest"
DEFAULT_QUOTE_SHADOW = "quotes_event_date_utc_rebuild"
DEFAULT_TRADE_SHADOW = "trades_event_date_utc_rebuild"

QUOTE_COLUMNS = [
    "ticker",
    "sip_timestamp_us",
    "participant_delta_us",
    "sequence_number",
    "bid_price_int",
    "ask_price_int",
    "bid_size",
    "ask_size",
    "bid_exchange",
    "ask_exchange",
    "conditions",
    "indicators",
    "quote_flags",
    "issue_flags",
]

TRADE_COLUMNS = [
    "ticker",
    "sip_timestamp_us",
    "participant_delta_us",
    "sequence_number",
    "price_int",
    "size",
    "exchange",
    "conditions",
    "trade_flags",
    "issue_flags",
]


@dataclass(frozen=True, slots=True)
class TablePlan:
    source_table: str
    shadow_table: str
    columns: list[str]
    required_types: dict[str, str]


@dataclass(frozen=True, slots=True)
class CopyResult:
    table_name: str
    shadow_table: str
    source_month: int
    source_rows: int
    copied_rows: int
    status: str
    wall_seconds: float
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild market_sip_compact quote/trade tables so event_date is materialized in UTC. "
            "The script copies rows into shadow tables month by month, validates them, and swaps only with --swap."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--quote-shadow-table", default=DEFAULT_QUOTE_SHADOW)
    parser.add_argument("--trade-shadow-table", default=DEFAULT_TRADE_SHADOW)
    parser.add_argument("--fix-manifest-table", default=DEFAULT_FIX_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=24)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--tables", default="quotes,trades", help="Comma-separated subset: quotes,trades")
    parser.add_argument("--start-month", default="", help="Optional YYYYMM lower bound over the current source table event_date partitions.")
    parser.add_argument("--end-month", default="", help="Optional YYYYMM upper bound over the current source table event_date partitions.")
    parser.add_argument("--copy", action="store_true", help="Copy source-table rows into UTC shadow tables.")
    parser.add_argument("--validate", action="store_true", help="Validate source/shadow counts and UTC event_date consistency.")
    parser.add_argument("--swap", action="store_true", help="Rename source tables to backups and promote validated shadow tables.")
    parser.add_argument(
        "--drop-stale-backups-after-swap",
        action="store_true",
        help="After a successful swap, immediately drop the old server-local-date backup tables.",
    )
    parser.add_argument("--retry-ok", action="store_true", help="Re-copy months even when the fix manifest has latest status ok.")
    parser.set_defaults(delete_before_copy=True)
    parser.add_argument("--no-delete-before-copy", action="store_false", dest="delete_before_copy")
    parser.add_argument("--backup-suffix", default="", help="Suffix for old-table backups during --swap. Default is event_date_local_backup_<run_id>.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_event_date_fix"))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    return parser.parse_args()


def copy_settings(args: argparse.Namespace) -> str:
    settings = []
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def source_month_expr() -> str:
    # This intentionally matches the stale server-local materialization used by existing tables.
    return "toYYYYMM(toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us))))"


def create_fix_manifest_sql(database: str, table: str, storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    table_name LowCardinality(String),
    shadow_table LowCardinality(String),
    source_month UInt32,
    source_rows UInt64,
    copied_rows UInt64,
    status LowCardinality(String),
    run_id String,
    query_id String,
    wall_seconds Float64,
    exception String,
    updated_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (table_name, source_month, updated_at)
SETTINGS {", ".join(settings)}
"""


def table_plans(args: argparse.Namespace) -> list[TablePlan]:
    selected = {item.strip() for item in args.tables.split(",") if item.strip()}
    invalid = selected - {"quotes", "trades"}
    if invalid:
        raise ValueError(f"Invalid --tables entries: {sorted(invalid)}")
    plans: list[TablePlan] = []
    if "quotes" in selected:
        plans.append(TablePlan(args.quote_table, args.quote_shadow_table, QUOTE_COLUMNS, REQUIRED_QUOTE_COLUMN_TYPES))
    if "trades" in selected:
        plans.append(TablePlan(args.trade_table, args.trade_shadow_table, TRADE_COLUMNS, REQUIRED_TRADE_COLUMN_TYPES))
    return plans


def create_shadow_tables(client: ClickHouseHttpClient, args: argparse.Namespace, plans: list[TablePlan]) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_fix_manifest_sql(args.database, args.fix_manifest_table, args.storage_policy))
    for plan in plans:
        if plan.source_table == args.quote_table:
            sql = quote_table_sql(args.database, plan.shadow_table, codecs=True, storage_policy=args.storage_policy)
        else:
            sql = trade_table_sql(args.database, plan.shadow_table, codecs=True, storage_policy=args.storage_policy)
        print(f"CREATE SHADOW IF NEEDED {args.database}.{plan.shadow_table}", flush=True)
        if not args.dry_run:
            client.execute(sql)
            validate_target_schema(client, args.database, plan.shadow_table, plan.required_types)
            validate_event_date_expression(client, args.database, plan.shadow_table)


def latest_ok_months(client: ClickHouseHttpClient, args: argparse.Namespace, plan: TablePlan) -> set[int]:
    rows = client.query_tsv(
        "SELECT source_month "
        "FROM ("
        "  SELECT source_month, argMax(status, updated_at) AS latest_status "
        f"  FROM {quote_ident(args.database)}.{quote_ident(args.fix_manifest_table)} "
        f"  WHERE table_name = {sql_string(plan.source_table)} "
        f"  AND shadow_table = {sql_string(plan.shadow_table)} "
        "  GROUP BY source_month"
        ") "
        "WHERE latest_status = 'ok'"
    ).strip()
    return {int(line) for line in rows.splitlines() if line.strip()}


def source_months(client: ClickHouseHttpClient, args: argparse.Namespace, plan: TablePlan) -> list[int]:
    where = []
    if args.start_month:
        where.append(f"toYYYYMM(event_date) >= {int(args.start_month)}")
    if args.end_month:
        where.append(f"toYYYYMM(event_date) <= {int(args.end_month)}")
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    rows = client.query_tsv(
        f"SELECT DISTINCT toYYYYMM(event_date) AS source_month "
        f"FROM {quote_ident(args.database)}.{quote_ident(plan.source_table)}"
        f"{where_sql} "
        "ORDER BY source_month"
    ).strip()
    return [int(line) for line in rows.splitlines() if line.strip()]


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = client.query_tsv(sql).strip()
    return int(text) if text else 0


def insert_manifest_row(client: ClickHouseHttpClient, args: argparse.Namespace, result: CopyResult, query_id: str) -> None:
    sql = (
        f"INSERT INTO {quote_ident(args.database)}.{quote_ident(args.fix_manifest_table)} "
        "(table_name, shadow_table, source_month, source_rows, copied_rows, status, run_id, query_id, wall_seconds, exception) "
        "VALUES ("
        f"{sql_string(result.table_name)}, "
        f"{sql_string(result.shadow_table)}, "
        f"{result.source_month}, "
        f"{result.source_rows}, "
        f"{result.copied_rows}, "
        f"{sql_string(result.status)}, "
        f"{sql_string(args.run_id)}, "
        f"{sql_string(query_id)}, "
        f"{result.wall_seconds:.6f}, "
        f"{sql_string(result.exception)}"
        ")"
    )
    if not args.dry_run:
        client.execute(sql)


def wait_for_mutations(client: ClickHouseHttpClient, database: str, table: str) -> None:
    while True:
        pending = scalar_int(
            client,
            "SELECT count() "
            "FROM system.mutations "
            f"WHERE database = {sql_string(database)} "
            f"AND table = {sql_string(table)} "
            "AND is_done = 0",
        )
        if pending == 0:
            return
        print(f"WAIT mutations {database}.{table} pending={pending}", flush=True)
        time.sleep(5)


def copy_month(client: ClickHouseHttpClient, args: argparse.Namespace, plan: TablePlan, source_month: int) -> CopyResult:
    columns_sql = ", ".join(quote_ident(column) for column in plan.columns)
    old_month_expr = source_month_expr()
    source_where = f"{old_month_expr} = {source_month}"
    t0 = time.time()
    source_rows = scalar_int(
        client,
        f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.source_table)} WHERE {source_where}",
    )
    query_id = f"event_date_fix_{plan.source_table}_{source_month}_{args.run_id}"
    try:
        if args.delete_before_copy:
            existing_rows = scalar_int(
                client,
                f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.shadow_table)} WHERE {source_where}",
            )
            if existing_rows:
                print(
                    f"DELETE shadow rows before copy table={plan.shadow_table} source_month={source_month} rows={existing_rows:,}",
                    flush=True,
                )
                if not args.dry_run:
                    client.execute(
                        f"ALTER TABLE {quote_ident(args.database)}.{quote_ident(plan.shadow_table)} "
                        f"DELETE WHERE {source_where}"
                    )
                    wait_for_mutations(client, args.database, plan.shadow_table)
        print(
            f"COPY START table={plan.source_table} -> {plan.shadow_table} source_month={source_month} "
            f"source_rows={source_rows:,}",
            flush=True,
        )
        if not args.dry_run:
            client.execute(
                f"INSERT INTO {quote_ident(args.database)}.{quote_ident(plan.shadow_table)} ({columns_sql}) "
                f"SELECT {columns_sql} "
                f"FROM {quote_ident(args.database)}.{quote_ident(plan.source_table)} "
                f"WHERE {source_where}"
                f"{copy_settings(args)}",
                query_id=query_id,
            )
        copied_rows = scalar_int(
            client,
            f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.shadow_table)} WHERE {source_where}",
        )
        status = "ok" if copied_rows == source_rows else "failed"
        exception = "" if status == "ok" else f"copied_rows {copied_rows} != source_rows {source_rows}"
        result = CopyResult(plan.source_table, plan.shadow_table, source_month, source_rows, copied_rows, status, time.time() - t0, exception)
        insert_manifest_row(client, args, result, query_id)
        print(
            f"COPY {status.upper()} table={plan.source_table} source_month={source_month} "
            f"copied={copied_rows:,}/{source_rows:,} seconds={result.wall_seconds:.1f}",
            flush=True,
        )
        return result
    except Exception as exc:
        result = CopyResult(plan.source_table, plan.shadow_table, source_month, source_rows, 0, "failed", time.time() - t0, repr(exc))
        insert_manifest_row(client, args, result, query_id)
        print(f"COPY FAILED table={plan.source_table} source_month={source_month}: {exc!r}", flush=True)
        return result


def validate_shadow(client: ClickHouseHttpClient, args: argparse.Namespace, plan: TablePlan) -> dict[str, int]:
    source_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.source_table)}")
    shadow_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.shadow_table)}")
    utc_mismatches = scalar_int(
        client,
        f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.shadow_table)} "
        f"WHERE event_date != {event_date_expr()}",
    )
    source_utc_mismatches = scalar_int(
        client,
        f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(plan.source_table)} "
        f"WHERE event_date != {event_date_expr()}",
    )
    result = {
        "source_rows": source_rows,
        "shadow_rows": shadow_rows,
        "shadow_utc_mismatches": utc_mismatches,
        "source_utc_mismatches": source_utc_mismatches,
    }
    print(f"VALIDATE {plan.source_table}: {json.dumps(result, sort_keys=True)}", flush=True)
    return result


def swap_tables(client: ClickHouseHttpClient, args: argparse.Namespace, plans: list[TablePlan]) -> None:
    backup_suffix = args.backup_suffix or f"event_date_local_backup_{args.run_id}"
    backup_tables: list[str] = []
    for plan in plans:
        validation = validate_shadow(client, args, plan)
        if validation["source_rows"] != validation["shadow_rows"] or validation["shadow_utc_mismatches"] != 0:
            raise RuntimeError(f"Refusing to swap {plan.source_table}; validation failed: {validation}")
        backup_table = f"{plan.source_table}_{backup_suffix}"
        exists = scalar_int(
            client,
            "SELECT count() FROM system.tables "
            f"WHERE database = {sql_string(args.database)} AND name = {sql_string(backup_table)}",
        )
        if exists:
            raise RuntimeError(f"Backup table already exists: {args.database}.{backup_table}")
        print(f"SWAP {plan.source_table}: backup={backup_table} promote={plan.shadow_table}", flush=True)
        if not args.dry_run:
            client.execute(
                f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(plan.source_table)} "
                f"TO {quote_ident(args.database)}.{quote_ident(backup_table)}, "
                f"{quote_ident(args.database)}.{quote_ident(plan.shadow_table)} "
                f"TO {quote_ident(args.database)}.{quote_ident(plan.source_table)}"
            )
            validate_event_date_expression(client, args.database, plan.source_table)
            production_validation = validate_shadow_after_swap(client, args, plan.source_table, validation["source_rows"])
            if production_validation["utc_mismatches"] != 0 or production_validation["rows"] != validation["source_rows"]:
                raise RuntimeError(f"Post-swap validation failed for {plan.source_table}: {production_validation}")
            backup_tables.append(backup_table)
    if args.drop_stale_backups_after_swap:
        for backup_table in backup_tables:
            print(f"DROP STALE BACKUP {args.database}.{backup_table}", flush=True)
            if not args.dry_run:
                client.execute(f"DROP TABLE {quote_ident(args.database)}.{quote_ident(backup_table)}")


def validate_shadow_after_swap(client: ClickHouseHttpClient, args: argparse.Namespace, table: str, expected_rows: int) -> dict[str, int]:
    rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(table)}")
    utc_mismatches = scalar_int(
        client,
        f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(table)} "
        f"WHERE event_date != {event_date_expr()}",
    )
    result = {"rows": rows, "expected_rows": expected_rows, "utc_mismatches": utc_mismatches}
    print(f"POST-SWAP VALIDATE {table}: {json.dumps(result, sort_keys=True)}", flush=True)
    return result


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files())
    args = parse_args()
    report_root = Path(args.output_root_win)
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"compact_event_date_fix_{args.run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    plans = table_plans(args)

    print("=" * 96, flush=True)
    print("Compact SIP event_date UTC fix", flush=True)
    print(f"database={args.database} timezone={EVENT_DATE_TIMEZONE}", flush=True)
    print(f"tables={[(plan.source_table, plan.shadow_table) for plan in plans]}", flush=True)
    print(
        f"copy={args.copy} validate={args.validate} swap={args.swap} "
        f"drop_stale_backups_after_swap={args.drop_stale_backups_after_swap} dry_run={args.dry_run}",
        flush=True,
    )
    print(f"storage_policy={args.storage_policy} max_threads={args.max_threads} max_memory={args.max_memory_usage}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    create_shadow_tables(client, args, plans)
    results: list[CopyResult] = []
    if args.copy:
        for plan in plans:
            ok_months = latest_ok_months(client, args, plan) if not args.retry_ok else set()
            months = source_months(client, args, plan)
            print(f"MONTHS table={plan.source_table} count={len(months)} months={months}", flush=True)
            for source_month in months:
                if source_month in ok_months:
                    print(f"SKIP OK table={plan.source_table} source_month={source_month}", flush=True)
                    continue
                result = copy_month(client, args, plan, source_month)
                results.append(result)
                with report_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(result), sort_keys=True) + "\n")
                if result.status != "ok":
                    raise RuntimeError(f"Copy failed for {plan.source_table} source_month={source_month}: {result.exception}")

    if args.validate or args.swap:
        validations = {plan.source_table: validate_shadow(client, args, plan) for plan in plans}
        with report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"validations": validations}, sort_keys=True) + "\n")

    if args.swap:
        swap_tables(client, args, plans)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
