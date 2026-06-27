from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import shutil
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
)
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.data.rolling import RollingMarketSampleEngine, RollingReadyIndexBlock
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.materialized_cache import (
    DEFAULT_MATERIALIZED_CACHE_ROOT,
    MATERIALIZED_CACHE_FORMAT,
    MATERIALIZED_CACHE_VERSION,
    RollingMaterializedShardWriter,
    cleanup_orphan_materialized_tmp,
    load_existing_materialized_shards,
    timestamp_us_to_utc,
)
from research.mlops.rolling_loader.streaming_training import (
    StreamingContextBlock,
    StreamingClickHouseTrainingSource,
    StreamingEventBlock,
    StreamingProfiler,
    batch_nbytes,
    current_rss_mib,
    date_from_us,
    parse_utc_us,
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
    "block_days": 1,
    "warmup_days": 3,
    "builder_batch_size": 4096,
    "sample_multiple": 4096,
    "workers": 4,
    "stage_workers": 0,
    "max_pending_tasks": 8,
    "prefetch_blocks": 1,
    "shard_size_gib": 16.0,
    "target_cache_gib": 16.0,
    "ready_sample_cap": 65536,
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "80G",
    "max_rss_gib": 0.0,
    "macro_lookback_days": 400,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "ticker_news_items": 8,
    "market_news_items": 16,
    "sec_filing_items": 4,
    "xbrl_items": 512,
    "event_row_limit": 0,
    "one_shard_max_days": 14,
    "output_root": str(DEFAULT_MATERIALIZED_CACHE_ROOT),
}


@dataclass(slots=True)
class WorkerState:
    worker_id: int
    status: str = "idle"
    ticker_count: int = 0
    queued_samples: int = 0
    materializing_done: int = 0
    materializing_total: int = 0
    encoding_done: int = 0
    encoding_total: int = 0
    features_done: int = 0
    features_total: int = 0
    labels_done: int = 0
    labels_total: int = 0
    context_done: int = 0
    context_total: int = 0
    text_done: int = 0
    text_total: int = 0
    xbrl_done: int = 0
    xbrl_total: int = 0
    writing_done: int = 0
    writing_total: int = 0
    batches_done: int = 0
    samples_done: int = 0
    active_slice_id: int = -1
    started_at: float = 0.0
    seconds: float = 0.0
    current_stage: str = ""
    last_message: str = ""


@dataclass(frozen=True, slots=True)
class PrefetchedTrainingBlock:
    block_index: int
    block_start: dt.date
    block_end: dt.date
    block: StreamingEventBlock
    context: StreamingContextBlock | None
    fetch_seconds: float


@dataclass(slots=True)
class BuildStats:
    started: float = field(default_factory=time.perf_counter)
    phase: str = "starting"
    block_index: int = 0
    block_start: str = ""
    block_end: str = ""
    rows_loaded: int = 0
    tickers_loaded: int = 0
    ready_samples: int = 0
    eligible_samples: int = 0
    skipped_before_start_samples: int = 0
    skipped_at_or_after_end_samples: int = 0
    submitted_tasks: int = 0
    completed_tasks: int = 0
    prefetch_depth: int = 0
    total_workers: int = 1
    materialization_workers: int = 1
    stage_workers: int = 1
    prefetch_pending: int = 0
    prefetch_ready: int = 0
    prefetch_next_block: int = 0
    prefetch_last_seconds: float = 0.0
    samples_written: int = 0
    bytes_written: int = 0
    target_bytes: int = 0
    shards_done: int = 0
    current_rss_mib: float = 0.0
    max_rss_mib: float = 0.0
    interrupted: bool = False
    messages: list[str] = field(default_factory=list)
    workers: dict[int, WorkerState] = field(default_factory=dict)
    log_path: Path | None = None

    def message(self, text: str, **fields: Any) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.messages.append(f"{stamp} {text}")
        self.messages = self.messages[-8:]
        if self.log_path is not None:
            payload = {
                "timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                "message": text,
                "phase": self.phase,
                **fields,
            }
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build 4096-aligned materialized rolling-training cache shards.")
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
    parser.add_argument("--one-shard", action="store_true", help="Stop after finalizing the first shard. Useful for test cache builds.")
    parser.add_argument(
        "--one-shard-max-days",
        type=int,
        default=DEFAULTS["one_shard_max_days"],
        help="When --one-shard is used without an explicit end timestamp, scan up to this many days to build the first complete shard.",
    )
    parser.add_argument("--start-utc", default=DEFAULTS["start_utc"])
    parser.add_argument("--start-timestamp-us", type=int, default=0)
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--end-timestamp-us", type=int, default=0)
    parser.add_argument("--days", type=int, default=DEFAULTS["days"])
    parser.add_argument("--block-days", type=int, default=DEFAULTS["block_days"])
    parser.add_argument("--warmup-days", type=int, default=DEFAULTS["warmup_days"])
    parser.add_argument("--builder-batch-size", type=int, default=DEFAULTS["builder_batch_size"])
    parser.add_argument("--sample-multiple", type=int, default=DEFAULTS["sample_multiple"])
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULTS["workers"],
        help=(
            "Total materialization worker budget. By default this is split into three lanes: "
            "encode/task workers, feature workers, label/context workers. For example, --workers 75 "
            "runs 25 outer materialization tasks with 3 branch lanes each."
        ),
    )
    parser.add_argument(
        "--stage-workers",
        type=int,
        default=DEFAULTS["stage_workers"],
        help=(
            "Per-materialization-task branch lanes. Default 0 means 3 lanes: features, labels, "
            "and context/text/xbrl. 1 disables branch parallelism."
        ),
    )
    parser.add_argument(
        "--max-pending-tasks",
        type=int,
        default=None,
        help=(
            "Maximum concurrently materializing outer tasks. Defaults to the derived outer worker count. "
            "Values below that count are raised so --workers controls the active split."
        ),
    )
    parser.add_argument(
        "--prefetch-blocks",
        type=int,
        default=None,
        help=(
            "Number of fetched event/context blocks to keep queued ahead of materialization. "
            "Defaults to 1 for full builds and 0 for --one-shard test builds."
        ),
    )
    parser.add_argument("--shard-size-gib", type=float, default=DEFAULTS["shard_size_gib"])
    parser.add_argument("--target-cache-gib", type=float, default=DEFAULTS["target_cache_gib"])
    parser.add_argument(
        "--ready-sample-cap",
        type=int,
        default=None,
        help=(
            "Maximum ready samples per event block. Defaults to "
            "--workers * --builder-batch-size, aligned to --sample-multiple. "
            "Pass 0 to disable the cap."
        ),
    )
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--max-rss-gib", type=float, default=DEFAULTS["max_rss_gib"], help="Soft process RSS limit in GiB. 0 disables the guard.")
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=365)
    parser.add_argument("--xbrl-lookback-days", type=int, default=730)
    parser.add_argument("--ticker-news-items", type=int, default=DEFAULTS["ticker_news_items"])
    parser.add_argument("--market-news-items", type=int, default=DEFAULTS["market_news_items"])
    parser.add_argument("--sec-filing-items", type=int, default=DEFAULTS["sec_filing_items"])
    parser.add_argument("--xbrl-items", type=int, default=DEFAULTS["xbrl_items"])
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--event-row-limit", type=int, default=DEFAULTS["event_row_limit"])
    parser.add_argument("--audit-samples", type=int, default=256)
    parser.add_argument("--skip-final-audit", action="store_true", help="Do not run the materialized-cache integrity audit after finalizing shards.")
    parser.add_argument("--audit-source-checks", action=argparse.BooleanOptionalAction, default=True, help="Spot-check sampled event windows against ClickHouse after the build.")
    parser.add_argument("--audit-sample-shards", type=int, default=3, help="Number of shards sampled by the final audit.")
    parser.add_argument("--audit-samples-per-shard", type=int, default=2, help="Number of samples checked per sampled shard.")
    parser.add_argument("--audit-zero-sample-rows", type=int, default=64, help="Rows per shard sampled for all-zero required tensor checks.")
    parser.add_argument("--audit-fail-on-warning", action="store_true", help="Treat audit warnings as failures.")
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--plain-status", action="store_true", help="Use a single non-Rich status line instead of the Rich panel dashboard.")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    return parser.parse_args()


def _normalize_parallel_args(args: argparse.Namespace) -> None:
    args.workers = max(1, int(args.workers))
    if int(args.stage_workers) <= 0:
        args.stage_workers = 3
    else:
        args.stage_workers = max(1, int(args.stage_workers))
    args.materialization_workers = max(1, int(args.workers) // max(1, int(args.stage_workers)))
    args.builder_batch_size = max(1, int(args.builder_batch_size))
    args.sample_multiple = max(1, int(args.sample_multiple))
    if args.max_pending_tasks is None:
        args.max_pending_tasks = int(args.materialization_workers)
    else:
        args.max_pending_tasks = max(1, int(args.max_pending_tasks), int(args.materialization_workers))
    if args.ready_sample_cap is None:
        args.ready_sample_cap = _default_ready_sample_cap(
            workers=int(args.materialization_workers),
            builder_batch_size=int(args.builder_batch_size),
            sample_multiple=int(args.sample_multiple),
        )
    else:
        args.ready_sample_cap = max(0, int(args.ready_sample_cap))
    args.max_rss_gib = max(0.0, float(args.max_rss_gib))


def _default_ready_sample_cap(*, workers: int, builder_batch_size: int, sample_multiple: int) -> int:
    raw = max(1, int(workers)) * max(1, int(builder_batch_size))
    multiple = max(1, int(sample_multiple))
    return ((raw + multiple - 1) // multiple) * multiple


def main() -> int:
    args = parse_args()
    _normalize_parallel_args(args)
    if args.prefetch_blocks is None:
        args.prefetch_blocks = 0 if bool(args.one_shard) else int(DEFAULTS["prefetch_blocks"])
    loaded_env = load_env_files(
        discover_clickhouse_env_files() if args.env_file is None else discover_clickhouse_env_files() + [args.env_file]
    )
    start_us = int(args.start_timestamp_us or parse_utc_us(args.start_utc))
    end_us = _resolve_end_timestamp_us(args, start_us)
    if args.one_shard and not _has_explicit_end_arg() and int(args.days) == int(DEFAULTS["days"]):
        one_shard_end_us = start_us + max(1, int(args.one_shard_max_days)) * 86_400_000_000
        end_us = max(end_us, one_shard_end_us)
    cache_id = args.cache_id.strip() or dt.datetime.now(tz=dt.timezone.utc).strftime("rolling_cache_%Y%m%d_%H%M%S")
    cache_root = Path(args.cache_root) / cache_id
    split_dir = cache_root / args.split
    cache_root.mkdir(parents=True, exist_ok=bool(args.resume))
    stats = BuildStats()
    stats.total_workers = int(args.workers)
    stats.materialization_workers = int(args.materialization_workers)
    stats.stage_workers = int(args.stage_workers)
    stats.target_bytes = int(float(args.target_cache_gib) * 1024**3)
    stats.max_rss_mib = float(args.max_rss_gib) * 1024.0
    stats.log_path = cache_root / "builder_events.jsonl"
    dashboard = MaterializedCacheDashboard(
        enabled=not args.no_rich,
        live=not bool(args.plain_status),
        refresh_seconds=float(args.refresh_seconds),
        stats=stats,
    )
    source: StreamingClickHouseTrainingSource | None = None
    writer: RollingMaterializedShardWriter | None = None
    executor: ThreadPoolExecutor | None = None
    fetch_executor: ThreadPoolExecutor | None = None

    try:
        stats.message(f"cache_root={cache_root}")
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
            sample_stride_events=max(1, int(args.sample_stride_events)),
            batch_size=max(1, int(args.builder_batch_size)),
            max_ready_samples=max(0, int(args.ready_sample_cap)),
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
        source = StreamingClickHouseTrainingSource(
            config=config,
            clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
            user=args.user or default_clickhouse_user(),
            password=args.password or default_clickhouse_password(),
        )
        profiler = StreamingProfiler(output_path=cache_root / "builder_profile_events.jsonl")
        source.profiler = profiler
        engine = RollingMarketSampleEngine(config)
        removed_tmp = cleanup_orphan_materialized_tmp(split_dir) if args.resume else 0
        existing_shards = load_existing_materialized_shards(cache_root, args.split) if args.resume else []
        writer = RollingMaterializedShardWriter(
            cache_root=cache_root,
            split=args.split,
            target_shard_bytes=int(float(args.shard_size_gib) * 1024**3),
            sample_multiple=max(1, int(args.sample_multiple)),
            start_shard_index=max((int(row["shard_index"]) for row in existing_shards), default=-1) + 1,
            existing_shards=existing_shards,
            audit_sample_limit=int(args.audit_samples),
            audit_rng=random.Random(17),
            origin_start_timestamp_us=start_us,
            origin_end_timestamp_us=end_us,
        )
        written_identities: set[tuple[str, int]] = set()
        manifest = _manifest(args, cache_id, cache_root, loaded_env, start_us, end_us, config)
        manifest["resume"] = {"enabled": bool(args.resume), "removed_tmp_dirs": removed_tmp, "existing_shards": len(existing_shards)}
        _write_json(cache_root / "manifest.json", manifest)
        dashboard.start()

        stats.phase = "initializing"
        _refresh(stats, dashboard, writer, force=True)
        with profiler.stage("category_references_fetch"):
            refs = source.fetch_category_references()
        engine.load_category_references(refs)
        stats.message(f"loaded category refs rows={len(refs):,}")
        start_date = date_from_us(start_us)
        end_date = _exclusive_timestamp_end_date(end_us)
        _refresh(stats, dashboard, writer, force=True)
        with profiler.stage("macro_bars_1d_full_fetch"):
            macro = source.fetch_macro_bars_1d(start_date=start_date, end_date=end_date)
        engine.load_macro_bars(macro)
        stats.message(f"loaded 1d macro/global rows={len(macro.rows):,}")
        if not args.skip_token_contexts:
            _refresh(stats, dashboard, writer, force=True)
            with profiler.stage("initial_token_context_fetch"):
                initial_context = source.fetch_token_contexts(
                    start_timestamp_us=start_us,
                    end_timestamp_us=start_us + 1,
                    include_lookback=True,
                    include_xbrl=not args.skip_xbrl,
                )
            engine.load_external_contexts(initial_context.rows_by_context)
            stats.message(f"loaded initial context rows={initial_context.row_count:,}")

        executor = ThreadPoolExecutor(max_workers=max(1, int(args.materialization_workers)))
        prefetch_depth = max(0, int(args.prefetch_blocks))
        stats.prefetch_depth = prefetch_depth
        fetch_executor = ThreadPoolExecutor(max_workers=1) if prefetch_depth > 0 else None
        for worker_id in range(max(1, int(args.materialization_workers))):
            stats.workers[worker_id] = WorkerState(worker_id=worker_id)
        cursor_date = date_from_us(start_us) - dt.timedelta(days=max(0, int(args.warmup_days)))
        final_date = _exclusive_timestamp_end_date(end_us)
        next_fetch_date = cursor_date
        next_fetch_index = 0
        fetch_queue: deque[Future[PrefetchedTrainingBlock]] = deque()
        stop_requested = False

        def submit_prefetches() -> None:
            nonlocal next_fetch_date, next_fetch_index
            if fetch_executor is None or prefetch_depth <= 0:
                return
            while not stop_requested and next_fetch_date < final_date and len(fetch_queue) < prefetch_depth:
                _enforce_rss_limit(args, stats, label="before_prefetch_submit")
                block_start = next_fetch_date
                block_end = min(final_date, next_fetch_date + dt.timedelta(days=max(1, int(args.block_days))))
                block_index = next_fetch_index
                future = fetch_executor.submit(
                    _fetch_materialized_cache_block,
                    source,
                    profiler,
                    block_start,
                    block_end,
                    block_index,
                    max(0, int(args.event_row_limit)),
                    start_us,
                    end_us,
                    not bool(args.skip_token_contexts),
                    not bool(args.skip_xbrl),
                )
                fetch_queue.append(future)
                next_fetch_date = block_end
                next_fetch_index += 1
                stats.prefetch_pending = len(fetch_queue)
                stats.prefetch_ready = sum(1 for item in fetch_queue if item.done())
                stats.prefetch_next_block = next_fetch_index
                stats.message(f"prefetch submitted block {block_index} window={block_start.isoformat()}->{block_end.isoformat()}")

        submit_prefetches()
        while not stop_requested:
            _enforce_rss_limit(args, stats, label="loop_start")
            if fetch_executor is None:
                if cursor_date >= final_date:
                    break
                block_start = cursor_date
                block_end = min(final_date, cursor_date + dt.timedelta(days=max(1, int(args.block_days))))
                stats.phase = "loading block"
                stats.block_index = next_fetch_index
                stats.block_start = block_start.isoformat()
                stats.block_end = block_end.isoformat()
                stats.message(f"loading block {next_fetch_index} window={block_start.isoformat()}->{block_end.isoformat()}")
                _refresh(stats, dashboard, writer, force=True)
                fetched = _fetch_materialized_cache_block(
                    source,
                    profiler,
                    block_start,
                    block_end,
                    next_fetch_index,
                    max(0, int(args.event_row_limit)),
                    start_us,
                    end_us,
                    not bool(args.skip_token_contexts),
                    not bool(args.skip_xbrl),
                )
                cursor_date = block_end
                next_fetch_index += 1
                stats.prefetch_next_block = next_fetch_index
            else:
                if not fetch_queue:
                    break
                stats.phase = "waiting prefetched block"
                stats.prefetch_pending = len(fetch_queue)
                stats.prefetch_ready = sum(1 for item in fetch_queue if item.done())
                _refresh(stats, dashboard, writer)
                future = fetch_queue.popleft()
                fetched = future.result()
                _enforce_rss_limit(args, stats, label="after_prefetch_result")
                stats.prefetch_pending = len(fetch_queue)
                stats.prefetch_ready = sum(1 for item in fetch_queue if item.done())

            block = fetched.block
            _enforce_rss_limit(args, stats, label="after_block_fetch")
            block_start = fetched.block_start
            block_end = fetched.block_end
            stats.block_index = int(fetched.block_index)
            stats.block_start = block_start.isoformat()
            stats.block_end = block_end.isoformat()
            stats.prefetch_last_seconds = float(fetched.fetch_seconds)
            stats.phase = "loading block"
            stats.rows_loaded = block.row_count
            stats.tickers_loaded = block.ticker_count
            stats.message(
                f"block {stats.block_index} events rows={block.row_count:,} tickers={block.ticker_count:,} "
                f"fetch_seconds={fetched.fetch_seconds:.1f}"
            )
            if block.row_count <= 0:
                submit_prefetches()
                continue
            stats.phase = "updating cache"
            _refresh(stats, dashboard, writer)
            with profiler.stage("event_block_append_engine", block_index=stats.block_index, rows=block.row_count, tickers=block.ticker_count):
                engine.append_rows_by_ticker(block.rows_by_ticker)
            _enforce_rss_limit(args, stats, label="after_event_block_append")
            if block.max_timestamp_us < start_us:
                with profiler.stage("warmup_event_cache_trim", block_index=stats.block_index):
                    _trim_pre_start_event_cache(engine, start_us)
                submit_prefetches()
                continue
            if fetched.context is not None:
                context = fetched.context
                with profiler.stage("token_context_block_load", block_index=stats.block_index, rows=context.row_count, counts=context.counts()):
                    engine.load_external_contexts(context.rows_by_context)
                stats.message(f"block context rows={context.row_count:,}")
                _enforce_rss_limit(args, stats, label="after_context_load")
            while not stop_requested:
                processed_ready, stop_requested = _materialize_ready_slice(
                    args=args,
                    cache_root=cache_root,
                    engine=engine,
                    executor=executor,
                    writer=writer,
                    stats=stats,
                    profiler=profiler,
                    dashboard=dashboard,
                    start_us=start_us,
                    end_us=end_us,
                    block=block,
                    block_start=block_start,
                    block_end=block_end,
                    written_identities=written_identities,
                    submit_prefetches=submit_prefetches,
                )
                if not processed_ready:
                    break
            if stop_requested:
                break
            submit_prefetches()
        if fetch_queue:
            cancelled = 0
            for future in fetch_queue:
                cancelled += 1 if future.cancel() else 0
            stats.prefetch_pending = 0
            stats.prefetch_ready = 0
            stats.message(f"prefetch queue stopped cancelled={cancelled:,} remaining={len(fetch_queue):,}")
        stats.phase = "finalizing"
        writer.close()
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        manifest["status"] = "interrupted" if stats.interrupted else "complete"
        manifest["shards"] = writer.shards
        manifest["summary"] = {
            "samples_written": stats.samples_written,
            "bytes_written": stats.bytes_written,
            "shard_count": len(writer.shards),
            "elapsed_seconds": time.perf_counter() - stats.started,
        }
        if not writer.shards:
            stats.message(
                "no shards were created; no materialized samples were written in the requested window",
                start_utc=timestamp_us_to_utc(start_us),
                end_utc=timestamp_us_to_utc(end_us),
            )
        _write_json(cache_root / "manifest.json", manifest)
        _write_progress(cache_root, args.split, stats, writer)
        if writer.shards and not stats.interrupted and not bool(args.skip_final_audit):
            stats.phase = "auditing"
            stats.message("running final materialized cache audit")
            _write_progress(cache_root, args.split, stats, writer)
            _refresh(stats, dashboard, writer, force=True)
            audit = _run_final_audit(args=args, cache_root=cache_root, start_us=start_us, end_us=end_us)
            manifest["audit"] = {
                "ok": bool(audit.ok),
                "status": audit.status,
                "report_path": audit.report_path,
                "sample_report_path": audit.sample_report_path,
                "elapsed_seconds": audit.elapsed_seconds,
                "summary": audit.summary,
            }
            if not audit.ok:
                manifest["status"] = "audit_failed"
                _write_json(cache_root / "manifest.json", manifest)
                stats.message(
                    "final materialized cache audit failed",
                    report_path=audit.report_path,
                    issue_counts=audit.summary.get("issue_counts", {}),
                )
                _write_progress(cache_root, args.split, stats, writer)
                raise RuntimeError(f"Final materialized cache audit failed; see {audit.report_path}")
            _write_json(cache_root / "manifest.json", manifest)
            stats.message(
                "final materialized cache audit passed",
                report_path=audit.report_path,
                issue_counts=audit.summary.get("issue_counts", {}),
            )
            _write_progress(cache_root, args.split, stats, writer)
        stats.phase = "done"
        stats.message("done")
        _write_progress(cache_root, args.split, stats, writer)
        _refresh(stats, dashboard, writer)
        return 130 if stats.interrupted else 0
    except KeyboardInterrupt:
        stats.interrupted = True
        stats.phase = "interrupt"
        stats.message("interrupt received; closing writer and stopping workers")
        if fetch_executor is not None:
            fetch_executor.shutdown(wait=False, cancel_futures=True)
            fetch_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if writer is not None:
            writer.close()
        _write_progress(cache_root, args.split, stats, writer)
        return 130
    finally:
        if fetch_executor is not None:
            fetch_executor.shutdown(wait=True, cancel_futures=True)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if source is not None:
            source.close()
        dashboard.stop()


def _fetch_materialized_cache_block(
    source: StreamingClickHouseTrainingSource,
    profiler: StreamingProfiler,
    block_start: dt.date,
    block_end: dt.date,
    block_index: int,
    event_row_limit: int,
    start_timestamp_us: int,
    end_timestamp_us: int,
    load_token_contexts: bool,
    include_xbrl: bool,
) -> PrefetchedTrainingBlock:
    started = time.perf_counter()
    with profiler.stage(
        "event_block_query_arrow_polars",
        block_index=int(block_index),
        start_date=block_start.isoformat(),
        end_date=block_end.isoformat(),
    ):
        frame = source.fetch_event_frame(start_date=block_start, end_date=block_end, row_limit=max(0, int(event_row_limit)))
    with profiler.stage("event_block_polars_to_numpy", block_index=int(block_index), rows=int(frame.height)):
        block = source.event_frame_to_block(frame=frame, start_date=block_start, end_date=block_end)
    context: StreamingContextBlock | None = None
    if load_token_contexts and block.row_count > 0 and block.max_timestamp_us >= int(start_timestamp_us):
        with profiler.stage("token_context_block_fetch", block_index=int(block_index)):
            context = source.fetch_token_contexts(
                start_timestamp_us=max(int(start_timestamp_us), int(block.min_timestamp_us)),
                end_timestamp_us=min(int(end_timestamp_us), int(block.max_timestamp_us) + 1),
                include_lookback=False,
                include_xbrl=bool(include_xbrl),
            )
    return PrefetchedTrainingBlock(
        block_index=int(block_index),
        block_start=block_start,
        block_end=block_end,
        block=block,
        context=context,
        fetch_seconds=time.perf_counter() - started,
    )


def _estimated_sample_bytes(writer: RollingMaterializedShardWriter) -> int:
    status = writer.current_shard_status()
    estimate = status.get("estimate") if isinstance(status, dict) else {}
    if isinstance(estimate, dict):
        sample_bytes = int(estimate.get("sample_bytes") or 0)
        if sample_bytes > 0:
            return sample_bytes
    for shard in reversed(writer.shards):
        samples = int(shard.get("num_samples") or 0)
        bytes_value = int(shard.get("actual_shard_bytes") or 0)
        if samples > 0 and bytes_value > 0:
            return max(1, bytes_value // samples)
    return 0


def _enforce_rss_limit(args: argparse.Namespace, stats: BuildStats, *, label: str) -> None:
    limit_mib = float(getattr(args, "max_rss_gib", 0.0) or 0.0) * 1024.0
    if limit_mib <= 0.0:
        return
    rss_mib = float(current_rss_mib())
    stats.current_rss_mib = rss_mib
    stats.max_rss_mib = limit_mib
    if rss_mib <= limit_mib:
        return
    stats.message(
        "rss limit exceeded",
        label=str(label),
        rss_mib=rss_mib,
        max_rss_mib=limit_mib,
    )
    raise MemoryError(f"RSS limit exceeded at {label}: {rss_mib:,.0f} MiB > {limit_mib:,.0f} MiB")


def _filter_ready_blocks_by_origin_window(
    blocks: tuple[RollingReadyIndexBlock, ...],
    *,
    start_timestamp_us: int,
    end_timestamp_us: int,
) -> tuple[tuple[RollingReadyIndexBlock, ...], dict[str, int]]:
    start_us = int(start_timestamp_us)
    end_us = int(end_timestamp_us)
    filtered: list[RollingReadyIndexBlock] = []
    total = 0
    before_start = 0
    at_or_after_end = 0
    for block in blocks:
        offsets = np.asarray(block.origin_offsets, dtype=np.int64)
        if offsets.size == 0:
            continue
        timestamps = block.rows["sip_timestamp_us"].astype(np.int64, copy=False)[offsets]
        total += int(offsets.shape[0])
        before_mask = timestamps < start_us
        after_mask = timestamps >= end_us
        eligible_mask = ~(before_mask | after_mask)
        before_start += int(np.count_nonzero(before_mask))
        at_or_after_end += int(np.count_nonzero(after_mask))
        if bool(np.any(eligible_mask)):
            filtered.append(
                RollingReadyIndexBlock(
                    ticker=block.ticker,
                    rows=block.rows,
                    origin_offsets=offsets[eligible_mask],
                )
            )
    eligible = int(sum(block.sample_count for block in filtered))
    return (
        tuple(filtered),
        {
            "total": int(total),
            "eligible": int(eligible),
            "before_start": int(before_start),
            "at_or_after_end": int(at_or_after_end),
        },
    )


def _assert_batch_origin_window(batch: Any, *, start_timestamp_us: int, end_timestamp_us: int) -> None:
    origins = np.asarray(batch.origin_timestamp_us, dtype=np.int64).reshape(-1)
    if origins.size == 0:
        raise RuntimeError("Materialized training batch has no origin timestamps.")
    min_origin = int(origins.min())
    max_origin = int(origins.max())
    if min_origin < int(start_timestamp_us):
        raise RuntimeError(
            "Materialized training batch contains origins before the requested cache period: "
            f"min={timestamp_us_to_utc(min_origin)} start={timestamp_us_to_utc(int(start_timestamp_us))}"
        )
    if max_origin >= int(end_timestamp_us):
        raise RuntimeError(
            "Materialized training batch contains origins at/after the requested cache period end: "
            f"max={timestamp_us_to_utc(max_origin)} end={timestamp_us_to_utc(int(end_timestamp_us))}"
        )


def _assert_batch_new_identities(batch: Any, seen: set[tuple[str, int]]) -> None:
    tickers = np.asarray(batch.ticker).reshape(-1)
    ordinals = np.asarray(batch.origin_ordinal, dtype=np.int64).reshape(-1)
    if tickers.shape[0] != ordinals.shape[0]:
        raise RuntimeError(f"Materialized training batch identity shape mismatch: tickers={tickers.shape} ordinals={ordinals.shape}")
    pending: list[tuple[str, int]] = []
    local_seen: set[tuple[str, int]] = set()
    for ticker_raw, ordinal_raw in zip(tickers.tolist(), ordinals.tolist(), strict=True):
        ticker = str(ticker_raw).upper()
        key = (ticker, int(ordinal_raw))
        if key in local_seen:
            raise RuntimeError(f"Materialized training batch contains duplicate sample identity within batch: ticker={ticker} origin_ordinal={int(ordinal_raw)}")
        if key in seen:
            raise RuntimeError(f"Materialized training cache would write duplicate sample identity: ticker={ticker} origin_ordinal={int(ordinal_raw)}")
        local_seen.add(key)
        pending.append(key)
    seen.update(pending)


def _ready_sample_cap_for_budget(
    *,
    args: argparse.Namespace,
    stats: BuildStats,
    writer: RollingMaterializedShardWriter,
) -> int:
    configured_cap = max(0, int(args.ready_sample_cap))
    sample_bytes = _estimated_sample_bytes(writer)
    if sample_bytes <= 0 or int(stats.target_bytes) <= 0:
        return configured_cap
    remaining_bytes = max(0, int(stats.target_bytes) - int(stats.bytes_written))
    if remaining_bytes <= 0:
        return 0
    needed = max(1, (remaining_bytes + sample_bytes - 1) // sample_bytes)
    multiple = max(1, int(args.sample_multiple))
    aligned = ((int(needed) + multiple - 1) // multiple) * multiple
    if configured_cap > 0:
        return max(1, min(configured_cap, int(aligned)))
    return max(1, int(aligned))


def _ready_samples_can_satisfy_target(
    *,
    stats: BuildStats,
    writer: RollingMaterializedShardWriter,
    ready_samples: int,
) -> bool:
    if int(stats.target_bytes) <= 0:
        return False
    remaining_bytes = max(0, int(stats.target_bytes) - int(stats.bytes_written))
    if remaining_bytes <= 0:
        return True
    sample_bytes = _estimated_sample_bytes(writer)
    if sample_bytes <= 0:
        return False
    return int(ready_samples) * int(sample_bytes) >= int(remaining_bytes)


def _materialize_worker_task(
    base_engine: RollingMarketSampleEngine,
    blocks: list[RollingReadyIndexBlock],
    batch_size: int,
    stage_workers: int,
    worker_id: int,
    slice_id: int,
    state: WorkerState | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if state is not None:
        state.status = "materializing"
        state.materializing_done = 0
        state.last_message = f"materializing slice={slice_id}"
    state_started = state.started_at if state is not None and state.started_at > 0 else started
    engine = RollingMarketSampleEngine(base_engine.config)
    engine._today_asof_day_cache = base_engine._today_asof_day_cache
    engine._today_asof_day_cache_lock = base_engine._today_asof_day_cache_lock
    engine._future_label_state_cache = base_engine._future_label_state_cache
    engine._future_label_state_cache_lock = base_engine._future_label_state_cache_lock
    tickers = {str(block.ticker).upper() for block in blocks}
    tickers.update(str(symbol).upper() for symbol in base_engine.config.global_symbols)
    engine.rows_by_ticker = {ticker: base_engine.rows_by_ticker[ticker] for ticker in tickers if ticker in base_engine.rows_by_ticker}
    engine.macro_bars = base_engine.macro_bars
    engine.external_contexts = base_engine.external_contexts
    engine.category_references = base_engine.category_references
    batches = []
    samples = 0
    for sample_tuple in engine.iter_ready_sample_batches(batch_size=batch_size, blocks=tuple(blocks)):
        batch_start_samples = samples
        batch_size_actual = len(sample_tuple)

        def progress_callback(stage: str, done: int, total: int) -> None:
            if state is None:
                return
            _update_worker_materialization_progress(
                state,
                stage=str(stage),
                done=int(done),
                total=int(total),
                completed_samples=batch_start_samples,
                batch_samples=batch_size_actual,
                state_started=state_started,
            )

        batch = engine.materialize_training_batch(
            sample_tuple,
            batch_id=slice_id,
            progress_callback=progress_callback,
            independent_stage_workers=max(1, int(stage_workers)),
        )
        batches.append(batch)
        batch_samples = int(batch.headers_uint8.shape[0])
        samples += batch_samples
        if state is not None:
            state.materializing_done = samples
            state.batches_done = len(batches)
            state.seconds = time.perf_counter() - state_started
            state.last_message = f"materialized {samples:,}/{state.materializing_total:,}"
    return {
        "worker_id": worker_id,
        "slice_id": slice_id,
        "batches": batches,
        "batch_count": len(batches),
        "samples": samples,
        "seconds": time.perf_counter() - started,
    }


def _materialize_ready_slice(
    *,
    args: argparse.Namespace,
    cache_root: Path,
    engine: RollingMarketSampleEngine,
    executor: ThreadPoolExecutor,
    writer: RollingMaterializedShardWriter,
    stats: BuildStats,
    profiler: StreamingProfiler,
    dashboard: BuildDashboard,
    start_us: int,
    end_us: int,
    block: StreamingEventBlock,
    block_start: dt.date,
    block_end: dt.date,
    written_identities: set[tuple[str, int]],
    submit_prefetches: Any,
) -> tuple[bool, bool]:
    stats.phase = "building ready index"
    _refresh(stats, dashboard, writer)
    ready_sample_cap = _ready_sample_cap_for_budget(args=args, stats=stats, writer=writer)
    with profiler.stage("ready_index_build", block_index=stats.block_index, max_samples=ready_sample_cap):
        ready_blocks = engine.build_ready_index_blocks(max_samples=ready_sample_cap)
    _enforce_rss_limit(args, stats, label="after_ready_index_build")
    stats.ready_samples = engine.ready_index_count(ready_blocks)
    with profiler.stage("ready_index_origin_window_filter", block_index=stats.block_index, samples=stats.ready_samples):
        eligible_blocks, origin_filter = _filter_ready_blocks_by_origin_window(
            ready_blocks,
            start_timestamp_us=start_us,
            end_timestamp_us=end_us,
        )
    stats.eligible_samples = int(origin_filter["eligible"])
    stats.skipped_before_start_samples += int(origin_filter["before_start"])
    stats.skipped_at_or_after_end_samples += int(origin_filter["at_or_after_end"])
    stats.message(
        f"ready samples={stats.ready_samples:,} eligible={stats.eligible_samples:,} "
        f"skip_before={int(origin_filter['before_start']):,} skip_after={int(origin_filter['at_or_after_end']):,} "
        f"blocks={len(eligible_blocks):,}"
    )
    if not ready_blocks:
        return False, False
    if not eligible_blocks:
        with profiler.stage("mark_ready_blocks_processed", block_index=stats.block_index, skipped_samples=stats.ready_samples):
            _mark_ready_blocks_processed(engine, ready_blocks)
        with profiler.stage("event_cache_trim_processed_tails", block_index=stats.block_index):
            engine.trim_processed_tails()
        _enforce_rss_limit(args, stats, label="after_event_cache_trim")
        all_after_end = int(origin_filter["at_or_after_end"]) >= int(origin_filter["total"]) > 0
        if all_after_end or block.min_timestamp_us >= end_us:
            stats.message("reached cache period end; no eligible origins remain")
            return True, True
        return True, False

    with profiler.stage("global_today_asof_cache_prewarm", block_index=stats.block_index):
        warmed_today_states = engine.prewarm_today_asof_day_cache(symbols=engine.config.global_symbols)
    stats.message(f"global today-asof cache states={warmed_today_states:,}")
    if not _ready_samples_can_satisfy_target(stats=stats, writer=writer, ready_samples=stats.eligible_samples):
        submit_prefetches()
    stats.phase = "materializing"
    futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
    next_slice_id = 0
    next_to_write = 0
    completed: dict[int, dict[str, Any]] = {}
    task_specs = _build_task_specs(
        eligible_blocks,
        batch_size=max(1, int(args.builder_batch_size)),
        workers=max(1, int(args.materialization_workers)),
    )
    task_queue = deque(task_specs)
    worker_task_samples = [0 for _index in range(max(1, int(args.materialization_workers)))]
    for worker_id, blocks_for_task in task_specs:
        worker_task_samples[int(worker_id)] += sum(block.sample_count for block in blocks_for_task)
    stats.message(
        f"ordered worker tasks active={sum(1 for value in worker_task_samples if value):,}/{len(worker_task_samples):,} "
        f"samples={','.join(str(int(value)) for value in worker_task_samples)}"
    )
    stop_requested = False

    def submit_available() -> None:
        nonlocal next_slice_id
        max_parallel = max(1, min(int(args.max_pending_tasks), max(1, int(args.materialization_workers))))
        while task_queue and len(futures) < max_parallel:
            active_workers = {int(meta["worker_id"]) for meta in futures.values()}
            selected: tuple[int, list[RollingReadyIndexBlock]] | None = None
            for _ in range(len(task_queue)):
                candidate_worker_id, candidate_blocks = task_queue.popleft()
                if int(candidate_worker_id) in active_workers:
                    task_queue.append((candidate_worker_id, candidate_blocks))
                    continue
                selected = (int(candidate_worker_id), candidate_blocks)
                break
            if selected is None:
                return
            worker_id, blocks_for_task = selected
            worker_state = stats.workers[worker_id]
            worker_state.status = "queued"
            worker_state.ticker_count = len({block.ticker for block in blocks_for_task})
            worker_state.queued_samples = sum(block.sample_count for block in blocks_for_task)
            worker_state.materializing_done = 0
            worker_state.materializing_total = max(1, worker_state.queued_samples)
            worker_state.encoding_done = 0
            worker_state.encoding_total = 0
            worker_state.features_done = 0
            worker_state.features_total = 0
            worker_state.labels_done = 0
            worker_state.labels_total = 0
            worker_state.context_done = 0
            worker_state.context_total = 0
            worker_state.text_done = 0
            worker_state.text_total = 0
            worker_state.xbrl_done = 0
            worker_state.xbrl_total = 0
            worker_state.writing_done = 0
            worker_state.writing_total = max(1, worker_state.queued_samples)
            worker_state.batches_done = 0
            worker_state.samples_done = 0
            worker_state.active_slice_id = next_slice_id
            worker_state.started_at = time.perf_counter()
            worker_state.seconds = 0.0
            worker_state.current_stage = "queued"
            worker_state.last_message = f"queued slice={next_slice_id} samples={worker_state.queued_samples:,}"
            future = executor.submit(
                _materialize_worker_task,
                engine,
                blocks_for_task,
                int(args.builder_batch_size),
                int(args.stage_workers),
                worker_id,
                next_slice_id,
                worker_state,
            )
            futures[future] = {"worker_id": worker_id, "slice_id": next_slice_id, "submitted_at": time.perf_counter()}
            stats.submitted_tasks += 1
            next_slice_id += 1

    submit_available()
    while futures or completed:
        done, _pending = wait(set(futures), timeout=max(0.5, float(args.refresh_seconds)), return_when=FIRST_COMPLETED)
        if not done:
            _write_progress(cache_root, args.split, stats, writer)
            _refresh(stats, dashboard, writer)
            continue
        for future in done:
            meta = futures.pop(future)
            result = future.result()
            _enforce_rss_limit(args, stats, label="after_materialize_task")
            worker_id = int(meta["worker_id"])
            worker_state = stats.workers[worker_id]
            worker_state.status = "materialized"
            worker_state.current_stage = "materialized"
            worker_state.materializing_done = int(result["samples"])
            worker_state.materializing_total = max(worker_state.materializing_total, int(result["samples"]))
            worker_state.batches_done = int(result["batch_count"])
            worker_state.seconds = time.perf_counter() - max(worker_state.started_at, 1e-9)
            worker_state.last_message = f"materialized samples={int(result['samples']):,} seconds={float(result['seconds']):.1f}"
            completed[int(meta["slice_id"])] = result
            stats.completed_tasks += 1
        while next_to_write in completed:
            result = completed.pop(next_to_write)
            write_worker = stats.workers[int(result["worker_id"])]
            write_worker.status = "writing"
            write_worker.current_stage = "writing"
            write_worker.writing_done = 0
            write_worker.writing_total = max(1, int(result["samples"]))
            for batch in result["batches"]:
                _assert_batch_origin_window(batch, start_timestamp_us=start_us, end_timestamp_us=end_us)
                _assert_batch_new_identities(batch, written_identities)
                writer.set_source_block(
                    block_index=stats.block_index,
                    start_timestamp_us=max(start_us, block.min_timestamp_us),
                    end_timestamp_us=min(end_us, block.max_timestamp_us),
                    start_label=block_start.isoformat(),
                    end_label=block_end.isoformat(),
                )
                writer.write_batch(batch)
                _enforce_rss_limit(args, stats, label="after_write_batch")
                batch_samples = int(batch.headers_uint8.shape[0])
                stats.samples_written += batch_samples
                stats.bytes_written += batch_nbytes(batch)
                write_worker.writing_done += batch_samples
                write_worker.samples_done += batch_samples
                write_worker.seconds = time.perf_counter() - max(write_worker.started_at, 1e-9)
                write_worker.last_message = f"wrote {write_worker.writing_done:,}/{write_worker.writing_total:,}"
                _write_progress(cache_root, args.split, stats, writer)
                _refresh(stats, dashboard, writer)
                if args.one_shard and len(writer.shards) >= 1:
                    stop_requested = True
                    stats.message(
                        "one-shard finalized first complete shard; stopping",
                        completed_shards=len(writer.shards),
                        current_shard_samples=writer.current_shard_samples,
                    )
                    break
            write_worker.status = "done"
            write_worker.current_stage = "done"
            write_worker.last_message = f"done samples={write_worker.samples_done:,}"
            next_to_write += 1
            if stop_requested:
                break
        _write_progress(cache_root, args.split, stats, writer)
        _refresh(stats, dashboard, writer)
        if stop_requested:
            break
        submit_available()
    with profiler.stage("mark_ready_blocks_processed", block_index=stats.block_index):
        _mark_ready_blocks_processed(engine, ready_blocks)
    with profiler.stage("event_cache_trim_processed_tails", block_index=stats.block_index):
        engine.trim_processed_tails()
    _enforce_rss_limit(args, stats, label="after_event_cache_trim")
    stats.shards_done = len(writer.shards)
    if stop_requested:
        return True, True
    if float(stats.bytes_written) >= float(args.target_cache_gib) * 1024**3:
        stats.message("target cache size reached")
        return True, True
    return True, False


def _update_worker_materialization_progress(
    state: WorkerState,
    *,
    stage: str,
    done: int,
    total: int,
    completed_samples: int,
    batch_samples: int,
    state_started: float,
) -> None:
    safe_total = max(1, int(total))
    safe_done = min(max(0, int(done)), safe_total)
    stage_name = str(stage)
    stage_group = stage_name.split(":", 1)[0]
    if stage_group == "encode":
        state.encoding_done = safe_done
        state.encoding_total = safe_total
    elif stage_group == "features":
        state.features_done = safe_done
        state.features_total = safe_total
    elif stage_group == "labels":
        state.labels_done = safe_done
        state.labels_total = safe_total
    elif stage_group == "context":
        state.context_done = safe_done
        state.context_total = safe_total
    elif stage_group == "text":
        state.text_done = safe_done
        state.text_total = safe_total
    elif stage_group == "xbrl":
        state.xbrl_done = safe_done
        state.xbrl_total = safe_total
    state.current_stage = stage_name
    batch_fraction = _weighted_materialization_fraction(stage_group, safe_done, safe_total)
    estimated_batch_done = int(round(float(max(0, int(batch_samples))) * batch_fraction))
    state.materializing_done = min(
        max(1, int(state.materializing_total)),
        int(completed_samples) + max(0, estimated_batch_done),
    )
    state.seconds = time.perf_counter() - state_started
    state.last_message = f"{stage_name} {safe_done:,}/{safe_total:,}"


def _weighted_materialization_fraction(stage: str, done: int, total: int) -> float:
    weights = (
        ("encode", 0.55),
        ("features", 0.10),
        ("labels", 0.10),
        ("context", 0.05),
        ("text", 0.15),
        ("xbrl", 0.05),
    )
    stage_name = str(stage)
    completed = 0.0
    for name, weight in weights:
        if name == stage_name:
            stage_fraction = min(1.0, max(0.0, float(done) / float(max(1, int(total)))))
            return min(1.0, completed + float(weight) * stage_fraction)
        completed += float(weight)
    return min(1.0, completed)


def _build_task_specs(
    blocks: tuple[RollingReadyIndexBlock, ...],
    *,
    batch_size: int,
    workers: int,
) -> list[tuple[int, list[RollingReadyIndexBlock]]]:
    specs: list[tuple[int, list[RollingReadyIndexBlock]]] = []
    worker_count = max(1, int(workers))
    current: list[RollingReadyIndexBlock] = []
    current_samples = 0
    for block in blocks:
        offset = 0
        while offset < block.origin_offsets.shape[0]:
            remaining = max(1, int(batch_size) - current_samples)
            take = min(remaining, int(block.origin_offsets.shape[0] - offset))
            sliced = RollingReadyIndexBlock(
                ticker=block.ticker,
                rows=block.rows,
                origin_offsets=block.origin_offsets[offset : offset + take],
            )
            current.append(sliced)
            current_samples += int(take)
            offset += int(take)
            if current_samples >= int(batch_size):
                specs.append((len(specs) % worker_count, current))
                current = []
                current_samples = 0
    if current:
        specs.append((len(specs) % worker_count, current))
    return specs


def _mark_ready_blocks_processed(engine: RollingMarketSampleEngine, blocks: tuple[RollingReadyIndexBlock, ...]) -> None:
    for block in blocks:
        if block.origin_offsets.size == 0:
            continue
        ticker = str(block.ticker).upper()
        max_offset = int(block.origin_offsets[-1]) + 1
        engine._processed_offsets[ticker] = max(int(engine._processed_offsets.get(ticker, 0)), max_offset)
        ordinals = block.rows["ordinal"].astype(np.int64, copy=False)
        max_ordinal = int(ordinals[block.origin_offsets].max())
        engine._processed_origin_ordinals[ticker] = max(
            int(engine._processed_origin_ordinals.get(ticker, -1)),
            max_ordinal,
        )


def _trim_pre_start_event_cache(engine: RollingMarketSampleEngine, start_timestamp_us: int) -> None:
    keep_tail = max(0, int(engine.config.carryover_events))
    for ticker, rows in list(engine.rows_by_ticker.items()):
        if rows.size == 0:
            continue
        pre_start_count = int(np.searchsorted(rows["sip_timestamp_us"], int(start_timestamp_us), side="left"))
        trim_to = max(0, pre_start_count - keep_tail)
        if trim_to <= 0:
            continue
        engine.rows_by_ticker[ticker] = rows[trim_to:].copy()
        engine._processed_offsets[ticker] = 0


def _resolve_end_timestamp_us(args: argparse.Namespace, start_us: int) -> int:
    if int(args.end_timestamp_us) > 0:
        return int(args.end_timestamp_us)
    if str(args.end_utc).strip():
        return parse_utc_us(str(args.end_utc))
    return start_us + max(1, int(args.days)) * 86_400_000_000


def _exclusive_timestamp_end_date(end_timestamp_us: int) -> dt.date:
    return date_from_us(max(0, int(end_timestamp_us) - 1)) + dt.timedelta(days=1)


def _run_final_audit(*, args: argparse.Namespace, cache_root: Path, start_us: int, end_us: int) -> Any:
    from research.mlops.rolling_loader.audit_materialized_cache import (
        MaterializedCacheAuditConfig,
        run_audit,
    )

    return run_audit(
        MaterializedCacheAuditConfig(
            cache_root=cache_root,
            split=args.split,
            start_timestamp_us=int(start_us),
            end_timestamp_us=int(end_us),
            sample_shards=max(0, int(args.audit_sample_shards)),
            samples_per_shard=max(0, int(args.audit_samples_per_shard)),
            zero_sample_rows=max(0, int(args.audit_zero_sample_rows)),
            seed=17,
            source_checks=bool(args.audit_source_checks),
            hash_files=False,
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


def _has_explicit_end_arg(argv: list[str] | None = None) -> bool:
    values = sys.argv[1:] if argv is None else argv
    return any(
        value == "--end-utc"
        or value.startswith("--end-utc=")
        or value == "--end-timestamp-us"
        or value.startswith("--end-timestamp-us=")
        for value in values
    )


def _manifest(
    args: argparse.Namespace,
    cache_id: str,
    cache_root: Path,
    loaded_env: list[Path],
    start_us: int,
    end_us: int,
    config: RollingMarketDataConfig,
) -> dict[str, Any]:
    return {
        "format": MATERIALIZED_CACHE_FORMAT,
        "version": MATERIALIZED_CACHE_VERSION,
        "cache_id": cache_id,
        "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "cache_root": str(cache_root),
        "source": {
            "database": args.database,
            "events_table": args.events_table,
            "macro_bars_table": args.macro_bars_table,
            "news_token_table": args.news_token_table,
            "sec_filing_text_token_table": args.sec_filing_text_token_table,
            "sec_xbrl_context_table": args.sec_xbrl_context_table,
        },
        "date_range": {
            "start_timestamp_us": int(start_us),
            "end_timestamp_us": int(end_us),
            "start_utc": timestamp_us_to_utc(start_us),
            "end_utc": timestamp_us_to_utc(end_us),
        },
        "loaded_env_files": [str(path) for path in loaded_env],
        "config": _jsonable(_redacted_args(args))
        | {
            "q_live_contexts": list(config.q_live_contexts),
            "macro_timeframes": list(config.macro_timeframes),
            "label_timeframes": list(config.label_timeframes),
            "events_per_chunk": int(config.events_per_chunk),
            "context_lags": list(config.context_lags),
        },
        "shards": [],
    }


def _write_progress(cache_root: Path, split: str, stats: BuildStats, writer: RollingMaterializedShardWriter | None) -> None:
    elapsed = time.perf_counter() - stats.started
    rate = stats.samples_written / max(elapsed, 1e-9)
    byte_rate = stats.bytes_written / max(elapsed, 1e-9)
    remaining_bytes = max(0, int(stats.target_bytes) - int(stats.bytes_written))
    eta_seconds = remaining_bytes / byte_rate if stats.target_bytes > 0 and byte_rate > 0 else None
    progress = {
        "split": split,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "phase": stats.phase,
        "block_index": stats.block_index,
        "prefetch_depth": stats.prefetch_depth,
        "total_workers": stats.total_workers,
        "materialization_workers": stats.materialization_workers,
        "stage_workers": stats.stage_workers,
        "max_rss_mib": stats.max_rss_mib,
        "prefetch_pending": stats.prefetch_pending,
        "prefetch_ready": stats.prefetch_ready,
        "prefetch_next_block": stats.prefetch_next_block,
        "prefetch_last_seconds": stats.prefetch_last_seconds,
        "ready_samples": stats.ready_samples,
        "eligible_samples": stats.eligible_samples,
        "skipped_before_start_samples": stats.skipped_before_start_samples,
        "skipped_at_or_after_end_samples": stats.skipped_at_or_after_end_samples,
        "samples_written": stats.samples_written,
        "bytes_written": stats.bytes_written,
        "target_bytes": stats.target_bytes,
        "remaining_bytes": remaining_bytes,
        "elapsed_seconds": elapsed,
        "samples_per_second": rate,
        "bytes_per_second": byte_rate,
        "eta_seconds": eta_seconds,
        "eta_minutes": eta_seconds / 60.0 if eta_seconds is not None else None,
        "eta_hours": eta_seconds / 3600.0 if eta_seconds is not None else None,
        "shards_done": len(writer.shards) if writer is not None else 0,
        "current_shard": writer.current_shard_status() if writer is not None else {},
        "workers": {str(key): asdict(worker) for key, worker in stats.workers.items()},
        "messages": stats.messages,
    }
    tmp = cache_root / f"{split}_progress.json.tmp"
    final = cache_root / f"{split}_progress.json"
    tmp.write_text(json.dumps(_jsonable(progress), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(final)


def _refresh(stats: BuildStats, dashboard: "MaterializedCacheDashboard", writer: RollingMaterializedShardWriter | None, *, force: bool = False) -> None:
    stats.current_rss_mib = current_rss_mib()
    stats.shards_done = len(writer.shards) if writer is not None else stats.shards_done
    dashboard.refresh(force=force)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _redacted_args(args: argparse.Namespace) -> dict[str, Any]:
    out = dict(vars(args))
    for key in list(out):
        upper = key.upper()
        if any(token in upper for token in ("PASSWORD", "TOKEN", "SECRET", "KEY")):
            out[key] = "<present>" if out.get(key) else ""
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "estimating"
    if seconds < 0:
        seconds = 0.0
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_rate_bytes(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "0 B/s"
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    value = float(bytes_per_second)
    unit = units[0]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    return f"{value:,.1f} {unit}"


def _format_rss(current_mib: float, limit_mib: float = 0.0) -> str:
    if float(limit_mib) > 0.0:
        return f"{float(current_mib) / 1024.0:,.1f}/{float(limit_mib) / 1024.0:,.1f} GiB"
    return f"{float(current_mib):,.0f} MiB"


def _worker_stage_label(worker: WorkerState, *, compact: bool) -> str:
    stage = str(worker.current_stage or worker.status or "idle")
    if compact:
        names = {
            "materializing": "mat",
            "features": "feat",
            "labels": "lbl",
            "context": "ctx",
            "writing": "write",
        }
        return names.get(stage, stage)[:7]
    return stage[:10]


def _single_line(value: Any, *, width: int) -> str:
    text = " ".join(str(value or "").split())
    if width <= 0 or len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "."


def _single_line_items(values: list[str], *, width: int) -> list[str]:
    return [_single_line(value, width=width) for value in values]


class MaterializedCacheDashboard:
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

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.live:
            return
        try:
            from rich import box
            from rich.console import Console
            from rich.console import Group
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
        except Exception:
            self.enabled = False
            return
        self._rich = {
            "box": box,
            "Console": Console,
            "Group": Group,
            "Live": Live,
            "Panel": Panel,
            "Table": Table,
            "Text": Text,
        }
        console = Console()
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=max(0.5, 1.0 / self.refresh_seconds),
            auto_refresh=False,
            screen=True,
            transient=False,
            vertical_overflow="visible",
        )
        self._live.start()

    def refresh(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if self.enabled and self._live is not None:
            if force or now - self._last >= self.refresh_seconds:
                self._live.update(self._render(), refresh=True)
                self._last = now
        elif self.enabled and not self.live:
            if force or now - self._last >= self.refresh_seconds:
                self._print_status_line(final=False)
                self._last = now
        elif force or now - self._last >= self.refresh_seconds:
            elapsed = now - self.stats.started
            byte_rate = self.stats.bytes_written / max(elapsed, 1e-9)
            remaining = max(0, int(self.stats.target_bytes) - int(self.stats.bytes_written))
            eta = remaining / byte_rate if self.stats.target_bytes > 0 and byte_rate > 0 else None
            print(
                f"[{self.stats.phase}] samples={self.stats.samples_written:,} shards={self.stats.shards_done:,} "
                f"rss={_format_rss(self.stats.current_rss_mib, self.stats.max_rss_mib)} "
                f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}",
                flush=True,
            )
            self._last = now

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            self._live.stop()
            self._live = None
        elif self.enabled and not self.live:
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
        byte_rate = self.stats.bytes_written / max(elapsed, 1e-9)
        remaining = max(0, int(self.stats.target_bytes) - int(self.stats.bytes_written))
        eta = remaining / byte_rate if self.stats.target_bytes > 0 and byte_rate > 0 else None
        worker_bits = []
        for worker in self.stats.workers.values():
            total_done = int(worker.materializing_done) + int(worker.writing_done)
            total_expected = int(worker.materializing_total) + int(worker.writing_total)
            pct = 0.0 if total_expected <= 0 else 100.0 * float(total_done) / float(max(1, total_expected))
            stage = str(worker.current_stage or worker.status)[:4]
            worker_bits.append(f"W{worker.worker_id}:{stage}:{pct:03.0f}%")
        text = (
            f"[{self.stats.phase}] block={self.stats.block_index} rows={self.stats.rows_loaded:,} "
            f"tickers={self.stats.tickers_loaded:,} ready={self.stats.ready_samples:,}/{self.stats.eligible_samples:,} "
            f"skip={self.stats.skipped_before_start_samples:,}/{self.stats.skipped_at_or_after_end_samples:,} "
            f"prefetch={self.stats.prefetch_ready}/{self.stats.prefetch_pending}/{self.stats.prefetch_depth} "
            f"samples={self.stats.samples_written:,} shards={self.stats.shards_done:,} "
            f"size={self.stats.bytes_written / 1024**3:.2f}/{max(1, self.stats.target_bytes) / 1024**3:.2f}GiB "
            f"rate={_format_rate_bytes(byte_rate)} rss={_format_rss(self.stats.current_rss_mib, self.stats.max_rss_mib)} "
            f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}"
        )
        if worker_bits:
            text = f"{text} {' '.join(worker_bits)}"
        return _single_line(text, width=width)

    def _render(self) -> Any:
        box = self._rich["box"]
        Group = self._rich["Group"]
        Panel = self._rich["Panel"]
        Table = self._rich["Table"]
        Text = self._rich["Text"]
        elapsed = time.perf_counter() - self.stats.started
        rate = self.stats.samples_written / max(elapsed, 1e-9)
        byte_rate = self.stats.bytes_written / max(elapsed, 1e-9)
        target_bytes = max(1, int(self.stats.target_bytes))
        remaining_bytes = max(0, target_bytes - int(self.stats.bytes_written))
        eta_seconds = remaining_bytes / byte_rate if byte_rate > 0 else None
        progress_pct = min(100.0, 100.0 * float(self.stats.bytes_written) / float(target_bytes))
        terminal_size = shutil.get_terminal_size((120, 40))
        terminal_width = terminal_size.columns
        terminal_height = terminal_size.lines
        compact = terminal_width < 140 or terminal_height < 26
        summary = Table.grid(expand=False)
        pair_count = 2 if compact else 3
        for pair_index in range(pair_count):
            if pair_index:
                summary.add_column(no_wrap=True, width=3)
            summary.add_column(justify="right", style="dim", no_wrap=True)
            summary.add_column(no_wrap=True)
        summary_rows = [
            (
                ("Phase", self.stats.phase),
                ("Elapsed", _format_duration(elapsed)),
                ("RSS", _format_rss(self.stats.current_rss_mib, self.stats.max_rss_mib)),
            ),
            (
                ("Block", str(self.stats.block_index)),
                ("Window", f"{self.stats.block_start}->{self.stats.block_end}"),
                ("Ready", f"{self.stats.ready_samples:,}"),
            ),
            (
                ("Eligible", f"{self.stats.eligible_samples:,}"),
                ("Skip<Start", f"{self.stats.skipped_before_start_samples:,}"),
                ("Skip>=End", f"{self.stats.skipped_at_or_after_end_samples:,}"),
            ),
            (
                ("Rows", f"{self.stats.rows_loaded:,}"),
                ("Tickers", f"{self.stats.tickers_loaded:,}"),
                ("Samples", f"{self.stats.samples_written:,}"),
            ),
            (
                ("Shards", f"{self.stats.shards_done:,}"),
                ("Size", f"{self.stats.bytes_written / 1024**3:.2f}/{target_bytes / 1024**3:.2f} GiB"),
                ("Rate", f"{rate:,.1f}/s"),
            ),
            (
                ("Bytes/s", _format_rate_bytes(byte_rate)),
                ("Remain", f"{remaining_bytes / 1024**3:.2f} GiB"),
                ("ETA", _format_duration(eta_seconds)),
            ),
            (
                ("Progress", f"{progress_pct:.1f}%"),
                ("Tasks", f"{self.stats.completed_tasks:,}/{self.stats.submitted_tasks:,}"),
                ("Logs", str(self.stats.log_path or "")),
            ),
            (
                ("Prefetch", f"{self.stats.prefetch_ready}/{self.stats.prefetch_pending}/{self.stats.prefetch_depth}"),
                ("Workers", f"{self.stats.total_workers}->{self.stats.materialization_workers}x{self.stats.stage_workers}"),
                ("Fetch", f"{self.stats.prefetch_last_seconds:.1f}s"),
            ),
        ]
        if terminal_height < 22:
            summary_rows = summary_rows[:4]
        for row in summary_rows:
            cells: list[Any] = []
            for pair_index, (label, value) in enumerate(row[:pair_count]):
                if pair_index:
                    cells.append("")
                cells.extend([f"{label}:", f" {value}"])
            summary.add_row(*cells)

        workers = Table(expand=True)
        workers.add_column("W", no_wrap=True, width=3 if compact else 6)
        workers.add_column("Stage", no_wrap=True, width=7 if compact else 10)
        workers.add_column("Enc", no_wrap=True, min_width=8 if compact else 16, ratio=1)
        workers.add_column("Feat", no_wrap=True, min_width=8 if compact else 14, ratio=1)
        workers.add_column("Lbl", no_wrap=True, min_width=8 if compact else 14, ratio=1)
        workers.add_column("Ctx", no_wrap=True, min_width=8 if compact else 14, ratio=1)
        workers.add_column("Text", no_wrap=True, min_width=8 if compact else 14, ratio=1)
        workers.add_column("XBRL", no_wrap=True, min_width=8 if compact else 14, ratio=1)
        workers.add_column("Write", no_wrap=True, min_width=8 if compact else 16, ratio=1)
        workers.add_column("Total", no_wrap=True, min_width=8 if compact else 16, ratio=1)
        if not compact:
            workers.add_column("Tickers", no_wrap=True, justify="right", width=8)
        workers.add_column("Elapsed", no_wrap=True, width=7 if compact else 8)
        workers.add_column("Message", no_wrap=True, overflow="ellipsis", ratio=2)
        for worker in self.stats.workers.values():
            total_done = int(worker.materializing_done) + int(worker.writing_done)
            total_expected = int(worker.materializing_total) + int(worker.writing_total)
            row = [
                str(worker.worker_id),
                _worker_stage_label(worker, compact=compact),
                self._progress_cell(
                    done=worker.encoding_done,
                    total=worker.encoding_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.features_done,
                    total=worker.features_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.labels_done,
                    total=worker.labels_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.context_done,
                    total=worker.context_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.text_done,
                    total=worker.text_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.xbrl_done,
                    total=worker.xbrl_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=worker.writing_done,
                    total=worker.writing_total,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
                self._progress_cell(
                    done=total_done,
                    total=total_expected,
                    elapsed_seconds=worker.seconds,
                    Text=Text,
                    compact=compact,
                ),
            ]
            if not compact:
                row.append(f"{worker.ticker_count:,}")
            message_width = 28 if compact else 48
            row.extend([_format_duration(worker.seconds), _single_line(worker.last_message, width=message_width)])
            workers.add_row(*row)

        summary_panel_rows = len(summary_rows) + 2
        workers_panel_rows = max(1, len(self.stats.workers)) + 6
        message_rows = max(0, min(8, terminal_height - summary_panel_rows - workers_panel_rows - 2))
        message_panel_height = message_rows + 2

        messages = Table.grid(expand=True)
        messages.add_column(no_wrap=True, overflow="ellipsis")
        visible_messages = self.stats.messages[-message_rows:] if message_rows > 0 else []
        padded_messages = [*_single_line_items(visible_messages, width=max(40, terminal_width - 8)), *[""] * message_rows][:message_rows]
        for message in padded_messages:
            messages.add_row(message)

        return Group(
            Panel(summary, title="Rolling Materialized Cache", box=box.ROUNDED, border_style="cyan", padding=(0, 1)),
            Panel(workers, title="Workers", box=box.ROUNDED, border_style="magenta", padding=(0, 1)),
            Panel(messages, title="Messages", box=box.ROUNDED, border_style="green", padding=(0, 1), height=message_panel_height),
        )

    @staticmethod
    def _progress_cell(
        *,
        done: int,
        total: int,
        elapsed_seconds: float,
        Text: Any,
        compact: bool,
    ) -> Any:
        if int(total) <= 0:
            return Text("-", style="dim")
        safe_total = max(1, int(total))
        safe_done = min(max(0, int(done)), safe_total)
        rate = safe_done / max(float(elapsed_seconds), 1e-9)
        remaining = max(0, safe_total - safe_done)
        eta = remaining / rate if safe_done > 0 and rate > 0 else None
        pct = 100.0 * float(safe_done) / float(safe_total)
        bar_width = 4 if compact else 10
        filled = min(bar_width, int(round((pct / 100.0) * bar_width)))
        bar = "#" * filled + "-" * (bar_width - filled)
        style = "green" if safe_done >= safe_total else "cyan"
        if compact:
            return Text(f"{bar}{pct:02.0f}%", style=style)
        return Text(f"[{bar}] {safe_done:,}/{safe_total:,} {pct:4.0f}% eta {_format_duration(eta)}", style=style)


if __name__ == "__main__":
    raise SystemExit(main())
