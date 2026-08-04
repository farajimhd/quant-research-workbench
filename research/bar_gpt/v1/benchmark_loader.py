from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urlsplit

import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.loader import BarGPTIterableDataset, ClickHouseBarStreamConfig, make_dataloader
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v1.train import preflight
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files


@dataclass(frozen=True, slots=True)
class LoaderBenchmarkResult:
    state: str
    split: str
    start_date: str
    end_date: str
    device: str
    workers: int
    batch_size: int
    warmup_batches: int
    measured_batches: int
    measured_origins: int
    cold_start_seconds: float
    measured_seconds: float
    origins_per_second: float
    batches_per_second: float
    wait_p50_ms: float
    wait_p95_ms: float
    wait_max_ms: float
    handoff_p50_ms: float
    handoff_p95_ms: float
    loader_wait_share: float
    required_origins_per_second: float
    capacity_multiple: float | None
    last_tickers: tuple[str, ...]
    last_dates: tuple[str, ...]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(
        description="Benchmark the real BarGPT Arrow, online-rollup, collation, and device-handoff path."
    )
    parser.add_argument("--split", choices=("train", "validation"), default="train")
    parser.add_argument("--start-date", default="2025-10-01")
    parser.add_argument("--end-date", default="2026-01-01")
    parser.add_argument("--tickers", default=",".join(defaults.tickers))
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--one-second-table", default=defaults.one_second_table)
    parser.add_argument("--manifest-table", default=defaults.manifest_table)
    parser.add_argument("--alias-manifest-table", default=defaults.alias_manifest_table)
    parser.add_argument("--daily-table", default=defaults.daily_table)
    parser.add_argument("--daily-manifest-table", default=defaults.daily_manifest_table)
    parser.add_argument("--condition-table", default=defaults.condition_table)
    parser.add_argument("--condition-status-table", default=defaults.condition_status_table)
    parser.add_argument("--identity-database", default=defaults.identity_database)
    parser.add_argument("--identity-interval-table", default=defaults.identity_interval_table)
    parser.add_argument("--identity-entity-table", default=defaults.identity_entity_table)
    parser.add_argument("--identity-event-table", default=defaults.identity_event_table)
    parser.add_argument("--split-database", default=defaults.split_database)
    parser.add_argument("--split-table", default=defaults.split_table)
    parser.add_argument("--context-bars-1s", type=int, default=defaults.context_bars_1s)
    parser.add_argument("--origin-bars-1s", type=int, default=defaults.origin_bars_1s)
    parser.add_argument("--coverage-blocks-per-unit", type=int, default=defaults.coverage_blocks_per_unit)
    parser.add_argument("--origin-fetch-candidate-blocks", type=int, default=defaults.origin_fetch_candidate_blocks)
    parser.add_argument("--origin-emit-blocks-per-chunk", type=int, default=defaults.origin_emit_blocks_per_chunk)
    parser.add_argument("--daily-context-bars", type=int, default=defaults.daily_context_bars)
    parser.add_argument("--batch-size", type=int, default=defaults.batch_size)
    parser.add_argument("--loader-workers", type=int, default=defaults.loader_workers)
    parser.add_argument("--ready-queue-blocks", type=int, default=defaults.ready_queue_blocks)
    parser.add_argument("--worker-prefetch-batches", type=int, default=defaults.worker_prefetch_batches)
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=defaults.clickhouse_max_threads_per_worker)
    parser.add_argument("--clickhouse-max-block-size", type=int, default=defaults.clickhouse_max_block_size)
    parser.add_argument("--clickhouse-max-memory-usage", type=int, default=defaults.clickhouse_max_memory_usage)
    parser.add_argument("--clickhouse-query-days", type=int, default=defaults.clickhouse_query_days)
    parser.add_argument(
        "--clickhouse-max-bytes-before-external-sort",
        type=int,
        default=defaults.clickhouse_max_bytes_before_external_sort,
    )
    parser.add_argument(
        "--balance-activity-regimes",
        action=argparse.BooleanOptionalAction,
        default=defaults.balance_activity_regimes,
    )
    parser.add_argument("--pin-memory", action=argparse.BooleanOptionalAction, default=defaults.pin_memory)
    parser.add_argument("--persistent-workers", action=argparse.BooleanOptionalAction, default=defaults.persistent_workers)
    parser.add_argument("--warmup-batches", type=int, default=16)
    parser.add_argument("--measured-batches", type=int, default=128)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--required-origins-per-second", type=float, default=0.0)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--json", action="store_true", help="Print only the final machine-readable result.")
    return parser.parse_args(list(argv) if argv is not None else None)


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def _device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda requested but CUDA is unavailable")
    return torch.device(name)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * min(1.0, max(0.0, float(quantile)))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


class BenchmarkReporter:
    def __init__(self, *, layout: str, total: int, json_only: bool) -> None:
        self.total = total
        self.json_only = json_only
        self.completed = 0
        self._progress = None
        self._task = None
        use_rich = layout == "rich" or (layout == "auto" and sys.stdout.isatty())
        if use_rich and not json_only:
            try:
                from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[bold cyan]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeElapsedColumn(),
                    transient=False,
                )
                self._task = self._progress.add_task("cold start", total=total)
            except ImportError:
                self._progress = None
        self.text = not json_only and self._progress is None and layout != "none"

    def __enter__(self) -> "BenchmarkReporter":
        if self._progress is not None:
            self._progress.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._progress is not None:
            self._progress.stop()

    def start(self, message: str) -> None:
        if self.text:
            print(message, flush=True)

    def advance(self, stage: str, *, rate: float | None = None) -> None:
        self.completed += 1
        description = stage if rate is None else f"{stage}  {rate:,.0f} origins/s"
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, advance=1, description=description)
        elif self.text and (self.completed == 1 or self.completed == self.total or self.completed % max(1, self.total // 10) == 0):
            print(f"{stage}: {self.completed}/{self.total}" + (f"  {rate:,.0f} origins/s" if rate is not None else ""), flush=True)

    def final(self, result: LoaderBenchmarkResult) -> None:
        payload = asdict(result)
        if self.json_only:
            print(json.dumps(payload, sort_keys=True), flush=True)
            return
        if self._progress is not None:
            from rich.console import Console
            from rich.table import Table

            table = Table(title="BarGPT loader benchmark", show_header=False, box=None)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", justify="right")
            table.add_row("state", result.state)
            table.add_row("scope", f"{result.split} [{result.start_date}, {result.end_date})")
            table.add_row("consumer", f"{result.device}; workers={result.workers}; batch={result.batch_size}")
            table.add_row("cold start", f"{result.cold_start_seconds:,.3f} s")
            table.add_row("steady capacity", f"{result.origins_per_second:,.0f} origins/s")
            table.add_row("batch rate", f"{result.batches_per_second:,.2f} batches/s")
            table.add_row("loader wait p50 / p95 / max", f"{result.wait_p50_ms:,.1f} / {result.wait_p95_ms:,.1f} / {result.wait_max_ms:,.1f} ms")
            table.add_row("handoff + targets p50 / p95", f"{result.handoff_p50_ms:,.1f} / {result.handoff_p95_ms:,.1f} ms")
            table.add_row("loader wait share", f"{result.loader_wait_share * 100:,.1f}%")
            if result.capacity_multiple is not None:
                table.add_row("required / capacity", f"{result.required_origins_per_second:,.0f} / {result.capacity_multiple:,.2f}x")
            table.add_row("latest", f"{','.join(result.last_tickers)}  {','.join(result.last_dates)}")
            Console().print(table)
        else:
            for key, value in payload.items():
                print(f"{key}={value}", flush=True)


def _make_data_config(args: argparse.Namespace) -> DataConfig:
    defaults = DataConfig()
    tickers = _csv(args.tickers)
    validation_tickers = tickers if args.split == "validation" else ()
    validation_slices = tuple((ticker, str(args.start_date), str(args.end_date)) for ticker in validation_tickers)
    data = DataConfig(
        database=str(args.database),
        one_second_table=str(args.one_second_table),
        manifest_table=str(args.manifest_table),
        alias_manifest_table=str(args.alias_manifest_table),
        daily_table=str(args.daily_table),
        daily_manifest_table=str(args.daily_manifest_table),
        condition_table=str(args.condition_table),
        condition_status_table=str(args.condition_status_table),
        identity_database=str(args.identity_database),
        identity_interval_table=str(args.identity_interval_table),
        identity_entity_table=str(args.identity_entity_table),
        identity_event_table=str(args.identity_event_table),
        split_database=str(args.split_database),
        split_table=str(args.split_table),
        daily_history_start_date="2019-01-01",
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=str(args.end_date if args.split == "train" else args.start_date),
        validation_slices=validation_slices,
        context_bars_1s=int(args.context_bars_1s),
        origin_bars_1s=int(args.origin_bars_1s),
        coverage_blocks_per_unit=int(args.coverage_blocks_per_unit),
        origin_fetch_candidate_blocks=int(args.origin_fetch_candidate_blocks),
        origin_emit_blocks_per_chunk=int(args.origin_emit_blocks_per_chunk),
        validation_blocks_per_slice=max(
            defaults.validation_blocks_per_slice,
            math.ceil((int(args.warmup_batches) + int(args.measured_batches)) * int(args.batch_size) / max(1, len(tickers))),
        ),
        daily_context_bars=int(args.daily_context_bars),
        batch_size=int(args.batch_size),
        loader_workers=int(args.loader_workers),
        ready_queue_blocks=int(args.ready_queue_blocks),
        worker_prefetch_batches=int(args.worker_prefetch_batches),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        clickhouse_max_block_size=int(args.clickhouse_max_block_size),
        clickhouse_max_memory_usage=int(args.clickhouse_max_memory_usage),
        clickhouse_query_days=int(args.clickhouse_query_days),
        clickhouse_max_bytes_before_external_sort=int(args.clickhouse_max_bytes_before_external_sort),
        pin_memory=bool(args.pin_memory),
        persistent_workers=bool(args.persistent_workers),
        balance_activity_regimes=bool(args.balance_activity_regimes),
    )
    data.validate()
    if int(args.warmup_batches) < 0 or int(args.measured_batches) <= 0:
        raise ValueError("warmup-batches cannot be negative and measured-batches must be positive")
    return data


def run_benchmark(args: argparse.Namespace) -> LoaderBenchmarkResult:
    load_env_files(discover_clickhouse_env_files(), verbose=not args.json)
    data = _make_data_config(args)
    device = _device(str(args.device))
    url = default_clickhouse_url()
    user = default_clickhouse_user()
    password = default_clickhouse_password()
    preflight(ClickHouseHttpClient(url, user, password), data)
    stream = ClickHouseBarStreamConfig(
        url=url,
        user=user,
        password=password,
        database=data.database,
        table=data.one_second_table,
        max_threads=data.clickhouse_max_threads_per_worker,
        max_block_size=data.clickhouse_max_block_size,
        max_memory_usage=data.clickhouse_max_memory_usage,
        query_days=data.clickhouse_query_days,
        max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
    )
    dataset = BarGPTIterableDataset(data_config=data, stream_config=stream, split=str(args.split), seed=int(args.seed))
    loader = make_dataloader(dataset, data, drop_last=True)
    total = int(args.warmup_batches) + int(args.measured_batches)
    host = urlsplit(url).hostname or "configured ClickHouse"
    reporter = BenchmarkReporter(layout=str(args.progress_layout), total=total, json_only=bool(args.json))
    waits: list[float] = []
    handoffs: list[float] = []
    origins = 0
    last_tickers: tuple[str, ...] = ()
    last_dates: tuple[str, ...] = ()
    cold_start = 0.0
    measured_started = 0.0
    measured_finished = 0.0
    iterator = None
    started = time.perf_counter()
    with reporter:
        reporter.start(
            f"BarGPT loader benchmark: {args.split} [{args.start_date},{args.end_date}) "
            f"from {host}; workers={data.loader_workers}; batch={data.batch_size}; device={device}"
        )
        try:
            iterator = DeviceBatchPrefetcher(
                loader,
                device,
                enabled=device.type == "cuda",
                host_cache_batches=max(1, math.ceil(data.ready_queue_blocks / data.batch_size)),
            )
            for index in range(total):
                if index == int(args.warmup_batches):
                    measured_started = time.perf_counter()
                batch, wait_seconds = iterator.next()
                if index == 0:
                    cold_start = time.perf_counter() - started
                handoff_started = time.perf_counter()
                if device.type == "cuda":
                    torch.cuda.synchronize()
                handoff_seconds = time.perf_counter() - handoff_started
                if index >= int(args.warmup_batches):
                    waits.append(wait_seconds)
                    handoffs.append(handoff_seconds)
                    origins += batch.origin_count
                    last_tickers = batch.tickers
                    last_dates = batch.local_dates
                    measured_finished = time.perf_counter()
                    elapsed = max(measured_finished - measured_started, 1e-9)
                    reporter.advance("measuring", rate=origins / elapsed)
                else:
                    reporter.advance("warming")
        except StopIteration as exc:
            raise RuntimeError(f"loader ended after {index} of {total} requested batches") from exc
        finally:
            if iterator is not None:
                iterator.close()
            del loader
    measured_seconds = max(measured_finished - measured_started, 1e-9)
    rate = origins / measured_seconds
    required = max(0.0, float(args.required_origins_per_second))
    multiple = rate / required if required > 0 else None
    state = "passed" if multiple is not None and multiple >= 1.0 else ("failed_capacity" if multiple is not None else "measured")
    result = LoaderBenchmarkResult(
        state=state,
        split=str(args.split),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        device=str(device),
        workers=data.loader_workers,
        batch_size=data.batch_size,
        warmup_batches=int(args.warmup_batches),
        measured_batches=len(waits),
        measured_origins=origins,
        cold_start_seconds=cold_start,
        measured_seconds=measured_seconds,
        origins_per_second=rate,
        batches_per_second=len(waits) / measured_seconds,
        wait_p50_ms=_percentile(waits, 0.50) * 1_000,
        wait_p95_ms=_percentile(waits, 0.95) * 1_000,
        wait_max_ms=max(waits, default=0.0) * 1_000,
        handoff_p50_ms=_percentile(handoffs, 0.50) * 1_000,
        handoff_p95_ms=_percentile(handoffs, 0.95) * 1_000,
        loader_wait_share=sum(waits) / measured_seconds,
        required_origins_per_second=required,
        capacity_multiple=multiple,
        last_tickers=last_tickers,
        last_dates=last_dates,
    )
    reporter.final(result)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_benchmark(args)
    except KeyboardInterrupt:
        print("BarGPT loader benchmark interrupted", file=sys.stderr, flush=True)
        return 130
    return 2 if result.state == "failed_capacity" else 0


if __name__ == "__main__":
    raise SystemExit(main())
