from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE,
    DEFAULT_CONTINUITY_TABLE,
    DEFAULT_DROP_TRADE_CORRECTION_CODES,
    DEFAULT_EVENTS_TABLE,
    create_continuity_table_sql,
    create_events_table_sql,
    ensure_continuity_table_columns,
    mergetree_settings,
    mutation_settings,
    query_settings,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob  # noqa: E402
from pipelines.market_sip.flatfiles.download_update_events import (  # noqa: E402
    DEFAULT_TICKER_DAY_INDEX_TABLE,
    DayFiles,
    create_ticker_day_index_table_sql,
    raw_event_union_sql,
    validate_ticker_day_index_table_schema,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files  # noqa: E402


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/clickhouse_sip_ingest/yearly_event_repair")
DEFAULT_REPAIR_WINDOWS = ("2020-10-06:2020-10-08", "2021-02-12:2021-02-17")
EVENT_COLUMNS = (
    "ticker",
    "ordinal",
    "event_meta",
    "sip_timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
    "event_date",
)
EVENT_VALUE_COLUMNS = tuple(column for column in EVENT_COLUMNS if column != "ordinal")


class QueryRunner:
    def __init__(self, client: ClickHouseHttpClient, *, execute: bool, report_path: Path) -> None:
        self.client = client
        self.execute = execute
        self.report_path = report_path
        self.current_query_id = ""

    def run(self, label: str, sql: str) -> QueryProfile:
        query_id = f"sip_yearly_event_repair_{label}_{uuid.uuid4().hex}"
        print(f"QUERY {'START' if self.execute else 'DRY'} {label} query_id={query_id}", flush=True)
        self._append({"type": "query_start", "label": label, "query_id": query_id, "execute": self.execute})
        started = time.perf_counter()
        exception = ""
        if self.execute:
            self.current_query_id = query_id
            try:
                self.client.execute(sql.rstrip(";"), query_id=query_id)
            except Exception as exc:  # noqa: BLE001
                exception = repr(exc)
            finally:
                self.current_query_id = ""
        wall_seconds = time.perf_counter() - started
        profile = QueryProfile(label=label, query_id=query_id, wall_seconds=wall_seconds, exception=exception)
        row = {
            "type": "query_done",
            "label": label,
            "query_id": query_id,
            "execute": self.execute,
            "wall_seconds": wall_seconds,
            "exception": exception,
        }
        self._append(row)
        if exception:
            print(f"QUERY FAILED {label}: {exception}", flush=True)
            raise RuntimeError(f"{label} failed: {exception}")
        print(f"QUERY OK {label} seconds={wall_seconds:.1f}", flush=True)
        return profile

    def kill_current(self) -> None:
        if not self.current_query_id:
            return
        query_id = self.current_query_id
        print(f"INTERRUPT received; cancelling ClickHouse query_id={query_id}", flush=True)
        try:
            self.client.execute(f"KILL QUERY WHERE query_id = {sql_string(query_id)} ASYNC")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN failed to cancel query_id={query_id}: {exc!r}", flush=True)

    def _append(self, row: dict[str, Any]) -> None:
        append_jsonl(self.report_path, row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair market_sip_compact.events into per-year event tables with dense global ticker ordinals. "
            "The current events table is kept as the source until each target year passes audit."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--source-events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--year-table-prefix", default="events_")
    parser.add_argument("--events-all-view", default="events_all")
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--ticker-day-index-table", default=DEFAULT_TICKER_DAY_INDEX_TABLE)
    parser.add_argument("--condition-token-reference-table", default=DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE)
    parser.add_argument("--state-table", default="events_yearly_repair_ordinal_state")
    parser.add_argument("--run-log-table", default="events_yearly_repair_log")
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--repair-window", action="append", default=list(DEFAULT_REPAIR_WINDOWS), help="Inclusive source-date window, e.g. 2020-10-06:2020-10-08. Repeatable.")
    parser.add_argument("--flatfiles-root-win", default="D:/market-data/flatfiles/us_stocks_sip")
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--partition-mode", choices=("month", "ticker_hash", "none"), default="month")
    parser.add_argument("--partition-buckets", type=int, default=256)
    parser.add_argument("--drop-trade-correction-codes", default=DEFAULT_DROP_TRADE_CORRECTION_CODES)
    parser.add_argument("--max-threads", type=int, default=64)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-partitions-per-insert-block", type=int, default=1024)
    parser.add_argument(
        "--insert-chunk-days",
        type=int,
        default=1,
        help="UTC event_date days per year-table insert. Smaller chunks reduce ClickHouse memory and keep ordinal state bounded.",
    )
    parser.add_argument(
        "--derived-chunk-days",
        type=int,
        default=7,
        help="UTC event_date days per derived continuity/index insert.",
    )
    parser.add_argument(
        "--early-audit-days",
        type=int,
        default=3,
        help="Run the first ordinal audit after this many inserted UTC event_date days.",
    )
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--force-rebuild-year", action="append", type=int, default=[], help="Drop and rebuild a target year table before inserting it.")
    parser.add_argument("--skip-existing-year", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Run SQL. Without this flag the script only logs planned steps.")
    parser.add_argument("--drop-old-year-partitions", action="store_true", help="After a year passes audit, drop the old source events monthly partitions for that year.")
    parser.add_argument("--replace-events-with-view", action="store_true", help="At the end, drop the old source events table and recreate it as a view over yearly tables.")
    parser.add_argument("--allow-drop-events-table", action="store_true", help="Required with --replace-events-with-view.")
    parser.add_argument("--build-derived-tables", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--promote-derived-tables", action="store_true", help="Replace production continuity/index tables with rebuilt versions after final audit.")
    return parser.parse_args()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def query_scalar(client: ClickHouseHttpClient, sql: str, default: int = 0) -> int:
    text = client.query_tsv(sql).strip()
    if not text:
        return default
    value = text.splitlines()[0].split("\t")[0]
    if value in {"", "\\N"}:
        return default
    return int(float(value))


def query_text(client: ClickHouseHttpClient, sql: str) -> str:
    return client.query_tsv(sql).strip()


def parse_tsv_with_names(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    keys = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        values = line.split("\t")
        rows.append(dict(zip(keys, values, strict=False)))
    return rows


def query_settings_clause(args: argparse.Namespace) -> str:
    return query_settings(args)


def source_date_expr() -> str:
    return "toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us), 'America/New_York'))"


def build_step_expr(source_date_sql: str) -> str:
    return f"toUInt32(dateDiff('day', toDate('1970-01-01'), {source_date_sql}) + 719163)"


def parse_repair_windows(values: list[str]) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    for value in values:
        if ":" not in value:
            raise ValueError(f"Repair window must be START:END, got {value!r}")
        start_text, end_text = value.split(":", 1)
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        if end < start:
            raise ValueError(f"Repair window end before start: {value!r}")
        windows.append((start, end))
    return sorted(windows)


def iter_dates(start: date, end: date) -> list[date]:
    return [date.fromordinal(ordinal) for ordinal in range(start.toordinal(), end.toordinal() + 1)]


def iter_date_ranges(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    ranges: list[tuple[date, date]] = []
    current = start
    while current < end:
        chunk_end = min(date.fromordinal(current.toordinal() + chunk_days), end)
        ranges.append((current, chunk_end))
        current = chunk_end
    return ranges


def flatfile_destination(root: Path, key: str) -> Path:
    key_parts = Path(key).parts
    root_name = root.name.replace("\\", "/").rstrip("/")
    if key_parts and key_parts[0] == root_name:
        return root.joinpath(*key_parts[1:])
    return root.joinpath(*key_parts)


def day_files_for_date(args: argparse.Namespace, source: date) -> DayFiles:
    root = Path(args.flatfiles_root_win)
    source_text = source.isoformat()
    year = source_text[:4]
    month = source_text[5:7]
    quote_key = f"us_stocks_sip/quotes_v1/{year}/{month}/{source_text}.csv.gz"
    trade_key = f"us_stocks_sip/trades_v1/{year}/{month}/{source_text}.csv.gz"
    return DayFiles(
        source_date=source_text,
        quote_job=DownloadJob(kind="quotes", session_date=source_text, key=quote_key, destination=str(flatfile_destination(root, quote_key))),
        trade_job=DownloadJob(kind="trades", session_date=source_text, key=trade_key, destination=str(flatfile_destination(root, trade_key))),
    )


def repair_day_file_status(args: argparse.Namespace, source: date) -> tuple[DayFiles, bool, bool]:
    day = day_files_for_date(args, source)
    quote_exists = Path(day.quote_job.destination).exists()
    trade_exists = Path(day.trade_job.destination).exists()
    return day, quote_exists, trade_exists


def validate_repair_flatfiles(args: argparse.Namespace, windows: list[tuple[date, date]]) -> dict[str, list[str]]:
    partial_missing: list[str] = []
    skipped_non_trading: list[str] = []
    empty_windows: list[str] = []
    for start, end in windows:
        available_in_window = 0
        for source in iter_dates(start, end):
            day, quote_exists, trade_exists = repair_day_file_status(args, source)
            if quote_exists and trade_exists:
                available_in_window += 1
                continue
            if not quote_exists and not trade_exists:
                skipped_non_trading.append(source.isoformat())
                continue
            if not quote_exists:
                partial_missing.append(day.quote_job.destination)
            if not trade_exists:
                partial_missing.append(day.trade_job.destination)
        if available_in_window == 0:
            empty_windows.append(f"{start.isoformat()}:{end.isoformat()}")
    if partial_missing:
        preview = "\n".join(partial_missing[:20])
        suffix = f"\n... {len(partial_missing) - 20:,} more" if len(partial_missing) > 20 else ""
        raise FileNotFoundError(f"Repair window has partial quote/trade flatfile pairs:\n{preview}{suffix}")
    if empty_windows:
        raise FileNotFoundError(
            "Repair window has no available quote/trade flatfile pairs. Check --flatfiles-root-win or window dates: "
            + ", ".join(empty_windows)
        )
    return {"skipped_non_trading": skipped_non_trading}


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    return bool(query_scalar(client, f"EXISTS TABLE {quote_ident(database)}.{quote_ident(table)}"))


def create_repair_log_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.run_log_table)}
(
    run_id String,
    stage LowCardinality(String),
    year UInt16,
    status LowCardinality(String),
    detail String,
    created_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (run_id, stage, year, created_at)
{mergetree_settings(args.storage_policy)}
"""


def create_state_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.state_table)}
(
    ticker LowCardinality(String),
    next_ordinal UInt64,
    processed_year UInt16,
    processed_through_date Date DEFAULT toDate('1970-01-01'),
    state_order UInt32 DEFAULT 0,
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY ticker
{mergetree_settings(args.storage_policy)}
"""


def ensure_state_table_columns(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    table = f"{quote_ident(args.database)}.{quote_ident(args.state_table)}"
    client.execute(
        f"""
ALTER TABLE {table}
    ADD COLUMN IF NOT EXISTS processed_through_date Date DEFAULT toDate('1970-01-01')
"""
    )
    client.execute(
        f"""
ALTER TABLE {table}
    ADD COLUMN IF NOT EXISTS state_order UInt32 DEFAULT 0
"""
    )


def create_repair_raw_table_sql(args: argparse.Namespace, table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(table)}
(
    source_date Date,
    ticker LowCardinality(String),
    event_meta UInt8,
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    sequence_number UInt32,
    price_primary_int UInt32 CODEC(T64, ZSTD(1)),
    price_secondary_int UInt32 CODEC(T64, ZSTD(1)),
    size_primary Float32 CODEC(ZSTD(1)),
    size_secondary Float32 CODEC(ZSTD(1)),
    exchange_primary UInt8,
    exchange_secondary UInt8,
    condition_token_1 UInt8,
    condition_token_2 UInt8,
    condition_token_3 UInt8,
    condition_token_4 UInt8,
    condition_token_5 UInt8,
    event_date Date
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, ticker, sip_timestamp_us, sequence_number, event_meta)
{mergetree_settings(args.storage_policy)}
"""


def year_table_name(args: argparse.Namespace, year: int) -> str:
    return f"{args.year_table_prefix}{year}"


def repair_raw_table_name(args: argparse.Namespace, year: int) -> str:
    return f"{args.year_table_prefix}{year}_repair_raw"


def derived_continuity_table(args: argparse.Namespace) -> str:
    return f"{args.continuity_table}_yearly_repair"


def derived_index_table(args: argparse.Namespace) -> str:
    return f"{args.ticker_day_index_table}_yearly_repair"


def create_year_table(runner: QueryRunner, args: argparse.Namespace, year: int) -> None:
    table = year_table_name(args, year)
    if year in set(args.force_rebuild_year):
        runner.run(f"drop_{table}", f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(table)} SYNC")
    year_args = argparse.Namespace(**vars(args))
    year_args.events_table = table
    runner.run(f"create_{table}", create_events_table_sql(year_args))


def delete_state_from_year_sql(args: argparse.Namespace, year: int) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.state_table)}
DELETE WHERE processed_year >= toUInt16({int(year)})
{mutation_settings(args)}
"""


def create_repair_raw_table(runner: QueryRunner, args: argparse.Namespace, year: int) -> None:
    table = repair_raw_table_name(args, year)
    if year in set(args.force_rebuild_year):
        runner.run(f"drop_{table}", f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(table)} SYNC")
    runner.run(f"create_{table}", create_repair_raw_table_sql(args, table))


def insert_repair_raw_day_sql(args: argparse.Namespace, table: str, day: DayFiles) -> str:
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(table)}
(
    source_date,
    ticker,
    event_meta,
    sip_timestamp_us,
    sequence_number,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    event_date
)
SELECT
    toDate({sql_string(day.source_date)}) AS source_date,
    ticker,
    event_meta,
    sip_timestamp_us,
    sequence_number,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    event_date
FROM
(
{raw_event_union_sql(args, day)}
)
ORDER BY source_date, ticker, sip_timestamp_us, sequence_number, bitAnd(event_meta, 1)
{query_settings_clause(args)}
"""


def repair_days_for_year(windows: list[tuple[date, date]], year: int) -> list[date]:
    days: list[date] = []
    for start, end in windows:
        for source in iter_dates(start, end):
            if source.year == year:
                days.append(source)
    return sorted(set(days))


def available_repair_days_for_year(args: argparse.Namespace, windows: list[tuple[date, date]], year: int) -> list[date]:
    available: list[date] = []
    for source in repair_days_for_year(windows, year):
        _day, quote_exists, trade_exists = repair_day_file_status(args, source)
        if quote_exists and trade_exists:
            available.append(source)
    return available


def build_repair_raw_for_year(runner: QueryRunner, args: argparse.Namespace, windows: list[tuple[date, date]], year: int) -> None:
    days = available_repair_days_for_year(args, windows, year)
    if not days:
        return
    create_repair_raw_table(runner, args, year)
    table = repair_raw_table_name(args, year)
    for source in days:
        day = day_files_for_date(args, source)
        runner.run(f"insert_{table}_{source.isoformat()}", insert_repair_raw_day_sql(args, table, day))


def repair_source_dates_sql(days: list[date]) -> str:
    if not days:
        return "SELECT toDate('1900-01-01') AS source_date WHERE 0"
    values = ", ".join(f"toDate({sql_string(day.isoformat())})" for day in days)
    return f"SELECT arrayJoin([{values}]) AS source_date"


def old_event_candidates_sql(
    args: argparse.Namespace,
    year: int,
    repair_days: list[date],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    period_start = start_date.isoformat() if start_date else f"{year}-01-01"
    period_end = end_date.isoformat() if end_date else f"{year + 1}-01-01"
    db = quote_ident(args.database)
    source = quote_ident(args.source_events_table)
    if repair_days:
        range_filter = f"""
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) - argMax(event_count, tuple(build_step, updated_at)) AS first_ordinal,
        argMax(next_ordinal, tuple(build_step, updated_at)) - 1 AS last_ordinal
    FROM {db}.{quote_ident(args.continuity_table)}
    WHERE source_date IN ({", ".join(f"toDate({sql_string(day.isoformat())})" for day in repair_days)})
    GROUP BY ticker, source_date
) AS r ON e.ticker = r.ticker AND e.ordinal BETWEEN r.first_ordinal AND r.last_ordinal
"""
        where_extra = "AND r.ticker IS NULL"
    else:
        range_filter = ""
        where_extra = ""
    return f"""
SELECT
    e.ticker,
    e.event_meta,
    e.sip_timestamp_us,
    toUInt64(e.ordinal) AS sort_ordinal,
    toUInt32(e.ordinal % 4294967295) AS sort_sequence,
    cityHash64(
        e.event_meta, e.price_primary_int, e.price_secondary_int, e.size_primary, e.size_secondary,
        e.exchange_primary, e.exchange_secondary, e.condition_token_1, e.condition_token_2,
        e.condition_token_3, e.condition_token_4, e.condition_token_5
    ) AS sort_hash,
    e.price_primary_int,
    e.price_secondary_int,
    e.size_primary,
    e.size_secondary,
    e.exchange_primary,
    e.exchange_secondary,
    e.condition_token_1,
    e.condition_token_2,
    e.condition_token_3,
    e.condition_token_4,
    e.condition_token_5,
    e.event_date
FROM {db}.{source} AS e
{range_filter}
WHERE e.event_date >= toDate({sql_string(period_start)})
  AND e.event_date < toDate({sql_string(period_end)})
  {where_extra}
"""


def repair_event_candidates_sql(
    args: argparse.Namespace,
    year: int,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    table = repair_raw_table_name(args, year)
    period_start = start_date.isoformat() if start_date else f"{year}-01-01"
    period_end = end_date.isoformat() if end_date else f"{year + 1}-01-01"
    return f"""
SELECT
    ticker,
    event_meta,
    sip_timestamp_us,
    toUInt64(0) AS sort_ordinal,
    sequence_number AS sort_sequence,
    cityHash64(
        event_meta, price_primary_int, price_secondary_int, size_primary, size_secondary,
        exchange_primary, exchange_secondary, condition_token_1, condition_token_2,
        condition_token_3, condition_token_4, condition_token_5
    ) AS sort_hash,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    event_date
FROM {quote_ident(args.database)}.{quote_ident(table)}
WHERE event_date >= toDate({sql_string(period_start)})
  AND event_date < toDate({sql_string(period_end)})
"""


def repair_days_can_affect_period(repair_days: list[date], start_date: date, end_date: date) -> bool:
    if not repair_days:
        return False
    min_day = min(repair_days)
    max_day = max(repair_days)
    # Source/session days can spill into the next UTC event_date after after-hours trading.
    affected_start = min_day
    affected_end = date.fromordinal(max_day.toordinal() + 2)
    return start_date < affected_end and end_date > affected_start


def insert_year_period_sql(args: argparse.Namespace, year: int, repair_days: list[date], start_date: date, end_date: date) -> str:
    target = year_table_name(args, year)
    include_repair = repair_days_can_affect_period(repair_days, start_date, end_date)
    candidate_parts = [old_event_candidates_sql(args, year, repair_days, start_date=start_date, end_date=end_date)]
    if include_repair:
        candidate_parts.append(repair_event_candidates_sql(args, year, start_date=start_date, end_date=end_date))
    candidates = "\nUNION ALL\n".join(candidate_parts)
    columns = ",\n    ".join(EVENT_COLUMNS)
    # Recalculate chronology from timestamps. Existing ordinals are only a deterministic
    # tie-breaker for old rows; repaired flatfile rows use source sequence instead.
    ordinal_order = (
        "c.sip_timestamp_us, c.sort_sequence, bitAnd(c.event_meta, 1), c.sort_hash, c.sort_ordinal"
        if include_repair
        else "c.sip_timestamp_us, c.sort_ordinal, bitAnd(c.event_meta, 1), c.sort_hash"
    )
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(target)}
(
    {columns}
)
SELECT
    c.ticker,
    coalesce(s.ordinal_offset, toUInt64(0))
        + toUInt64(row_number() OVER (
            PARTITION BY c.ticker
            ORDER BY {ordinal_order}
        ) - 1) AS ordinal,
    c.event_meta,
    c.sip_timestamp_us,
    c.price_primary_int,
    c.price_secondary_int,
    c.size_primary,
    c.size_secondary,
    c.exchange_primary,
    c.exchange_secondary,
    c.condition_token_1,
    c.condition_token_2,
    c.condition_token_3,
    c.condition_token_4,
    c.condition_token_5,
    c.event_date
FROM
(
{candidates}
) AS c
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(state_order, next_ordinal)) AS ordinal_offset
    FROM {quote_ident(args.database)}.{quote_ident(args.state_table)}
    GROUP BY ticker
) AS s ON s.ticker = c.ticker
ORDER BY c.ticker, ordinal
{query_settings_clause(args)}
"""


def insert_year_sql(args: argparse.Namespace, year: int, repair_days: list[date]) -> str:
    return insert_year_period_sql(args, year, repair_days, date(year, 1, 1), date(year + 1, 1, 1))


def insert_state_after_year_sql(args: argparse.Namespace, year: int) -> str:
    target = year_table_name(args, year)
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.state_table)}
(
    ticker,
    next_ordinal,
    processed_year,
    processed_through_date,
    state_order
)
SELECT
    ticker,
    max(next_ordinal) AS next_ordinal,
    toUInt16({int(year)}) AS processed_year,
    toDate({sql_string(f"{year + 1}-01-01")}) AS processed_through_date,
    toUInt32(dateDiff('day', toDate('1970-01-01'), toDate({sql_string(f"{year + 1}-01-01")}))) AS state_order
FROM
(
    SELECT
        ticker,
        max(ordinal) + 1 AS next_ordinal
    FROM {quote_ident(args.database)}.{quote_ident(target)}
    GROUP BY ticker

    UNION ALL

    SELECT
        ticker,
        argMax(next_ordinal, tuple(state_order, next_ordinal)) AS next_ordinal
    FROM {quote_ident(args.database)}.{quote_ident(args.state_table)}
    GROUP BY ticker
)
GROUP BY ticker
{query_settings_clause(args)}
"""


def insert_state_after_period_sql(args: argparse.Namespace, year: int, start_date: date, end_date: date) -> str:
    target = year_table_name(args, year)
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.state_table)}
(
    ticker,
    next_ordinal,
    processed_year,
    processed_through_date,
    state_order
)
SELECT
    ticker,
    max(ordinal) + 1 AS next_ordinal,
    toUInt16({int(year)}) AS processed_year,
    toDate({sql_string(end_date.isoformat())}) AS processed_through_date,
    toUInt32(dateDiff('day', toDate('1970-01-01'), toDate({sql_string(end_date.isoformat())}))) AS state_order
FROM {quote_ident(args.database)}.{quote_ident(target)}
WHERE event_date >= toDate({sql_string(start_date.isoformat())})
  AND event_date < toDate({sql_string(end_date.isoformat())})
GROUP BY ticker
{query_settings_clause(args)}
"""


def year_table_row_count(client: ClickHouseHttpClient, args: argparse.Namespace, year: int) -> int:
    table = year_table_name(args, year)
    if not table_exists(client, args.database, table):
        return 0
    return query_scalar(client, f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(table)}")


def source_old_year_count_sql(args: argparse.Namespace, year: int, repair_days: list[date]) -> str:
    return f"SELECT count() FROM ({old_event_candidates_sql(args, year, repair_days)})"


def repair_year_count_sql(args: argparse.Namespace, year: int) -> str:
    table = repair_raw_table_name(args, year)
    return f"""
SELECT count()
FROM {quote_ident(args.database)}.{quote_ident(table)}
WHERE event_date >= toDate({sql_string(f"{year}-01-01")})
  AND event_date < toDate({sql_string(f"{year + 1}-01-01")})
"""


def audit_year_period_sql(args: argparse.Namespace, year: int, start_date: date, end_date: date) -> str:
    table = year_table_name(args, year)
    return f"""
WITH ordered AS
(
    SELECT
        ticker,
        ordinal,
        sip_timestamp_us,
        lagInFrame(ordinal) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS prev_ordinal,
        lagInFrame(sip_timestamp_us) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS prev_ts
    FROM {quote_ident(args.database)}.{quote_ident(table)}
    WHERE event_date >= toDate({sql_string(start_date.isoformat())})
      AND event_date < toDate({sql_string(end_date.isoformat())})
)
SELECT
    count() AS rows,
    count() - uniqExact(ticker, ordinal) AS duplicate_ticker_ordinal_rows,
    countIf(prev_ordinal != 0 AND ordinal != prev_ordinal + 1) AS intra_period_ordinal_gap_steps,
    countIf(prev_ordinal != 0 AND sip_timestamp_us < prev_ts) AS timestamp_backsteps,
    uniqExact(ticker) AS tickers
FROM ordered
FORMAT TSVWithNames
"""


def audit_year_period_boundaries_sql(args: argparse.Namespace, year: int, start_date: date, end_date: date) -> str:
    table = year_table_name(args, year)
    return f"""
SELECT
    ticker,
    min(ordinal) AS min_ordinal,
    max(ordinal) AS max_ordinal,
    min(sip_timestamp_us) AS min_sip_timestamp_us,
    max(sip_timestamp_us) AS max_sip_timestamp_us,
    count() AS rows
FROM {quote_ident(args.database)}.{quote_ident(table)}
WHERE event_date >= toDate({sql_string(start_date.isoformat())})
  AND event_date < toDate({sql_string(end_date.isoformat())})
GROUP BY ticker
ORDER BY ticker
FORMAT TSVWithNames
"""


def new_year_audit_state(year: int) -> dict[str, Any]:
    return {
        "year": int(year),
        "last_audit_end": date(year, 1, 1),
        "last_by_ticker": {},
        "rows": 0,
        "duplicate_ticker_ordinal_rows": 0,
        "intra_period_ordinal_gap_steps": 0,
        "inter_period_ordinal_gap_steps": 0,
        "inter_period_ordinal_overlap_steps": 0,
        "timestamp_backsteps": 0,
        "periods": 0,
    }


def should_run_year_audit(args: argparse.Namespace, year: int, last_audit_end: date, current_end: date) -> bool:
    year_start = date(year, 1, 1)
    year_end = date(year + 1, 1, 1)
    early_days = max(1, int(args.early_audit_days))
    first_audit_end = min(date.fromordinal(year_start.toordinal() + early_days), year_end)
    if last_audit_end < first_audit_end <= current_end:
        return True
    if current_end >= year_end:
        return True
    return current_end.day == 1 and current_end > last_audit_end


def audit_year_range(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    year: int,
    start_date: date,
    end_date: date,
    state: dict[str, Any],
    report_path: Path,
    *,
    final: bool = False,
    expected_rows: int | None = None,
) -> None:
    if end_date <= start_date:
        return
    label = "final" if final else "incremental"
    audit: dict[str, Any] = {
        "type": "year_audit",
        "scope": label,
        "year": int(year),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "expected_rows": int(expected_rows) if expected_rows is not None else None,
        "rows": 0,
        "duplicate_ticker_ordinal_rows": 0,
        "intra_period_ordinal_gap_steps": 0,
        "inter_period_ordinal_gap_steps": 0,
        "inter_period_ordinal_overlap_steps": 0,
        "timestamp_backsteps": 0,
        "periods": 0,
        "status": "ok",
    }
    last_by_ticker: dict[str, tuple[int, int]] = state["last_by_ticker"]
    first_failure_examples: list[dict[str, Any]] = []
    for chunk_start, chunk_end in iter_date_ranges(start_date, end_date, args.insert_chunk_days):
        summary_rows = parse_tsv_with_names(query_text(client, audit_year_period_sql(args, year, chunk_start, chunk_end)))
        if not summary_rows:
            raise RuntimeError(f"Year {year} audit returned no summary for {chunk_start} -> {chunk_end}")
        summary = {key: int(float(value or 0)) for key, value in summary_rows[0].items()}
        audit["rows"] += summary["rows"]
        audit["duplicate_ticker_ordinal_rows"] += summary["duplicate_ticker_ordinal_rows"]
        audit["intra_period_ordinal_gap_steps"] += summary["intra_period_ordinal_gap_steps"]
        audit["timestamp_backsteps"] += summary["timestamp_backsteps"]
        audit["periods"] += 1

        boundary_rows = parse_tsv_with_names(query_text(client, audit_year_period_boundaries_sql(args, year, chunk_start, chunk_end)))
        for row in boundary_rows:
            ticker = row["ticker"]
            min_ordinal = int(row["min_ordinal"])
            max_ordinal = int(row["max_ordinal"])
            min_ts = int(row["min_sip_timestamp_us"])
            max_ts = int(row["max_sip_timestamp_us"])
            previous = last_by_ticker.get(ticker)
            if previous is not None:
                prev_max_ordinal, prev_max_ts = previous
                if min_ordinal <= prev_max_ordinal:
                    audit["inter_period_ordinal_overlap_steps"] += 1
                    if len(first_failure_examples) < 10:
                        first_failure_examples.append(
                            {
                                "ticker": ticker,
                                "period_start": chunk_start.isoformat(),
                                "previous_max_ordinal": prev_max_ordinal,
                                "current_min_ordinal": min_ordinal,
                                "kind": "ordinal_overlap",
                            }
                        )
                elif min_ordinal != prev_max_ordinal + 1:
                    audit["inter_period_ordinal_gap_steps"] += 1
                    if len(first_failure_examples) < 10:
                        first_failure_examples.append(
                            {
                                "ticker": ticker,
                                "period_start": chunk_start.isoformat(),
                                "previous_max_ordinal": prev_max_ordinal,
                                "current_min_ordinal": min_ordinal,
                                "kind": "ordinal_gap",
                            }
                        )
                if min_ts < prev_max_ts:
                    audit["timestamp_backsteps"] += 1
                    if len(first_failure_examples) < 10:
                        first_failure_examples.append(
                            {
                                "ticker": ticker,
                                "period_start": chunk_start.isoformat(),
                                "previous_max_sip_timestamp_us": prev_max_ts,
                                "current_min_sip_timestamp_us": min_ts,
                                "kind": "timestamp_backstep",
                            }
                        )
            last_by_ticker[ticker] = (max_ordinal, max_ts)

    state["last_audit_end"] = end_date
    state["rows"] += audit["rows"]
    state["duplicate_ticker_ordinal_rows"] += audit["duplicate_ticker_ordinal_rows"]
    state["intra_period_ordinal_gap_steps"] += audit["intra_period_ordinal_gap_steps"]
    state["inter_period_ordinal_gap_steps"] += audit["inter_period_ordinal_gap_steps"]
    state["inter_period_ordinal_overlap_steps"] += audit["inter_period_ordinal_overlap_steps"]
    state["timestamp_backsteps"] += audit["timestamp_backsteps"]
    state["periods"] += audit["periods"]

    audit["status"] = "ok"
    failures = {
        key: value
        for key, value in audit.items()
        if key
        in {
            "duplicate_ticker_ordinal_rows",
            "intra_period_ordinal_gap_steps",
            "inter_period_ordinal_gap_steps",
            "inter_period_ordinal_overlap_steps",
            "timestamp_backsteps",
        }
        and value
    }
    if final and expected_rows is not None and state["rows"] != expected_rows:
        failures["row_count_mismatch"] = {"actual": state["rows"], "expected": expected_rows}
    if failures:
        audit["status"] = "failed"
        audit["failures"] = failures
        audit["first_failure_examples"] = first_failure_examples
        append_jsonl(report_path, audit)
        raise RuntimeError(f"Year {year} audit failed: {failures}")
    append_jsonl(report_path, audit)
    print(
        f"YEAR AUDIT OK {year} {start_date.isoformat()} -> {end_date.isoformat()} "
        f"rows={audit['rows']:,} total={state['rows']:,}",
        flush=True,
    )


def audit_year(client: ClickHouseHttpClient, args: argparse.Namespace, year: int, expected_rows: int, report_path: Path) -> None:
    state = new_year_audit_state(year)
    year_end = date(year + 1, 1, 1)
    for chunk_start, chunk_end in iter_date_ranges(date(year, 1, 1), year_end, args.insert_chunk_days):
        if should_run_year_audit(args, year, state["last_audit_end"], chunk_end):
            audit_year_range(
                client,
                args,
                year,
                state["last_audit_end"],
                chunk_end,
                state,
                report_path,
                final=chunk_end >= year_end,
                expected_rows=expected_rows if chunk_end >= year_end else None,
            )


def drop_old_year_partitions_sql(args: argparse.Namespace, year: int) -> list[tuple[str, str]]:
    statements: list[tuple[str, str]] = []
    for month in range(1, 13):
        partition = f"{year}{month:02d}"
        label = f"drop_old_events_partition_{partition}"
        sql = f"ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.source_events_table)} DROP PARTITION {partition}"
        statements.append((label, sql))
    return statements


def create_events_all_view_sql(args: argparse.Namespace, years: list[int], *, view_name: str | None = None) -> str:
    view = view_name or args.events_all_view
    parts = [
        f"SELECT {', '.join(EVENT_COLUMNS)} FROM {quote_ident(args.database)}.{quote_ident(year_table_name(args, year))}"
        for year in years
    ]
    union_sql = "\nUNION ALL\n".join(parts) if parts else "SELECT * FROM system.one WHERE 0"
    return f"""
CREATE OR REPLACE VIEW {quote_ident(args.database)}.{quote_ident(view)} AS
{union_sql}
"""


def create_derived_tables(runner: QueryRunner, args: argparse.Namespace) -> None:
    cont_args = argparse.Namespace(**vars(args))
    cont_args.continuity_table = derived_continuity_table(args)
    runner.run(f"drop_{derived_continuity_table(args)}", f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(derived_continuity_table(args))} SYNC")
    runner.run(f"create_{derived_continuity_table(args)}", create_continuity_table_sql(cont_args))
    if runner.execute:
        ensure_continuity_table_columns(runner.client, cont_args)
    index_args = argparse.Namespace(**vars(args))
    index_args.ticker_day_index_table = derived_index_table(args)
    runner.run(f"drop_{derived_index_table(args)}", f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(derived_index_table(args))} SYNC")
    runner.run(f"create_{derived_index_table(args)}", create_ticker_day_index_table_sql(index_args))
    if runner.execute:
        validate_ticker_day_index_table_schema(runner.client, index_args)


def insert_derived_continuity_sql(
    args: argparse.Namespace,
    view_name: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    src_date = source_date_expr()
    build_step = build_step_expr(src_date)
    where_clause = ""
    if start_date and end_date:
        where_clause = (
            f"\nWHERE event_date >= toDate({sql_string(start_date.isoformat())})"
            f"\n  AND event_date < toDate({sql_string(end_date.isoformat())})"
        )
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(derived_continuity_table(args))}
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
    ticker,
    {build_step} AS build_step,
    {src_date} AS source_date,
    count() AS event_count,
    max(ordinal) + 1 AS next_ordinal,
    max(ordinal) AS last_ordinal,
    min(sip_timestamp_us) AS first_sip_timestamp_us,
    max(sip_timestamp_us) AS last_sip_timestamp_us
FROM {quote_ident(args.database)}.{quote_ident(view_name)}
{where_clause}
GROUP BY ticker, source_date
{query_settings_clause(args)}
"""


def insert_derived_index_sql(
    args: argparse.Namespace,
    view_name: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    src_date = source_date_expr()
    build_step = build_step_expr(src_date)
    where_clause = ""
    if start_date and end_date:
        where_clause = (
            f"\nWHERE event_date >= toDate({sql_string(start_date.isoformat())})"
            f"\n  AND event_date < toDate({sql_string(end_date.isoformat())})"
        )
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(derived_index_table(args))}
(
    ticker,
    source_date,
    event_count,
    first_ordinal,
    last_ordinal,
    next_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    build_step
)
SELECT
    ticker,
    {src_date} AS source_date,
    count() AS event_count,
    min(ordinal) AS first_ordinal,
    max(ordinal) AS last_ordinal,
    max(ordinal) + 1 AS next_ordinal,
    min(sip_timestamp_us) AS first_sip_timestamp_us,
    max(sip_timestamp_us) AS last_sip_timestamp_us,
    {build_step} AS build_step
FROM {quote_ident(args.database)}.{quote_ident(view_name)}
{where_clause}
GROUP BY ticker, source_date
{query_settings_clause(args)}
"""


def audit_derived_tables_sql(args: argparse.Namespace, view_name: str) -> str:
    return f"""
SELECT
    (SELECT count() FROM {quote_ident(args.database)}.{quote_ident(view_name)}) AS event_rows,
    (SELECT coalesce(sum(event_count), toUInt64(0)) FROM {quote_ident(args.database)}.{quote_ident(derived_continuity_table(args))}) AS continuity_rows,
    (SELECT coalesce(sum(event_count), toUInt64(0)) FROM {quote_ident(args.database)}.{quote_ident(derived_index_table(args))}) AS index_rows,
    (SELECT count() FROM {quote_ident(args.database)}.{quote_ident(derived_continuity_table(args))}) AS continuity_ticker_days,
    (SELECT count() FROM {quote_ident(args.database)}.{quote_ident(derived_index_table(args))}) AS index_ticker_days
FORMAT TSVWithNames
"""


def audit_derived_tables(client: ClickHouseHttpClient, args: argparse.Namespace, view_name: str, report_path: Path) -> None:
    text = query_text(client, audit_derived_tables_sql(args, view_name))
    lines = text.splitlines()
    if len(lines) < 2:
        raise RuntimeError("Derived table audit returned no rows")
    keys = lines[0].split("\t")
    values = lines[1].split("\t")
    audit = {key: int(float(value or 0)) for key, value in zip(keys, values, strict=False)}
    audit["type"] = "derived_audit"
    audit["status"] = "ok"
    if audit["event_rows"] != audit["continuity_rows"] or audit["event_rows"] != audit["index_rows"]:
        audit["status"] = "failed"
        append_jsonl(report_path, audit)
        raise RuntimeError(f"Derived table row sums do not match events: {audit}")
    append_jsonl(report_path, audit)
    print(
        f"DERIVED AUDIT OK event_rows={audit['event_rows']:,} ticker_days={audit['continuity_ticker_days']:,}",
        flush=True,
    )


def promote_derived_tables(runner: QueryRunner, args: argparse.Namespace) -> None:
    backup_suffix = f"backup_before_yearly_repair_{int(time.time())}"
    runner.run(
        "backup_old_continuity",
        f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(args.continuity_table)} "
        f"TO {quote_ident(args.database)}.{quote_ident(args.continuity_table + '_' + backup_suffix)}",
    )
    runner.run(
        "backup_old_ticker_day_index",
        f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)} "
        f"TO {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table + '_' + backup_suffix)}",
    )
    runner.run(
        "promote_continuity",
        f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(derived_continuity_table(args))} "
        f"TO {quote_ident(args.database)}.{quote_ident(args.continuity_table)}",
    )
    runner.run(
        "promote_ticker_day_index",
        f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(derived_index_table(args))} "
        f"TO {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)}",
    )


def replace_events_with_view(runner: QueryRunner, args: argparse.Namespace, years: list[int]) -> None:
    if not args.allow_drop_events_table:
        raise RuntimeError("--replace-events-with-view requires --allow-drop-events-table")
    backup_name = f"{args.source_events_table}_empty_backup_before_yearly_view_{int(time.time())}"
    runner.run(
        "backup_old_events_table",
        f"RENAME TABLE {quote_ident(args.database)}.{quote_ident(args.source_events_table)} "
        f"TO {quote_ident(args.database)}.{quote_ident(backup_name)}",
    )
    runner.run("create_events_view", create_events_all_view_sql(args, years, view_name=args.source_events_table))


def log_repair_stage(runner: QueryRunner, args: argparse.Namespace, run_id: str, stage: str, year: int, status: str, detail: str) -> None:
    runner.run(
        f"log_{stage}_{year}_{status}",
        f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.run_log_table)}
(run_id, stage, year, status, detail)
VALUES ({sql_string(run_id)}, {sql_string(stage)}, toUInt16({int(year)}), {sql_string(status)}, {sql_string(detail)})
""",
    )


def main() -> int:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args()
    if args.end_year < args.start_year:
        raise ValueError("--end-year must be >= --start-year")
    if args.replace_events_with_view and not args.drop_old_year_partitions:
        raise ValueError("--replace-events-with-view should only be used after --drop-old-year-partitions")
    windows = parse_repair_windows(args.repair_window)
    flatfile_status = validate_repair_flatfiles(args, windows)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"yearly_event_repair_{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    runner = QueryRunner(client, execute=bool(args.execute), report_path=report_path)

    def handle_interrupt(signum: int, _frame: Any) -> None:
        print(f"Signal {signum} received; attempting graceful ClickHouse query cancellation.", flush=True)
        runner.kill_current()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    years = list(range(args.start_year, args.end_year + 1))
    append_jsonl(
        report_path,
        {
            "type": "run_start",
            "run_id": run_id,
            "args": {key: value for key, value in vars(args).items() if key != "password"},
            "repair_windows": [(start.isoformat(), end.isoformat()) for start, end in windows],
            "flatfile_status": flatfile_status,
        },
    )
    print(f"YEARLY EVENT REPAIR run_id={run_id} report={report_path}", flush=True)
    print(f"execute={args.execute} years={years[0]}..{years[-1]} drop_old_year_partitions={args.drop_old_year_partitions}", flush=True)
    if flatfile_status["skipped_non_trading"]:
        print(
            "Repair window skips missing non-trading dates with no quote/trade files: "
            + ", ".join(flatfile_status["skipped_non_trading"]),
            flush=True,
        )

    runner.run("create_repair_log", create_repair_log_table_sql(args))
    runner.run("create_state", create_state_table_sql(args))
    if args.execute:
        ensure_state_table_columns(client, args)

    for year in years:
        table = year_table_name(args, year)
        existing_rows = year_table_row_count(client, args, year) if args.execute else 0
        if existing_rows and args.skip_existing_year and year not in set(args.force_rebuild_year):
            print(f"YEAR SKIP {year} table={table} existing_rows={existing_rows:,}", flush=True)
            repair_days = available_repair_days_for_year(args, windows, year)
            if repair_days:
                old_count = query_scalar(client, source_old_year_count_sql(args, year, repair_days)) if args.execute else 0
                repair_count = query_scalar(client, repair_year_count_sql(args, year)) if args.execute else 0
                expected_rows = old_count + repair_count
            else:
                old_count = existing_rows
                repair_count = 0
                expected_rows = existing_rows
            append_jsonl(
                report_path,
                {
                    "type": "year_skip_existing",
                    "year": year,
                    "existing_rows": existing_rows,
                    "old_rows": old_count,
                    "repair_rows": repair_count,
                    "expected_rows": expected_rows,
                },
            )
            if args.execute:
                audit_year(client, args, year, expected_rows, report_path)
            for chunk_start, chunk_end in iter_date_ranges(date(year, 1, 1), date(year + 1, 1, 1), args.insert_chunk_days):
                chunk_label = f"{chunk_start.strftime('%Y%m%d')}_{chunk_end.strftime('%Y%m%d')}"
                runner.run(
                    f"insert_state_{year}_{chunk_label}",
                    insert_state_after_period_sql(args, year, chunk_start, chunk_end),
                )
            if args.drop_old_year_partitions:
                for label, sql in drop_old_year_partitions_sql(args, year):
                    runner.run(label, sql)
            log_repair_stage(runner, args, run_id, "year", year, "ok", f"existing_rows={existing_rows}")
            continue
        print("=" * 96, flush=True)
        print(f"YEAR START {year} table={table}", flush=True)
        if year in set(args.force_rebuild_year):
            runner.run(f"delete_state_from_{year}", delete_state_from_year_sql(args, year))
        create_year_table(runner, args, year)
        repair_days = available_repair_days_for_year(args, windows, year)
        build_repair_raw_for_year(runner, args, windows, year)
        old_count = query_scalar(client, source_old_year_count_sql(args, year, repair_days)) if args.execute else 0
        repair_count = query_scalar(client, repair_year_count_sql(args, year)) if args.execute and repair_days else 0
        expected_rows = old_count + repair_count
        append_jsonl(report_path, {"type": "year_plan", "year": year, "old_rows": old_count, "repair_rows": repair_count, "expected_rows": expected_rows})
        audit_state = new_year_audit_state(year)
        year_end = date(year + 1, 1, 1)
        for chunk_start, chunk_end in iter_date_ranges(date(year, 1, 1), date(year + 1, 1, 1), args.insert_chunk_days):
            chunk_label = f"{chunk_start.strftime('%Y%m%d')}_{chunk_end.strftime('%Y%m%d')}"
            print(f"YEAR CHUNK {year} {chunk_start.isoformat()} -> {chunk_end.isoformat()}", flush=True)
            append_jsonl(
                report_path,
                {
                    "type": "year_chunk_start",
                    "year": year,
                    "start_date": chunk_start.isoformat(),
                    "end_date": chunk_end.isoformat(),
                },
            )
            runner.run(
                f"insert_{table}_{chunk_label}",
                insert_year_period_sql(args, year, repair_days, chunk_start, chunk_end),
            )
            runner.run(
                f"insert_state_{year}_{chunk_label}",
                insert_state_after_period_sql(args, year, chunk_start, chunk_end),
            )
            if args.execute and should_run_year_audit(args, year, audit_state["last_audit_end"], chunk_end):
                audit_year_range(
                    client,
                    args,
                    year,
                    audit_state["last_audit_end"],
                    chunk_end,
                    audit_state,
                    report_path,
                    final=chunk_end >= year_end,
                    expected_rows=expected_rows if chunk_end >= year_end else None,
                )
        if args.execute and audit_state["last_audit_end"] < year_end:
            audit_year_range(
                client,
                args,
                year,
                audit_state["last_audit_end"],
                year_end,
                audit_state,
                report_path,
                final=True,
                expected_rows=expected_rows,
            )
        if args.drop_old_year_partitions:
            for label, sql in drop_old_year_partitions_sql(args, year):
                runner.run(label, sql)
        log_repair_stage(runner, args, run_id, "year", year, "ok", f"expected_rows={expected_rows}")

    runner.run(f"create_{args.events_all_view}", create_events_all_view_sql(args, years))

    if args.build_derived_tables:
        create_derived_tables(runner, args)
        for year in years:
            for chunk_start, chunk_end in iter_date_ranges(date(year, 1, 1), date(year + 1, 1, 1), args.derived_chunk_days):
                chunk_label = f"{chunk_start.strftime('%Y%m%d')}_{chunk_end.strftime('%Y%m%d')}"
                print(f"DERIVED CHUNK {chunk_start.isoformat()} -> {chunk_end.isoformat()}", flush=True)
                runner.run(
                    f"insert_{derived_continuity_table(args)}_{chunk_label}",
                    insert_derived_continuity_sql(args, args.events_all_view, start_date=chunk_start, end_date=chunk_end),
                )
                runner.run(
                    f"insert_{derived_index_table(args)}_{chunk_label}",
                    insert_derived_index_sql(args, args.events_all_view, start_date=chunk_start, end_date=chunk_end),
                )
        if args.execute:
            audit_derived_tables(client, args, args.events_all_view, report_path)
        if args.promote_derived_tables:
            promote_derived_tables(runner, args)

    if args.replace_events_with_view:
        replace_events_with_view(runner, args, years)

    append_jsonl(report_path, {"type": "run_done", "run_id": run_id, "status": "ok"})
    print(f"DONE report={report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
