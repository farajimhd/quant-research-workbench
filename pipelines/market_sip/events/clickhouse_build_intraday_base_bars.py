from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_unified_events import events_table_for_year, events_table_uses_year_suffix  # noqa: E402
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    enrich_profile_from_query_log,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_INTRADAY_BASE_BARS_TABLE = "intraday_base_bars_by_time_ticker"
DEFAULT_STATUS_TABLE = "intraday_base_bars_build_status"
DEFAULT_RESOLUTIONS = "100ms,1s,5s,30s,60s"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/clickhouse_sip_ingest/intraday_base_bars")
SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60
BUILD_VERSION_PREFIX = "v1_clickhouse_daily_intraday_base_bars"


@dataclass(frozen=True, slots=True)
class DayBuildResult:
    local_date: str
    status: str
    row_count: int = 0
    event_count: int = 0
    duplicate_keys: int = 0
    seconds: float = 0.0
    message: str = ""


class IntradayBarBuildReporter:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        dates: list[dt.date],
        resolutions_us: tuple[int, ...],
        report_path: Path,
        build_version: str,
    ) -> None:
        self.args = args
        self.dates = dates
        self.resolutions_us = resolutions_us
        self.report_path = report_path
        self.build_version = build_version
        self.started_at = time.perf_counter()
        self.completed_days = 0
        self.built_days = 0
        self.skipped_days = 0
        self.adopted_days = 0
        self.failed_days = 0
        self.total_rows = 0
        self.total_events = 0
        self.current_chunk = "-"
        self.current_phase = "starting"
        self.current_query = "-"
        self.current_query_started_at = 0.0
        self.last_error = ""
        self.recent_results: deque[DayBuildResult] = deque(maxlen=12)
        self.recent_queries: deque[QueryProfile] = deque(maxlen=12)
        self.messages: deque[str] = deque(maxlen=max(4, int(args.progress_log_lines)))
        self._console = None
        self._live = None
        self._rich_enabled = False

    def __enter__(self) -> "IntradayBarBuildReporter":
        if str(self.args.progress_layout) != "text":
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(
                    self._render(),
                    console=self._console,
                    refresh_per_second=max(1.0, float(self.args.progress_refresh_per_second)),
                    transient=False,
                    auto_refresh=True,
                )
                self._live.start()
                self._rich_enabled = True
            except Exception as exc:  # noqa: BLE001
                self._rich_enabled = False
                print(f"Rich progress unavailable; falling back to text progress: {exc!r}", flush=True)
        self.message("intraday base-bar build started")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc is not None:
            self.last_error = repr(exc)
            self.current_phase = "error"
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()

    def message(self, text: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} {text}"
        self.messages.append(line)
        if self._live is not None:
            self._live.update(self._render())
        else:
            print(line, flush=True)

    def chunk_start(self, label: str, phase: str = "chunk") -> None:
        self.current_chunk = label
        self.current_phase = phase
        self.message(f"chunk start {label}")

    def query_start(self, label: str) -> None:
        self.current_query = label
        self.current_query_started_at = time.perf_counter()
        self.current_phase = label
        if self._live is not None:
            self._live.update(self._render())

    def query_done(self, profile: QueryProfile) -> None:
        self.recent_queries.append(profile)
        self.current_query = "-"
        self.current_query_started_at = 0.0
        self.message(f"query done {profile.label} seconds={profile.wall_seconds:.1f}")

    def day_result(self, result: DayBuildResult) -> None:
        self.completed_days += 1
        self.total_rows += int(result.row_count)
        self.total_events += int(result.event_count)
        if result.status == "built":
            self.built_days += 1
        elif result.status == "adopted":
            self.adopted_days += 1
        elif result.status in {"complete", "dry_run"}:
            self.skipped_days += 1
        elif result.status == "failed":
            self.failed_days += 1
        self.recent_results.append(result)
        if self._live is not None:
            self._live.update(self._render())

    def interrupted(self) -> None:
        self.current_phase = "interrupted"
        self.message("INTERRUPT received; cancelling active ClickHouse query if one is running")

    def finish(self) -> None:
        self.current_phase = "done"
        self.message("intraday base-bar build finished")

    def _render(self) -> object:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
        from rich.table import Table
        from rich.text import Text

        elapsed = max(0.001, time.perf_counter() - self.started_at)
        total_days = len(self.dates)
        remaining_days = max(0, total_days - self.completed_days)
        rate = self.completed_days / elapsed
        eta = remaining_days / rate if rate > 0 else 0.0
        query_elapsed = time.perf_counter() - self.current_query_started_at if self.current_query_started_at else 0.0
        title = f"Intraday Base Bars  {self.current_phase.upper()}"
        summary = Table(box=box.SIMPLE, expand=True, show_edge=False)
        summary.add_column("Metric", style="cyan", no_wrap=True)
        summary.add_column("Value", no_wrap=True)
        summary.add_column("Detail")
        summary.add_row("Period", f"{self.dates[0]} -> {self.dates[-1] + dt.timedelta(days=1)}", f"days={total_days:,}")
        summary.add_row("Progress", f"{self.completed_days:,}/{total_days:,}", f"built={self.built_days:,} adopted={self.adopted_days:,} skipped={self.skipped_days:,}")
        summary.add_row("Rows", f"{self.total_rows:,}", f"source_events={self.total_events:,}")
        summary.add_row("Speed", f"{rate * 60.0:.2f} days/min", f"elapsed={elapsed / 60.0:.1f}m eta={eta / 60.0:.1f}m")
        summary.add_row("Current", self.current_chunk, f"query={self.current_query} query_elapsed={query_elapsed:.1f}s")
        summary.add_row("Output", str(self.report_path), "")
        if self.last_error:
            summary.add_row("Last error", self.last_error, "")
        progress = Progress(
            TextColumn("[cyan]Days"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            expand=True,
        )
        progress.add_task("days", total=total_days, completed=self.completed_days)

        queries = Table(title="Recent Query Timings", box=box.ROUNDED, expand=True, header_style="bold cyan")
        queries.add_column("Label")
        queries.add_column("Sec", justify="right")
        queries.add_column("Read Rows", justify="right")
        queries.add_column("Written Rows", justify="right")
        queries.add_column("Memory", justify="right")
        for profile in list(self.recent_queries)[-8:]:
            queries.add_row(
                profile.label,
                f"{profile.wall_seconds:.1f}",
                _fmt_int(profile.read_rows),
                _fmt_int(profile.written_rows),
                _fmt_bytes(profile.memory_usage_bytes),
            )

        results = Table(title="Recent Days", box=box.ROUNDED, expand=True, header_style="bold cyan")
        results.add_column("Date")
        results.add_column("Status")
        results.add_column("Rows", justify="right")
        results.add_column("Events", justify="right")
        results.add_column("Dup Keys", justify="right")
        results.add_column("Sec", justify="right")
        for result in list(self.recent_results)[-10:]:
            style = "green" if result.status in {"built", "adopted", "complete"} and result.duplicate_keys == 0 else "yellow"
            results.add_row(
                result.local_date,
                f"[{style}]{result.status}[/{style}]",
                f"{result.row_count:,}",
                f"{result.event_count:,}",
                f"{result.duplicate_keys:,}",
                f"{result.seconds:.1f}",
            )

        messages = Table(title="Messages", box=box.ROUNDED, expand=True, show_header=False)
        messages.add_column("Message")
        for line in list(self.messages)[-int(self.args.progress_log_lines) :]:
            messages.add_row(Text(line, overflow="fold"))
        header = Panel(summary, title=title, border_style="red" if self.last_error else "green", box=box.ROUNDED, padding=(0, 1))
        return Group(header, progress, queries, results, messages)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build reusable intraday base bars in ClickHouse from compact SIP events.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--intraday-base-bars-table", default=DEFAULT_INTRADAY_BASE_BARS_TABLE)
    parser.add_argument("--status-table", default=DEFAULT_STATUS_TABLE)
    parser.add_argument("--start-date", default="", help="Inclusive New York local session date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="Exclusive New York local session date, YYYY-MM-DD.")
    parser.add_argument("--date", default="", help="Build one New York local session date, YYYY-MM-DD.")
    parser.add_argument("--resolutions", default=DEFAULT_RESOLUTIONS, help="Comma-separated bar grids, e.g. 100ms,1s,5s,30s,60s.")
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker filter for smoke tests or repair.")
    parser.add_argument("--chunk-days", type=int, default=1, help="Number of local dates per insert query. Default keeps repair/audit granular.")
    parser.add_argument("--replace-existing", action="store_true", help="Synchronously delete existing bars for the day/chunk before inserting.")
    parser.add_argument("--adopt-existing-complete", action="store_true", help="Mark existing day rows complete if audit passes and no status row exists.")
    parser.add_argument("--no-audit", action="store_true", help="Skip post-insert duplicate-key audit.")
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--max-memory-usage", default="300G")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=2.0)
    parser.add_argument("--progress-log-lines", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    loaded_env = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args(argv)
    local_dates = _date_range(args)
    resolutions_us = parse_resolutions(args.resolutions)
    build_version = _build_version(resolutions_us)
    run_id = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    report_dir = Path(args.output_root) / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "intraday_base_bars_build.jsonl"
    summary_path = report_dir / "summary.json"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    results: list[DayBuildResult] = []
    reporter = IntradayBarBuildReporter(args, dates=local_dates, resolutions_us=resolutions_us, report_path=report_path, build_version=build_version)
    try:
        with reporter:
            reporter.message(f"database={args.database} events_table={args.events_table} bars_table={args.intraday_base_bars_table}")
            reporter.message(f"resolutions={','.join(format_resolution(value) for value in resolutions_us)} storage_policy={args.storage_policy or '<default>'}")
            reporter.message(f"loaded_env_files={[str(path) for path in loaded_env]}")
            reporter.message(f"secret_status={secret_status(['CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD'])}")
            if not args.dry_run:
                execute_profiled(client, "intraday_base_bars_create", create_intraday_base_bars_table_sql(args), "", reporter)
                execute_profiled(client, "intraday_base_bars_status_create", create_status_table_sql(args), "", reporter)
            for chunk in _chunks(local_dates, max(1, int(args.chunk_days))):
                chunk_results = build_date_chunk(
                    client=client,
                    args=args,
                    dates=chunk,
                    resolutions_us=resolutions_us,
                    build_version=build_version,
                    report_path=report_path,
                    reporter=reporter,
                )
                results.extend(chunk_results)
            reporter.finish()
    except KeyboardInterrupt:
        reporter.interrupted()
        append_jsonl(report_path, {"event": "interrupted", "elapsed_seconds": time.perf_counter() - started})
        return 130
    summary = summarize_results(results=results, started_at=started, args=args, build_version=build_version)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def build_date_chunk(
    *,
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    dates: list[dt.date],
    resolutions_us: tuple[int, ...],
    build_version: str,
    report_path: Path,
    reporter: IntradayBarBuildReporter,
) -> list[DayBuildResult]:
    first = dates[0]
    last_exclusive = dates[-1] + dt.timedelta(days=1)
    label = f"{first.isoformat()}_{last_exclusive.isoformat()}"
    reporter.chunk_start(label)
    started = time.perf_counter()
    if args.dry_run:
        sql = insert_intraday_base_bars_sql(args=args, dates=dates, resolutions_us=resolutions_us)
        append_jsonl(report_path, {"event": "dry_run_sql", "chunk": label, "sql": sql})
        reporter.message(f"dry-run SQL generated for {label}; written to report JSONL")
        results = [DayBuildResult(local_date=day.isoformat(), status="dry_run") for day in dates]
        for result in results:
            reporter.day_result(result)
        return results
    existing = query_existing_day_state(client=client, args=args, dates=dates, build_version=build_version, reporter=reporter)
    rows_existing = {day: int(state.get("rows", 0)) for day, state in existing.items()}
    complete_days = {day for day, state in existing.items() if bool(state.get("complete"))}
    pending_dates = [day for day in dates if day not in complete_days]
    blocked = [day for day in pending_dates if rows_existing.get(day, 0) > 0 and not args.replace_existing and not args.adopt_existing_complete]
    if blocked:
        blocked_text = ", ".join(day.isoformat() for day in blocked)
        raise RuntimeError(f"Existing intraday bar rows found without complete status for {blocked_text}; use --replace-existing or --adopt-existing-complete.")
    adopted: list[DayBuildResult] = []
    if args.adopt_existing_complete:
        for day in list(pending_dates):
            if rows_existing.get(day, 0) <= 0:
                continue
            audit = audit_day(client=client, args=args, day=day, audit=not args.no_audit, reporter=reporter)
            if audit["duplicate_keys"] != 0:
                raise RuntimeError(f"Cannot adopt {day.isoformat()}: duplicate_keys={audit['duplicate_keys']}")
            insert_status(client=client, args=args, day=day, row_count=audit["row_count"], build_version=build_version, reporter=reporter)
            result = DayBuildResult(local_date=day.isoformat(), status="adopted", row_count=audit["row_count"], duplicate_keys=audit["duplicate_keys"])
            append_jsonl(report_path, asdict(result))
            reporter.day_result(result)
            adopted.append(result)
            pending_dates.remove(day)
    if not pending_dates:
        skipped = [DayBuildResult(local_date=day.isoformat(), status="complete", row_count=rows_existing.get(day, 0)) for day in complete_days]
        for result in skipped:
            append_jsonl(report_path, asdict(result))
            reporter.day_result(result)
        reporter.message(f"chunk skip {label} complete={len(complete_days):,} adopted={len(adopted):,}")
        return [*adopted, *skipped]
    if args.replace_existing:
        execute_profiled(client, f"intraday_base_bars_delete_{label}", delete_intraday_base_bars_sql(args=args, dates=pending_dates), query_settings(args, extra={"mutations_sync": 2}), reporter)
    event_count = query_source_event_count(client=client, args=args, dates=pending_dates, reporter=reporter)
    execute_profiled(client, f"intraday_base_bars_insert_{label}", insert_intraday_base_bars_sql(args=args, dates=pending_dates, resolutions_us=resolutions_us), query_settings(args), reporter)
    results: list[DayBuildResult] = list(adopted)
    for day in pending_dates:
        audit = audit_day(client=client, args=args, day=day, audit=not args.no_audit, reporter=reporter)
        if audit["duplicate_keys"] != 0:
            raise RuntimeError(f"Intraday bar audit failed for {day.isoformat()}: duplicate_keys={audit['duplicate_keys']}")
        insert_status(client=client, args=args, day=day, row_count=audit["row_count"], build_version=build_version, reporter=reporter)
        result = DayBuildResult(
            local_date=day.isoformat(),
            status="built",
            row_count=audit["row_count"],
            event_count=event_count.get(day.isoformat(), 0),
            duplicate_keys=audit["duplicate_keys"],
            seconds=time.perf_counter() - started,
        )
        append_jsonl(report_path, asdict(result))
        reporter.day_result(result)
        results.append(result)
    reporter.message(f"chunk done {label} built={len(pending_dates):,} adopted={len(adopted):,} seconds={time.perf_counter() - started:.1f}")
    return results


def create_intraday_base_bars_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}
(
    local_date Date,
    ticker LowCardinality(String),
    label_resolution_us UInt64,
    bucket_index UInt64,
    bar_family LowCardinality(String),
    open Float32,
    close Float32,
    high Float32,
    low Float32,
    size_sum Float64,
    size_open Float64,
    size_close Float64,
    size_high Float64,
    size_low Float64,
    event_count UInt64,
    first_event_timestamp_us UInt64,
    last_event_timestamp_us UInt64,
    built_at DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(local_date)
ORDER BY (ticker, local_date, label_resolution_us, bucket_index, bar_family)
{mergetree_settings_sql(str(args.storage_policy or ""))}
"""


def create_status_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.status_table)}
(
    artifact_name LowCardinality(String),
    local_date Date,
    status LowCardinality(String),
    row_count UInt64,
    build_version LowCardinality(String),
    built_at DateTime DEFAULT now(),
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (artifact_name, local_date)
{mergetree_settings_sql(str(args.storage_policy or ""))}
"""


def insert_intraday_base_bars_sql(*, args: argparse.Namespace, dates: list[dt.date], resolutions_us: tuple[int, ...]) -> str:
    target_table = f"{quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}"
    source_table = event_source_table(args=args, first_date=min(dates), last_exclusive=max(dates) + dt.timedelta(days=1))
    local_date_filter = ", ".join(f"toDate({sql_string(day.isoformat())})" for day in dates)
    resolutions = ", ".join(f"toUInt64({value})" for value in resolutions_us)
    ticker_filter = ticker_filter_sql(args)
    first_event_date = min(dates)
    last_event_date = max(dates) + dt.timedelta(days=1)
    return f"""
INSERT INTO {target_table}
(
    local_date,
    ticker,
    label_resolution_us,
    bucket_index,
    bar_family,
    open,
    close,
    high,
    low,
    size_sum,
    size_open,
    size_close,
    size_high,
    size_low,
    event_count,
    first_event_timestamp_us,
    last_event_timestamp_us,
    built_at
)
SELECT
    local_date,
    ticker,
    label_resolution_us,
    intDiv(toUInt64(local_session_us), label_resolution_us) AS bucket_index,
    bar_family,
    toFloat32(argMin(price, tuple(sip_timestamp_us, ordinal))) AS open,
    toFloat32(argMax(price, tuple(sip_timestamp_us, ordinal))) AS close,
    toFloat32(max(price)) AS high,
    toFloat32(min(price)) AS low,
    sum(size) AS size_sum,
    argMin(size, tuple(sip_timestamp_us, ordinal)) AS size_open,
    argMax(size, tuple(sip_timestamp_us, ordinal)) AS size_close,
    max(size) AS size_high,
    min(size) AS size_low,
    count() AS event_count,
    min(toUInt64(sip_timestamp_us)) AS first_event_timestamp_us,
    max(toUInt64(sip_timestamp_us)) AS last_event_timestamp_us,
    now() AS built_at
FROM
(
    SELECT
        upper(event_ticker) AS ticker,
        local_date,
        local_session_us,
        sip_timestamp_us,
        ordinal,
        tupleElement(family_tuple, 1) AS bar_family,
        tupleElement(family_tuple, 2) AS price,
        tupleElement(family_tuple, 3) AS size,
        arrayJoin([{resolutions}]) AS label_resolution_us
    FROM
    (
        SELECT
            ticker AS event_ticker,
            ordinal,
            sip_timestamp_us,
            bitAnd(event_meta, 1) AS event_type,
            toFloat32(if(price_primary_int > 0, price_primary_int / if(bitAnd(event_meta, 2) = 2, 10000.0, 100.0), 0.0)) AS price_primary,
            toFloat32(if(price_secondary_int > 0, price_secondary_int / if(bitAnd(event_meta, 4) = 4, 10000.0, 100.0), 0.0)) AS price_secondary,
            toFloat64(size_primary) AS size_primary,
            toFloat64(size_secondary) AS size_secondary,
            toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
            toDate(ts_local) AS local_date,
            dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second,
            dateDiff('microsecond', toStartOfDay(ts_local), ts_local) AS local_session_us
        FROM {source_table}
        PREWHERE event_date >= toDate({sql_string(first_event_date.isoformat())})
          AND event_date <= toDate({sql_string(last_event_date.isoformat())})
          {ticker_filter}
        WHERE local_date IN ({local_date_filter})
          AND local_second >= {SESSION_START_SECOND}
          AND local_second < {SESSION_END_SECOND}
    )
    ARRAY JOIN arrayFilter(
        x -> tupleElement(x, 2) > 0 AND tupleElement(x, 3) > 0,
        if(
            event_type = 1,
            [tuple('trade', price_primary, size_primary)],
            [tuple('quote_bid', price_secondary, size_secondary), tuple('quote_ask', price_primary, size_primary)]
        )
    ) AS family_tuple
)
GROUP BY
    local_date,
    ticker,
    label_resolution_us,
    bucket_index,
    bar_family
"""


def delete_intraday_base_bars_sql(*, args: argparse.Namespace, dates: list[dt.date]) -> str:
    local_date_filter = ", ".join(f"toDate({sql_string(day.isoformat())})" for day in dates)
    ticker_filter = ticker_filter_sql(args, column="ticker")
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}
DELETE WHERE local_date IN ({local_date_filter})
  {ticker_filter}
"""


def query_existing_day_state(
    *,
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    dates: list[dt.date],
    build_version: str,
    reporter: IntradayBarBuildReporter,
) -> dict[dt.date, dict[str, object]]:
    local_date_filter = ", ".join(f"toDate({sql_string(day.isoformat())})" for day in dates)
    ticker_filter = ticker_filter_sql(args, column="ticker")
    rows_sql = f"""
SELECT
    local_date,
    count() AS rows
FROM {quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}
WHERE local_date IN ({local_date_filter})
  {ticker_filter}
GROUP BY local_date
FORMAT TSV
"""
    status_sql = f"""
SELECT
    local_date,
    argMax(status, updated_at) AS status,
    argMax(build_version, updated_at) AS build_version
FROM {quote_ident(args.database)}.{quote_ident(args.status_table)}
WHERE artifact_name = {sql_string(artifact_name(args))}
  AND local_date IN ({local_date_filter})
GROUP BY local_date
FORMAT TSV
"""
    state: dict[dt.date, dict[str, object]] = {day: {"rows": 0, "complete": False} for day in dates}
    for line in execute_query_tsv(client, f"intraday_base_bars_existing_rows_{dates[0]}_{dates[-1]}", rows_sql, reporter).splitlines():
        if not line.strip():
            continue
        day_text, rows_text = line.split("\t")[:2]
        state[dt.date.fromisoformat(day_text)]["rows"] = int(rows_text)
    for line in execute_query_tsv(client, f"intraday_base_bars_existing_status_{dates[0]}_{dates[-1]}", status_sql, reporter).splitlines():
        if not line.strip():
            continue
        day_text, status, version = (line.split("\t") + ["", ""])[:3]
        state[dt.date.fromisoformat(day_text)]["complete"] = status == "complete" and version == build_version
        state[dt.date.fromisoformat(day_text)]["status"] = status
        state[dt.date.fromisoformat(day_text)]["build_version"] = version
    return state


def query_source_event_count(*, client: ClickHouseHttpClient, args: argparse.Namespace, dates: list[dt.date], reporter: IntradayBarBuildReporter) -> dict[str, int]:
    source_table = event_source_table(args=args, first_date=min(dates), last_exclusive=max(dates) + dt.timedelta(days=1))
    local_date_filter = ", ".join(f"toDate({sql_string(day.isoformat())})" for day in dates)
    ticker_filter = ticker_filter_sql(args)
    sql = f"""
WITH
    toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    toDate(ts_local) AS local_date,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
    local_date,
    count() AS rows
FROM {source_table}
PREWHERE event_date >= toDate({sql_string(min(dates).isoformat())})
  AND event_date <= toDate({sql_string((max(dates) + dt.timedelta(days=1)).isoformat())})
  {ticker_filter}
WHERE local_date IN ({local_date_filter})
  AND local_second >= {SESSION_START_SECOND}
  AND local_second < {SESSION_END_SECOND}
GROUP BY local_date
FORMAT TSV
"""
    out = {day.isoformat(): 0 for day in dates}
    for line in execute_query_tsv(client, f"intraday_base_bars_source_events_{dates[0]}_{dates[-1]}", sql, reporter).splitlines():
        if not line.strip():
            continue
        day_text, rows_text = line.split("\t")[:2]
        out[day_text] = int(rows_text)
    return out


def audit_day(*, client: ClickHouseHttpClient, args: argparse.Namespace, day: dt.date, audit: bool, reporter: IntradayBarBuildReporter) -> dict[str, int]:
    ticker_filter = ticker_filter_sql(args, column="ticker")
    if audit:
        sql = f"""
SELECT
    count() AS rows,
    rows - uniqExact(tuple(ticker, local_date, label_resolution_us, bucket_index, bar_family)) AS duplicate_keys
FROM {quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}
WHERE local_date = toDate({sql_string(day.isoformat())})
  {ticker_filter}
FORMAT TSV
"""
    else:
        sql = f"""
SELECT
    count() AS rows,
    0 AS duplicate_keys
FROM {quote_ident(args.database)}.{quote_ident(args.intraday_base_bars_table)}
WHERE local_date = toDate({sql_string(day.isoformat())})
  {ticker_filter}
FORMAT TSV
"""
    first = next((line for line in execute_query_tsv(client, f"intraday_base_bars_audit_{day.isoformat()}", sql, reporter).splitlines() if line.strip()), "0\t0")
    rows, duplicate_keys = (first.split("\t") + ["0"])[:2]
    return {"row_count": int(rows), "duplicate_keys": int(duplicate_keys)}


def insert_status(
    *,
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    day: dt.date,
    row_count: int,
    build_version: str,
    reporter: IntradayBarBuildReporter,
) -> None:
    sql = f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.status_table)}
(
    artifact_name,
    local_date,
    status,
    row_count,
    build_version,
    built_at,
    updated_at
)
SELECT
    {sql_string(artifact_name(args))} AS artifact_name,
    toDate({sql_string(day.isoformat())}) AS local_date,
    'complete' AS status,
    toUInt64({int(row_count)}) AS row_count,
    {sql_string(build_version)} AS build_version,
    now() AS built_at,
    now() AS updated_at
"""
    execute_profiled(client, f"intraday_base_bars_status_{day.isoformat()}", sql, query_settings(args), reporter)


def execute_query_tsv(client: ClickHouseHttpClient, label: str, sql: str, reporter: IntradayBarBuildReporter) -> str:
    query = sql.strip().rstrip(";").rstrip()
    if not re.search(r"\bFORMAT\s+[A-Za-z0-9_]+\s*$", query, flags=re.IGNORECASE):
        query += "\nFORMAT TSV"
    profile, text = execute_profiled(client, label, query, "", reporter, return_text=True)
    reporter.query_done(profile)
    return text


def execute_profiled(
    client: ClickHouseHttpClient,
    label: str,
    sql: str,
    settings: str,
    reporter: IntradayBarBuildReporter,
    *,
    return_text: bool = False,
) -> QueryProfile | tuple[QueryProfile, str]:
    query_id = f"intraday_base_bars_{_query_id_label(label)}_{uuid.uuid4().hex}"
    full_sql = sql.rstrip(";") + settings
    reporter.query_start(label)
    started = time.perf_counter()
    exception = ""
    text = ""
    try:
        text = client.execute(full_sql, query_id=query_id)
    except KeyboardInterrupt:
        reporter.interrupted()
        try:
            client.execute(f"KILL QUERY WHERE query_id = {sql_string(query_id)} ASYNC")
        except Exception as kill_exc:  # noqa: BLE001
            reporter.message(f"warning: failed to cancel query_id={query_id}: {kill_exc!r}")
        raise
    except Exception as exc:  # noqa: BLE001
        exception = repr(exc)
        reporter.last_error = exception
        reporter.message(f"query failed {label}: {exception}")
    profile = QueryProfile(label=label, query_id=query_id, wall_seconds=time.perf_counter() - started, exception=exception)
    enrich_profile_from_query_log(client, profile)
    if exception:
        raise RuntimeError(f"{label} failed: {exception}")
    if return_text:
        return profile, text
    reporter.query_done(profile)
    return profile


def _query_id_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value)[:80].strip("_") or "query"


def event_source_table(*, args: argparse.Namespace, first_date: dt.date, last_exclusive: dt.date) -> str:
    base_table = str(args.events_table)
    last_event_date = last_exclusive
    if not events_table_uses_year_suffix(base_table):
        return f"{quote_ident(args.database)}.{quote_ident(base_table)}"
    tables = [events_table_for_year(base_table, year) for year in range(first_date.year, last_event_date.year + 1)]
    if len(tables) == 1:
        return f"{quote_ident(args.database)}.{quote_ident(tables[0])}"
    pattern = "^(" + "|".join(re.escape(table) for table in tables) + ")$"
    return f"merge({sql_string(args.database)}, {sql_string(pattern)})"


def parse_resolutions(text: str) -> tuple[int, ...]:
    values = []
    for raw in str(text).split(","):
        item = raw.strip().lower().replace("_", "")
        if not item:
            continue
        if item.endswith("ms"):
            value = int(float(item[:-2]) * 1_000)
        elif item.endswith("s"):
            value = int(float(item[:-1]) * 1_000_000)
        elif item.endswith("m"):
            value = int(float(item[:-1]) * 60_000_000)
        elif item.endswith("h"):
            value = int(float(item[:-1]) * 3_600_000_000)
        else:
            value = int(item)
        if value <= 0:
            raise ValueError(f"Invalid resolution: {raw!r}")
        values.append(value)
    if not values:
        raise ValueError("At least one resolution is required.")
    return tuple(sorted(set(values)))


def format_resolution(value: int) -> str:
    if value % 3_600_000_000 == 0:
        return f"{value // 3_600_000_000}h"
    if value % 60_000_000 == 0:
        return f"{value // 60_000_000}m"
    if value % 1_000_000 == 0:
        return f"{value // 1_000_000}s"
    if value % 1_000 == 0:
        return f"{value // 1_000}ms"
    return str(value)


def _date_range(args: argparse.Namespace) -> list[dt.date]:
    if str(args.date).strip():
        day = dt.date.fromisoformat(str(args.date).strip())
        return [day]
    if not str(args.start_date).strip() or not str(args.end_date).strip():
        raise ValueError("Provide --date or both --start-date and --end-date.")
    start = dt.date.fromisoformat(str(args.start_date).strip())
    end = dt.date.fromisoformat(str(args.end_date).strip())
    if end <= start:
        raise ValueError("--end-date must be after --start-date. End date is exclusive.")
    out = []
    current = start
    while current < end:
        out.append(current)
        current += dt.timedelta(days=1)
    return out


def _chunks(items: list[dt.date], size: int) -> Iterable[list[dt.date]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def ticker_filter_sql(args: argparse.Namespace, *, column: str = "ticker") -> str:
    tickers = sorted({item.strip().upper() for item in str(args.tickers).split(",") if item.strip()})
    if not tickers:
        return ""
    return f"AND {column} IN (" + ", ".join(sql_string(ticker) for ticker in tickers) + ")"


def artifact_name(args: argparse.Namespace) -> str:
    tickers = sorted({item.strip().upper() for item in str(args.tickers).split(",") if item.strip()})
    if not tickers:
        return str(args.intraday_base_bars_table)
    return str(args.intraday_base_bars_table) + ":tickers=" + ",".join(tickers)


def query_settings(args: argparse.Namespace, *, extra: dict[str, int | str] | None = None) -> str:
    settings: dict[str, int | str] = {}
    if int(args.max_threads) > 0:
        settings["max_threads"] = int(args.max_threads)
    if str(args.max_memory_usage) != "0":
        settings["max_memory_usage"] = parse_size_bytes(str(args.max_memory_usage))
    settings.update(extra or {})
    if not settings:
        return ""
    parts = []
    for key, value in settings.items():
        parts.append(f"{key} = {sql_string(value) if isinstance(value, str) else value}")
    return "\nSETTINGS " + ", ".join(parts)


def _build_version(resolutions_us: tuple[int, ...]) -> str:
    return BUILD_VERSION_PREFIX + "_" + "_".join(str(value) for value in resolutions_us)


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}"


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    raw = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if raw < 1024.0 or suffix == "TiB":
            return f"{raw:.1f} {suffix}" if suffix != "B" else f"{int(raw)} B"
        raw /= 1024.0


def summarize_results(*, results: list[DayBuildResult], started_at: float, args: argparse.Namespace, build_version: str) -> dict[str, object]:
    by_status: dict[str, int] = {}
    for result in results:
        by_status[result.status] = by_status.get(result.status, 0) + 1
    return {
        "database": args.database,
        "events_table": args.events_table,
        "intraday_base_bars_table": args.intraday_base_bars_table,
        "status_table": args.status_table,
        "build_version": build_version,
        "days": len(results),
        "by_status": by_status,
        "rows": sum(result.row_count for result in results),
        "events": sum(result.event_count for result in results),
        "duplicate_keys": sum(result.duplicate_keys for result in results),
        "elapsed_seconds": time.perf_counter() - started_at,
    }


if __name__ == "__main__":
    raise SystemExit(main())
