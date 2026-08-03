from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.bar_gpt.v1.schema import (  # noqa: E402
    FEATURE_SPECS,
    FEATURE_VERSION,
    ONE_SECOND_US,
    SCHEMA_VERSION,
    SESSION_END_SECOND,
    SESSION_START_SECOND,
    SESSION_TIMEZONE,
    table_columns,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    default_clickhouse_url,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


BUILD_VERSION = "bar_gpt_1s_clickhouse_v1"
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE_BASE = "events"
DEFAULT_INDEX_TABLE = "events_ticker_day_index"
DEFAULT_TARGET_TABLE = "bar_gpt_1s_bars_v1"
DEFAULT_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v1"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_1s")


@dataclass(frozen=True, slots=True)
class TickerBatch:
    index: int
    tickers: tuple[str, ...]
    event_count: int

    @property
    def unit_id(self) -> str:
        digest = hashlib.sha1("\n".join(self.tickers).encode("utf-8")).hexdigest()[:12]
        return f"batch_{self.index:05d}_{digest}"


class BuildReporter:
    """Compact truthful terminal state with readable redirected output."""

    def __init__(
        self,
        *,
        report_path: Path,
        total_days: int,
        interactive: bool,
        title: str = "BarGPT 1s materialization",
        progress_noun: str = "days",
        job_label: str = "BarGPT one-second build",
    ) -> None:
        self.report_path = report_path
        self.total_days = int(total_days)
        self.interactive = bool(interactive and sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
        self.title = title
        self.progress_noun = progress_noun
        self.job_label = job_label
        self.started = time.perf_counter()
        self.day = "-"
        self.unit = "-"
        self.stage = "preflight"
        self.completed_days = 0
        self.completed_units = 0
        self.skipped_units = 0
        self.rows = 0
        self.source_events = 0
        self.last_unit_rows = 0
        self.last_unit_source_events = 0
        self.last_unit_seconds = 0.0
        self.was_interrupted = False
        self.last_message = "Starting"
        self._live = None
        self._console = None

    def __enter__(self) -> "BuildReporter":
        if self.interactive:
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(self._render(), console=self._console, refresh_per_second=2, transient=False)
                self._live.start()
            except Exception:
                self.interactive = False
        self.event("start", message=f"{self.job_label} started")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        suppress = False
        if isinstance(exc, KeyboardInterrupt):
            self.mark_interrupted("Interrupted outside an active materialization query")
            suppress = True
        elif exc is not None:
            self.stage = "failed"
            self.last_message = str(exc)
            self.event("failed", message=str(exc))
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            self._live.stop()
        return suppress

    def _render(self):
        from rich.console import Group
        from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
        from rich.table import Table

        width = self._console.width if self._console is not None else shutil.get_terminal_size((100, 24)).columns
        table = Table(title=self.title, expand=True)
        table.add_column("Status", no_wrap=True, width=11)
        table.add_column("Value", overflow="fold", ratio=1)
        state_style = {
            "complete": "bold green",
            "failed": "bold red",
            "interrupted": "bold yellow",
        }.get(self.stage, "bold cyan")
        table.add_row("state", self.stage, style=state_style)
        table.add_row("current", f"{self.day}  {self.unit}")
        table.add_row(
            "durable",
            f"{self.progress_noun} {self.completed_days}/{self.total_days}  units {self.completed_units}  skipped {self.skipped_units}  "
            f"rows {self.rows:,}  source events {self.source_events:,}",
        )
        if self.last_unit_seconds > 0:
            rows_per_second = self.last_unit_rows / self.last_unit_seconds
            events_per_second = self.last_unit_source_events / self.last_unit_seconds
            table.add_row(
                "last unit",
                f"{self.last_unit_rows:,} rows in {self.last_unit_seconds:,.1f}s  "
                f"({rows_per_second:,.0f} rows/s; {events_per_second:,.0f} source events/s)",
            )
        table.add_row("elapsed", f"{time.perf_counter() - self.started:,.1f}s")
        table.add_row("latest", self.last_message)
        if width >= 80:
            table.add_row("evidence", str(self.report_path))

        progress = Progress(
            TextColumn(self.progress_noun),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            expand=True,
        )
        progress.add_task(
            "materialization",
            total=max(self.total_days, 1),
            completed=min(self.completed_days, max(self.total_days, 1)),
        )
        return Group(table, progress)

    def update(self, *, stage: str | None = None, day: str | None = None, unit: str | None = None, message: str | None = None) -> None:
        if stage is not None:
            self.stage = stage
        if day is not None:
            self.day = day
        if unit is not None:
            self.unit = unit
        if message is not None:
            self.last_message = message
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def event(self, kind: str, **payload: object) -> None:
        record = {
            "event": kind,
            "utc": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            "elapsed_seconds": time.perf_counter() - self.started,
            **payload,
        }
        with self.report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        if not self.interactive:
            detail = str(payload.get("message") or payload.get("unit_id") or "")
            print(f"[{kind}] day={self.day} unit={self.unit} {detail}".rstrip(), flush=True)

    def record_unit_complete(self, *, output_rows: int, source_events: int, seconds: float) -> None:
        self.completed_units += 1
        self.rows += int(output_rows)
        self.source_events += int(source_events)
        self.last_unit_rows = int(output_rows)
        self.last_unit_source_events = int(source_events)
        self.last_unit_seconds = max(float(seconds), 1e-9)
        self.update(
            message=f"Completed {output_rows:,} rows from {source_events:,} source events in {seconds:,.1f}s"
        )

    def mark_interrupted(self, message: str, **payload: object) -> None:
        self.was_interrupted = True
        self.update(stage="interrupted", message=message)
        self.event("interrupted", message=message, **payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize certified rich one-second BarGPT sufficient statistics in ClickHouse.")
    parser.add_argument("--execute", action="store_true", help="Execute writes. Without this flag only schema and plan SQL are printed.")
    parser.add_argument("--validate-sql", action="store_true", help="Ask ClickHouse to parse and analyze the generated SELECT without writing.")
    parser.add_argument("--no-print-sql", action="store_true", help="Suppress full SQL text in preview mode.")
    parser.add_argument("--start-date", default="auto", help="Inclusive New York date or 'auto' for index minimum.")
    parser.add_argument("--end-date", default="auto", help="Exclusive New York date or 'auto' for one day after index maximum.")
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker restriction.")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table-base", default=DEFAULT_EVENTS_TABLE_BASE)
    parser.add_argument("--index-table", default=DEFAULT_INDEX_TABLE)
    parser.add_argument("--target-table", default=DEFAULT_TARGET_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--allow-empty-storage-policy", action="store_true")
    parser.add_argument("--ticker-batch-max-events", type=int, default=40_000_000)
    parser.add_argument("--ticker-batch-max-tickers", type=int, default=256)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="48G")
    parser.add_argument("--max-bytes-before-external-group-by", default="12G")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    return parser.parse_args(argv)


def _size_literal(value: str) -> str:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:i?B)?\s*", str(value), flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid byte size {value!r}")
    multipliers = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    return str(int(float(match.group(1)) * multipliers[match.group(2).upper()]))


def query_settings(args: argparse.Namespace, *, mutation: bool = False) -> str:
    settings = {
        "max_threads": max(1, int(args.max_threads)),
        "max_memory_usage": _size_literal(args.max_memory_usage),
        "max_bytes_before_external_group_by": _size_literal(args.max_bytes_before_external_group_by),
        "max_execution_time": 0,
        "log_queries": 1,
    }
    if mutation:
        settings["mutations_sync"] = 2
    return "\nSETTINGS " + ", ".join(f"{name} = {value}" for name, value in settings.items())


def create_target_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {column_type}" for name, column_type in table_columns())
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(local_date)
ORDER BY (ticker, local_date, bucket_index)
{mergetree_settings_sql(args.storage_policy)}
"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    artifact_name LowCardinality(String),
    local_date Date,
    unit_id String,
    status LowCardinality(String),
    build_version LowCardinality(String),
    feature_version LowCardinality(String),
    ticker_count UInt32,
    source_event_count UInt64,
    output_row_count UInt64,
    message String,
    updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(local_date)
ORDER BY (artifact_name, local_date, unit_id)
{mergetree_settings_sql(args.storage_policy)}
"""


def _event_source(args: argparse.Namespace, day: dt.date) -> str:
    years = sorted({day.year, (day + dt.timedelta(days=1)).year})
    tables = [f"{args.events_table_base}_{year}" for year in years]
    if len(tables) == 1:
        return f"{quote_ident(args.database)}.{quote_ident(tables[0])}"
    pattern = "^(" + "|".join(re.escape(table) for table in tables) + ")$"
    return f"merge({sql_string(args.database)}, {sql_string(pattern)})"


def _ohlc(prefix: str, value: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toFloat32(argMinIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_open')}",
        f"toFloat32(maxIf({value}, {condition})) AS {quote_ident(prefix + '_high')}",
        f"toFloat32(minIf({value}, {condition})) AS {quote_ident(prefix + '_low')}",
        f"toFloat32(argMaxIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_close')}",
    ]


def _family_aggregates(prefix: str, price: str, size: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toUInt8(countIf({condition}) > 0) AS {quote_ident(prefix + '_present')}",
        *_ohlc(prefix, price, condition),
        f"toFloat64(sumIf({size}, {condition})) AS {quote_ident(prefix + '_size_sum')}",
        f"toFloat64(argMinIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_open')}",
        f"toFloat64(maxIf({size}, {condition})) AS {quote_ident(prefix + '_size_high')}",
        f"toFloat64(minIf({size}, {condition})) AS {quote_ident(prefix + '_size_low')}",
        f"toFloat64(argMaxIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_close')}",
        f"toFloat64(sumIf({size} * {size}, {condition})) AS {quote_ident(prefix + '_size_squared_sum')}",
        f"toFloat64(sumIf({price} * {size}, {condition})) AS {quote_ident(prefix + '_price_size_sum')}",
        f"toUInt64(countIf({condition})) AS {quote_ident(prefix + '_event_count')}",
    ]


def _relation_aggregates(prefix: str, value: str, condition: str) -> list[str]:
    return [
        *_ohlc(prefix, value, condition),
        f"toFloat64(sumIf({value}, {condition})) AS {quote_ident(prefix + '_sum')}",
        f"toFloat64(sumIf({value} * {value}, {condition})) AS {quote_ident(prefix + '_squared_sum')}",
    ]


def insert_one_second_sql(args: argparse.Namespace, day: dt.date, tickers: tuple[str, ...]) -> str:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    source = _event_source(args, day)
    ticker_filter = ""
    if tickers:
        ticker_filter = "\n      AND ticker IN (" + ", ".join(sql_string(ticker) for ticker in tickers) + ")"
    trade_valid = "event_type = 1 AND trade_price > 0 AND trade_size > 0"
    bid_valid = "event_type = 0 AND bid_price > 0 AND bid_size > 0"
    ask_valid = "event_type = 0 AND ask_price > 0 AND ask_size > 0"
    pair_valid = "event_type = 0 AND bid_price > 0 AND ask_price > 0 AND bid_size > 0 AND ask_size > 0 AND bid_price <= ask_price"
    aggregates = [
        *_family_aggregates("trade", "trade_price", "trade_size", trade_valid),
        *_family_aggregates("bid", "bid_price", "bid_size", bid_valid),
        *_family_aggregates("ask", "ask_price", "ask_size", ask_valid),
        f"toUInt8(countIf({pair_valid}) > 0) AS quote_pair_present",
        f"toUInt64(countIf({pair_valid})) AS quote_pair_count",
        *_relation_aggregates("spread", "spread", pair_valid),
        *_relation_aggregates("midpoint", "midpoint", pair_valid),
        *_relation_aggregates("microprice", "microprice", pair_valid),
        *_relation_aggregates("queue_imbalance", "queue_imbalance", pair_valid),
        "toUInt64(countIf(event_type = 0 AND bid_price = ask_price AND bid_price > 0)) AS locked_quote_count",
        "toUInt64(countIf(event_type = 0 AND bid_price > ask_price AND ask_price > 0)) AS crossed_quote_count",
        "toUInt64(sum(toUInt8(condition_token_1 > 0) + toUInt8(condition_token_2 > 0) + toUInt8(condition_token_3 > 0) + toUInt8(condition_token_4 > 0) + toUInt8(condition_token_5 > 0))) AS condition_nonzero_count",
        "toUInt64(count()) AS source_event_count",
    ]
    aggregate_sql = ",\n    ".join(aggregates)
    insert_columns = ",\n    ".join(quote_ident(name) for name, _ in table_columns())
    first_event_date = day.isoformat()
    last_event_date = (day + dt.timedelta(days=1)).isoformat()
    return f"""
INSERT INTO {target}
(
    {insert_columns}
)
WITH
    toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    toDate(ts_local) AS local_date_value,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second,
    dateDiff('microsecond', toStartOfDay(ts_local), ts_local) AS local_session_us,
    bitAnd(event_meta, 1) AS event_type,
    toFloat64(if(price_primary_int > 0, price_primary_int / if(bitAnd(event_meta, 2) = 2, 10000.0, 100.0), 0.0)) AS primary_price,
    toFloat64(if(price_secondary_int > 0, price_secondary_int / if(bitAnd(event_meta, 4) = 4, 10000.0, 100.0), 0.0)) AS secondary_price,
    if(event_type = 1, primary_price, 0.0) AS trade_price,
    if(event_type = 1, toFloat64(size_primary), 0.0) AS trade_size,
    if(event_type = 0, primary_price, 0.0) AS ask_price,
    if(event_type = 0, secondary_price, 0.0) AS bid_price,
    if(event_type = 0, toFloat64(size_primary), 0.0) AS ask_size,
    if(event_type = 0, toFloat64(size_secondary), 0.0) AS bid_size,
    ask_price - bid_price AS spread,
    (ask_price + bid_price) / 2.0 AS midpoint,
    if(ask_size + bid_size > 0, (ask_price * bid_size + bid_price * ask_size) / (ask_size + bid_size), 0.0) AS microprice,
    if(ask_size + bid_size > 0, (bid_size - ask_size) / (bid_size + ask_size), 0.0) AS queue_imbalance,
    intDiv(toUInt64(local_session_us), toUInt64({ONE_SECOND_US})) AS second_bucket_index,
    intDiv(toUInt64(sip_timestamp_us), toUInt64({ONE_SECOND_US})) * toUInt64({ONE_SECOND_US}) AS second_start_us
SELECT
    toUInt16({SCHEMA_VERSION}) AS schema_version,
    {sql_string(FEATURE_VERSION)} AS feature_version,
    local_date_value AS local_date,
    upper(ticker) AS ticker,
    second_bucket_index AS bucket_index,
    second_start_us AS bar_start_us,
    second_start_us + toUInt64({ONE_SECOND_US}) AS bar_end_us,
    second_start_us + toUInt64({ONE_SECOND_US}) AS available_at_us,
    min(toUInt64(ordinal)) AS source_first_ordinal,
    max(toUInt64(ordinal)) AS source_last_ordinal,
    min(toUInt64(sip_timestamp_us)) AS source_first_timestamp_us,
    max(toUInt64(sip_timestamp_us)) AS source_last_timestamp_us,
    {aggregate_sql},
    now64(3, 'UTC') AS built_at
FROM {source}
PREWHERE event_date >= toDate({sql_string(first_event_date)})
  AND event_date < toDate({sql_string(last_event_date)})
  {ticker_filter}
WHERE local_date_value = toDate({sql_string(day.isoformat())})
  AND local_second >= {SESSION_START_SECOND}
  AND local_second < {SESSION_END_SECOND}
GROUP BY
    local_date_value,
    ticker,
    second_bucket_index,
    second_start_us
{query_settings(args)}
"""


def _execute(client: ClickHouseHttpClient, sql: str, *, query_id: str | None = None) -> str:
    return client.execute(sql.strip().rstrip(";"), query_id=query_id)


def explain_insert_select(client: ClickHouseHttpClient, insert_sql: str) -> str:
    match = re.search(r"\nWITH\n", insert_sql)
    if match is None:
        raise ValueError("generated INSERT SELECT has no WITH boundary")
    select_sql = insert_sql[match.start() + 1 :].strip().rstrip(";")
    return _execute(client, "EXPLAIN SYNTAX\n" + select_sql)


def _query_tsv(client: ClickHouseHttpClient, sql: str) -> list[list[str]]:
    # The values consumed here are bounded identifiers and numeric/metadata
    # fields. TSVRaw avoids ClickHouse's backslash escaping of schema strings
    # such as DateTime64(3, 'UTC'), which must compare byte-for-byte with the
    # declared model schema.
    query = sql.strip().rstrip(";") + "\nFORMAT TSVRaw"
    return [line.split("\t") for line in client.execute(query).splitlines() if line.strip()]


def _show_create_raw(client: ClickHouseHttpClient, database: str, table: str) -> str:
    return _execute(
        client,
        f"SHOW CREATE TABLE {quote_ident(database)}.{quote_ident(table)} FORMAT TSVRaw",
    )


def resolve_date_range(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    if args.start_date != "auto" and args.end_date != "auto":
        start = dt.date.fromisoformat(args.start_date)
        end = dt.date.fromisoformat(args.end_date)
    else:
        rows = _query_tsv(
            client,
            f"SELECT min(source_date), addDays(max(source_date), 1) FROM {quote_ident(args.database)}.{quote_ident(args.index_table)}",
        )
        if not rows or rows[0][0] in {"", "1970-01-01", "\\N"}:
            raise RuntimeError("ticker/day index has no coverage")
        start = dt.date.fromisoformat(args.start_date) if args.start_date != "auto" else dt.date.fromisoformat(rows[0][0])
        end = dt.date.fromisoformat(args.end_date) if args.end_date != "auto" else dt.date.fromisoformat(rows[0][1])
    if end <= start:
        raise ValueError("end-date must be later than start-date")
    return start, end


def _requested_tickers(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(sorted({item.strip().upper() for item in str(args.tickers).split(",") if item.strip()}))


def ticker_fingerprint(tickers: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(sorted(tickers)).encode("utf-8")).hexdigest() if tickers else "all_tickers"


def plan_ticker_batches(client: ClickHouseHttpClient, args: argparse.Namespace, day: dt.date) -> list[TickerBatch]:
    restriction = ""
    requested = _requested_tickers(args)
    if requested:
        restriction = " AND upper(ticker) IN (" + ", ".join(sql_string(item) for item in requested) + ")"
    rows = _query_tsv(
        client,
        f"""
SELECT upper(ticker), sum(event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.index_table)}
WHERE source_date = toDate({sql_string(day.isoformat())}){restriction}
GROUP BY ticker
HAVING sum(event_count) > 0
ORDER BY sum(event_count) DESC, ticker
""",
    )
    max_events = max(1, int(args.ticker_batch_max_events))
    max_tickers = max(1, int(args.ticker_batch_max_tickers))
    batches: list[TickerBatch] = []
    tickers: list[str] = []
    events = 0
    for ticker, count_text, *_ in rows:
        count = int(count_text)
        if tickers and (events + count > max_events or len(tickers) >= max_tickers):
            batches.append(TickerBatch(len(batches) + 1, tuple(tickers), events))
            tickers = []
            events = 0
        tickers.append(ticker)
        events += count
    if tickers:
        batches.append(TickerBatch(len(batches) + 1, tuple(tickers), events))
    return batches


def completed_units(client: ClickHouseHttpClient, args: argparse.Namespace, day: dt.date) -> set[str]:
    rows = _query_tsv(
        client,
        f"""
SELECT unit_id
FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name = {sql_string(args.target_table)}
  AND local_date = toDate({sql_string(day.isoformat())})
  AND status = 'complete'
  AND build_version = {sql_string(BUILD_VERSION)}
""",
    )
    return {row[0] for row in rows}


def insert_manifest(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    *,
    day: dt.date,
    unit_id: str,
    status: str,
    ticker_count: int,
    source_events: int,
    output_rows: int,
    message: str = "",
) -> None:
    _execute(
        client,
        f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.manifest_table)} VALUES
(
    {sql_string(args.target_table)},
    toDate({sql_string(day.isoformat())}),
    {sql_string(unit_id)},
    {sql_string(status)},
    {sql_string(BUILD_VERSION)},
    {sql_string(FEATURE_VERSION)},
    toUInt32({int(ticker_count)}),
    toUInt64({int(source_events)}),
    toUInt64({int(output_rows)}),
    {sql_string(message)},
    now64(3, 'UTC')
)
""",
    )


def query_unit_stats(client: ClickHouseHttpClient, args: argparse.Namespace, day: dt.date, tickers: tuple[str, ...]) -> tuple[int, int]:
    ticker_filter = ", ".join(sql_string(ticker) for ticker in tickers)
    rows = _query_tsv(
        client,
        f"""
SELECT count(), uniqExact(tuple(ticker, local_date, bucket_index)), sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE local_date = toDate({sql_string(day.isoformat())})
  AND ticker IN ({ticker_filter})
""",
    )
    if not rows:
        return 0, 0
    output_rows, unique_rows, source_events = (int(value) for value in rows[0])
    if output_rows != unique_rows:
        raise RuntimeError(f"unit key audit failed for {day}: rows={output_rows} unique={unique_rows}")
    return output_rows, source_events


def certify_month(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    month: str,
    reporter: BuildReporter,
    *,
    requested_start: dt.date,
    requested_end: dt.date,
    planned_units: list[tuple[dt.date, str]],
) -> None:
    month_start = dt.date.fromisoformat(month + "-01")
    if month_start.month == 12:
        month_end = dt.date(month_start.year + 1, 1, 1)
    else:
        month_end = dt.date(month_start.year, month_start.month + 1, 1)
    partition = month_start.year * 100 + month_start.month
    reporter.update(stage="certifying", day=month, unit="partition", message="Collapsing retry duplicates and auditing keys")
    _execute(
        client,
        f"OPTIMIZE TABLE {quote_ident(args.database)}.{quote_ident(args.target_table)} PARTITION {partition} FINAL{query_settings(args)}",
        query_id=f"bar_gpt_1s_optimize_{partition}_{uuid.uuid4().hex}",
    )
    audit_start = max(month_start, requested_start)
    audit_end = min(month_end, requested_end)
    requested_tickers = _requested_tickers(args)
    ticker_filter = (
        "\n  AND upper(ticker) IN (" + ", ".join(sql_string(value) for value in requested_tickers) + ")"
        if requested_tickers
        else ""
    )
    rows = _query_tsv(
        client,
        f"""
SELECT count(), uniqExact(tuple(ticker, local_date, bucket_index)), countIf(available_at_us != bar_end_us), min(schema_version), max(schema_version), sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)}
WHERE local_date >= toDate({sql_string(audit_start.isoformat())})
  AND local_date < toDate({sql_string(audit_end.isoformat())}){ticker_filter}
""",
    )
    total, unique, bad_availability, min_schema, max_schema, source_events = (int(value) for value in rows[0])
    if not planned_units:
        insert_manifest(
            client,
            args,
            day=month_start,
            unit_id=f"__range__{audit_start.isoformat()}__{audit_end.isoformat()}",
            status="certified_range",
            ticker_count=0,
            source_events=0,
            output_rows=0,
            message=f"certified empty range [{audit_start},{audit_end})",
        )
        reporter.event("month_no_source", month=month, start=audit_start, end=audit_end)
        return
    unit_filter = ", ".join(
        f"tuple(toDate({sql_string(day.isoformat())}), {sql_string(unit_id)})"
        for day, unit_id in planned_units
    )
    manifest_rows = _query_tsv(
        client,
        f"""
SELECT sum(output_row_count), sum(source_event_count)
FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name = {sql_string(args.target_table)}
  AND tuple(local_date, unit_id) IN ({unit_filter})
  AND status = 'complete'
  AND build_version = {sql_string(BUILD_VERSION)}
""",
    )
    expected_rows, expected_source_events = (int(value) for value in manifest_rows[0])
    if total != unique or bad_availability or min_schema != SCHEMA_VERSION or max_schema != SCHEMA_VERSION:
        raise RuntimeError(
            f"month certification failed {month}: rows={total} unique={unique} bad_availability={bad_availability} schema={min_schema}..{max_schema}"
        )
    if total != expected_rows or source_events != expected_source_events:
        raise RuntimeError(
            f"month manifest audit failed {month}: rows={total}/{expected_rows} source_events={source_events}/{expected_source_events}"
        )
    insert_manifest(
        client,
        args,
        day=month_start,
        unit_id=f"__range__{audit_start.isoformat()}__{audit_end.isoformat()}",
        status="certified_range",
        ticker_count=0,
        source_events=0,
        output_rows=total,
        message=f"certified range [{audit_start},{audit_end})",
    )
    reporter.event("month_certified", month=month, start=audit_start, end=audit_end, rows=total)


def date_range(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = start
    while current < end:
        yield current
        current += dt.timedelta(days=1)


def validate_storage_policy(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if not args.storage_policy and not args.allow_empty_storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required for the BarGPT one-second tables")
    if args.storage_policy:
        rows = _query_tsv(client, f"SELECT count() FROM system.storage_policies WHERE policy_name = {sql_string(args.storage_policy)}")
        if not rows or int(rows[0][0]) != 1:
            raise RuntimeError(f"ClickHouse storage policy {args.storage_policy!r} does not exist")


def validate_created_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    expected = dict(table_columns())
    rows = _query_tsv(
        client,
        f"""
SELECT name, type
FROM system.columns
WHERE database = {sql_string(args.database)}
  AND table = {sql_string(args.target_table)}
""",
    )
    actual = {name: column_type for name, column_type, *_ in rows}
    mismatches = {
        name: {"expected": column_type, "actual": actual.get(name)}
        for name, column_type in expected.items()
        if actual.get(name) != column_type
    }
    if mismatches:
        raise RuntimeError(f"{args.database}.{args.target_table} schema mismatch: {mismatches}")
    create_sql = _show_create_raw(client, args.database, args.target_table)
    required = (
        "ReplacingMergeTree(built_at)",
        "PARTITION BY toYYYYMM(local_date)",
        "ORDER BY (ticker, local_date, bucket_index)",
    )
    absent = [fragment for fragment in required if fragment not in create_sql]
    if absent:
        raise RuntimeError(f"{args.database}.{args.target_table} physical contract mismatch: missing {absent}")
    if args.storage_policy and f"storage_policy = {sql_string(args.storage_policy)}" not in create_sql:
        raise RuntimeError(f"{args.database}.{args.target_table} is not on requested storage policy {args.storage_policy!r}")
    manifest_sql = _show_create_raw(client, args.database, args.manifest_table)
    if args.storage_policy and f"storage_policy = {sql_string(args.storage_policy)}" not in manifest_sql:
        raise RuntimeError(f"{args.database}.{args.manifest_table} is not on requested storage policy {args.storage_policy!r}")


def main(argv: list[str] | None = None) -> int:
    loaded_env = load_env_files(discover_clickhouse_env_files())
    args = parse_args(argv)
    # Defaults are evaluated before .env loading; resolve environment-owned values again.
    if not args.storage_policy:
        args.storage_policy = os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", "")
    if args.clickhouse_url == default_clickhouse_url():
        args.clickhouse_url = default_clickhouse_url()
    if args.clickhouse_user == default_clickhouse_user():
        args.clickhouse_user = default_clickhouse_user()
    if args.clickhouse_password == default_clickhouse_password():
        args.clickhouse_password = default_clickhouse_password()
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, args.clickhouse_password, persistent=True)
    validate_storage_policy(client, args)
    start, end = resolve_date_range(client, args)
    days = list(date_range(start, end))
    run_id = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = args.runtime_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "build.jsonl"
    interactive = args.progress_layout == "rich" or (args.progress_layout == "auto" and sys.stdout.isatty())
    with BuildReporter(report_path=report_path, total_days=len(days), interactive=interactive) as reporter:
        requested_tickers = _requested_tickers(args)
        cohort_sha256 = ticker_fingerprint(requested_tickers)
        reporter.event(
            "preflight",
            message=f"range=[{start},{end}) execute={args.execute} storage_policy_present={bool(args.storage_policy)} loaded_env_files={len(loaded_env)}",
            secret_status=secret_status(["CLICKHOUSE_LIVE_STORAGE_POLICY", "CLICKHOUSE_WORKSTATION_PASSWORD", "CLICKHOUSE_PASSWORD"]),
            requested_ticker_count=len(requested_tickers),
            requested_ticker_sha256=cohort_sha256,
            target_table=args.target_table,
            manifest_table=args.manifest_table,
        )
        if not args.execute:
            sample_day = days[0]
            sample_batches = plan_ticker_batches(client, args, sample_day)
            sample_tickers = sample_batches[0].tickers if sample_batches else _requested_tickers(args)
            sample_sql = insert_one_second_sql(args, sample_day, sample_tickers)
            if args.validate_sql:
                explained = explain_insert_select(client, sample_sql)
                reporter.event("sql_validated", sample_day=sample_day, explained_characters=len(explained))
            if not args.no_print_sql:
                print(create_target_table_sql(args).strip())
                print(create_manifest_table_sql(args).strip())
                print(sample_sql.strip())
            reporter.update(stage="dry-run complete", day=sample_day.isoformat(), unit="sample", message="No ClickHouse writes executed")
            reporter.event("dry_run_complete", sample_day=sample_day, sample_ticker_count=len(sample_tickers))
            return 0
        _execute(client, create_target_table_sql(args))
        _execute(client, create_manifest_table_sql(args))
        validate_created_tables(client, args)
        months_touched: list[str] = []
        certification_units: dict[str, list[tuple[dt.date, str]]] = {}
        for day in days:
            day_text = day.isoformat()
            month = day_text[:7]
            if month not in months_touched:
                months_touched.append(month)
            reporter.update(stage="planning", day=day_text, unit="-", message="Reading ticker/day index")
            batches = plan_ticker_batches(client, args, day)
            certification_units.setdefault(month, []).extend((day, batch.unit_id) for batch in batches)
            done = completed_units(client, args, day)
            if not batches:
                reporter.completed_days += 1
                reporter.event("day_no_source", day=day_text)
                continue
            for batch in batches:
                reporter.update(stage="building", day=day_text, unit=batch.unit_id, message=f"{len(batch.tickers)} tickers; {batch.event_count:,} source events")
                if batch.unit_id in done:
                    reporter.skipped_units += 1
                    reporter.event("unit_skipped", day=day_text, unit_id=batch.unit_id, message="durable complete manifest exists")
                    continue
                query_id = f"bar_gpt_1s_{day.strftime('%Y%m%d')}_{batch.index:05d}_{uuid.uuid4().hex}"
                started = time.perf_counter()
                try:
                    _execute(client, insert_one_second_sql(args, day, batch.tickers), query_id=query_id)
                except KeyboardInterrupt:
                    try:
                        _execute(client, f"KILL QUERY WHERE query_id = {sql_string(query_id)} ASYNC")
                    finally:
                        reporter.mark_interrupted(
                            "Cancellation requested; active ClickHouse query kill submitted",
                            day=day_text,
                            unit_id=batch.unit_id,
                            query_id=query_id,
                        )
                    return 130
                output_rows, aggregated_source_events = query_unit_stats(client, args, day, batch.tickers)
                insert_manifest(
                    client,
                    args,
                    day=day,
                    unit_id=batch.unit_id,
                    status="complete",
                    ticker_count=len(batch.tickers),
                    source_events=aggregated_source_events,
                    output_rows=output_rows,
                )
                unit_seconds = time.perf_counter() - started
                reporter.record_unit_complete(
                    output_rows=output_rows,
                    source_events=aggregated_source_events,
                    seconds=unit_seconds,
                )
                reporter.event(
                    "unit_complete",
                    day=day_text,
                    unit_id=batch.unit_id,
                    source_events=aggregated_source_events,
                    planned_index_events=batch.event_count,
                    output_rows=output_rows,
                    seconds=unit_seconds,
                )
            reporter.completed_days += 1
            reporter.event("day_complete", day=day_text, units=len(batches))
        for month in months_touched:
            certify_month(
                client,
                args,
                month,
                reporter,
                requested_start=start,
                requested_end=end,
                planned_units=certification_units.get(month, []),
            )
        reporter.update(stage="complete", day=end.isoformat(), unit="-", message=f"Certified {len(months_touched)} monthly partitions")
        reporter.event("complete", months=len(months_touched), rows=reporter.rows)
    return 130 if reporter.was_interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
