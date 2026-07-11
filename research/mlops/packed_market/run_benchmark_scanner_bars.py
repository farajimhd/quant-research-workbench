from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402

SESSION_TZ = "America/New_York"
SESSION_OPEN_ET = "09:45:00"
DEFAULT_END_ET = "10:15:00"
DEFAULT_TIMEFRAMES = ("1s", "5s", "15s", "30s", "1m")


@dataclass(slots=True)
class BenchStep:
    name: str
    status: str
    seconds: float
    query_id: str = ""
    rows: int = 0
    tickers: int = 0
    source_events: int = 0
    read_rows: int | None = None
    read_bytes: int | None = None
    memory_usage: int | None = None
    written_rows: int | None = None
    written_bytes: int | None = None
    detail: dict[str, object] = field(default_factory=dict)
    error: str = ""


class ActiveQueries:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.query_ids: set[str] = set()

    def new_id(self, label: str) -> str:
        safe_label = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_")[:80]
        query_id = f"{self.prefix}_{safe_label}_{uuid.uuid4().hex[:10]}"
        self.query_ids.add(query_id)
        return query_id

    def finish(self, query_id: str) -> None:
        self.query_ids.discard(query_id)

    def cancel_all(self, client: ClickHouseHttpClient) -> None:
        for query_id in sorted(self.query_ids):
            try:
                print(f"CANCEL query_id={query_id}", flush=True)
                client.execute(f"KILL QUERY WHERE query_id = {sql_string(query_id)} SYNC")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN cancel failed query_id={query_id}: {exc!r}", flush=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark ClickHouse scanner-bar construction for all tickers over one ET date/window. "
            "It times direct raw aggregation and the sidecar shape: build one base bar table, then aggregate larger bars from it."
        )
    )
    parser.add_argument("--date", required=True, help="ET source date, for example 2019-02-01.")
    parser.add_argument("--start-et", default=SESSION_OPEN_ET, help="ET start time. Default: 09:45:00.")
    parser.add_argument("--end-et", default=DEFAULT_END_ET, help="ET end time. Default: 10:15:00. Use 20:00:00 for full extended session.")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES), help="Comma-separated bar lengths, e.g. 1s,5s,15s,30s,1m.")
    parser.add_argument("--base-timeframe", default="1s", help="Smallest bar materialized once for sidecar aggregation.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--mode", choices=("direct", "sidecar", "both"), default="both")
    parser.add_argument("--materialize-engine", choices=("Memory", "MergeTree"), default="Memory")
    parser.add_argument("--keep-temp-table", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-rank", action=argparse.BooleanOptionalAction, default=True, help="Also time scanner rank features from the base trade bars.")
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="64G")
    parser.add_argument("--max-bytes-before-external-group-by", default="32G")
    parser.add_argument("--max-bytes-before-external-sort", default="16G")
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\packed_market_model\v1\scanner_bar_benchmarks")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_dir = Path(args.output_root) / f"scanner_bar_benchmark_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "scanner_bar_benchmark.jsonl"
    temp_table = f"tmp_scanner_bar_benchmark_{run_id}"
    active = ActiveQueries(prefix=f"scanner_bar_bench_{run_id}")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())

    window = compute_window(args.date, args.start_et, args.end_et)
    timeframes = tuple(parse_csv(args.timeframes))
    base_timeframe = str(args.base_timeframe).strip().lower()
    if base_timeframe not in timeframes:
        timeframes = (base_timeframe, *timeframes)
    timeframes = tuple(sorted(set(timeframes), key=duration_us))

    append_jsonl(
        report_path,
        {
            "event": "start",
            "run_id": run_id,
            "args": vars(args),
            "window": window,
            "timeframes": timeframes,
            "temp_table": f"{args.database}.{temp_table}",
        },
    )
    print(f"SCANNER BAR BENCHMARK {run_dir}", flush=True)
    print(
        f"date={args.date} window_et={args.start_et}->{args.end_et} "
        f"timeframes={','.join(timeframes)} mode={args.mode} temp={args.database}.{temp_table}",
        flush=True,
    )

    interrupted = False

    def _interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    old_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _interrupt)
    try:
        steps: list[BenchStep] = []
        raw_step = run_timed_query(
            client,
            active,
            "raw_event_count",
            raw_event_count_sql(args, window),
            row_parser=parse_count_row,
        )
        steps.append(raw_step)
        append_jsonl(report_path, {"event": "step", **asdict(raw_step)})
        print_step(raw_step)

        if args.mode in {"direct", "both"}:
            for timeframe in timeframes:
                step = run_timed_query(
                    client,
                    active,
                    f"direct_{timeframe}_trade_bid_ask_bars",
                    direct_bar_count_sql(args, window, duration_us(timeframe)),
                    row_parser=parse_bar_count_row,
                )
                step.detail["timeframe"] = timeframe
                steps.append(step)
                append_jsonl(report_path, {"event": "step", **asdict(step)})
                print_step(step)

        if args.mode in {"sidecar", "both"}:
            create_step = run_timed_execute(
                client,
                active,
                "sidecar_create_base_table",
                create_base_table_sql(args, temp_table),
            )
            steps.append(create_step)
            append_jsonl(report_path, {"event": "step", **asdict(create_step)})
            print_step(create_step)

            insert_step = run_timed_execute(
                client,
                active,
                f"sidecar_insert_base_{base_timeframe}_trade_bid_ask_bars",
                insert_base_bars_sql(args, window, temp_table, duration_us(base_timeframe)),
            )
            insert_step.detail["timeframe"] = base_timeframe
            steps.append(insert_step)
            append_jsonl(report_path, {"event": "step", **asdict(insert_step)})
            print_step(insert_step)

            base_count = run_timed_query(
                client,
                active,
                "sidecar_base_table_count",
                table_count_sql(args, temp_table),
                row_parser=parse_bar_count_row,
            )
            base_count.detail["timeframe"] = base_timeframe
            steps.append(base_count)
            append_jsonl(report_path, {"event": "step", **asdict(base_count)})
            print_step(base_count)

            for timeframe in timeframes:
                if duration_us(timeframe) == duration_us(base_timeframe):
                    continue
                step = run_timed_query(
                    client,
                    active,
                    f"sidecar_aggregate_{timeframe}_from_{base_timeframe}",
                    aggregate_from_base_sql(args, temp_table, duration_us(base_timeframe), duration_us(timeframe)),
                    row_parser=parse_bar_count_row,
                )
                step.detail["timeframe"] = timeframe
                steps.append(step)
                append_jsonl(report_path, {"event": "step", **asdict(step)})
                print_step(step)

            if bool(args.include_rank):
                rank_step = run_timed_query(
                    client,
                    active,
                    f"sidecar_rank_scanner_from_{base_timeframe}",
                    rank_from_base_sql(args, temp_table),
                    row_parser=parse_rank_row,
                )
                steps.append(rank_step)
                append_jsonl(report_path, {"event": "step", **asdict(rank_step)})
                print_step(rank_step)

    except KeyboardInterrupt:
        interrupted = True
        print("INTERRUPT received; cancelling active ClickHouse queries.", flush=True)
        active.cancel_all(client)
        append_jsonl(report_path, {"event": "interrupted"})
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {exc!r}", flush=True)
        active.cancel_all(client)
        append_jsonl(report_path, {"event": "error", "error": repr(exc)})
        return_code = 1
    else:
        return_code = 0
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        if not bool(args.keep_temp_table):
            try:
                cleanup_step = run_timed_execute(
                    client,
                    active,
                    "cleanup_drop_temp_table",
                    f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(temp_table)} SYNC",
                )
                append_jsonl(report_path, {"event": "step", **asdict(cleanup_step)})
                print_step(cleanup_step)
            except Exception as exc:  # noqa: BLE001
                append_jsonl(report_path, {"event": "cleanup_error", "error": repr(exc)})
                print(f"WARN cleanup failed: {exc!r}", flush=True)

    append_jsonl(report_path, {"event": "summary", "interrupted": interrupted})
    print(f"REPORT {report_path}", flush=True)
    return 130 if interrupted else return_code


def compute_window(source_date: str, start_et: str, end_et: str) -> dict[str, object]:
    tz = ZoneInfo(SESSION_TZ)
    day = date.fromisoformat(source_date)
    start_dt = datetime.combine(day, parse_clock(start_et), tzinfo=tz)
    end_dt = datetime.combine(day, parse_clock(end_et), tzinfo=tz)
    if end_dt <= start_dt:
        raise ValueError(f"end-et must be after start-et: {start_et} -> {end_et}")
    start_utc = start_dt.astimezone(ZoneInfo("UTC"))
    end_utc = end_dt.astimezone(ZoneInfo("UTC"))
    return {
        "source_date": source_date,
        "start_et": start_dt.isoformat(),
        "end_et": end_dt.isoformat(),
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "start_us": int(start_utc.timestamp() * 1_000_000),
        "end_us": int(end_utc.timestamp() * 1_000_000),
        "event_date_start": start_utc.date().isoformat(),
        "event_date_end": end_utc.date().isoformat(),
    }


def parse_clock(value: str) -> dt_time:
    parts = [int(item) for item in str(value).strip().split(":")]
    if len(parts) == 2:
        return dt_time(parts[0], parts[1])
    if len(parts) == 3:
        return dt_time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time value {value!r}; expected HH:MM or HH:MM:SS.")


def run_timed_execute(client: ClickHouseHttpClient, active: ActiveQueries, name: str, sql: str) -> BenchStep:
    return _run_timed(client, active, name, sql, expects_rows=False, row_parser=None)


def run_timed_query(
    client: ClickHouseHttpClient,
    active: ActiveQueries,
    name: str,
    sql: str,
    *,
    row_parser: object,
) -> BenchStep:
    return _run_timed(client, active, name, sql, expects_rows=True, row_parser=row_parser)


def _run_timed(
    client: ClickHouseHttpClient,
    active: ActiveQueries,
    name: str,
    sql: str,
    *,
    expects_rows: bool,
    row_parser: object,
) -> BenchStep:
    query_id = active.new_id(name)
    started = time.perf_counter()
    print(f"START {name} query_id={query_id}", flush=True)
    try:
        text = client.execute(sql.strip().rstrip(";") + ("\nFORMAT TSV" if expects_rows else ""), query_id=query_id)
        seconds = time.perf_counter() - started
        step = BenchStep(name=name, status="ok", seconds=seconds, query_id=query_id)
        if expects_rows and callable(row_parser):
            parsed = row_parser(text)
            step.rows = int(parsed.get("rows", 0))
            step.tickers = int(parsed.get("tickers", 0))
            step.source_events = int(parsed.get("source_events", 0))
            step.detail.update({key: value for key, value in parsed.items() if key not in {"rows", "tickers", "source_events"}})
        enrich_query_log(client, step)
        return step
    except Exception as exc:  # noqa: BLE001
        step = BenchStep(name=name, status="error", seconds=time.perf_counter() - started, query_id=query_id, error=repr(exc))
        enrich_query_log(client, step)
        raise RuntimeError(f"{name} failed: {exc!r}") from exc
    finally:
        active.finish(query_id)


def enrich_query_log(client: ClickHouseHttpClient, step: BenchStep) -> None:
    try:
        client.execute("SYSTEM FLUSH LOGS")
        text = client.query_tsv(
            "SELECT query_duration_ms, memory_usage, read_rows, read_bytes, written_rows, written_bytes "
            "FROM system.query_log "
            f"WHERE query_id = {sql_string(step.query_id)} AND type = 'QueryFinish' "
            "ORDER BY event_time_microseconds DESC LIMIT 1"
        ).strip()
        if not text:
            return
        fields = text.split("\t")
        step.detail["query_duration_ms"] = int(fields[0])
        step.memory_usage = int(fields[1])
        step.read_rows = int(fields[2])
        step.read_bytes = int(fields[3])
        step.written_rows = int(fields[4])
        step.written_bytes = int(fields[5])
    except Exception as exc:  # noqa: BLE001
        step.detail["query_log_error"] = repr(exc)


def raw_event_count_sql(args: argparse.Namespace, window: dict[str, object]) -> str:
    table = events_table(args.database, args.events_table_base, str(window["source_date"]))
    return f"""
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    min(sip_timestamp_us) AS first_timestamp_us,
    max(sip_timestamp_us) AS last_timestamp_us
FROM {table}
PREWHERE event_date >= toDate({sql_string(str(window["event_date_start"]))})
    AND event_date <= toDate({sql_string(str(window["event_date_end"]))})
WHERE sip_timestamp_us >= toInt64({int(window["start_us"])})
  AND sip_timestamp_us < toInt64({int(window["end_us"])})
  AND ticker != ''
{settings_sql(args)}
"""


def direct_bar_count_sql(args: argparse.Namespace, window: dict[str, object], timeframe_us: int) -> str:
    return bar_count_wrapper_sql(bar_aggregation_sql(args, window, timeframe_us)) + "\n" + settings_sql(args)


def insert_base_bars_sql(args: argparse.Namespace, window: dict[str, object], temp_table: str, timeframe_us: int) -> str:
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(temp_table)}
{bar_aggregation_sql(args, window, timeframe_us)}
{settings_sql(args)}
"""


def bar_aggregation_sql(args: argparse.Namespace, window: dict[str, object], timeframe_us: int) -> str:
    table = events_table(args.database, args.events_table_base, str(window["source_date"]))
    return f"""
WITH
raw AS
(
    SELECT
        ticker,
        cityHash64(ticker) AS ticker_id,
        sip_timestamp_us,
        ordinal,
        bitAnd(event_meta, 1) AS event_type,
        toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TZ)})) AS source_date,
        toUInt64(dateDiff('microsecond', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TZ)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TZ)}))) AS local_session_us,
        toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) > 0, 10000.0, 100.0) AS primary_price,
        toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) > 0, 10000.0, 100.0) AS secondary_price,
        toFloat64(size_primary) AS size_primary,
        toFloat64(size_secondary) AS size_secondary
    FROM {table}
    PREWHERE event_date >= toDate({sql_string(str(window["event_date_start"]))})
        AND event_date <= toDate({sql_string(str(window["event_date_end"]))})
    WHERE sip_timestamp_us >= toInt64({int(window["start_us"])})
      AND sip_timestamp_us < toInt64({int(window["end_us"])})
      AND ticker != ''
),
expanded AS
(
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, 'trade' AS bar_family, primary_price AS price, size_primary AS size
    FROM raw
    WHERE event_type = 1 AND primary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, 'quote_bid' AS bar_family, secondary_price AS price, size_secondary AS size
    FROM raw
    WHERE event_type = 0 AND secondary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, 'quote_ask' AS bar_family, primary_price AS price, size_primary AS size
    FROM raw
    WHERE event_type = 0 AND primary_price > 0
)
SELECT
    source_date,
    ticker,
    ticker_id,
    bar_family,
    toInt64(intDiv(toInt64(local_session_us), toInt64({timeframe_us}))) AS bucket_index,
    toInt64(intDiv(toInt64(local_session_us), toInt64({timeframe_us})) * toInt64({timeframe_us})) AS bar_start_us,
    toInt64((intDiv(toInt64(local_session_us), toInt64({timeframe_us})) + 1) * toInt64({timeframe_us})) AS bar_end_us,
    toFloat32(argMin(price, tuple(sip_timestamp_us, ordinal))) AS open,
    toFloat32(argMax(price, tuple(sip_timestamp_us, ordinal))) AS close,
    toFloat32(max(price)) AS high,
    toFloat32(min(price)) AS low,
    toFloat64(sum(size)) AS size_sum,
    toUInt32(count()) AS event_count,
    toInt64(min(sip_timestamp_us)) AS first_event_timestamp_us,
    toInt64(max(sip_timestamp_us)) AS last_event_timestamp_us
FROM expanded
GROUP BY source_date, ticker, ticker_id, bar_family, bucket_index, bar_start_us, bar_end_us
"""


def bar_count_wrapper_sql(inner_sql: str) -> str:
    return f"""
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    sum(toUInt64(event_count)) AS source_events,
    min(bar_start_us) AS first_bar_start_us,
    max(bar_end_us) AS last_bar_end_us,
    sum(toFloat64(open) + toFloat64(close) + toFloat64(high) + toFloat64(low) + toFloat64(size_sum)) AS checksum
FROM
(
{inner_sql}
)
"""


def create_base_table_sql(args: argparse.Namespace, temp_table: str) -> str:
    engine = "Memory" if str(args.materialize_engine) == "Memory" else "MergeTree ORDER BY (source_date, bar_family, ticker, bucket_index)"
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(temp_table)}
(
    source_date Date,
    ticker LowCardinality(String),
    ticker_id UInt64,
    bar_family LowCardinality(String),
    bucket_index Int64,
    bar_start_us Int64,
    bar_end_us Int64,
    open Float32,
    close Float32,
    high Float32,
    low Float32,
    size_sum Float64,
    event_count UInt32,
    first_event_timestamp_us Int64,
    last_event_timestamp_us Int64
)
ENGINE = {engine}
"""


def table_count_sql(args: argparse.Namespace, temp_table: str) -> str:
    return f"""
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    sum(toUInt64(event_count)) AS source_events,
    min(bar_start_us) AS first_bar_start_us,
    max(bar_end_us) AS last_bar_end_us,
    sum(toFloat64(open) + toFloat64(close) + toFloat64(high) + toFloat64(low) + toFloat64(size_sum)) AS checksum
FROM {quote_ident(args.database)}.{quote_ident(temp_table)}
{settings_sql(args)}
"""


def aggregate_from_base_sql(args: argparse.Namespace, temp_table: str, base_us: int, target_us: int) -> str:
    if target_us % base_us != 0:
        raise ValueError(f"target timeframe {target_us}us must be a multiple of base timeframe {base_us}us")
    inner = f"""
SELECT
    source_date,
    ticker,
    ticker_id,
    bar_family,
    toInt64(intDiv(bar_start_us, toInt64({target_us}))) AS bucket_index,
    toInt64(intDiv(bar_start_us, toInt64({target_us})) * toInt64({target_us})) AS bar_start_us,
    toInt64((intDiv(bar_start_us, toInt64({target_us})) + 1) * toInt64({target_us})) AS bar_end_us,
    toFloat32(argMin(open, bar_start_us)) AS open,
    toFloat32(argMax(close, bar_end_us)) AS close,
    toFloat32(max(high)) AS high,
    toFloat32(min(low)) AS low,
    toFloat64(sum(size_sum)) AS size_sum,
    toUInt32(sum(event_count)) AS event_count,
    toInt64(min(first_event_timestamp_us)) AS first_event_timestamp_us,
    toInt64(max(last_event_timestamp_us)) AS last_event_timestamp_us
FROM {quote_ident(args.database)}.{quote_ident(temp_table)}
GROUP BY source_date, ticker, ticker_id, bar_family, bucket_index, bar_start_us, bar_end_us
"""
    return bar_count_wrapper_sql(inner) + "\n" + settings_sql(args)


def rank_from_base_sql(args: argparse.Namespace, temp_table: str) -> str:
    return f"""
WITH
trade_bars AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        close,
        size_sum,
        if(first_value(open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) > 0,
            close / first_value(open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) - 1.0,
            0.0) AS change_score
    FROM {quote_ident(args.database)}.{quote_ident(temp_table)}
    WHERE bar_family = 'trade'
),
ranks AS
(
    SELECT
        source_date,
        ticker,
        bucket_index,
        row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY change_score DESC, ticker ASC) - 1 AS gainers_rank,
        row_number() OVER (PARTITION BY source_date, bucket_index ORDER BY size_sum DESC, ticker ASC) - 1 AS volume_rank,
        change_score,
        size_sum
    FROM trade_bars
)
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    count() AS source_events,
    max(gainers_rank) AS max_gainers_rank,
    max(volume_rank) AS max_volume_rank,
    sum(toFloat64(change_score) + toFloat64(size_sum)) AS checksum
FROM ranks
{settings_sql(args)}
"""


def events_table(database: str, base: str, source_date: str) -> str:
    year = int(source_date[:4])
    return f"{quote_ident(database)}.{quote_ident(f'{base}_{year}')}"


def settings_sql(args: argparse.Namespace) -> str:
    settings: dict[str, int] = {
        "max_threads": int(args.max_threads),
        "max_memory_usage": parse_size_bytes(str(args.max_memory_usage)),
        "max_bytes_before_external_group_by": parse_size_bytes(str(args.max_bytes_before_external_group_by)),
        "max_bytes_before_external_sort": parse_size_bytes(str(args.max_bytes_before_external_sort)),
    }
    return "SETTINGS " + ", ".join(f"{key} = {value}" for key, value in settings.items())


def parse_count_row(text: str) -> dict[str, object]:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return {}
    fields = lines[0].split("\t")
    return {
        "rows": int(fields[0]),
        "tickers": int(fields[1]),
        "first_timestamp_us": parse_tsv_int(fields[2]) if len(fields) > 2 else 0,
        "last_timestamp_us": parse_tsv_int(fields[3]) if len(fields) > 3 else 0,
    }


def parse_bar_count_row(text: str) -> dict[str, object]:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return {}
    fields = lines[0].split("\t")
    return {
        "rows": int(fields[0]),
        "tickers": int(fields[1]),
        "source_events": int(fields[2]),
        "first_bar_start_us": parse_tsv_int(fields[3]) if len(fields) > 3 else 0,
        "last_bar_end_us": parse_tsv_int(fields[4]) if len(fields) > 4 else 0,
        "checksum": parse_tsv_float(fields[5]) if len(fields) > 5 else 0.0,
    }


def parse_rank_row(text: str) -> dict[str, object]:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return {}
    fields = lines[0].split("\t")
    return {
        "rows": int(fields[0]),
        "tickers": int(fields[1]),
        "source_events": int(fields[2]),
        "max_gainers_rank": parse_tsv_int(fields[3]) if len(fields) > 3 else 0,
        "max_volume_rank": parse_tsv_int(fields[4]) if len(fields) > 4 else 0,
        "checksum": parse_tsv_float(fields[5]) if len(fields) > 5 else 0.0,
    }


def print_step(step: BenchStep) -> None:
    memory = "-" if step.memory_usage is None else f"{step.memory_usage / (1024**3):.2f} GiB"
    read_rows = "-" if step.read_rows is None else f"{step.read_rows:,}"
    written_rows = "-" if step.written_rows is None else f"{step.written_rows:,}"
    print(
        f"DONE {step.name} status={step.status} sec={step.seconds:.1f} rows={step.rows:,} "
        f"tickers={step.tickers:,} source_events={step.source_events:,} "
        f"read_rows={read_rows} written_rows={written_rows} mem={memory}",
        flush=True,
    )


def duration_us(value: str) -> int:
    text = str(value).strip().lower()
    units = (
        ("ms", 1_000),
        ("us", 1),
        ("s", 1_000_000),
        ("m", 60_000_000),
        ("h", 3_600_000_000),
    )
    for suffix, scale in units:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    raise ValueError(f"Invalid duration: {value!r}")


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parse_tsv_int(value: str) -> int:
    if value in {"", "\\N", "NULL", "nan"}:
        return 0
    return int(float(value))


def parse_tsv_float(value: str) -> float:
    if value in {"", "\\N", "NULL", "nan"}:
        return 0.0
    return float(value)


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
