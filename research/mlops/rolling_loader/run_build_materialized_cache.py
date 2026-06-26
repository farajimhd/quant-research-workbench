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
    partition_ready_blocks,
    timestamp_us_to_utc,
)
from research.mlops.rolling_loader.streaming_training import (
    StreamingClickHouseTrainingSource,
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
    "warmup_days": 0,
    "builder_batch_size": 4096,
    "sample_multiple": 4096,
    "workers": 4,
    "max_pending_tasks": 8,
    "shard_size_gib": 16.0,
    "target_cache_gib": 16.0,
    "ready_sample_cap": 65536,
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "80G",
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
    writing_done: int = 0
    writing_total: int = 0
    batches_done: int = 0
    samples_done: int = 0
    active_slice_id: int = -1
    started_at: float = 0.0
    seconds: float = 0.0
    last_message: str = ""


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
    submitted_tasks: int = 0
    completed_tasks: int = 0
    samples_written: int = 0
    bytes_written: int = 0
    target_bytes: int = 0
    shards_done: int = 0
    current_rss_mib: float = 0.0
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
    parser.add_argument("--workers", type=int, default=DEFAULTS["workers"])
    parser.add_argument("--max-pending-tasks", type=int, default=DEFAULTS["max_pending_tasks"])
    parser.add_argument("--shard-size-gib", type=float, default=DEFAULTS["shard_size_gib"])
    parser.add_argument("--target-cache-gib", type=float, default=DEFAULTS["target_cache_gib"])
    parser.add_argument("--ready-sample-cap", type=int, default=DEFAULTS["ready_sample_cap"])
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
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
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loaded_env = load_env_files(
        discover_clickhouse_env_files() if args.env_file is None else discover_clickhouse_env_files() + [args.env_file]
    )
    start_us = int(args.start_timestamp_us or parse_utc_us(args.start_utc))
    end_us = _resolve_end_timestamp_us(args, start_us)
    if args.one_shard and not _has_explicit_end_arg() and int(args.days) == int(DEFAULTS["days"]):
        one_shard_end_us = start_us + max(1, int(args.one_shard_max_days)) * 86_400_000_000 - 1
        end_us = max(end_us, one_shard_end_us)
    cache_id = args.cache_id.strip() or dt.datetime.now(tz=dt.timezone.utc).strftime("rolling_cache_%Y%m%d_%H%M%S")
    cache_root = Path(args.cache_root) / cache_id
    split_dir = cache_root / args.split
    cache_root.mkdir(parents=True, exist_ok=bool(args.resume))
    stats = BuildStats()
    stats.target_bytes = int(float(args.target_cache_gib) * 1024**3)
    stats.log_path = cache_root / "builder_events.jsonl"
    dashboard = MaterializedCacheDashboard(enabled=not args.no_rich, refresh_seconds=float(args.refresh_seconds), stats=stats)
    source: StreamingClickHouseTrainingSource | None = None
    writer: RollingMaterializedShardWriter | None = None
    executor: ThreadPoolExecutor | None = None

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
        )
        manifest = _manifest(args, cache_id, cache_root, loaded_env, start_us, end_us, config)
        manifest["resume"] = {"enabled": bool(args.resume), "removed_tmp_dirs": removed_tmp, "existing_shards": len(existing_shards)}
        _write_json(cache_root / "manifest.json", manifest)
        dashboard.start()

        stats.phase = "initializing"
        _refresh(stats, dashboard, writer)
        with profiler.stage("category_references_fetch"):
            refs = source.fetch_category_references()
        engine.load_category_references(refs)
        stats.message(f"loaded category refs rows={len(refs):,}")
        start_date = date_from_us(start_us)
        end_date = date_from_us(end_us) + dt.timedelta(days=1)
        with profiler.stage("macro_bars_1d_full_fetch"):
            macro = source.fetch_macro_bars_1d(start_date=start_date, end_date=end_date)
        engine.load_macro_bars(macro)
        stats.message(f"loaded 1d macro/global rows={len(macro.rows):,}")
        if not args.skip_token_contexts:
            with profiler.stage("initial_token_context_fetch"):
                initial_context = source.fetch_token_contexts(
                    start_timestamp_us=start_us,
                    end_timestamp_us=start_us + 1,
                    include_lookback=True,
                    include_xbrl=not args.skip_xbrl,
                )
            engine.load_external_contexts(initial_context.rows_by_context)
            stats.message(f"loaded initial context rows={initial_context.row_count:,}")

        executor = ThreadPoolExecutor(max_workers=max(1, int(args.workers)))
        for worker_id in range(max(1, int(args.workers))):
            stats.workers[worker_id] = WorkerState(worker_id=worker_id)
        cursor_date = date_from_us(start_us) - dt.timedelta(days=max(0, int(args.warmup_days)))
        final_date = date_from_us(end_us) + dt.timedelta(days=1)
        stop_requested = False
        while cursor_date < final_date and not stop_requested:
            block_start = cursor_date
            block_end = min(final_date, cursor_date + dt.timedelta(days=max(1, int(args.block_days))))
            stats.phase = "loading block"
            stats.block_start = block_start.isoformat()
            stats.block_end = block_end.isoformat()
            _refresh(stats, dashboard, writer)
            with profiler.stage("event_block_query_arrow_polars", block_index=stats.block_index):
                frame = source.fetch_event_frame(start_date=block_start, end_date=block_end, row_limit=max(0, int(args.event_row_limit)))
            with profiler.stage("event_block_polars_to_numpy", block_index=stats.block_index, rows=int(frame.height)):
                block = source.event_frame_to_block(frame=frame, start_date=block_start, end_date=block_end)
            stats.rows_loaded = block.row_count
            stats.tickers_loaded = block.ticker_count
            stats.message(f"block {stats.block_index} events rows={block.row_count:,} tickers={block.ticker_count:,}")
            if block.row_count <= 0:
                cursor_date = block_end
                stats.block_index += 1
                continue
            stats.phase = "updating cache"
            _refresh(stats, dashboard, writer)
            engine.append_rows_by_ticker(block.rows_by_ticker)
            if block.max_timestamp_us < start_us:
                _trim_pre_start_event_cache(engine, start_us)
                cursor_date = block_end
                stats.block_index += 1
                continue
            if not args.skip_token_contexts:
                with profiler.stage("token_context_block_fetch", block_index=stats.block_index):
                    context = source.fetch_token_contexts(
                        start_timestamp_us=max(start_us, block.min_timestamp_us),
                        end_timestamp_us=min(end_us + 1, block.max_timestamp_us + 1),
                        include_lookback=False,
                        include_xbrl=not args.skip_xbrl,
                    )
                engine.load_external_contexts(context.rows_by_context)
                stats.message(f"block context rows={context.row_count:,}")
            stats.phase = "building ready index"
            _refresh(stats, dashboard, writer)
            ready_blocks = engine.build_ready_index_blocks(max_samples=max(0, int(args.ready_sample_cap)))
            stats.ready_samples = engine.ready_index_count(ready_blocks)
            stats.message(f"ready samples={stats.ready_samples:,} blocks={len(ready_blocks):,}")
            if not ready_blocks:
                cursor_date = block_end
                stats.block_index += 1
                continue
            stats.phase = "materializing"
            worker_partitions = partition_ready_blocks(ready_blocks, max(1, int(args.workers)))
            futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
            next_slice_id = 0
            next_to_write = 0
            completed: dict[int, dict[str, Any]] = {}
            task_queue = deque(_build_task_specs(worker_partitions, batch_size=max(1, int(args.builder_batch_size))))
            partition_samples = [sum(block.sample_count for block in partition) for partition in worker_partitions]
            stats.message(
                f"worker partitions active={sum(1 for value in partition_samples if value):,}/{len(partition_samples):,} "
                f"samples={','.join(str(int(value)) for value in partition_samples)}"
            )

            def submit_available() -> None:
                nonlocal next_slice_id
                max_parallel = max(1, min(int(args.max_pending_tasks), max(1, int(args.workers))))
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
                    worker_state.writing_done = 0
                    worker_state.writing_total = max(1, worker_state.queued_samples)
                    worker_state.batches_done = 0
                    worker_state.samples_done = 0
                    worker_state.active_slice_id = next_slice_id
                    worker_state.started_at = time.perf_counter()
                    worker_state.seconds = 0.0
                    worker_state.last_message = f"queued slice={next_slice_id} samples={worker_state.queued_samples:,}"
                    future = executor.submit(
                        _materialize_worker_task,
                        engine,
                        blocks_for_task,
                        int(args.builder_batch_size),
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
                    worker_id = int(meta["worker_id"])
                    worker_state = stats.workers[worker_id]
                    worker_state.status = "materialized"
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
                    write_worker.writing_done = 0
                    write_worker.writing_total = max(1, int(result["samples"]))
                    for batch in result["batches"]:
                        writer.set_source_block(
                            block_index=stats.block_index,
                            start_timestamp_us=max(start_us, block.min_timestamp_us),
                            end_timestamp_us=min(end_us, block.max_timestamp_us),
                            start_label=block_start.isoformat(),
                            end_label=block_end.isoformat(),
                        )
                        writer.write_batch(batch)
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
                    write_worker.last_message = f"done samples={write_worker.samples_done:,}"
                    next_to_write += 1
                    if stop_requested:
                        break
                _write_progress(cache_root, args.split, stats, writer)
                _refresh(stats, dashboard, writer)
                if stop_requested:
                    break
                submit_available()
            _mark_ready_blocks_processed(engine, ready_blocks)
            engine.trim_processed_tails()
            cursor_date = block_end
            stats.block_index += 1
            stats.shards_done = len(writer.shards)
            if float(stats.bytes_written) >= float(args.target_cache_gib) * 1024**3:
                stats.message("target cache size reached")
                break
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
        stats.message("done")
        _refresh(stats, dashboard, writer)
        return 130 if stats.interrupted else 0
    except KeyboardInterrupt:
        stats.interrupted = True
        stats.phase = "interrupt"
        stats.message("interrupt received; closing writer and stopping workers")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if writer is not None:
            writer.close()
        _write_progress(cache_root, args.split, stats, writer)
        return 130
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if source is not None:
            source.close()
        dashboard.stop()


def _materialize_worker_task(
    base_engine: RollingMarketSampleEngine,
    blocks: list[RollingReadyIndexBlock],
    batch_size: int,
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
    tickers = {block.ticker for block in blocks}
    engine.rows_by_ticker = {ticker: base_engine.rows_by_ticker[ticker] for ticker in tickers if ticker in base_engine.rows_by_ticker}
    engine.macro_bars = base_engine.macro_bars
    engine.external_contexts = base_engine.external_contexts
    engine.category_references = base_engine.category_references
    batches = []
    samples = 0
    for sample_tuple in engine.iter_ready_sample_batches(batch_size=batch_size, blocks=tuple(blocks)):
        batch = engine.materialize_training_batch(sample_tuple, batch_id=slice_id)
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


def _build_task_specs(partitions: list[list[RollingReadyIndexBlock]], *, batch_size: int) -> list[tuple[int, list[RollingReadyIndexBlock]]]:
    per_worker: list[list[tuple[int, list[RollingReadyIndexBlock]]]] = []
    for worker_id, blocks in enumerate(partitions):
        worker_specs: list[tuple[int, list[RollingReadyIndexBlock]]] = []
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
                    worker_specs.append((worker_id, current))
                    current = []
                    current_samples = 0
        if current:
            worker_specs.append((worker_id, current))
        per_worker.append(worker_specs)
    specs: list[tuple[int, list[RollingReadyIndexBlock]]] = []
    max_worker_tasks = max((len(items) for items in per_worker), default=0)
    for task_index in range(max_worker_tasks):
        for worker_specs in per_worker:
            if task_index < len(worker_specs):
                specs.append(worker_specs[task_index])
    return specs


def _mark_ready_blocks_processed(engine: RollingMarketSampleEngine, blocks: tuple[RollingReadyIndexBlock, ...]) -> None:
    for block in blocks:
        if block.origin_offsets.size == 0:
            continue
        max_offset = int(block.origin_offsets[-1]) + 1
        engine._processed_offsets[block.ticker] = max(int(engine._processed_offsets.get(block.ticker, 0)), max_offset)


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
    return start_us + max(1, int(args.days)) * 86_400_000_000 - 1


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


def _refresh(stats: BuildStats, dashboard: "MaterializedCacheDashboard", writer: RollingMaterializedShardWriter | None) -> None:
    stats.current_rss_mib = current_rss_mib()
    stats.shards_done = len(writer.shards) if writer is not None else stats.shards_done
    dashboard.refresh()


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
    def __init__(self, *, enabled: bool, refresh_seconds: float, stats: BuildStats) -> None:
        self.enabled = bool(enabled)
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self.stats = stats
        self._last = 0.0
        self._live: Any | None = None
        self._rich: dict[str, Any] = {}

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            from rich import box
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
            "Group": Group,
            "Live": Live,
            "Panel": Panel,
            "Table": Table,
            "Text": Text,
        }
        self._live = Live(
            self._render(),
            auto_refresh=False,
            transient=False,
            vertical_overflow="crop",
        )
        self._live.start()

    def refresh(self) -> None:
        now = time.perf_counter()
        if self.enabled and self._live is not None:
            if now - self._last >= self.refresh_seconds:
                self._live.update(self._render(), refresh=True)
                self._last = now
        elif now - self._last >= self.refresh_seconds:
            elapsed = now - self.stats.started
            byte_rate = self.stats.bytes_written / max(elapsed, 1e-9)
            remaining = max(0, int(self.stats.target_bytes) - int(self.stats.bytes_written))
            eta = remaining / byte_rate if self.stats.target_bytes > 0 and byte_rate > 0 else None
            print(
                f"[{self.stats.phase}] samples={self.stats.samples_written:,} shards={self.stats.shards_done:,} "
                f"rss_mib={self.stats.current_rss_mib:.0f} elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}",
                flush=True,
            )
            self._last = now

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            self._live.stop()
            self._live = None

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
        terminal_width = shutil.get_terminal_size((120, 40)).columns
        compact = terminal_width < 140
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
                ("RSS", f"{self.stats.current_rss_mib:,.0f} MiB"),
            ),
            (
                ("Block", str(self.stats.block_index)),
                ("Window", f"{self.stats.block_start}->{self.stats.block_end}"),
                ("Ready", f"{self.stats.ready_samples:,}"),
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
        ]
        for row in summary_rows:
            cells: list[Any] = []
            for pair_index, (label, value) in enumerate(row[:pair_count]):
                if pair_index:
                    cells.append("")
                cells.extend([f"{label}:", f" {value}"])
            summary.add_row(*cells)

        workers = Table(expand=True)
        workers.add_column("W", no_wrap=True, width=3 if compact else 6)
        workers.add_column("Status", no_wrap=True, width=10 if compact else 13)
        workers.add_column("Mat", no_wrap=True, min_width=10 if compact else 26, ratio=2)
        workers.add_column("Write", no_wrap=True, min_width=10 if compact else 26, ratio=2)
        workers.add_column("Total", no_wrap=True, min_width=10 if compact else 26, ratio=2)
        if not compact:
            workers.add_column("Tickers", no_wrap=True, justify="right", width=8)
        workers.add_column("Elapsed", no_wrap=True, width=7 if compact else 8)
        workers.add_column("Message", no_wrap=True, overflow="ellipsis", ratio=2)
        for worker in self.stats.workers.values():
            total_done = int(worker.materializing_done) + int(worker.writing_done)
            total_expected = int(worker.materializing_total) + int(worker.writing_total)
            row = [
                str(worker.worker_id),
                worker.status[:10] if compact else worker.status,
                self._progress_cell(
                    done=worker.materializing_done,
                    total=worker.materializing_total,
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

        messages = Table.grid(expand=True)
        messages.add_column(no_wrap=True, overflow="ellipsis")
        padded_messages = [*_single_line_items(self.stats.messages[-8:], width=max(40, terminal_width - 8)), *[""] * 8][:8]
        for message in padded_messages:
            messages.add_row(message)

        return Group(
            Panel(summary, title="Rolling Materialized Cache", box=box.ROUNDED, border_style="cyan", padding=(0, 1)),
            Panel(workers, title="Workers", box=box.ROUNDED, border_style="magenta", padding=(0, 1)),
            Panel(messages, title="Messages", box=box.ROUNDED, border_style="green", padding=(0, 1)),
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
