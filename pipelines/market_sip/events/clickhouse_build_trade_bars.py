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

from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_TIMEFRAMES = ("1s", "5s", "1m", "5m", "1d", "1w", "1mo")
DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT_WIN / "trade_bars"


@dataclass(frozen=True, slots=True)
class TimeframeSpec:
    name: str
    table: str
    bucket_sql: str
    end_sql: str


TIMEFRAME_SPECS: dict[str, TimeframeSpec] = {
    "1s": TimeframeSpec(
        name="1s",
        table="bars_1s",
        bucket_sql="toStartOfInterval(event_dt, INTERVAL 1 SECOND, 'UTC')",
        end_sql="bar_start + INTERVAL 1 SECOND",
    ),
    "5s": TimeframeSpec(
        name="5s",
        table="bars_5s",
        bucket_sql="toStartOfInterval(event_dt, INTERVAL 5 SECOND, 'UTC')",
        end_sql="bar_start + INTERVAL 5 SECOND",
    ),
    "1m": TimeframeSpec(
        name="1m",
        table="bars_1m",
        bucket_sql="toStartOfInterval(event_dt, INTERVAL 1 MINUTE, 'UTC')",
        end_sql="bar_start + INTERVAL 1 MINUTE",
    ),
    "5m": TimeframeSpec(
        name="5m",
        table="bars_5m",
        bucket_sql="toStartOfInterval(event_dt, INTERVAL 5 MINUTE, 'UTC')",
        end_sql="bar_start + INTERVAL 5 MINUTE",
    ),
    "1d": TimeframeSpec(
        name="1d",
        table="bars_1d",
        bucket_sql="toStartOfDay(event_dt, 'UTC')",
        end_sql="bar_start + INTERVAL 1 DAY",
    ),
    "1w": TimeframeSpec(
        name="1w",
        table="bars_1w",
        bucket_sql="toStartOfWeek(event_dt, 1, 'UTC')",
        end_sql="bar_start + INTERVAL 1 WEEK",
    ),
    "1mo": TimeframeSpec(
        name="1mo",
        table="bars_1mo",
        bucket_sql="toStartOfMonth(event_dt, 'UTC')",
        end_sql="addMonths(bar_start, 1)",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build reusable trade OHLCV bar tables from market_sip_compact.events. "
            "Bars are trade-based: OHLC from trade price and volume from trade size."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2026-12-31")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--drop-tables", action="store_true", help="Drop selected bar tables before rebuilding.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    specs = parse_timeframes(args.timeframes)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"trade_bars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    settings = query_settings(args)

    print("=" * 96, flush=True)
    print("Build ClickHouse SIP trade bars", flush=True)
    print(f"database={args.database} events_table={args.events_table}", flush=True)
    print(f"timeframes={','.join(spec.name for spec in specs)}", flush=True)
    print(f"date_range={args.start_date}->{args.end_date}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"settings={settings.strip() or '<none>'}", flush=True)
    print(f"replace_range={args.replace_range} drop_tables={args.drop_tables} dry_run={args.dry_run}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(['CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD', 'CLICKHOUSE_STORAGE_POLICY', 'CLICKHOUSE_HISTORICAL_STORAGE_POLICY'])}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    for index, spec in enumerate(specs, start=1):
        print("=" * 96, flush=True)
        print(f"BAR START [{index:,}/{len(specs):,}] timeframe={spec.name} table={spec.table}", flush=True)
        if args.dry_run:
            print_sql_preview("create", create_bar_table_sql(args.database, spec.table, args.storage_policy))
            if args.drop_tables:
                print_sql_preview("drop", drop_table_sql(args.database, spec.table))
        if args.replace_range:
            print_sql_preview("delete range", delete_range_sql(args.database, spec.table, args.start_date, args.end_date, args))
            print_sql_preview("insert", insert_trade_bars_sql(args, spec))
            continue

        if args.drop_tables:
            client.execute(drop_table_sql(args.database, spec.table))
            print(f"DROPPED {args.database}.{spec.table}", flush=True)
        client.execute(create_bar_table_sql(args.database, spec.table, args.storage_policy))
        if args.replace_range:
            delete_profile = run_profiled(
                client,
                f"delete_{spec.table}_{args.start_date}_{args.end_date}",
                delete_range_sql(args.database, spec.table, args.start_date, args.end_date, args),
            )
            append_jsonl(report_path, {"operation": "delete_range", "timeframe": spec.name, "profile": asdict(delete_profile)})
            print_profile("DELETE", delete_profile)

        insert_profile = run_profiled(
            client,
            f"insert_{spec.table}_{args.start_date}_{args.end_date}",
            insert_trade_bars_sql(args, spec),
            settings,
        )
        summary = summarize_table(client, args.database, spec.table, args.start_date, args.end_date)
        append_jsonl(report_path, {"operation": "insert", "timeframe": spec.name, "profile": asdict(insert_profile), "summary": summary})
        print_profile("INSERT", insert_profile)
        print(
            f"BAR DONE timeframe={spec.name} rows={summary['rows']:,} tickers={summary['tickers']:,} "
            f"volume={summary['volume']:.0f} min_bar={summary['min_bar_start_us']} max_bar={summary['max_bar_start_us']}",
            flush=True,
        )

    print("=" * 96, flush=True)
    print(f"DONE elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
    print("=" * 96, flush=True)


def parse_timeframes(text: str) -> list[TimeframeSpec]:
    requested = [item.strip() for item in text.split(",") if item.strip()]
    if not requested:
        raise ValueError("--timeframes must include at least one value")
    invalid = [item for item in requested if item not in TIMEFRAME_SPECS]
    if invalid:
        raise ValueError(f"Unsupported timeframes {invalid}; supported={sorted(TIMEFRAME_SPECS)}")
    seen: set[str] = set()
    specs: list[TimeframeSpec] = []
    for item in requested:
        if item not in seen:
            specs.append(TIMEFRAME_SPECS[item])
            seen.add(item)
    return specs


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def create_bar_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timeframe LowCardinality(String),
    session_date Date,
    bar_start DateTime64(6, 'UTC'),
    bar_end DateTime64(6, 'UTC'),
    bar_start_us UInt64,
    bar_end_us UInt64,
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume Float64,
    trade_count UInt64,
    first_trade_timestamp_us UInt64,
    last_trade_timestamp_us UInt64,
    built_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(session_date)
ORDER BY (ticker, bar_start_us)
{mergetree_settings_sql(storage_policy)}
"""


def drop_table_sql(database: str, table: str) -> str:
    return f"DROP TABLE IF EXISTS {quote_ident(database)}.{quote_ident(table)}"


def delete_range_sql(database: str, table: str, start_date: str, end_date: str, args: argparse.Namespace) -> str:
    settings = mutation_settings(args)
    return f"""
ALTER TABLE {quote_ident(database)}.{quote_ident(table)}
DELETE WHERE bar_start < (toDateTime64(toDate({sql_string(end_date)}) + INTERVAL 1 DAY, 6, 'UTC'))
  AND bar_end > toDateTime64(toDate({sql_string(start_date)}), 6, 'UTC')
{settings}
"""


def insert_trade_bars_sql(args: argparse.Namespace, spec: TimeframeSpec) -> str:
    db = quote_ident(args.database)
    src = f"{db}.{quote_ident(args.events_table)}"
    dst = f"{db}.{quote_ident(spec.table)}"
    decoded_price = "if(bitAnd(event_flags, 1) = 1, toFloat64(price_primary_int) / 10000.0, toFloat64(price_primary_int) / 100.0)"
    return f"""
INSERT INTO {dst}
(
    ticker, timeframe, session_date, bar_start, bar_end, bar_start_us, bar_end_us,
    open, high, low, close, volume, trade_count, first_trade_timestamp_us,
    last_trade_timestamp_us
)
WITH
    fromUnixTimestamp64Micro(toInt64(sip_timestamp_us), 'UTC') AS event_dt,
    {spec.bucket_sql} AS bar_start,
    {spec.end_sql} AS bar_end,
    {decoded_price} AS trade_price
SELECT
    ticker,
    {sql_string(spec.name)} AS timeframe,
    toDate(bar_start) AS session_date,
    bar_start,
    bar_end,
    toUInt64(toUnixTimestamp64Micro(bar_start)) AS bar_start_us,
    toUInt64(toUnixTimestamp64Micro(bar_end)) AS bar_end_us,
    argMin(trade_price, tuple(sip_timestamp_us, ordinal)) AS open,
    max(trade_price) AS high,
    min(trade_price) AS low,
    argMax(trade_price, tuple(sip_timestamp_us, ordinal)) AS close,
    sum(toFloat64(size_primary)) AS volume,
    count() AS trade_count,
    min(sip_timestamp_us) AS first_trade_timestamp_us,
    max(sip_timestamp_us) AS last_trade_timestamp_us
FROM {src}
WHERE event_date >= toDate({sql_string(args.start_date)})
  AND event_date <= toDate({sql_string(args.end_date)})
  AND event_type = 1
  AND ticker != ''
  AND sip_timestamp_us > 0
  AND price_primary_int > 0
GROUP BY ticker, bar_start, bar_end
"""


def summarize_table(client: ClickHouseHttpClient, database: str, table: str, start_date: str, end_date: str) -> dict[str, int | float]:
    rows = client.query_tsv(
        f"""
SELECT
    count(),
    uniqExact(ticker),
    if(count() = 0, 0, min(bar_start_us)),
    if(count() = 0, 0, max(bar_start_us)),
    if(count() = 0, 0, sum(volume)),
    if(count() = 0, 0, sum(trade_count))
FROM {quote_ident(database)}.{quote_ident(table)}
WHERE bar_start < (toDateTime64(toDate({sql_string(end_date)}) + INTERVAL 1 DAY, 6, 'UTC'))
  AND bar_end > toDateTime64(toDate({sql_string(start_date)}), 6, 'UTC')
"""
    ).strip()
    parts = rows.split("\t") if rows else ["0", "0", "0", "0", "0", "0"]
    return {
        "rows": int(parts[0] or 0),
        "tickers": int(parts[1] or 0),
        "min_bar_start_us": int(parts[2] or 0),
        "max_bar_start_us": int(parts[3] or 0),
        "volume": float(parts[4] or 0.0),
        "trade_count": int(float(parts[5] or 0)),
    }


def print_sql_preview(label: str, sql: str, *, limit: int = 2400) -> None:
    body = sql.strip()
    print(f"--- {label} SQL preview ---", flush=True)
    print(body[:limit] + ("\n..." if len(body) > limit else ""), flush=True)


def print_profile(prefix: str, profile: object) -> None:
    print(
        f"{prefix} profile wall={profile.wall_seconds:.1f}s "
        f"read_rows={getattr(profile, 'read_rows', None) or 0:,} "
        f"written_rows={getattr(profile, 'written_rows', None) or 0:,} "
        f"memory={getattr(profile, 'memory_usage_bytes', None) or 0:,}",
        flush=True,
    )


def append_jsonl(path: Path, item: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def mutation_settings(args: argparse.Namespace) -> str:
    settings = ["mutations_sync = 2"]
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


if __name__ == "__main__":
    main()
