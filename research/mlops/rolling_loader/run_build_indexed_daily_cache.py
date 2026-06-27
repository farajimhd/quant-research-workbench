from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import shutil
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.indexed_daily_cache import (
    DEFAULT_INDEXED_DAILY_CACHE_ROOT,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_SOURCE_COLUMNS,
    INDEXED_DAILY_CACHE_FORMAT,
    INDEXED_DAILY_CACHE_VERSION,
    SESSION_TIMEZONE,
    IndexedDailyCacheDayResult,
    cleanup_tmp_dirs,
    day_dir_for,
    directory_size,
    iter_session_dates,
    jsonable,
    parse_utc_us,
    replace_complete_dir,
    session_window,
    timestamp_us_to_utc,
    utc_date_from_us,
    write_json_atomic,
)
from research.mlops.rolling_loader.streaming_training import (
    StreamingClickHouseTrainingSource,
    StreamingContextBlock,
    StreamingEventBlock,
    StreamingProfiler,
    current_rss_mib,
)


DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "category_reference_table": "training_category_reference",
    "start_utc": "2019-01-05T00:00:00Z",
    "days": 1,
    "warmup_days": 3,
    "workers": 4,
    "stage_workers": 3,
    "prefetch_days": 1,
    "sample_stride_events": 1,
    "events_per_chunk": 128,
    "short_context_chunks": 32,
    "context_chunk_stride_events": 64,
    "short_context_stride_chunks": 1,
    "long_context_lags": "",
    "max_threads": 8,
    "max_memory_usage": "80G",
    "macro_lookback_days": 400,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "ticker_news_items": 8,
    "market_news_items": 16,
    "sec_filing_items": 4,
    "xbrl_items": 512,
    "event_row_limit": 0,
    "output_root": str(DEFAULT_INDEXED_DAILY_CACHE_ROOT),
}


@dataclass(slots=True)
class DayState:
    worker_id: int
    session_date: str
    status: str = "pending"
    stage: str = ""
    events_done: int = 0
    events_total: int = 1
    origins_done: int = 0
    origins_total: int = 1
    context_done: int = 0
    context_total: int = 1
    write_done: int = 0
    write_total: int = 1
    seconds: float = 0.0
    message: str = ""


@dataclass(slots=True)
class BuildStats:
    started: float = field(default_factory=time.perf_counter)
    split: str = "train"
    phase: str = "starting"
    days_total: int = 0
    days_done: int = 0
    days_failed: int = 0
    events_written: int = 0
    origins_written: int = 0
    bytes_written: int = 0
    current_rss_mib: float = 0.0
    max_rss_mib: float = 0.0
    prefetch_pending: int = 0
    prefetch_ready: int = 0
    messages: list[str] = field(default_factory=list)
    workers: dict[int, DayState] = field(default_factory=dict)
    log_path: Path | None = None
    progress_path: Path | None = None
    interrupted: bool = False
    stop_requested: bool = False

    def message(self, text: str, **fields: Any) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.messages.append(f"{stamp} {text}")
        self.messages = self.messages[-10:]
        if self.log_path is not None:
            payload = {
                "timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                "phase": self.phase,
                "message": text,
                **fields,
            }
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(jsonable(payload), sort_keys=True) + "\n")


@dataclass(frozen=True, slots=True)
class DayFetch:
    session_date: dt.date
    block: StreamingEventBlock
    context: StreamingContextBlock | None
    macro_rows: list[dict[str, Any]]
    fetch_seconds: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build daily indexed rolling-training cache packages.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--sec-context-database", default=DEFAULTS["sec_context_database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=DEFAULTS["category_reference_table"])
    parser.add_argument("--cache-root", type=Path, default=Path(DEFAULTS["output_root"]))
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--split", default="train")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start-utc", default=DEFAULTS["start_utc"])
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--days", type=int, default=DEFAULTS["days"])
    parser.add_argument("--start-session-date", default="", help="Inclusive session date label YYYY-MM-DD. Defaults to --start-utc date.")
    parser.add_argument("--end-session-date", default="", help="Exclusive session date label YYYY-MM-DD. Defaults to --end-utc date or start+days.")
    parser.add_argument("--warmup-days", type=int, default=DEFAULTS["warmup_days"])
    parser.add_argument("--workers", type=int, default=DEFAULTS["workers"])
    parser.add_argument("--stage-workers", type=int, default=DEFAULTS["stage_workers"], help="Per-day workers for independent dependency writes after event/origin indexing.")
    parser.add_argument("--prefetch-days", type=int, default=DEFAULTS["prefetch_days"])
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--short-context-chunks", type=int, default=DEFAULTS["short_context_chunks"])
    parser.add_argument("--context-chunk-stride-events", type=int, default=DEFAULTS["context_chunk_stride_events"])
    parser.add_argument("--short-context-stride-chunks", type=int, default=DEFAULTS["short_context_stride_chunks"])
    parser.add_argument("--long-context-lags", default=DEFAULTS["long_context_lags"])
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--ticker-news-items", type=int, default=DEFAULTS["ticker_news_items"])
    parser.add_argument("--market-news-items", type=int, default=DEFAULTS["market_news_items"])
    parser.add_argument("--sec-filing-items", type=int, default=DEFAULTS["sec_filing_items"])
    parser.add_argument("--xbrl-items", type=int, default=DEFAULTS["xbrl_items"])
    parser.add_argument("--event-row-limit", type=int, default=DEFAULTS["event_row_limit"])
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--skip-final-audit", action="store_true")
    parser.add_argument("--audit-source-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--audit-sample-days", type=int, default=3)
    parser.add_argument("--audit-samples-per-day", type=int, default=3)
    parser.add_argument("--audit-fail-on-warning", action="store_true")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--plain-status", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded_env = load_env_files(env_files)
    if loaded_env:
        print("Loaded .env files: " + ", ".join(str(path) for path in loaded_env), flush=True)

    start_session, end_session = _resolve_session_dates(args)
    session_dates = tuple(iter_session_dates(start_session, end_session))
    if not session_dates:
        raise SystemExit("No session dates selected.")

    cache_id = args.cache_id or f"{args.split}_{start_session:%Y%m%d}_{end_session:%Y%m%d}_indexed"
    cache_root = Path(args.cache_root) / cache_id
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / args.split).mkdir(parents=True, exist_ok=True)
    cleanup_tmp_dirs(cache_root)

    contexts = [] if args.skip_token_contexts else ["ticker_news", "market_news", "sec_filings"]
    if not args.skip_token_contexts and not args.skip_xbrl:
        contexts.append("xbrl")
    config = RollingMarketDataConfig(
        database=args.database,
        sec_context_database=args.sec_context_database,
        events_table=args.events_table,
        macro_bars_table=args.macro_bars_table,
        news_token_table=args.news_token_table,
        sec_filing_text_token_table=args.sec_filing_text_token_table,
        sec_xbrl_context_table=args.sec_xbrl_context_table,
        category_reference_table=args.category_reference_table,
        events_per_chunk=max(1, int(args.events_per_chunk)),
        short_context_chunks=max(0, int(args.short_context_chunks)),
        short_context_stride_chunks=max(1, int(args.short_context_stride_chunks)),
        long_context_lags=_parse_lags(args.long_context_lags),
        sample_stride_events=max(1, int(args.sample_stride_events)),
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
        macro_timeframes=("1d",),
        label_timeframes=("1d",),
        macro_lookback_days=max(0, int(args.macro_lookback_days)),
        label_lookahead_days=max(0, int(args.label_lookahead_days)),
        q_live_contexts=tuple(contexts),
        news_lookback_days=max(0, int(args.news_lookback_days)),
        sec_lookback_days=max(0, int(args.sec_lookback_days)),
        xbrl_lookback_days=max(0, int(args.xbrl_lookback_days)),
        news_max_items=max(0, int(args.ticker_news_items)),
        market_news_max_items=max(0, int(args.market_news_items)),
        sec_max_items=max(0, int(args.sec_filing_items)),
        xbrl_max_items=max(0, int(args.xbrl_items)),
    )
    context_lags = _context_lags(args)
    profiler = StreamingProfiler(output_path=cache_root / "builder_profile_events.jsonl")
    source = StreamingClickHouseTrainingSource(
        config=config,
        clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
        user=args.user or default_clickhouse_user(),
        password=args.password or default_clickhouse_password(),
    )
    source.profiler = profiler
    stats = BuildStats(
        split=str(args.split),
        days_total=len(session_dates),
        log_path=cache_root / "builder_events.jsonl",
        progress_path=cache_root / f"{args.split}_progress.json",
    )
    stats.workers = {idx: DayState(worker_id=idx, session_date="") for idx in range(max(1, int(args.workers)))}
    dashboard = IndexedCacheDashboard(enabled=not args.no_rich, live=not args.plain_status, refresh_seconds=args.refresh_seconds, stats=stats)
    manifest = _manifest(args, cache_id, cache_root, loaded_env, session_dates, context_lags)
    write_json_atomic(cache_root / "manifest.json", manifest | {"status": "running"})

    fetch_executor: ThreadPoolExecutor | None = None
    build_executor: ThreadPoolExecutor | None = None
    stop_event = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)

    def request_stop(signum: int, _frame: Any) -> None:
        stop_event.set()
        stats.interrupted = True
        stats.stop_requested = True
        stats.phase = "stopping"
        stats.message("stop requested; cancelling queued work after completed day packages")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_stop)
    try:
        dashboard.start()
        stats.message(f"cache_root={cache_root}")
        fetch_depth = max(0, int(args.prefetch_days))
        fetch_executor = ThreadPoolExecutor(max_workers=1) if fetch_depth else None
        build_executor = ThreadPoolExecutor(max_workers=max(1, int(args.workers)))
        pending_fetches: dict[Future[DayFetch], dt.date] = {}
        pending_builds: dict[Future[IndexedDailyCacheDayResult], int] = {}
        next_fetch_index = 0
        next_worker = 0

        def update_prefetch_stats() -> None:
            stats.prefetch_pending = len(pending_fetches)
            stats.prefetch_ready = sum(1 for future in pending_fetches if future.done())

        def submit_fetches() -> None:
            nonlocal next_fetch_index
            if fetch_executor is None:
                update_prefetch_stats()
                return
            while next_fetch_index < len(session_dates) and len(pending_fetches) < max(1, fetch_depth):
                day = session_dates[next_fetch_index]
                pending_fetches[fetch_executor.submit(_fetch_day, args, source, config, day, context_lags)] = day
                stats.message(f"prefetch submitted {day.isoformat()}")
                next_fetch_index += 1
            update_prefetch_stats()

        submit_fetches()
        stats.phase = "fetching"
        for day in session_dates:
            if stop_event.is_set():
                break
            if fetch_executor is None:
                fetched = _fetch_day(args, source, config, day, context_lags)
            else:
                while True:
                    if stop_event.is_set():
                        raise KeyboardInterrupt
                    ready = [future for future in pending_fetches if future.done()]
                    if ready:
                        future = min(ready, key=lambda item: session_dates.index(pending_fetches[item]))
                        fetched = future.result()
                        del pending_fetches[future]
                        submit_fetches()
                        update_prefetch_stats()
                        break
                    update_prefetch_stats()
                    _refresh(stats, dashboard, cache_root)
                    time.sleep(0.25)
            worker_id = next_worker % max(1, int(args.workers))
            next_worker += 1
            state = stats.workers[worker_id]
            state.session_date = fetched.session_date.isoformat()
            state.status = "queued"
            state.stage = "queued"
            stats.phase = "building"
            pending_builds[build_executor.submit(_write_indexed_day, args, cache_root, config, context_lags, fetched, worker_id, stats, stop_event)] = worker_id
            while len(pending_builds) >= max(1, int(args.workers)):
                if stop_event.is_set():
                    break
                _drain_one_build(pending_builds, stats, manifest, cache_root, dashboard)
            if stop_event.is_set():
                break
        while pending_builds:
            if stop_event.is_set():
                break
            _drain_one_build(pending_builds, stats, manifest, cache_root, dashboard)
        if stop_event.is_set():
            raise KeyboardInterrupt

        stats.prefetch_pending = 0
        stats.prefetch_ready = 0
        manifest["status"] = "complete"
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        manifest["summary"] = {
            "days": int(stats.days_done),
            "events": int(stats.events_written),
            "origins": int(stats.origins_written),
            "bytes": int(stats.bytes_written),
        }
        write_json_atomic(cache_root / "manifest.json", manifest)
        if not args.skip_final_audit:
            stats.phase = "auditing"
            stats.message("running final indexed cache audit")
            _refresh(stats, dashboard, cache_root, force=True)
            from research.mlops.rolling_loader.audit_indexed_daily_cache import IndexedDailyCacheAuditConfig, run_audit

            audit = run_audit(
                IndexedDailyCacheAuditConfig(
                    cache_root=cache_root,
                    split=args.split,
                    sample_days=max(0, int(args.audit_sample_days)),
                    samples_per_day=max(0, int(args.audit_samples_per_day)),
                    source_checks=bool(args.audit_source_checks),
                    fail_on_warning=bool(args.audit_fail_on_warning),
                    clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
                    clickhouse_user=args.user or default_clickhouse_user(),
                    clickhouse_password=args.password or default_clickhouse_password(),
                    database=args.database,
                    events_table=args.events_table,
                    max_threads=max(1, int(args.max_threads)),
                    max_memory_usage=str(args.max_memory_usage),
                )
            )
            manifest["audit"] = {"ok": audit.ok, "status": audit.status, "summary": audit.summary, "report_path": audit.report_path}
            manifest["status"] = "complete" if audit.ok else "audit_failed"
            write_json_atomic(cache_root / "manifest.json", manifest)
            if not audit.ok:
                raise RuntimeError(f"Final indexed cache audit failed; see {audit.report_path}")
        stats.phase = "complete"
        stats.message("indexed daily cache build complete")
        _refresh(stats, dashboard, cache_root, force=True)
        return 0
    except KeyboardInterrupt:
        stop_event.set()
        stats.interrupted = True
        stats.stop_requested = True
        stats.phase = "interrupted"
        stats.message("interrupted by user; complete day folders remain usable and temporary folders are ignored")
        write_json_atomic(cache_root / "manifest.json", manifest | {"status": "interrupted"})
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if fetch_executor is not None:
            fetch_executor.shutdown(wait=False, cancel_futures=True)
        if build_executor is not None:
            build_executor.shutdown(wait=not stats.interrupted, cancel_futures=True)
        source.close()
        _write_progress(cache_root, args.split, stats)
        dashboard.stop()


def _fetch_day(
    args: argparse.Namespace,
    source: StreamingClickHouseTrainingSource,
    config: RollingMarketDataConfig,
    session_date: dt.date,
    context_lags: tuple[int, ...],
) -> DayFetch:
    started = time.perf_counter()
    session = session_window(session_date)
    block = _fetch_session_dependency_events(args, source, config, session_date, session, context_lags)
    context = None
    if not args.skip_token_contexts:
        if block.row_count:
            context = source.fetch_token_contexts(
                start_timestamp_us=session.start_timestamp_us,
                end_timestamp_us=session.end_timestamp_us,
                include_lookback=True,
                include_xbrl=not bool(args.skip_xbrl),
            )
        else:
            context = _empty_context_block(config, session, include_xbrl=not bool(args.skip_xbrl))
    macro = source.fetch_macro_bars_1d(start_date=session_date, end_date=session_date + dt.timedelta(days=1))
    return DayFetch(session_date=session_date, block=block, context=context, macro_rows=macro.rows, fetch_seconds=time.perf_counter() - started)


def _empty_context_block(config: RollingMarketDataConfig, session: Any, *, include_xbrl: bool) -> StreamingContextBlock:
    enabled = set(config.q_live_contexts)
    rows: dict[str, list[dict[str, Any]]] = {}
    for name in ("ticker_news", "market_news", "sec_filings"):
        if name in enabled:
            rows[name] = []
    if include_xbrl and "xbrl" in enabled:
        rows["xbrl"] = []
    return StreamingContextBlock(
        start_timestamp_us=int(session.start_timestamp_us),
        end_timestamp_us=int(session.end_timestamp_us),
        rows_by_context=rows,
    )


def _fetch_session_dependency_events(
    args: argparse.Namespace,
    source: StreamingClickHouseTrainingSource,
    config: RollingMarketDataConfig,
    session_date: dt.date,
    session: Any,
    context_lags: tuple[int, ...],
) -> StreamingEventBlock:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    required_lookback = int(max(context_lags, default=0)) + max(1, int(args.events_per_chunk))
    dep_start_date = session_date - dt.timedelta(days=max(0, int(args.warmup_days)))
    dep_end_date = utc_date_from_us(session.end_timestamp_us) + dt.timedelta(days=1)
    session_start_date = utc_date_from_us(session.start_timestamp_us)
    session_end_date = utc_date_from_us(session.end_timestamp_us) + dt.timedelta(days=1)
    limit_sql = f"\nLIMIT {int(args.event_row_limit)}" if int(args.event_row_limit) > 0 else ""
    query = f"""
SELECT
    e.ticker,
    e.ordinal,
    e.event_type,
    e.sip_timestamp_us,
    e.price_primary_int,
    e.price_secondary_int,
    e.size_primary,
    e.size_secondary,
    e.exchange_primary,
    e.exchange_secondary,
    e.event_flags,
    e.conditions_packed
FROM
(
    SELECT
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
        conditions_packed
    FROM {table}
    PREWHERE event_date >= toDate({sql_string(dep_start_date.isoformat())})
      AND event_date < toDate({sql_string(dep_end_date.isoformat())})
) AS e
INNER JOIN
(
    SELECT
        ticker,
        min(ordinal) AS min_ordinal,
        max(ordinal) AS max_ordinal
    FROM {table}
    PREWHERE event_date >= toDate({sql_string(session_start_date.isoformat())})
      AND event_date < toDate({sql_string(session_end_date.isoformat())})
    WHERE sip_timestamp_us >= {int(session.start_timestamp_us)}
      AND sip_timestamp_us < {int(session.end_timestamp_us)}
    GROUP BY ticker
) AS b ON e.ticker = b.ticker
WHERE e.ordinal >= if(b.min_ordinal > {required_lookback}, b.min_ordinal - {required_lookback}, 0)
  AND e.ordinal <= b.max_ordinal
ORDER BY e.ticker, e.ordinal
{limit_sql}
{source._settings()}
"""
    frame = source.query_polars(query)
    return source.event_frame_to_block(frame=frame, start_date=dep_start_date, end_date=dep_end_date)


def _write_indexed_day(
    args: argparse.Namespace,
    cache_root: Path,
    config: RollingMarketDataConfig,
    context_lags: tuple[int, ...],
    fetched: DayFetch,
    worker_id: int,
    stats: BuildStats,
    stop_event: threading.Event,
) -> IndexedDailyCacheDayResult:
    started = time.perf_counter()
    pl = _polars()
    state = stats.workers[worker_id]
    session = session_window(fetched.session_date)
    final_dir = day_dir_for(cache_root, args.split, fetched.session_date)
    tmp_dir = final_dir.with_name(final_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    _raise_if_stopping(stop_event)
    state.status = "running"
    state.stage = "events"
    state.message = "normalizing events"

    frame = fetched.block.frame
    if frame.height:
        events = (
            frame.with_columns(pl.col("ticker").cast(pl.Utf8).str.to_uppercase())
            .rename({"sip_timestamp_us": "timestamp_us"})
            .filter((pl.col("timestamp_us") >= int(session.start_timestamp_us - int(args.warmup_days) * 86_400_000_000)) & (pl.col("timestamp_us") < int(session.end_timestamp_us)))
            .sort(["ticker", "ordinal"])
        )
    else:
        events = pl.DataFrame({column: [] for column in ("ticker", *EVENT_PAYLOAD_COLUMNS)})
    tickers = sorted(str(value).upper() for value in events.get_column("ticker").unique().to_list()) if events.height else []
    ticker_id_map = {ticker: idx for idx, ticker in enumerate(tickers)}
    if events.height:
        events = (
            events.with_columns(pl.col("ticker").replace(ticker_id_map).cast(pl.UInt32).alias("ticker_id"))
            .with_row_index("row_offset")
            .select(["row_offset", "ticker", "ticker_id", *EVENT_PAYLOAD_COLUMNS])
        )
    else:
        events = pl.DataFrame({"row_offset": [], "ticker": [], "ticker_id": [], **{column: [] for column in EVENT_PAYLOAD_COLUMNS}})
    state.events_total = max(1, int(events.height))
    state.events_done = int(events.height)
    _raise_if_stopping(stop_event)

    state.stage = "origins"
    state.message = "building origin and event-window indices"
    origins, windows, skipped_history, skipped_gap = _build_origin_windows(
        events=events,
        context_lags=context_lags,
        events_per_chunk=max(1, int(args.events_per_chunk)),
        sample_stride=max(1, int(args.sample_stride_events)),
        session_start_us=session.start_timestamp_us,
        session_end_us=session.end_timestamp_us,
    )
    state.origins_total = max(1, int(origins.height))
    state.origins_done = int(origins.height)
    _raise_if_stopping(stop_event)

    state.stage = "context"
    state.message = "writing independent cache files"
    active_tickers = set(origins.get_column("ticker").unique().to_list()) if origins.height else set()
    _write_day_files_concurrently(
        args=args,
        day_dir=tmp_dir,
        pl=pl,
        events=events,
        ticker_id_map=ticker_id_map,
        origins=origins,
        windows=windows,
        fetched=fetched,
        session=session,
        config=config,
        active_tickers=active_tickers,
        state=state,
        stop_event=stop_event,
    )
    _raise_if_stopping(stop_event)

    source_session_events = int(events.filter((pl.col("timestamp_us") >= session.start_timestamp_us) & (pl.col("timestamp_us") < session.end_timestamp_us)).height)
    day_manifest = {
        "format": INDEXED_DAILY_CACHE_FORMAT,
        "version": INDEXED_DAILY_CACHE_VERSION,
        "status": "complete",
        "split": args.split,
        "session": asdict(session),
        "session_start_utc": session.start_utc,
        "session_end_utc": session.end_utc,
        "event_dependency": {
            "warmup_days": int(args.warmup_days),
            "required_event_lookback_rows": int(max(context_lags, default=0)) + int(args.events_per_chunk),
            "rows": int(events.height),
            "source_session_event_count": source_session_events,
        },
        "config": {
            "events_per_chunk": int(args.events_per_chunk),
            "context_lags": list(context_lags),
            "sample_stride_events": int(args.sample_stride_events),
            "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
        },
        "counts": {
            "events": int(events.height),
            "tickers": len(active_tickers),
            "origins": int(origins.height),
            "windows": int(windows.height) * len(context_lags),
            "skipped_not_enough_history": int(skipped_history),
            "skipped_window_gap": int(skipped_gap),
        },
        "files": {
            "events": "events.parquet",
            "event_ranges": "event_ranges.parquet",
            "origins": "origins.parquet",
            "event_window_index": "event_window_index.parquet",
            "macro_bars": "macro_bars.parquet",
            "macro_ranges": "macro_ranges.parquet",
            "intraday_label_index": "intraday_label_index.parquet",
        },
        "completed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
    }
    write_json_atomic(tmp_dir / "day_manifest.json", day_manifest)
    _raise_if_stopping(stop_event)
    state.stage = "write"
    state.write_done = 1
    state.write_total = 1
    replace_complete_dir(tmp_dir, final_dir, resume=bool(args.resume))
    state.status = "done"
    state.stage = "done"
    state.seconds = time.perf_counter() - started
    state.message = f"done origins={int(origins.height):,}"
    return IndexedDailyCacheDayResult(
        session_date=fetched.session_date.isoformat(),
        day_dir=final_dir,
        origin_count=int(origins.height),
        event_count=int(events.height),
        ticker_count=len(active_tickers),
        bytes_written=directory_size(final_dir),
        source_session_event_count=source_session_events,
        skipped_not_enough_history=int(skipped_history),
        skipped_window_gap=int(skipped_gap),
        status="complete",
    )


def _build_origin_windows(
    *,
    events: Any,
    context_lags: tuple[int, ...],
    events_per_chunk: int,
    sample_stride: int,
    session_start_us: int,
    session_end_us: int,
) -> tuple[Any, Any, int, int]:
    pl = _polars()
    origin_frames: list[Any] = []
    window_frames: list[Any] = []
    skipped_history = 0
    skipped_gap = 0
    if events.height == 0 or not context_lags:
        return _empty_origins(pl), _empty_windows(pl, context_lags), 0, 0
    origin_id = 0
    lags = np.asarray(context_lags, dtype=np.int64)
    context = int(events_per_chunk)
    for key, part in events.partition_by("ticker", as_dict=True, maintain_order=True).items():
        ticker = key[0] if isinstance(key, tuple) else key
        ordinals = part.get_column("ordinal").to_numpy().astype(np.int64, copy=False)
        timestamps = part.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
        row_offsets = part.get_column("row_offset").to_numpy().astype(np.int64, copy=False)
        ticker_id = int(part.get_column("ticker_id")[0])
        if ordinals.size == 0:
            continue
        session_positions = np.flatnonzero((timestamps >= int(session_start_us)) & (timestamps < int(session_end_us)))
        if session_positions.size == 0:
            continue
        candidates = session_positions[:: max(1, int(sample_stride))]
        if candidates.size == 0:
            continue
        ends = candidates[:, None] - lags[None, :]
        starts = ends - context + 1
        history_ok = (starts >= 0) & (ends >= 0) & (ends < int(ordinals.shape[0]))
        history_all = np.all(history_ok, axis=1)
        skipped_history += int(np.count_nonzero(~history_all))
        if not bool(history_all.any()):
            continue
        valid_candidate_indexes = np.flatnonzero(history_all)
        valid_starts = starts[valid_candidate_indexes]
        valid_ends = ends[valid_candidate_indexes]
        start_ordinals = ordinals[valid_starts]
        end_ordinals = ordinals[valid_ends]
        contiguous_all = np.all((end_ordinals - start_ordinals) == (context - 1), axis=1)
        skipped_gap += int(np.count_nonzero(~contiguous_all))
        if not bool(contiguous_all.any()):
            continue
        final_candidate_indexes = valid_candidate_indexes[np.flatnonzero(contiguous_all)]
        final_positions = candidates[final_candidate_indexes]
        final_starts = valid_starts[np.flatnonzero(contiguous_all)]
        count = int(final_positions.shape[0])
        ids = np.arange(origin_id, origin_id + count, dtype=np.int64)
        origin_frames.append(
            pl.DataFrame(
                {
                    "origin_id": ids,
                    "ticker": [str(ticker)] * count,
                    "ticker_id": np.full((count,), ticker_id, dtype=np.uint32),
                    "origin_ordinal": ordinals[final_positions],
                    "origin_timestamp_us": timestamps[final_positions],
                    "event_row_offset": row_offsets[final_positions],
                }
            )
        )
        window_columns: dict[str, Any] = {"origin_id": ids}
        start_offsets = row_offsets[final_starts]
        for context_index in range(len(context_lags)):
            window_columns[f"window_start_{context_index:03d}"] = start_offsets[:, context_index]
        window_frames.append(pl.DataFrame(window_columns))
        origin_id += count
    origins = pl.concat(origin_frames, how="vertical") if origin_frames else _empty_origins(pl)
    windows = pl.concat(window_frames, how="vertical") if window_frames else _empty_windows(pl, context_lags)
    return origins, windows, skipped_history, skipped_gap


def _write_event_ranges(day_dir: Path, events: Any, ticker_id_map: Mapping[str, int]) -> None:
    pl = _polars()
    if events.height == 0:
        pl.DataFrame(
            {
                "ticker": [],
                "ticker_id": [],
                "first_row_offset": [],
                "last_row_offset": [],
                "first_ordinal": [],
                "last_ordinal": [],
                "first_timestamp_us": [],
                "last_timestamp_us": [],
                "row_count": [],
            }
        ).write_parquet(day_dir / "event_ranges.parquet", compression="zstd")
        return
    rows = (
        events.group_by(["ticker", "ticker_id"], maintain_order=True)
        .agg(
            pl.col("row_offset").min().alias("first_row_offset"),
            pl.col("row_offset").max().alias("last_row_offset"),
            pl.col("ordinal").min().alias("first_ordinal"),
            pl.col("ordinal").max().alias("last_ordinal"),
            pl.col("timestamp_us").min().alias("first_timestamp_us"),
            pl.col("timestamp_us").max().alias("last_timestamp_us"),
            pl.len().alias("row_count"),
        )
        .sort("ticker")
    )
    rows.write_parquet(day_dir / "event_ranges.parquet", compression="zstd")
    write_json_atomic(day_dir / "ticker_map.json", {"ticker_to_id": dict(ticker_id_map)})


def _write_macro_rows(day_dir: Path, pl: Any, rows: list[dict[str, Any]], *, active_tickers: set[str], global_symbols: set[str]) -> None:
    symbols = {str(value).upper() for value in active_tickers}.union(str(value).upper() for value in global_symbols)
    filtered = [row for row in rows if str(row.get("sym", "")).upper() in symbols]
    frame = pl.DataFrame(filtered) if filtered else _empty_macro_bars(pl)
    if frame.height:
        frame = frame.with_columns(pl.col("sym").cast(pl.Utf8).str.to_uppercase()).sort(["sym", "timeframe", "bar_start_ms"])
        frame = frame.with_row_index("bar_offset")
    frame.write_parquet(day_dir / "macro_bars.parquet", compression="zstd")
    if frame.height:
        ranges = (
            frame.group_by(["sym", "timeframe"], maintain_order=True)
            .agg(
                pl.col("bar_offset").min().alias("first_bar_offset"),
                pl.col("bar_offset").max().alias("last_bar_offset"),
                pl.col("bar_start_ms").min().alias("first_bar_start_ms"),
                pl.col("bar_start_ms").max().alias("last_bar_start_ms"),
                pl.len().alias("row_count"),
            )
            .sort(["sym", "timeframe"])
        )
    else:
        ranges = _empty_macro_ranges(pl)
    ranges.write_parquet(day_dir / "macro_ranges.parquet", compression="zstd")


def _write_day_files_concurrently(
    *,
    args: argparse.Namespace,
    day_dir: Path,
    pl: Any,
    events: Any,
    ticker_id_map: Mapping[str, int],
    origins: Any,
    windows: Any,
    fetched: DayFetch,
    session: Any,
    config: RollingMarketDataConfig,
    active_tickers: set[str],
    state: DayState,
    stop_event: threading.Event,
) -> None:
    tasks = {
        "events": lambda: events.write_parquet(day_dir / "events.parquet", compression="zstd"),
        "ranges": lambda: _write_event_ranges(day_dir, events, ticker_id_map),
        "origins": lambda: origins.write_parquet(day_dir / "origins.parquet", compression="zstd"),
        "windows": lambda: windows.write_parquet(day_dir / "event_window_index.parquet", compression="zstd"),
        "macro": lambda: _write_macro_rows(day_dir, pl, fetched.macro_rows, active_tickers=active_tickers, global_symbols=set(config.global_symbols)),
        "context": lambda: _write_context_rows(day_dir, pl, fetched.context, active_tickers=active_tickers),
        "labels": lambda: _write_label_indices(day_dir, pl, origins, session=session, config=config),
    }
    state.write_total = len(tasks)
    state.write_done = 0
    workers = max(1, min(len(tasks), int(args.stage_workers)))
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        future_to_name = {executor.submit(task): name for name, task in tasks.items()}
        pending = set(future_to_name)
        while pending:
            _raise_if_stopping(stop_event)
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                name = future_to_name[future]
                state.stage = name
                state.message = f"{name} file write"
                future.result()
                state.write_done += 1
        _raise_if_stopping(stop_event)
    except BaseException:
        for future in future_to_name:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True, cancel_futures=False)


def _write_context_rows(day_dir: Path, pl: Any, context: StreamingContextBlock | None, *, active_tickers: set[str]) -> None:
    if context is None:
        return
    active = {str(value).upper() for value in active_tickers}
    for name, rows in context.rows_by_context.items():
        if name in {"ticker_news", "sec_filings", "xbrl"}:
            rows = [row for row in rows if str(row.get("ticker", "")).upper() in active]
        frame = pl.DataFrame(rows) if rows else pl.DataFrame({"ticker": [], "timestamp_us": [], "context_offset": []})
        if frame.height and "ticker" in frame.columns:
            frame = frame.with_columns(pl.col("ticker").cast(pl.Utf8).str.to_uppercase())
        if frame.height and "text_hash" in frame.columns:
            frame = frame.with_columns(pl.col("text_hash").cast(pl.Utf8))
        if frame.height and "timestamp_us" in frame.columns:
            sort_cols = [column for column in ("ticker", "timestamp_us", "source_id", "token_chunk_index") if column in frame.columns]
            frame = frame.sort(sort_cols).with_row_index("context_offset")
        frame.write_parquet(day_dir / f"{name}.parquet", compression="zstd")
        if frame.height and {"ticker", "timestamp_us", "context_offset"}.issubset(set(frame.columns)):
            ranges = (
                frame.group_by("ticker", maintain_order=True)
                .agg(
                    pl.col("context_offset").min().alias("first_context_offset"),
                    pl.col("context_offset").max().alias("last_context_offset"),
                    pl.col("timestamp_us").min().alias("first_timestamp_us"),
                    pl.col("timestamp_us").max().alias("last_timestamp_us"),
                    pl.len().alias("row_count"),
                )
                .sort("ticker")
            )
        else:
            ranges = pl.DataFrame({"ticker": [], "first_context_offset": [], "last_context_offset": [], "first_timestamp_us": [], "last_timestamp_us": [], "row_count": []})
        ranges.write_parquet(day_dir / f"{name}_ranges.parquet", compression="zstd")


def _write_label_indices(day_dir: Path, pl: Any, origins: Any, *, session: Any, config: RollingMarketDataConfig) -> None:
    if origins.height == 0:
        pl.DataFrame({"origin_id": [], "ticker": [], "ticker_id": [], "origin_timestamp_us": []}).write_parquet(day_dir / "intraday_label_index.parquet", compression="zstd")
        return
    out = origins.select(["origin_id", "ticker", "ticker_id", "origin_timestamp_us"])
    expressions = []
    for horizon in config.intraday_label_horizons:
        name = str(horizon.name).replace(" ", "_")
        target = pl.col("origin_timestamp_us") + int(horizon.microseconds)
        expressions.append(pl.when(target < int(session.end_timestamp_us)).then(target).otherwise(None).alias(f"intraday_target_{name}_us"))
        expressions.append((target < int(session.end_timestamp_us)).alias(f"intraday_target_{name}_valid"))
    out = out.with_columns(expressions)
    out.write_parquet(day_dir / "intraday_label_index.parquet", compression="zstd")


def _raise_if_stopping(stop_event: threading.Event) -> None:
    if stop_event.is_set():
        raise KeyboardInterrupt


def _empty_origins(pl: Any) -> Any:
    return pl.DataFrame(
        {
            "origin_id": [],
            "ticker": [],
            "ticker_id": [],
            "origin_ordinal": [],
            "origin_timestamp_us": [],
            "event_row_offset": [],
        }
    )


def _empty_windows(pl: Any, context_lags: tuple[int, ...]) -> Any:
    return pl.DataFrame({"origin_id": [], **{f"window_start_{index:03d}": [] for index, _lag in enumerate(context_lags)}})


def _empty_macro_bars(pl: Any) -> Any:
    return pl.DataFrame(
        {
            "bar_offset": [],
            "sym": [],
            "timeframe": [],
            "bar_start_ms": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
            "dollar_volume": [],
            "trade_count": [],
            "quote_count": [],
            "vwap": [],
        }
    )


def _empty_macro_ranges(pl: Any) -> Any:
    return pl.DataFrame(
        {
            "sym": [],
            "timeframe": [],
            "first_bar_offset": [],
            "last_bar_offset": [],
            "first_bar_start_ms": [],
            "last_bar_start_ms": [],
            "row_count": [],
        }
    )


def _drain_one_build(
    pending: dict[Future[IndexedDailyCacheDayResult], int],
    stats: BuildStats,
    manifest: dict[str, Any],
    cache_root: Path,
    dashboard: "IndexedCacheDashboard",
) -> None:
    while True:
        ready = [future for future in pending if future.done()]
        if ready:
            future = ready[0]
            worker_id = pending.pop(future)
            result = future.result()
            stats.days_done += 1
            stats.events_written += int(result.event_count)
            stats.origins_written += int(result.origin_count)
            stats.bytes_written += int(result.bytes_written)
            manifest.setdefault("days", []).append(
                {
                    "session_date": result.session_date,
                    "path": str(result.day_dir.relative_to(cache_root)),
                    "origins": int(result.origin_count),
                    "events": int(result.event_count),
                    "tickers": int(result.ticker_count),
                    "bytes": int(result.bytes_written),
                    "source_session_event_count": int(result.source_session_event_count),
                    "status": result.status,
                }
            )
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "running"})
            stats.message(f"day complete {result.session_date} origins={result.origin_count:,} bytes={result.bytes_written / 1024**3:.2f} GiB")
            _refresh(stats, dashboard, cache_root, force=True)
            return
        _refresh(stats, dashboard, cache_root)
        time.sleep(0.25)


def _manifest(
    args: argparse.Namespace,
    cache_id: str,
    cache_root: Path,
    loaded_env: list[Path],
    session_dates: tuple[dt.date, ...],
    context_lags: tuple[int, ...],
) -> dict[str, Any]:
    sessions = [session_window(day) for day in session_dates]
    return {
        "format": INDEXED_DAILY_CACHE_FORMAT,
        "version": INDEXED_DAILY_CACHE_VERSION,
        "cache_id": cache_id,
        "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "cache_root": str(cache_root),
        "split": args.split,
        "source": {
            "database": args.database,
            "events_table": args.events_table,
            "macro_bars_table": args.macro_bars_table,
            "news_token_table": args.news_token_table,
            "sec_filing_text_token_table": args.sec_filing_text_token_table,
            "sec_xbrl_context_table": args.sec_xbrl_context_table,
        },
        "session_range": {
            "start_session_date": session_dates[0].isoformat(),
            "end_session_date_exclusive": (session_dates[-1] + dt.timedelta(days=1)).isoformat(),
            "start_timestamp_us": sessions[0].start_timestamp_us,
            "end_timestamp_us": sessions[-1].end_timestamp_us,
            "start_utc": sessions[0].start_utc,
            "end_utc": sessions[-1].end_utc,
            "timezone": SESSION_TIMEZONE,
            "session_start_local_time": "04:00:00",
            "session_end_local_time": "20:00:00",
        },
        "loaded_env_files": [str(path) for path in loaded_env],
        "config": {
            **_redacted_args(args),
            "events_per_chunk": int(args.events_per_chunk),
            "context_lags": list(context_lags),
            "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
            "event_source_columns": list(EVENT_SOURCE_COLUMNS),
        },
        "days": [],
    }


def _write_progress(cache_root: Path, split: str, stats: BuildStats) -> None:
    elapsed = time.perf_counter() - stats.started
    rate = stats.origins_written / max(elapsed, 1e-9)
    progress = {
        "split": split,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "phase": stats.phase,
        "days_done": stats.days_done,
        "days_total": stats.days_total,
        "days_failed": stats.days_failed,
        "events_written": stats.events_written,
        "origins_written": stats.origins_written,
        "bytes_written": stats.bytes_written,
        "elapsed_seconds": elapsed,
        "origins_per_second": rate,
        "current_rss_mib": stats.current_rss_mib,
        "prefetch_pending": stats.prefetch_pending,
        "prefetch_ready": stats.prefetch_ready,
        "workers": {str(key): asdict(value) for key, value in stats.workers.items()},
        "messages": stats.messages,
    }
    path = stats.progress_path or (cache_root / f"{split}_progress.json")
    write_json_atomic(path, progress)


def _refresh(stats: BuildStats, dashboard: "IndexedCacheDashboard", cache_root: Path, *, force: bool = False) -> None:
    stats.current_rss_mib = current_rss_mib()
    _write_progress(cache_root, stats.split, stats)
    dashboard.refresh(force=force)


def _resolve_session_dates(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    if args.start_session_date:
        start = dt.date.fromisoformat(str(args.start_session_date))
    else:
        start = dt.datetime.fromtimestamp(parse_utc_us(args.start_utc) / 1_000_000.0, tz=dt.timezone.utc).date()
    if args.end_session_date:
        end = dt.date.fromisoformat(str(args.end_session_date))
    elif args.end_utc:
        end = dt.datetime.fromtimestamp(parse_utc_us(args.end_utc) / 1_000_000.0, tz=dt.timezone.utc).date()
    else:
        end = start + dt.timedelta(days=max(1, int(args.days)))
    return start, end


def _context_lags(args: argparse.Namespace) -> tuple[int, ...]:
    stride = max(1, int(args.context_chunk_stride_events)) * max(1, int(args.short_context_stride_chunks))
    dense = range(0, max(0, int(args.short_context_chunks)) * stride, stride)
    return tuple(sorted(set(int(value) for value in dense).union(_parse_lags(args.long_context_lags))))


def _parse_lags(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    return tuple(sorted({int(part.strip()) for part in text.split(",") if part.strip()}))


def _redacted_args(args: argparse.Namespace) -> dict[str, Any]:
    out = dict(vars(args))
    for key in list(out):
        upper = key.upper()
        if any(token in upper for token in ("PASSWORD", "TOKEN", "SECRET", "KEY")):
            out[key] = "<present>" if out.get(key) else ""
    return out


def _polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars to build indexed rolling caches.") from exc
    return pl


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "estimating"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            return f"{amount:,.2f} {unit}"
        amount /= 1024.0
    return f"{amount:,.2f} B"


class IndexedCacheDashboard:
    def __init__(self, *, enabled: bool, live: bool, refresh_seconds: float, stats: BuildStats) -> None:
        self.enabled = bool(enabled)
        self.live = bool(live)
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self.stats = stats
        self._last = 0.0
        self._live: Any | None = None
        self._rich: dict[str, Any] = {}
        self._printed_messages = 0
        self._status_width = 0
        self._fallback_reason = ""

    def start(self) -> None:
        if not self.enabled or not self.live:
            return
        try:
            from rich import box
            from rich.console import Console, Group
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
        except Exception as exc:
            self.enabled = False
            self._fallback_reason = repr(exc)
            self.stats.message(f"Rich dashboard unavailable; using compact status line: {exc!r}")
            return
        self._rich = {"box": box, "Console": Console, "Group": Group, "Live": Live, "Panel": Panel, "Table": Table}
        console = Console(force_terminal=True, color_system="auto", soft_wrap=False)
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=max(0.5, 1.0 / self.refresh_seconds),
            auto_refresh=False,
            screen=False,
            transient=False,
            vertical_overflow="crop",
        )
        try:
            self._live.start()
        except Exception as exc:
            self._live = None
            self.enabled = False
            self._fallback_reason = repr(exc)
            self.stats.message(f"Rich dashboard failed to start; using compact status line: {exc!r}")
            return
        self.stats.message("Rich dashboard active")

    def refresh(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if self._live is not None and (force or now - self._last >= self.refresh_seconds):
            self._live.update(self._render(), refresh=True)
            self._last = now
        elif force or now - self._last >= self.refresh_seconds:
            self._print_status_line(final=False)
            self._last = now

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            self._live.stop()
            self._live = None
        elif not self.live or not self.enabled:
            self._print_status_line(final=True)

    def _print_status_line(self, *, final: bool) -> None:
        while self._printed_messages < len(self.stats.messages):
            if self._status_width:
                sys.stdout.write("\r" + " " * self._status_width + "\r")
            print(self.stats.messages[self._printed_messages], flush=True)
            self._printed_messages += 1
            self._status_width = 0
        terminal_width = shutil.get_terminal_size((120, 40)).columns
        status = self._status_text(width=max(40, terminal_width - 1))
        padding = max(0, self._status_width - len(status))
        end = "\n" if final else ""
        sys.stdout.write("\r" + status + (" " * padding) + end)
        sys.stdout.flush()
        self._status_width = 0 if final else len(status)

    def _status_text(self, *, width: int) -> str:
        elapsed = time.perf_counter() - self.stats.started
        remaining_days = max(0, int(self.stats.days_total) - int(self.stats.days_done))
        day_rate = self.stats.days_done / max(elapsed, 1e-9)
        eta = remaining_days / day_rate if day_rate > 0 else None
        active = []
        for worker in self.stats.workers.values():
            if worker.status not in {"pending", "idle", "done"} or worker.stage not in {"", "done"}:
                active.append(f"W{worker.worker_id}:{worker.session_date}:{worker.stage or worker.status}")
        text = (
            f"[{self.stats.phase}] days={self.stats.days_done}/{self.stats.days_total} "
            f"origins={self.stats.origins_written:,} events={self.stats.events_written:,} "
            f"size={_format_bytes(self.stats.bytes_written)} prefetch={self.stats.prefetch_ready}/{self.stats.prefetch_pending} "
            f"rss={_format_bytes(self.stats.current_rss_mib * 1024 * 1024)} elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}"
        )
        if active:
            text += " " + " ".join(active[:6])
        if len(text) <= width:
            return text
        return text[: max(0, width - 1)] + "."

    def _render(self) -> Any:
        box = self._rich["box"]
        Group = self._rich["Group"]
        Panel = self._rich["Panel"]
        Table = self._rich["Table"]
        elapsed = time.perf_counter() - self.stats.started
        remaining_days = max(0, int(self.stats.days_total) - int(self.stats.days_done))
        day_rate = self.stats.days_done / max(elapsed, 1e-9)
        eta = remaining_days / day_rate if day_rate > 0 else None
        summary = Table.grid(expand=False)
        for index in range(3):
            if index:
                summary.add_column(width=3)
            summary.add_column(justify="right", style="dim", no_wrap=True)
            summary.add_column(no_wrap=True)
        rows = [
            (("Phase", self.stats.phase), ("Elapsed", _format_duration(elapsed)), ("ETA", _format_duration(eta))),
            (("Days", f"{self.stats.days_done}/{self.stats.days_total}"), ("Origins", f"{self.stats.origins_written:,}"), ("Events", f"{self.stats.events_written:,}")),
            (("Size", _format_bytes(self.stats.bytes_written)), ("RSS", _format_bytes(self.stats.current_rss_mib * 1024 * 1024)), ("Prefetch", f"{self.stats.prefetch_ready}/{self.stats.prefetch_pending}")),
        ]
        for row in rows:
            cells: list[str] = []
            for idx, (key, value) in enumerate(row):
                if idx:
                    cells.append("")
                cells.extend([f"{key}:", f" {value}"])
            summary.add_row(*cells)
        workers = Table(expand=True, box=box.ASCII)
        workers.add_column("W", width=4, no_wrap=True)
        workers.add_column("Day", width=12, no_wrap=True)
        workers.add_column("Stage", width=10, no_wrap=True)
        workers.add_column("Events", no_wrap=True)
        workers.add_column("Origins", no_wrap=True)
        workers.add_column("Context", no_wrap=True)
        workers.add_column("Write", no_wrap=True)
        workers.add_column("Seconds", width=8, no_wrap=True)
        workers.add_column("Message", overflow="ellipsis")
        for worker in self.stats.workers.values():
            workers.add_row(
                str(worker.worker_id),
                worker.session_date,
                worker.stage or worker.status,
                _cell(worker.events_done, worker.events_total),
                _cell(worker.origins_done, worker.origins_total),
                _cell(worker.context_done, worker.context_total),
                _cell(worker.write_done, worker.write_total),
                f"{worker.seconds:.1f}",
                worker.message,
            )
        messages = Table.grid(expand=True)
        for message in self.stats.messages[-8:]:
            messages.add_row(message)
        return Group(
            Panel(summary, title="Indexed Rolling Cache", box=box.ASCII),
            Panel(workers, title="Daily Workers", box=box.ASCII),
            Panel(messages, title="Messages", box=box.ASCII),
        )


def _cell(done: int, total: int) -> str:
    if total <= 0:
        return "0/0"
    pct = 100.0 * float(done) / float(max(1, total))
    return f"{int(done):,}/{int(total):,} {pct:5.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
