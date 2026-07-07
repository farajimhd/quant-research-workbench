from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files, quote_ident, sql_string
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.run_build_ticker_month_cache import _events_source_table, _settings_sql, current_rss_mib, query_polars
from research.mlops.rolling_loader.ticker_month_cache import month_window


DEFAULT_TARGET_ROWS = "1000000,10000000,100000000"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/rolling_ticker_month_cache/fetch_benchmarks")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark ClickHouse ticker+ordinal event fetches into Polars.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--month", default="2019-09", help="Month used to choose ticker ordinal ranges, YYYY-MM. Ignored when --start-date/--end-date are set.")
    parser.add_argument("--start-date", default="", help="Optional inclusive source_date start, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="Optional exclusive source_date end, YYYY-MM-DD.")
    parser.add_argument("--ticker", default="", help="Optional explicit ticker. If omitted, the script chooses a ticker with enough rows per target.")
    parser.add_argument("--targets", default=DEFAULT_TARGET_ROWS, help="Comma-separated row targets.")
    parser.add_argument("--columns-mode", choices=("full", "payload"), default="full", help="full matches builder event fetch with time features; payload reads only stored event payload columns.")
    parser.add_argument("--warmup-rows", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="160G")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--keep-frames", action="store_true", help="Keep fetched frames until process exit. Useful for RSS tests; disabled by default.")
    parser.add_argument("--dry-run", action="store_true", help="Only print selected ticker ranges; do not fetch event rows.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded_env = load_env_files(env_files, verbose=False)
    if loaded_env:
        print("Loaded .env files: " + ", ".join(str(path) for path in loaded_env), flush=True)
    if not args.clickhouse_url:
        args.clickhouse_url = default_clickhouse_url()
    if not args.user:
        args.user = default_clickhouse_user()
    if not args.password:
        args.password = default_clickhouse_password()

    targets = _parse_targets(args.targets)
    run_id = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    output_dir = Path(args.output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "ticker_ordinal_fetch_benchmark.jsonl"
    summary_path = output_dir / "summary.json"
    client_opts = _client_opts(args)
    config = _build_query_config(args)
    start_date, end_date, period_label = _date_window(args)
    candidates = _query_candidates(args=args, client_opts=client_opts, config=config, start_date=start_date, end_date=end_date)
    if not candidates:
        raise SystemExit(f"No ticker candidates found for {period_label}.")
    print(f"FETCH BENCHMARK {output_dir}", flush=True)
    print(
        json.dumps(
            {
                "period": period_label,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "targets": targets,
                "columns_mode": args.columns_mode,
                "ticker": args.ticker or "<auto>",
                "repeats": args.repeats,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    retained: list[Any] = []
    results: list[dict[str, Any]] = []
    if int(args.warmup_rows) > 0 and not args.dry_run:
        warm_plan = _select_plan(candidates, target_rows=int(args.warmup_rows), explicit_ticker=str(args.ticker))
        if warm_plan is not None:
            event = _run_fetch(args=args, client_opts=client_opts, config=config, plan=warm_plan, target_rows=int(args.warmup_rows), repeat_index=-1)
            event["kind"] = "warmup"
            _append_jsonl(report_path, event)
            print(json.dumps(_summary_event(event), sort_keys=True), flush=True)

    for target in targets:
        plan = _select_plan(candidates, target_rows=target, explicit_ticker=str(args.ticker))
        if plan is None:
            event = {"kind": "skip", "target_rows": int(target), "reason": "no ticker has enough rows in selected date window"}
            results.append(event)
            _append_jsonl(report_path, event)
            print(json.dumps(event, sort_keys=True), flush=True)
            continue
        plan_event = {"kind": "plan", "target_rows": int(target), **plan}
        results.append(plan_event)
        _append_jsonl(report_path, plan_event)
        print(json.dumps(plan_event, sort_keys=True), flush=True)
        if args.dry_run:
            continue
        for repeat_index in range(max(1, int(args.repeats))):
            event = _run_fetch(args=args, client_opts=client_opts, config=config, plan=plan, target_rows=target, repeat_index=repeat_index)
            results.append(event)
            _append_jsonl(report_path, event)
            print(json.dumps(_summary_event(event), sort_keys=True), flush=True)
            frame = event.pop("_frame", None)
            if args.keep_frames and frame is not None:
                retained.append(frame)
            else:
                del frame
    summary = _build_summary(results)
    summary["report_path"] = str(report_path)
    summary["output_dir"] = str(output_dir)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def _client_opts(args: argparse.Namespace) -> dict[str, str]:
    return {
        "clickhouse_url": str(args.clickhouse_url),
        "user": str(args.user),
        "password": str(args.password),
        "query_retries": "0",
        "query_retry_backoff_seconds": "0",
    }


def _parse_targets(text: str) -> list[int]:
    out = [int(item.strip().replace("_", "")) for item in str(text).split(",") if item.strip()]
    if not out:
        raise ValueError("At least one target row count is required.")
    return sorted(set(out))


def _build_query_config(args: argparse.Namespace) -> RollingMarketDataConfig:
    return RollingMarketDataConfig(
        database=str(args.database),
        events_table=str(args.events_table),
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
    )


def _date_window(args: argparse.Namespace) -> tuple[dt.date, dt.date, str]:
    if str(args.start_date).strip() or str(args.end_date).strip():
        if not str(args.start_date).strip() or not str(args.end_date).strip():
            raise ValueError("--start-date and --end-date must be supplied together.")
        start_date = dt.date.fromisoformat(str(args.start_date).strip())
        end_date = dt.date.fromisoformat(str(args.end_date).strip())
        if end_date <= start_date:
            raise ValueError("--end-date must be after --start-date.")
        return start_date, end_date, f"{start_date.isoformat()}..{end_date.isoformat()}"
    window = month_window(str(args.month))
    return window.first_date, window.next_month_date, str(args.month)


def _query_candidates(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, start_date: dt.date, end_date: dt.date) -> list[dict[str, Any]]:
    table = f"{quote_ident(config.database)}.{quote_ident(str(args.events_ticker_day_index_table))}"
    ticker_filter = ""
    if str(args.ticker).strip():
        ticker_filter = f"AND upper(ticker) = {sql_string(str(args.ticker).strip().upper())}"
    query = f"""
SELECT
    upper(ticker) AS ticker,
    min(first_ordinal) AS first_ordinal,
    max(last_ordinal) AS last_ordinal,
    sum(event_count) AS row_count,
    min(source_date) AS first_date,
    max(source_date) AS last_date
FROM
(
    SELECT
        upper(ticker) AS ticker,
        source_date,
        argMax(event_count, built_at) AS event_count,
        argMax(first_ordinal, built_at) AS first_ordinal,
        argMax(last_ordinal, built_at) AS last_ordinal
    FROM {table}
    WHERE source_date >= toDate({sql_string(start_date.isoformat())})
      AND source_date < toDate({sql_string(end_date.isoformat())})
      {ticker_filter}
    GROUP BY
        ticker,
        source_date
)
GROUP BY ticker
HAVING row_count > 0
ORDER BY row_count DESC
LIMIT 100
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    return [dict(row) for row in frame.iter_rows(named=True)]


def _select_plan(candidates: list[dict[str, Any]], *, target_rows: int, explicit_ticker: str) -> dict[str, Any] | None:
    ticker = explicit_ticker.strip().upper()
    rows = [row for row in candidates if (not ticker or str(row["ticker"]).upper() == ticker)]
    for row in rows:
        if int(row["row_count"]) >= int(target_rows):
            return {
                "ticker": str(row["ticker"]).upper(),
                "first_ordinal": int(row["first_ordinal"]),
                "last_ordinal": int(row["last_ordinal"]),
                "available_rows": int(row["row_count"]),
                "first_date": str(row["first_date"]),
                "last_date": str(row["last_date"]),
            }
    return None


def _run_fetch(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, plan: dict[str, Any], target_rows: int, repeat_index: int) -> dict[str, Any]:
    fetch_rows = int(target_rows)
    start_ordinal = int(plan["first_ordinal"])
    end_ordinal = start_ordinal + fetch_rows - 1
    table = _events_source_table(config, str(plan["first_date"]), str(plan["last_date"]))
    query = _event_fetch_sql(args=args, config=config, table=table, ticker=str(plan["ticker"]), start_ordinal=start_ordinal, end_ordinal=end_ordinal)
    rss_before = current_rss_mib()
    started = time.perf_counter()
    frame = query_polars(client_opts, query)
    seconds = time.perf_counter() - started
    rss_after = current_rss_mib()
    rows = int(frame.height)
    width = int(frame.width)
    estimated_bytes = int(frame.estimated_size()) if hasattr(frame, "estimated_size") else 0
    event = {
        "kind": "fetch",
        "ticker": str(plan["ticker"]),
        "target_rows": int(target_rows),
        "rows": rows,
        "width": width,
        "columns_mode": str(args.columns_mode),
        "start_ordinal": start_ordinal,
        "end_ordinal": end_ordinal,
        "repeat_index": int(repeat_index),
        "seconds": seconds,
        "rows_per_second": rows / max(seconds, 1e-9),
        "estimated_frame_mib": estimated_bytes / (1024 * 1024),
        "rss_before_mib": rss_before,
        "rss_after_mib": rss_after,
        "rss_delta_mib": rss_after - rss_before,
        "_frame": frame,
    }
    return event


def _event_fetch_sql(*, args: argparse.Namespace, config: Any, table: str, ticker: str, start_ordinal: int, end_ordinal: int) -> str:
    if str(args.columns_mode) == "payload":
        select_sql = """
    upper(ticker) AS ticker,
    ordinal,
    event_meta,
    sip_timestamp_us AS timestamp_us,
    price_primary_int,
    price_secondary_int,
    toFloat32(size_primary) AS size_primary,
    toFloat32(size_secondary) AS size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5
"""
    else:
        select_sql = """
    cityHash64(ticker) AS ticker_id,
    upper(ticker) AS ticker,
    ordinal,
    event_meta,
    sip_timestamp_us AS timestamp_us,
    price_primary_int,
    price_secondary_int,
    toFloat32(size_primary) AS size_primary,
    toFloat32(size_secondary) AS size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    toFloat32(sin(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_cos,
    toFloat32(toYear(ts_utc) - 2000 + (utc_doy - 1) / 366.0) AS years_since_2000,
    toDate(ts_local) AS local_date,
    toUInt64(dateDiff('microsecond', toStartOfDay(ts_local), ts_local)) AS local_session_us,
    toUInt32(local_second) AS session_second,
    toFloat32(greatest(0, least(57600, local_second - 14400)) / 57600.0) AS session_progress,
    toUInt8(local_second >= 34200 AND local_second < 57600) AS is_regular_hours,
    toUInt8(local_second >= 14400 AND local_second < 34200) AS is_premarket,
    toUInt8(local_second >= 57600 AND local_second < 72000) AS is_afterhours
"""
    return f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, 'America/New_York') AS ts_local,
    dateDiff('second', toStartOfDay(ts_utc), ts_utc) AS utc_second,
    toDayOfWeek(ts_utc) AS utc_dow,
    toDayOfYear(ts_utc) AS utc_doy,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
{select_sql}
FROM {table}
PREWHERE ticker = {sql_string(ticker)}
  AND ordinal >= {int(start_ordinal)}
  AND ordinal <= {int(end_ordinal)}
ORDER BY ticker, ordinal
{_settings_sql(config)}
"""


def _append_jsonl(path: Path, event: Mapping[str, Any]) -> None:
    clean = {key: value for key, value in event.items() if key != "_frame"}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(clean, sort_keys=True) + "\n")


def _summary_event(event: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("kind", "ticker", "target_rows", "rows", "seconds", "rows_per_second", "estimated_frame_mib", "rss_delta_mib", "repeat_index")
    return {key: event[key] for key in keys if key in event}


def _build_summary(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    fetches = [event for event in events if event.get("kind") == "fetch"]
    by_target: dict[str, dict[str, Any]] = {}
    for target in sorted({int(event["target_rows"]) for event in fetches}):
        rows = [event for event in fetches if int(event["target_rows"]) == target]
        if not rows:
            continue
        by_target[str(target)] = {
            "runs": len(rows),
            "ticker": rows[-1].get("ticker"),
            "rows": int(rows[-1].get("rows") or 0),
            "seconds_min": min(float(row["seconds"]) for row in rows),
            "seconds_avg": sum(float(row["seconds"]) for row in rows) / len(rows),
            "rows_per_second_avg": sum(float(row["rows_per_second"]) for row in rows) / len(rows),
            "frame_mib_avg": sum(float(row.get("estimated_frame_mib") or 0.0) for row in rows) / len(rows),
        }
    return {"targets": by_target, "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat()}


if __name__ == "__main__":
    raise SystemExit(main())
