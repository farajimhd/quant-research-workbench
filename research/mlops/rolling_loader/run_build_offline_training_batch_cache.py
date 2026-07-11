from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.rolling_loader.daily_index_cache import DEFAULT_DAILY_INDEX_CACHE_ROOT
from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader
from research.mlops.rolling_loader.offline_training_batch_cache import (
    OFFLINE_BATCH_CACHE_FORMAT,
    OFFLINE_BATCH_CACHE_VERSION,
    OfflineBatchCacheWriter,
    OfflineBatchCacheWriterConfig,
    OfflineShardStats,
)
from research.temporal_event_model.v3.config import (
    DEFAULT_DATA_GROUPS,
    DEFAULT_INTRADAY_LABEL_HORIZONS,
    LoaderConfig,
    to_dict,
)
from research.temporal_event_model.v3.data import loader_config_from_v3

try:  # pragma: no cover - fallback is exercised only when rich is unavailable.
    from rich import box
    from rich.console import Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except Exception:  # noqa: BLE001
    box = None
    Group = None
    Live = None
    Panel = None
    Table = None


DEFAULT_CACHE_ROOT = DEFAULT_DAILY_INDEX_CACHE_ROOT / "events_daily_index_2019-02"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/offline_training_batch_cache")
DEFAULT_CACHE_ID = "temporal_v3_offline_batches_2019-02_bs1024"
DEFAULT_BATCH_SIZE = 1024
DEFAULT_BATCHES_PER_SHARD = 10


@dataclass(slots=True)
class BuildProgressState:
    status: str = "starting"
    message: str = ""
    cache_id: str = ""
    cache_root: str = ""
    output_root: str = ""
    started_at: float = field(default_factory=time.perf_counter)
    target_batches: int = 0
    target_samples: int = 0
    batches_per_shard: int = DEFAULT_BATCHES_PER_SHARD
    batches_created: int = 0
    batches_committed: int = 0
    shards_saved: int = 0
    samples_created: int = 0
    samples_committed: int = 0
    tensor_payload_bytes: int = 0
    parquet_bytes: int = 0
    current_day: str = "-"
    current_segment: int = 0
    current_shard: int = 0
    pending_batches: int = 0
    last_batch_seconds: float = 0.0
    last_flush_seconds: float = 0.0
    loader_summary: dict[str, Any] = field(default_factory=dict)
    loader_telemetry: dict[str, Any] = field(default_factory=dict)
    recent_shards: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.perf_counter() - float(self.started_at))


class BuildReporter:
    def __init__(self, state: BuildProgressState, *, refresh_per_second: float = 2.0) -> None:
        self.state = state
        self.refresh_per_second = float(refresh_per_second)
        self._live: Any | None = None
        self._last_plain_print = 0.0

    def __enter__(self) -> "BuildReporter":
        if Live is not None:
            self._live = Live(self.render(), refresh_per_second=self.refresh_per_second, screen=False, transient=False)
            self._live.__enter__()
        else:
            self._plain_update(force=True)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.update(force=True)
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.state.messages.append(f"{stamp} {message}")
        self.state.messages = self.state.messages[-8:]
        self.state.message = message
        self.update()

    def update(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self.render(), refresh=force)
        else:
            self._plain_update(force=force)

    def render(self) -> Any:
        if Group is None:
            return ""
        return Group(
            _summary_panel(self.state),
            _work_panel(self.state),
            _shard_panel(self.state),
            _messages_panel(self.state),
        )

    def _plain_update(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_plain_print < 5.0:
            return
        self._last_plain_print = now
        eta = _eta_text(self.state)
        print(
            "offline_batch_cache "
            f"status={self.state.status} batches={self.state.batches_created:,} "
            f"shards={self.state.shards_saved:,} samples={self.state.samples_created:,} "
            f"parquet={_human_bytes(self.state.parquet_bytes)} eta={eta} "
            f"message={self.state.message}",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an offline day/segment/shard cache of fully materialized "
            "Temporal v3 training batches from the daily-index rolling cache. "
            "Each tensor is saved as its own parquet file inside a shard."
        )
    )
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cache-id", default=DEFAULT_CACHE_ID)
    parser.add_argument("--months", default="2019-02", help="Comma-separated month list.")
    parser.add_argument("--training-days", default="", help="Comma-separated YYYY-MM-DD days. Empty means all days in selected months.")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker filter for smoke runs.")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--data-groups", default=",".join(DEFAULT_DATA_GROUPS))
    parser.add_argument("--intraday-label-horizons", default=",".join(DEFAULT_INTRADAY_LABEL_HORIZONS))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--batches-per-shard", type=int, default=DEFAULT_BATCHES_PER_SHARD)
    parser.add_argument("--max-batches", type=int, default=0, help="0 means materialize all available origins selected by the loader.")
    parser.add_argument("--max-samples", type=int, default=0, help="0 means no explicit sample cap.")
    parser.add_argument("--max-shards", type=int, default=0, help="0 means no explicit shard cap.")
    parser.add_argument("--max-samples-per-segment", type=int, default=0, help="0 means one segment per day.")
    parser.add_argument("--read-workers", type=int, default=0, help="0 resolves to min(cpu_count, 64).")
    parser.add_argument("--materialize-workers", type=int, default=0, help="0 resolves to min(cpu_count, 64).")
    parser.add_argument("--scanner-prefetch-workers", type=int, default=0, help="0 resolves to min(cpu_count, 16).")
    parser.add_argument("--loaded-parts-per-group", type=int, default=256)
    parser.add_argument("--materialize-chunk-size", type=int, default=0)
    parser.add_argument("--time-window-seconds", type=float, default=60.0)
    parser.add_argument("--frontier-max-origins-per-window", type=int, default=0)
    parser.add_argument("--ticker-cache-capacity", type=int, default=15_000)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=1024)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=4)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--compression", default="zstd", choices=("zstd", "snappy", "gzip", "brotli", "none"))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--refresh-per-second", type=float, default=2.0)
    parser.add_argument("--print-config", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    read_workers = _resolve_workers(int(args.read_workers), cap=64)
    materialize_workers = _resolve_workers(int(args.materialize_workers), cap=64)
    scanner_workers = _resolve_workers(int(args.scanner_prefetch_workers), cap=16)
    loader_config = _build_loader_config(
        args=args,
        read_workers=read_workers,
        materialize_workers=materialize_workers,
        scanner_workers=scanner_workers,
    )
    daily_config = loader_config_from_v3(loader_config)
    writer_config = OfflineBatchCacheWriterConfig(
        output_root=Path(args.output_root),
        cache_id=str(args.cache_id),
        batches_per_shard=int(args.batches_per_shard),
        max_samples_per_segment=int(args.max_samples_per_segment),
        compression=None if str(args.compression).lower() == "none" else str(args.compression),
        overwrite=bool(args.overwrite),
        source_cache_root=Path(args.cache_root),
        loader_config=to_dict(loader_config),
        run_args=vars(args) | {
            "read_workers_resolved": read_workers,
            "materialize_workers_resolved": materialize_workers,
            "scanner_prefetch_workers_resolved": scanner_workers,
        },
    )
    writer = OfflineBatchCacheWriter(writer_config)
    state = BuildProgressState(
        cache_id=str(args.cache_id),
        cache_root=str(args.cache_root),
        output_root=str(Path(args.output_root) / str(args.cache_id)),
        target_batches=int(args.max_batches),
        target_samples=int(args.max_samples),
        batches_per_shard=int(args.batches_per_shard),
    )
    loader: AsyncDailyIndexBatchLoader | None = None
    interrupted = False
    if bool(args.print_config):
        print(
            json.dumps(
                {
                    "offline_batch_cache_format": OFFLINE_BATCH_CACHE_FORMAT,
                    "version": OFFLINE_BATCH_CACHE_VERSION,
                    "cache_root": str(args.cache_root),
                    "output_root": str(Path(args.output_root) / str(args.cache_id)),
                    "months": _split_csv(str(args.months)),
                    "training_days": _split_csv(str(args.training_days)),
                    "batch_size": int(args.batch_size),
                    "batches_per_shard": int(args.batches_per_shard),
                    "max_batches": int(args.max_batches),
                    "max_samples": int(args.max_samples),
                    "max_shards": int(args.max_shards),
                    "read_workers": read_workers,
                    "materialize_workers": materialize_workers,
                    "scanner_prefetch_workers": scanner_workers,
                    "data_groups": _split_csv(str(args.data_groups)),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    try:
        writer.prepare()
        log_path = writer.logs_dir / "build_log.jsonl"
        error_path = writer.logs_dir / "fatal_error.txt"
        _append_jsonl(log_path, {"event": "start", "created_utc": _now_iso(), "args": _jsonable(vars(args)), "loader_config": _jsonable(to_dict(loader_config))})
        with BuildReporter(state, refresh_per_second=float(args.refresh_per_second)) as reporter:
            reporter.log("discovering daily-index cache packages")
            loader = AsyncDailyIndexBatchLoader(daily_config)
            summary = loader.summary()
            state.loader_summary = dict(summary)
            if int(args.max_batches) > 0:
                state.target_samples = int(args.max_batches) * int(args.batch_size)
            elif state.target_samples <= 0:
                state.target_samples = int(summary.get("total_available_origins") or 0)
            if state.target_batches <= 0 and state.target_samples > 0:
                state.target_batches = int(math.ceil(state.target_samples / max(1, int(args.batch_size))))
            reporter.log(
                "starting materialization "
                f"origins={int(summary.get('total_available_origins') or 0):,} "
                f"parts={int(summary.get('part_count') or summary.get('package_count') or 0):,}"
            )
            for batch in loader.iter_batches():
                batch_started = time.perf_counter()
                if batch is None:
                    continue
                sample_count = int(getattr(batch, "sample_count", 0) or 0)
                state.status = "materializing"
                state.batches_created += 1
                state.samples_created += sample_count
                state.current_day = _batch_primary_day(batch)
                state.pending_batches = int(writer.pending_batches) + 1
                stats = writer.add_batch(batch)
                state.last_batch_seconds = time.perf_counter() - batch_started
                state.loader_telemetry = loader.telemetry_snapshot()
                if stats is not None:
                    _record_shard_stats(state, stats)
                    state.pending_batches = int(writer.pending_batches)
                    _append_jsonl(log_path, {"event": "shard_saved", **_stats_payload(stats)})
                    reporter.log(
                        f"saved shard day={stats.day} shard={stats.shard_id:06d} "
                        f"batches={stats.batches} samples={stats.samples:,} parquet={_human_bytes(stats.parquet_bytes)}"
                    )
                else:
                    reporter.update()
                if _should_stop(args, state):
                    reporter.log("target reached; flushing final shard")
                    break
            state.status = "flushing"
            flush_started = time.perf_counter()
            stats = writer.flush(status="complete")
            state.last_flush_seconds = time.perf_counter() - flush_started
            if stats is not None:
                _record_shard_stats(state, stats)
                _append_jsonl(log_path, {"event": "shard_saved", **_stats_payload(stats)})
            writer.write_root_manifest(status="complete")
            state.status = "complete"
            state.pending_batches = 0
            reporter.log("offline batch cache complete")
            reporter.update(force=True)
        _append_jsonl(log_path, {"event": "complete", "finished_utc": _now_iso(), "batches": writer.batches, "samples": writer.samples, "shards": writer.shards})
        print(
            "OFFLINE BATCH CACHE COMPLETE "
            f"path={writer.cache_root} batches={writer.batches:,} shards={writer.shards:,} "
            f"samples={writer.samples:,} parquet={_human_bytes(writer.parquet_bytes)}",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        interrupted = True
        state.status = "interrupted"
        print("Interrupt received; cancelling loader and committing any complete pending shard.", flush=True)
        if loader is not None:
            loader.cancel()
        try:
            stats = writer.flush(status="partial_after_interrupt")
            if stats is not None:
                _record_shard_stats(state, stats)
        finally:
            writer.write_root_manifest(status="interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        state.status = "error"
        if "error_path" in locals():
            Path(error_path).write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        writer.write_root_manifest(status="error")
        raise
    finally:
        if loader is not None:
            loader.close()
        if interrupted:
            print(f"Partial offline cache is in {writer.cache_root}", flush=True)


def _build_loader_config(
    *,
    args: argparse.Namespace,
    read_workers: int,
    materialize_workers: int,
    scanner_workers: int,
) -> LoaderConfig:
    max_origins = int(args.max_samples) if int(args.max_samples) > 0 else 0
    if int(args.max_batches) > 0:
        max_origins = int(args.max_batches) * int(args.batch_size)
    return LoaderConfig(
        cache_root=Path(args.cache_root),
        split="train",
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        months=tuple(_split_csv(str(args.months))),
        tickers=tuple(_split_csv(str(args.tickers))),
        batch_size=int(args.batch_size),
        seed=17,
        dataset_id=str(args.cache_id),
        data_groups=tuple(_split_csv(str(args.data_groups))),
        intraday_label_horizons=tuple(_split_csv(str(args.intraday_label_horizons))),
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        read_workers=int(read_workers),
        materialize_workers=int(materialize_workers),
        materialize_chunk_size=int(args.materialize_chunk_size),
        prefetch_batches=64,
        chronological_replay=True,
        time_window_seconds=float(args.time_window_seconds),
        frontier_max_origins_per_window=int(args.frontier_max_origins_per_window),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(scanner_workers),
        max_origins_per_epoch=max_origins,
        training_days=tuple(_split_csv(str(args.training_days))),
        shuffle_parts=True,
        shuffle_within_loaded_group=True,
    )


def _record_shard_stats(state: BuildProgressState, stats: OfflineShardStats) -> None:
    state.batches_committed += int(stats.batches)
    state.samples_committed += int(stats.samples)
    state.shards_saved += 1
    state.tensor_payload_bytes += int(stats.tensor_payload_bytes)
    state.parquet_bytes += int(stats.parquet_bytes)
    state.current_day = str(stats.day)
    state.current_segment = int(stats.segment_id)
    state.current_shard = int(stats.shard_id)
    state.pending_batches = 0
    row = _stats_payload(stats)
    state.recent_shards.append(row)
    state.recent_shards = state.recent_shards[-6:]


def _stats_payload(stats: OfflineShardStats) -> dict[str, Any]:
    return {
        "day": stats.day,
        "segment_id": int(stats.segment_id),
        "shard_id": int(stats.shard_id),
        "shard_path": str(stats.shard_path),
        "batches": int(stats.batches),
        "samples": int(stats.samples),
        "tensor_count": int(stats.tensor_count),
        "tensor_payload_bytes": int(stats.tensor_payload_bytes),
        "parquet_bytes": int(stats.parquet_bytes),
        "started_utc": stats.started_utc,
        "finished_utc": stats.finished_utc,
        "first_origin": stats.first_origin,
        "last_origin": stats.last_origin,
    }


def _should_stop(args: argparse.Namespace, state: BuildProgressState) -> bool:
    if int(args.max_batches) > 0 and int(state.batches_created) >= int(args.max_batches):
        return True
    if int(args.max_samples) > 0 and int(state.samples_created) >= int(args.max_samples):
        return True
    if int(args.max_shards) > 0 and int(state.shards_saved) >= int(args.max_shards):
        return True
    return False


def _summary_panel(state: BuildProgressState) -> Any:
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(
        _kv_block(
            (
                ("Status", state.status),
                ("Message", state.message),
                ("Cache id", state.cache_id),
                ("Current day", state.current_day),
            )
        ),
        _kv_block(
            (
                ("Elapsed", _duration(state.elapsed_seconds)),
                ("ETA", _eta_text(state)),
                ("Output", state.output_root),
                ("Parquet", _human_bytes(state.parquet_bytes)),
            )
        ),
    )
    return Panel(table, title="Offline Training Batch Cache", border_style=_status_color(state.status), box=box.ROUNDED if box else None)


def _work_panel(state: BuildProgressState) -> Any:
    table = Table(expand=True, box=box.SIMPLE_HEAVY if box else None, show_edge=False)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Progress", ratio=2)
    table.add_column("Value", justify="right")
    table.add_column("Rate", justify="right")
    elapsed = max(1e-9, state.elapsed_seconds)
    batch_total = max(int(state.target_batches), int(state.batches_created), 1)
    sample_total = max(int(state.target_samples), int(state.samples_created), 1)
    shard_total = max(1, int(math.ceil(batch_total / max(1, int(state.batches_per_shard)))))
    table.add_row("Batches created", _bar(state.batches_created, batch_total), f"{state.batches_created:,}/{state.target_batches or '?'}", f"{state.batches_created / elapsed:.3f}/s")
    table.add_row("Batches committed", _bar(state.batches_committed, batch_total), f"{state.batches_committed:,}", "")
    table.add_row("Samples created", _bar(state.samples_created, sample_total), f"{state.samples_created:,}/{state.target_samples or '?'}", f"{state.samples_created / elapsed:,.1f}/s")
    table.add_row("Shards saved", _bar(state.shards_saved, shard_total), f"{state.shards_saved:,}", f"{state.shards_saved / elapsed:.4f}/s")
    table.add_row("Pending in shard", _bar(state.pending_batches, int(state.batches_per_shard)), f"{state.pending_batches}/{state.batches_per_shard}", "")
    return Panel(table, title="Build Progress", border_style="blue", box=box.ROUNDED if box else None)


def _shard_panel(state: BuildProgressState) -> Any:
    table = Table(expand=True, box=box.SIMPLE_HEAVY if box else None, show_edge=False)
    table.add_column("Day", no_wrap=True)
    table.add_column("Seg", justify="right")
    table.add_column("Shard", justify="right")
    table.add_column("Batches", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Parquet", justify="right")
    if not state.recent_shards:
        table.add_row(state.current_day, str(state.current_segment), str(state.current_shard), "0", "0", "0 B")
    for row in state.recent_shards:
        table.add_row(
            str(row.get("day") or ""),
            str(row.get("segment_id") or 0),
            str(row.get("shard_id") or 0),
            f"{int(row.get('batches') or 0):,}",
            f"{int(row.get('samples') or 0):,}",
            _human_bytes(int(row.get("parquet_bytes") or 0)),
        )
    telemetry = Table.grid(expand=True)
    telemetry.add_column(ratio=1)
    telemetry.add_column(ratio=1)
    telemetry.add_row(
        _kv_block(
            (
                ("Loader phase", state.loader_telemetry.get("loader_phase", "-")),
                ("Raw ready", state.loader_telemetry.get("raw_ready_batches", "-")),
                ("Raw limit", state.loader_telemetry.get("raw_ready_limit", "-")),
                ("Seen origins", state.loader_telemetry.get("seen_origins_total", "-")),
            )
        ),
        _kv_block(
            (
                ("Total origins", state.loader_summary.get("total_available_origins", "-")),
                ("Ticker count", state.loader_summary.get("ticker_count", "-")),
                ("Part count", state.loader_summary.get("part_count", state.loader_summary.get("package_count", "-"))),
                ("Last batch sec", f"{state.last_batch_seconds:.3f}"),
            )
        ),
    )
    return Panel(Group(table, telemetry), title="Shards And Loader", border_style="green", box=box.ROUNDED if box else None)


def _messages_panel(state: BuildProgressState) -> Any:
    text = "\n".join(state.messages[-8:]) if state.messages else "-"
    return Panel(text, title="Messages", border_style="yellow", box=box.ROUNDED if box else None)


def _kv_block(rows: Any) -> Any:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for key, value in rows:
        table.add_row(str(key), str(value))
    return table


def _bar(done: int, total: int, *, width: int = 28) -> str:
    total = max(1, int(total))
    done = max(0, min(int(done), total))
    filled = int(round(width * done / total))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {done / total:5.1%}"


def _eta_text(state: BuildProgressState) -> str:
    if state.target_samples > 0 and state.samples_created > 0:
        rate = state.samples_created / max(1e-9, state.elapsed_seconds)
        remaining = max(0, state.target_samples - state.samples_created)
        return _duration(remaining / max(rate, 1e-9))
    if state.target_batches > 0 and state.batches_created > 0:
        rate = state.batches_created / max(1e-9, state.elapsed_seconds)
        remaining = max(0, state.target_batches - state.batches_created)
        return _duration(remaining / max(rate, 1e-9))
    return "estimating"


def _status_color(status: str) -> str:
    if status in {"complete"}:
        return "green"
    if status in {"error"}:
        return "red"
    if status in {"interrupted"}:
        return "yellow"
    return "blue"


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _human_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or suffix == "TiB":
            return f"{size:.1f} {suffix}" if suffix != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _batch_primary_day(batch: Any) -> str:
    import numpy as np

    timestamps = np.asarray(getattr(batch, "origin_timestamp_us", np.asarray([], dtype=np.int64)))
    if timestamps.size == 0:
        return "unknown"
    timestamp_us = int(timestamps[0])
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc).date().isoformat()


def _resolve_workers(value: int, *, cap: int) -> int:
    if value > 0:
        return int(value)
    return max(1, min(int(cap), int(os.cpu_count() or 1)))


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
