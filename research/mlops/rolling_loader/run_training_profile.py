from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    clickhouse_env_status_keys,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.env import load_env_files, secret_status
from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.loader import RollingContextLoader
from research.mlops.rolling_loader.profiler import RollingLoaderProfiler
from research.mlops.rolling_loader.sources import (
    ClickHouseExternalContextConfig,
    ClickHouseExternalContextSource,
    ClickHouseReplayConfig,
    ClickHouseRollingSource,
    replay_items_for_block,
)


DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "index_table": "train_2019_to_2025",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "tickers": 0,
    "batch_size": 4096,
    "batches": 4,
    "events_per_ticker_block": 64,
    "replay_mode": "time-window",
    "replay_window_us": 200_000,
    "context_chunks": 32,
    "context_chunk_stride_events": 64,
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "80G",
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "macro_lookback_days": 400,
    "max_replay_windows": 100_000,
    "progress_every_blocks": 100,
    "output_root": "D:/market-data/prepared/data_provider_profiles/rolling_loader_training",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile the rolling loader as a real training data path with phase timing and RSS memory samples."
    )
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--sec-context-database", default=DEFAULTS["sec_context_database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--index-table", default=DEFAULTS["index_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--tickers", type=int, default=DEFAULTS["tickers"], help="Maximum ticker count. Use 0 for all tickers available at replay start.")
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--batches", type=int, default=DEFAULTS["batches"])
    parser.add_argument("--events-per-ticker-block", type=int, default=DEFAULTS["events_per_ticker_block"])
    parser.add_argument("--replay-mode", choices=("time-window", "ordinal"), default=DEFAULTS["replay_mode"])
    parser.add_argument("--replay-window-us", type=int, default=DEFAULTS["replay_window_us"])
    parser.add_argument("--context-chunks", type=int, default=DEFAULTS["context_chunks"])
    parser.add_argument("--context-chunk-stride-events", type=int, default=DEFAULTS["context_chunk_stride_events"])
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--long-context-lags", default="", help="Comma-separated additional event lags.")
    parser.add_argument("--start-timestamp-us", type=int, default=0)
    parser.add_argument("--start-utc", default="", help="Alternative replay start, for example 2025-01-02T15:00:00Z.")
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--materialize-external-payloads", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--memory-sample-interval-seconds", type=float, default=0.25)
    parser.add_argument("--output-root", type=Path, default=Path(DEFAULTS["output_root"]))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--max-replay-windows", type=int, default=DEFAULTS["max_replay_windows"])
    parser.add_argument("--progress-every-blocks", type=int, default=DEFAULTS["progress_every_blocks"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start_timestamp_us = int(args.start_timestamp_us or _parse_utc_us(args.start_utc))
    run_dir = _make_run_dir(args.output_root, args.run_name)
    events_path = run_dir / "profile_events.jsonl"
    summary_path = run_dir / "summary.json"
    memory_path = run_dir / "memory_samples.jsonl"
    recorder = JsonlRecorder(events_path)
    memory = MemorySampler(interval_seconds=max(0.05, float(args.memory_sample_interval_seconds)), output_path=memory_path)
    source: ClickHouseRollingSource | None = None
    started = time.perf_counter()

    try:
        memory.start()
        with StepTimer(recorder, memory, "env_load"):
            loaded_env_files = load_env_files(
                discover_clickhouse_env_files()
                if args.env_file is None
                else discover_clickhouse_env_files() + [args.env_file]
            )
            args.clickhouse_url = args.clickhouse_url or default_clickhouse_url()
            args.user = args.user or default_clickhouse_user()
            args.password = args.password or default_clickhouse_password()

        loader_config = RollingLoaderConfig(
            batch_size=int(args.batch_size),
            short_context_chunks=int(args.context_chunks),
            chunk_stride_events=1,
            context_chunk_stride_events=int(args.context_chunk_stride_events),
            replay_time_window_us=int(args.replay_window_us),
            long_context_lags=_parse_int_tuple(args.long_context_lags),
            sample_stride_events=int(args.sample_stride_events),
        )
        config_payload = {
            "kind": "config",
            "run_dir": str(run_dir),
            "loaded_env_files": [str(path) for path in loaded_env_files],
            "secret_status": secret_status(clickhouse_env_status_keys()),
            "clickhouse_url": args.clickhouse_url,
            "tables": _table_config(args),
            "loader": _loader_config_payload(loader_config),
            "profile": {
                "ticker_limit": int(args.tickers),
                "batch_size": int(args.batch_size),
                "batches": int(args.batches),
                "events_per_ticker_block": int(args.events_per_ticker_block),
                "replay_mode": str(args.replay_mode),
                "replay_window_us": int(loader_config.replay_time_window_us),
                "start_timestamp_us": start_timestamp_us,
                "start_utc": _format_us(start_timestamp_us) if start_timestamp_us else "",
                "materialize_external_payloads": bool(args.materialize_external_payloads),
            },
        }
        recorder.write(config_payload)
        print(f"PROFILE RUN {run_dir}", flush=True)
        print(json.dumps(config_payload["profile"], sort_keys=True), flush=True)

        with StepTimer(recorder, memory, "client_create"):
            text_client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
            bytes_client = PersistentClickHouseBytesClient(args.clickhouse_url, args.user, args.password)
            source = ClickHouseRollingSource(
                config=ClickHouseReplayConfig(
                    database=str(args.database),
                    events_table=str(args.events_table),
                    index_table=str(args.index_table),
                    max_threads=int(args.max_threads),
                    max_memory_usage=str(args.max_memory_usage),
                ),
                text_client=text_client,
                bytes_client=bytes_client,
            )
            context_source = ClickHouseExternalContextSource(
                config=ClickHouseExternalContextConfig(
                    database=str(args.database),
                    sec_context_database=str(args.sec_context_database),
                    news_token_table=str(args.news_token_table),
                    sec_filing_text_token_table=str(args.sec_filing_text_token_table),
                    sec_xbrl_context_table=str(args.sec_xbrl_context_table),
                    macro_bars_table=str(args.macro_bars_table),
                    news_lookback_days=int(args.news_lookback_days),
                    sec_lookback_days=int(args.sec_lookback_days),
                    xbrl_lookback_days=int(args.xbrl_lookback_days),
                    macro_lookback_days=int(args.macro_lookback_days),
                    ticker_news_items=int(loader_config.ticker_news_cache_size),
                    global_news_items=int(loader_config.global_news_cache_size),
                    sec_filing_items=int(loader_config.sec_filing_cache_size),
                    xbrl_items=int(loader_config.xbrl_cache_size),
                    news_token_chunks=int(loader_config.news_token_chunks),
                    sec_token_chunks=int(loader_config.sec_token_chunks),
                    text_max_tokens=int(loader_config.text_max_tokens),
                ),
                text_client=text_client,
            )
            loader = RollingContextLoader(loader_config, profiler=RollingLoaderProfiler(enabled=False))

        cursors, init_summary = initialize_replay_from_scratch(
            args=args,
            loader=loader,
            loader_config=loader_config,
            source=source,
            context_source=context_source,
            recorder=recorder,
            memory=memory,
            start_timestamp_us=start_timestamp_us,
        )
        recorder.write({"kind": "initialization_summary", **init_summary, "cache": loader.cache_summary()})
        print(f"INITIALIZED {json.dumps(init_summary, sort_keys=True)}", flush=True)

        replay_summary = replay_training_batches(
            args=args,
            loader=loader,
            loader_config=loader_config,
            source=source,
            context_source=context_source,
            cursors=cursors,
            recorder=recorder,
            memory=memory,
            started=started,
            start_timestamp_us=int(init_summary["start_timestamp_us"]),
        )
        final_summary = {
            "kind": "summary",
            "run_dir": str(run_dir),
            "events_path": str(events_path),
            "memory_path": str(memory_path),
            "summary_path": str(summary_path),
            "elapsed_seconds": time.perf_counter() - started,
            "rss_peak_mib": memory.peak_bytes / (1024 * 1024),
            "rss_final_mib": _rss_bytes() / (1024 * 1024),
            "initialization": init_summary,
            "replay": replay_summary,
            "cache": loader.cache_summary(),
        }
        summary_path.write_text(json.dumps(final_summary, indent=2, sort_keys=True), encoding="utf-8")
        recorder.write(final_summary)
        print("SUMMARY")
        print(json.dumps(final_summary, indent=2, sort_keys=True), flush=True)
        return 0
    finally:
        if source is not None:
            source.close()
        memory.stop()


def initialize_replay_from_scratch(
    *,
    args: argparse.Namespace,
    loader: RollingContextLoader,
    loader_config: RollingLoaderConfig,
    source: ClickHouseRollingSource,
    context_source: ClickHouseExternalContextSource,
    recorder: "JsonlRecorder",
    memory: "MemorySampler",
    start_timestamp_us: int,
) -> tuple[dict[str, int], dict[str, Any]]:
    warm_count = int(loader_config.warmup_events_per_ticker)
    min_events = warm_count + max(1, int(args.events_per_ticker_block))
    with StepTimer(recorder, memory, "source_load_ticker_index", {"ticker_limit": int(args.tickers), "min_events": min_events}):
        index_rows = tuple(source.load_ticker_index_rows(limit=int(args.tickers), min_events=min_events))
    if not index_rows:
        raise RuntimeError(f"No eligible tickers found for min_events={min_events:,}")

    if start_timestamp_us > 0:
        with StepTimer(recorder, memory, "resolve_start_ordinals", {"tickers": len(index_rows), "start_timestamp_us": start_timestamp_us}):
            start_ordinals = source.load_start_ordinals(index_rows=index_rows, start_timestamp_us=start_timestamp_us)
        if not start_ordinals:
            raise RuntimeError(f"No ticker has events at or before start_timestamp_us={start_timestamp_us}")
        available_index_rows = tuple(row for row in index_rows if row.ticker in start_ordinals)
        with StepTimer(recorder, memory, "initialize_universe", {"tickers": len(available_index_rows), "source": "start_time_available"}):
            initialized_tickers = loader.initialize_universe(row.ticker for row in available_index_rows)
        with StepTimer(recorder, memory, "warm_load_source_rows", {"tickers": len(initialized_tickers), "warm_count": warm_count}):
            warm_rows = source.warm_rows_ending_at(index_rows=available_index_rows, end_ordinals=start_ordinals, warm_count=warm_count)
        cursors = source.initial_cursors_from_ordinals(end_ordinals={ticker: start_ordinals[ticker] for ticker in initialized_tickers})
        initial_context_asof_us = start_timestamp_us
    else:
        with StepTimer(recorder, memory, "initialize_universe", {"tickers": len(index_rows), "source": "index"}):
            initialized_tickers = loader.initialize_universe(row.ticker for row in index_rows)
        with StepTimer(recorder, memory, "warm_load_source_rows", {"tickers": len(index_rows), "warm_count": warm_count}):
            warm_rows = source.warm_rows_from_index(index_rows=index_rows, warm_count=warm_count)
        cursors = source.initial_cursors_from_index(index_rows=index_rows, warm_count=warm_count)
        initial_context_asof_us = _initial_context_asof_timestamp_us(warm_rows)

    with StepTimer(recorder, memory, "warm_apply_event_rows", {"tickers": len(warm_rows), "rows": _row_count(warm_rows)}):
        loader.warm_load_events(warm_rows)

    initial_updates = []
    if initial_context_asof_us > 0:
        with StepTimer(
            recorder,
            memory,
            "initial_context_fetch",
            {"tickers": len(initialized_tickers), "asof_timestamp_us": initial_context_asof_us},
        ):
            initial_updates = context_source.load_initial_context_asof(
                tickers=initialized_tickers,
                asof_timestamp_us=initial_context_asof_us,
            )
        with StepTimer(
            recorder,
            memory,
            "initial_context_apply",
            {"updates": len(initial_updates), "payload_bytes": sum(update.payload.nbytes for update in initial_updates)},
        ):
            for update in initial_updates:
                loader.push_external(
                    kind=update.kind,
                    ticker=update.ticker,
                    timestamp_us=update.timestamp_us,
                    payload=update.payload,
                    global_item=update.global_item,
                )

    summary = {
        "ticker_limit": int(args.tickers),
        "tickers_indexed": len(index_rows),
        "tickers_available_at_start": len(initialized_tickers),
        "tickers_initialized": len(initialized_tickers),
        "tickers_warmed": len(warm_rows),
        "warm_count": warm_count,
        "warm_rows": _row_count(warm_rows),
        "start_timestamp_us": initial_context_asof_us,
        "start_utc": _format_us(initial_context_asof_us) if initial_context_asof_us else "",
        "initial_context_updates": len(initial_updates),
        "initial_context_payload_mib": sum(update.payload.nbytes for update in initial_updates) / (1024 * 1024),
    }
    return cursors, summary


def replay_training_batches(
    *,
    args: argparse.Namespace,
    loader: RollingContextLoader,
    loader_config: RollingLoaderConfig,
    source: ClickHouseRollingSource,
    context_source: ClickHouseExternalContextSource,
    cursors: dict[str, int],
    recorder: "JsonlRecorder",
    memory: "MemorySampler",
    started: float,
    start_timestamp_us: int,
) -> dict[str, Any]:
    completed_batches = 0
    completed_samples = 0
    event_count = 0
    block_count = 0
    context_updates = 0
    last_batch_mib = 0.0
    replay_started = time.perf_counter()
    exhausted = False
    replay_mode = str(args.replay_mode)
    current_time_us = int(start_timestamp_us)
    active_tickers = tuple(sorted(loader.initialized_tickers))

    while completed_batches < int(args.batches):
        block_count += 1
        if int(args.max_replay_windows) > 0 and block_count > int(args.max_replay_windows):
            exhausted = True
            break
        if replay_mode == "time-window":
            window_start_us = current_time_us
            window_end_us = current_time_us + int(loader_config.replay_time_window_us)
            with StepTimer(
                recorder,
                memory,
                "fetch_event_window",
                {
                    "block_index": block_count,
                    "active_tickers": len(active_tickers),
                    "window_start_us": window_start_us,
                    "window_end_us": window_end_us,
                    "replay_window_us": int(loader_config.replay_time_window_us),
                },
            ):
                block = source.fetch_time_window(
                    tickers=active_tickers,
                    start_exclusive_us=window_start_us,
                    end_inclusive_us=window_end_us,
                )
            context_start_us = window_start_us
            context_end_us = window_end_us
            current_time_us = window_end_us
        else:
            with StepTimer(recorder, memory, "fetch_event_block", {"block_index": block_count, "active_tickers": len(cursors)}):
                block = source.fetch_next_by_ordinal(cursors=cursors, rows_per_ticker=int(args.events_per_ticker_block))
            if block.row_count == 0:
                exhausted = True
                break
            cursors.update(block.latest_ordinals())
            context_start_us = int(block.min_timestamp_us or 0) - 1
            context_end_us = int(block.max_timestamp_us or 0)
        with StepTimer(
            recorder,
            memory,
            "fetch_context_updates",
            {
                "block_index": block_count,
                "event_rows": block.row_count,
                "replay_mode": replay_mode,
                "window_start_us": context_start_us,
                "window_end_us": context_end_us,
                "min_timestamp_us": int(block.min_timestamp_us or 0),
                "max_timestamp_us": int(block.max_timestamp_us or 0),
            },
        ):
            updates = context_source.fetch_context_updates(
                tickers=active_tickers if replay_mode == "time-window" else block.tickers,
                start_exclusive_us=context_start_us,
                end_inclusive_us=context_end_us,
            )
        context_updates += len(updates)
        replay_block_started = time.perf_counter()
        block_events = 0
        block_samples = 0
        with StepTimer(
            recorder,
            memory,
            "replay_block",
            {"block_index": block_count, "event_rows": block.row_count, "context_updates": len(updates)},
        ):
            for item in replay_items_for_block(block, updates):
                if item.context is not None:
                    update = item.context
                    loader.push_external(
                        kind=update.kind,
                        ticker=update.ticker,
                        timestamp_us=update.timestamp_us,
                        payload=update.payload,
                        global_item=update.global_item,
                    )
                    continue
                if item.event is None:
                    continue
                event_count += 1
                block_events += 1
                loader.push_event(item.event.ticker, item.event.row)
                while len(loader.ready_samples) >= loader_config.batch_size and completed_batches < int(args.batches):
                    batch_index = completed_batches + 1
                    samples = loader.drain_ready_samples(loader_config.batch_size)
                    with StepTimer(
                        recorder,
                        memory,
                        "materialize_batch",
                        {
                            "batch_index": batch_index,
                            "samples": len(samples),
                            "materialize_external_payloads": bool(args.materialize_external_payloads),
                        },
                    ):
                        batch = loader.materialize_training_batch(
                            samples,
                            materialize_external_payloads=bool(args.materialize_external_payloads),
                        )
                    completed_batches += 1
                    completed_samples += len(samples)
                    block_samples += len(samples)
                    last_batch_mib = batch.nbytes / (1024 * 1024)
                    batch_payload = {
                        "kind": "batch",
                        "batch_index": completed_batches,
                        "samples": len(samples),
                        "events_replayed": event_count,
                        "context_updates": context_updates,
                        "batch_mib": last_batch_mib,
                        "elapsed_seconds": time.perf_counter() - started,
                        "rss_mib": _rss_bytes() / (1024 * 1024),
                        "rss_peak_mib": memory.peak_bytes / (1024 * 1024),
                        "cache": loader.cache_summary(),
                    }
                    recorder.write(batch_payload)
                    print(
                        f"BATCH {completed_batches}/{int(args.batches)} "
                        f"samples={len(samples):,} events={event_count:,} batch_mib={last_batch_mib:.2f} "
                        f"rss={batch_payload['rss_mib']:.1f}MiB peak={batch_payload['rss_peak_mib']:.1f}MiB",
                        flush=True,
                    )
        block_seconds = time.perf_counter() - replay_block_started
        if int(args.progress_every_blocks) > 0 and block_count % int(args.progress_every_blocks) == 0:
            block_payload = {
                "kind": "block",
                "block_index": block_count,
                "replay_mode": replay_mode,
                "event_rows": block.row_count,
                "events_replayed": block_events,
                "samples_materialized": block_samples,
                "context_updates": len(updates),
                "seconds": block_seconds,
                "events_per_sec": block_events / block_seconds if block_seconds > 0 else 0.0,
                "ready_samples": len(loader.ready_samples),
                "cache": loader.cache_summary(),
            }
            recorder.write(block_payload)
            print(
                f"BLOCK {block_count} rows={block.row_count:,} updates={len(updates):,} "
                f"events_per_sec={block_payload['events_per_sec']:.1f} ready={len(loader.ready_samples):,}",
                flush=True,
            )

    replay_seconds = time.perf_counter() - replay_started
    return {
        "completed_batches": completed_batches,
        "completed_samples": completed_samples,
        "events_replayed": event_count,
        "context_updates": context_updates,
        "blocks": block_count,
        "exhausted": exhausted,
        "replay_mode": replay_mode,
        "replay_window_us": int(loader_config.replay_time_window_us),
        "current_time_us": current_time_us,
        "current_utc": _format_us(current_time_us) if current_time_us else "",
        "replay_seconds": replay_seconds,
        "events_per_sec": event_count / replay_seconds if replay_seconds > 0 else 0.0,
        "samples_per_sec": completed_samples / replay_seconds if replay_seconds > 0 else 0.0,
        "last_batch_mib": last_batch_mib,
    }


@dataclass(slots=True)
class JsonlRecorder:
    path: Path

    def write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        enriched = {"profile_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(enriched, sort_keys=True) + "\n")


class MemorySampler:
    def __init__(self, *, interval_seconds: float, output_path: Path) -> None:
        self.interval_seconds = float(interval_seconds)
        self.output_path = output_path
        self.samples: list[tuple[float, int]] = []
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rolling-loader-memory-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def peak_between(self, start_perf: float, end_perf: float) -> int:
        with self._lock:
            values = [rss for timestamp, rss in self.samples if start_perf <= timestamp <= end_perf]
        if values:
            return max(values)
        return _rss_bytes()

    def _run(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        while not self._stop.is_set():
            now = time.perf_counter()
            rss = _rss_bytes()
            with self._lock:
                self.samples.append((now, rss))
                self.peak_bytes = max(self.peak_bytes, rss)
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "profile_time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "perf_seconds": now,
                            "rss_mib": rss / (1024 * 1024),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._stop.wait(self.interval_seconds)


class StepTimer:
    def __init__(self, recorder: JsonlRecorder, memory: MemorySampler, name: str, extra: dict[str, Any] | None = None) -> None:
        self.recorder = recorder
        self.memory = memory
        self.name = name
        self.extra = dict(extra or {})
        self.started = 0.0
        self.rss_start = 0

    def __enter__(self) -> "StepTimer":
        self.started = time.perf_counter()
        self.rss_start = _rss_bytes()
        self.recorder.write({"kind": "phase_start", "phase": self.name, "rss_start_mib": self.rss_start / (1024 * 1024), **self.extra})
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        ended = time.perf_counter()
        rss_end = _rss_bytes()
        payload = {
            "kind": "phase_end",
            "phase": self.name,
            "seconds": ended - self.started,
            "rss_start_mib": self.rss_start / (1024 * 1024),
            "rss_end_mib": rss_end / (1024 * 1024),
            "rss_delta_mib": (rss_end - self.rss_start) / (1024 * 1024),
            "rss_peak_mib": self.memory.peak_between(self.started, ended) / (1024 * 1024),
            **self.extra,
        }
        if exc is not None:
            payload["error"] = repr(exc)
        self.recorder.write(payload)


def _make_run_dir(output_root: Path, run_name: str) -> Path:
    suffix = run_name.strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    return output_root / suffix


def _parse_utc_us(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    normalized = text.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def _format_us(timestamp_us: int) -> str:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000, tz=dt.timezone.utc).isoformat()


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    return tuple(int(part.strip()) for part in text.split(",") if part.strip())


def _row_count(rows_by_ticker: dict[str, Any]) -> int:
    return sum(int(getattr(rows, "shape", (0,))[0]) for rows in rows_by_ticker.values())


def _initial_context_asof_timestamp_us(rows_by_ticker: dict[str, Any]) -> int:
    values = [int(rows["sip_timestamp_us"][-1]) for rows in rows_by_ticker.values() if getattr(rows, "size", 0)]
    return min(values) if values else 0


def _rss_bytes() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0


def _table_config(args: argparse.Namespace) -> dict[str, str]:
    return {
        "database": str(args.database),
        "sec_context_database": str(args.sec_context_database),
        "events_table": str(args.events_table),
        "index_table": str(args.index_table),
        "news_token_table": str(args.news_token_table),
        "sec_filing_text_token_table": str(args.sec_filing_text_token_table),
        "sec_xbrl_context_table": str(args.sec_xbrl_context_table),
        "macro_bars_table": str(args.macro_bars_table),
    }


def _loader_config_payload(config: RollingLoaderConfig) -> dict[str, Any]:
    return {
        "events_per_chunk": int(config.events_per_chunk),
        "context_chunks": int(config.context_chunks),
        "context_lags": tuple(int(value) for value in config.context_lags),
        "context_chunk_stride_events": int(config.context_chunk_stride_events),
        "sample_stride_events": int(config.sample_stride_events),
        "replay_time_window_us": int(config.replay_time_window_us),
        "warmup_events_per_ticker": int(config.warmup_events_per_ticker),
        "event_cache_events_per_ticker": int(config.event_cache_events_per_ticker),
        "batch_size": int(config.batch_size),
        "global_news_cache_size": int(config.global_news_cache_size),
        "ticker_news_cache_size": int(config.ticker_news_cache_size),
        "sec_filing_cache_size": int(config.sec_filing_cache_size),
        "xbrl_cache_size": int(config.xbrl_cache_size),
        "macro_bar_cache_size": int(config.macro_bar_cache_size),
        "global_bar_cache_size": int(config.global_bar_cache_size),
        "text_max_tokens": int(config.text_max_tokens),
        "news_token_chunks": int(config.news_token_chunks),
        "sec_token_chunks": int(config.sec_token_chunks),
    }


if __name__ == "__main__":
    raise SystemExit(main())
