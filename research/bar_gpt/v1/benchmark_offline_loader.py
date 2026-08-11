from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.offline_shards import (
    OfflineShardDataset,
    discover_offline_units,
    hydrate_offline_runtime_config,
    make_offline_dataloader,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_loader_benchmark")


@dataclass(frozen=True, slots=True)
class Candidate:
    workers: int
    worker_prefetch_batches: int
    host_cache_batches: int
    length_bucket_batches: int


def _ints(value: str) -> tuple[int, ...]:
    result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not result or any(item < 0 for item in result):
        raise ValueError("benchmark grids require nonnegative integers")
    return result


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the complete v11 mmap, worker, collation, pinning, and CUDA handoff path."
    )
    parser.add_argument("--offline-shard-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2020-01-01")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--workers", default="4,8,12,16")
    parser.add_argument("--worker-prefetch", default="1,2,4")
    parser.add_argument("--host-cache-batches", default="2,4,8")
    parser.add_argument("--length-bucket-batches", default="0,2")
    parser.add_argument("--warmup-batches", type=int, default=4)
    parser.add_argument("--measured-batches", type=int, default=32)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args(list(argv) if argv is not None else None)


def _device(name: str) -> torch.device:
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def _run(
    candidate: Candidate,
    *,
    base: DataConfig,
    units: tuple,
    batch_size: int,
    warmup_batches: int,
    measured_batches: int,
    device: torch.device,
) -> dict[str, object]:
    data = dataclasses.replace(
        base,
        batch_size=batch_size,
        loader_workers=candidate.workers,
        worker_prefetch_batches=candidate.worker_prefetch_batches,
        ready_queue_blocks=batch_size * candidate.host_cache_batches,
        offline_length_bucket_batches=candidate.length_bucket_batches,
        persistent_workers=False,
    )
    data.validate()
    dataset = OfflineShardDataset(units, seed=17, shuffle_units=True)
    loader = make_offline_dataloader(dataset, data, drop_last=False)
    cold_started = time.perf_counter()
    prefetcher = DeviceBatchPrefetcher(
        loader,
        device,
        enabled=device.type == "cuda",
        host_cache_batches=candidate.host_cache_batches,
    )
    cold_start_seconds = time.perf_counter() - cold_started
    waits: list[float] = []
    origins = padded = blocks = 0
    started = 0.0
    completed = 0
    try:
        for index in range(warmup_batches + measured_batches):
            if index == warmup_batches:
                if device.type == "cuda":
                    torch.cuda.synchronize(device)
                started = time.perf_counter()
            batch, wait = prefetcher.next()
            if index >= warmup_batches:
                waits.append(wait)
                origins += batch.origin_count
                padded += int(batch.origin_mask.numel())
                blocks += len(batch.tickers)
                completed += 1
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elapsed = max(time.perf_counter() - started, 1e-9)
        telemetry = prefetcher.telemetry()
    finally:
        prefetcher.close()
    return {
        **dataclasses.asdict(candidate),
        "state": "passed",
        "measured_batches": completed,
        "blocks": blocks,
        "origins": origins,
        "padded_origin_slots": padded,
        "origin_slot_utilization": origins / max(1, padded),
        "elapsed_seconds": elapsed,
        "cold_start_seconds": cold_start_seconds,
        "origins_per_second": origins / elapsed,
        "blocks_per_second": blocks / elapsed,
        "wait_p50_ms": statistics.median(waits) * 1_000 if waits else 0.0,
        "wait_p95_ms": _percentile(waits, 0.95) * 1_000,
        "wait_max_ms": max(waits, default=0.0) * 1_000,
        **telemetry,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0 or args.warmup_batches <= 0 or args.measured_batches <= 0:
        raise ValueError("batch size, warmup batches, and measured batches must be positive")
    root = Path(args.offline_shard_root)
    verify_shard_catalog_lock(root)
    runtime = DataConfig(
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=str(args.end_date),
        validation_slices=(),
    )
    base = hydrate_offline_runtime_config(root, runtime)
    requested = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    tickers = requested or base.training_tickers
    units = discover_offline_units(
        root,
        base,
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    device = _device(str(args.device))
    candidates = tuple(
        Candidate(workers, prefetch, cache, bucket)
        for bucket in _ints(args.length_bucket_batches)
        for workers in _ints(args.workers)
        for prefetch in _ints(args.worker_prefetch)
        for cache in _ints(args.host_cache_batches)
        if workers > 0 and prefetch > 0 and cache > 0
    )
    results: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates, start=1):
        print(
            f"[{index}/{len(candidates)}] workers={candidate.workers} "
            f"prefetch={candidate.worker_prefetch_batches} cache={candidate.host_cache_batches} "
            f"bucket={candidate.length_bucket_batches}",
            flush=True,
        )
        try:
            result = _run(
                candidate,
                base=base,
                units=units,
                batch_size=int(args.batch_size),
                warmup_batches=int(args.warmup_batches),
                measured_batches=int(args.measured_batches),
                device=device,
            )
        except Exception as exc:
            result = {**dataclasses.asdict(candidate), "state": "failed", "message": str(exc)}
        results.append(result)
        if result["state"] == "passed":
            print(
                f"  {float(result['origins_per_second']):,.0f} origins/s | "
                f"utilization={float(result['origin_slot_utilization']):.1%} | "
                f"wait p95={float(result['wait_p95_ms']):.1f} ms",
                flush=True,
            )
    passed = [row for row in results if row["state"] == "passed"]
    if not passed:
        raise RuntimeError("every offline loader benchmark candidate failed")
    # Prefer throughput, then the smaller memory/worker footprint within 2%
    # of the maximum so the recommendation does not buy noise with RAM.
    maximum = max(float(row["origins_per_second"]) for row in passed)
    efficient = [row for row in passed if float(row["origins_per_second"]) >= maximum * 0.98]
    recommended = min(
        efficient,
        key=lambda row: (
            int(row["workers"]) * int(row["worker_prefetch_batches"]) + int(row["host_cache_batches"]),
            -float(row["origins_per_second"]),
        ),
    )
    payload = {
        "contract": "bar_gpt_v11_offline_loader_benchmark_v1",
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "shard_root": str(root),
        "device": str(device),
        "batch_size": int(args.batch_size),
        "units": len(units),
        "recommended": recommended,
        "results": results,
    }
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"offline_loader_{time.strftime('%Y%m%d-%H%M%S')}.json"
    temporary = output_path.with_suffix(output_path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, output_path)
    print(f"Recommended: {json.dumps(recommended, sort_keys=True)}", flush=True)
    print(f"Results: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
