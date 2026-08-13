from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import torch

from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.offline_shards import (
    OfflineBlockRef,
    OfflineShardDataset,
    OfflineShardUnit,
    discover_offline_units,
    hydrate_offline_runtime_config,
    load_shard,
    make_offline_dataloader,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v2.prefetch import DeviceBatchPrefetcher


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v2\offline_loader_benchmark")


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
        description=(
            "Benchmark the v12 mmap loader, worker, collation, pinning, and CUDA handoff path. "
            "This is a loader benchmark; it does not run model forward or backward compute."
        )
    )
    parser.add_argument("--offline-shard-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2020-01-01")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batch-size", type=int, default=32)
    # Twelve focused candidates bracket the production loader shape without
    # the former 72-way Cartesian sweep. Larger queues were already shown to
    # amplify host memory and page-cache pressure without establishing a
    # training-throughput benefit.
    parser.add_argument("--workers", default="8,12,16")
    parser.add_argument("--worker-prefetch", default="1,2")
    parser.add_argument("--host-cache-batches", default="2,4")
    parser.add_argument("--length-bucket-batches", default="4")
    parser.add_argument("--warmup-batches", type=int, default=4)
    parser.add_argument("--measured-batches", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=3)
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


def _stable_score(*parts: object) -> bytes:
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()


def _allocate_unit_batches(
    units: Sequence[OfflineShardUnit],
    *,
    batches: int,
    batch_size: int,
    minimum_units: int,
    seed: int,
) -> tuple[tuple[OfflineShardUnit, int], ...]:
    eligible = sorted(
        (
            (unit, int(unit.blocks) // int(batch_size))
            for unit in units
            if int(unit.blocks) >= int(batch_size)
        ),
        key=lambda item: _stable_score(seed, "loader-workload-unit", item[0].unit_key),
    )
    required_units = min(int(batches), max(1, int(minimum_units)))
    selected: list[tuple[OfflineShardUnit, int]] = []
    capacity = 0
    for item in eligible:
        selected.append(item)
        capacity += item[1]
        if len(selected) >= required_units and capacity >= int(batches):
            break
    if len(selected) < required_units or capacity < int(batches):
        raise RuntimeError(
            f"fixed loader workload requires {batches} full batches across at least "
            f"{required_units} units; available capacity={capacity} units={len(selected)}"
        )
    allocated = [0] * len(selected)
    remaining = int(batches)
    while remaining:
        progressed = False
        for index, (_unit, unit_capacity) in enumerate(selected):
            if allocated[index] >= unit_capacity:
                continue
            allocated[index] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            raise RuntimeError("unable to allocate fixed loader workload")
    return tuple(
        (unit, count)
        for (unit, _capacity), count in zip(selected, allocated, strict=True)
        if count
    )


def _unit_block_refs(unit: OfflineShardUnit) -> tuple[OfflineBlockRef, ...]:
    shard = load_shard(unit.path)
    refs = tuple(
        OfflineBlockRef(
            unit_key=unit.unit_key,
            session_index=session_index,
            block_index=block_index,
            origins=int(block["origin_indices"].numel()),
            ticker=str(session["ticker"]),
            local_date=str(session["local_date"]),
            activity_regime=int(block["activity_regime"]),
            session_phase=str(block["session_phase"]),
            has_condition_target=bool(block["has_condition_target"]),
            unit_index=int(block["unit_index"]),
            block_offset=int(block["block_offset"]),
        )
        for session_index, session in enumerate(shard["sessions"])
        for block_index, block in enumerate(session["blocks"])
    )
    del shard
    return refs


def _fixed_workloads(
    units: Sequence[OfflineShardUnit],
    *,
    warmup_batches: int,
    measured_batches: int,
    batch_size: int,
    minimum_units: int,
    seed: int,
) -> tuple[tuple[OfflineBlockRef, ...], tuple[OfflineBlockRef, ...]]:
    allocation = _allocate_unit_batches(
        units,
        batches=int(warmup_batches) + int(measured_batches),
        batch_size=batch_size,
        minimum_units=min(int(minimum_units), int(measured_batches)),
        seed=seed,
    )
    warmup_by_unit = [0] * len(allocation)
    remaining_warmup = int(warmup_batches)
    # Allocate warmup batches across the same files used by measurement while
    # preserving whole batches per worker-owned unit.
    while remaining_warmup:
        progressed = False
        for index, (_unit, total_batches) in enumerate(allocation):
            if warmup_by_unit[index] >= total_batches - 1:
                continue
            warmup_by_unit[index] += 1
            remaining_warmup -= 1
            progressed = True
            if remaining_warmup == 0:
                break
        if not progressed:
            raise RuntimeError("unable to allocate disjoint warmup and measured workloads")
    warmup: list[OfflineBlockRef] = []
    measured: list[OfflineBlockRef] = []
    for index, (unit, total_batches) in enumerate(allocation):
        refs = sorted(
            _unit_block_refs(unit),
            key=lambda ref: _stable_score(
                seed, "loader-workload-block", ref.unit_key, ref.session_index, ref.block_index
            ),
        )[: total_batches * int(batch_size)]
        split = warmup_by_unit[index] * int(batch_size)
        warmup.extend(refs[:split])
        measured.extend(refs[split:])
    if len(warmup) != int(warmup_batches) * int(batch_size):
        raise RuntimeError("fixed warmup workload has the wrong block count")
    if len(measured) != int(measured_batches) * int(batch_size):
        raise RuntimeError("fixed measured workload has the wrong block count")
    return tuple(warmup), tuple(measured)


def _workload_hash(refs: Sequence[OfflineBlockRef]) -> str:
    identities = sorted(f"{ref.unit_index}:{ref.block_offset}" for ref in refs)
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


def _consume_fixed_refs(
    candidate: Candidate,
    *,
    base: DataConfig,
    units: tuple[OfflineShardUnit, ...],
    refs: tuple[OfflineBlockRef, ...],
    batch_size: int,
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
    dataset = OfflineShardDataset(
        units,
        seed=17,
        shuffle_units=True,
        block_refs=refs,
        batch_size=data.batch_size,
        length_bucket_batches=data.offline_length_bucket_batches,
    )
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
    observed_identities: list[str] = []
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    started = time.perf_counter()
    try:
        while True:
            try:
                batch, wait = prefetcher.next()
            except StopIteration:
                break
            waits.append(wait)
            origins += batch.origin_count
            padded += int(batch.origin_mask.numel())
            blocks += len(batch.tickers)
            observed_identities.extend(
                f"{unit_index}:{block_offset}"
                for unit_index, block_offset in zip(batch.unit_indices, batch.block_offsets, strict=True)
            )
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elapsed = max(time.perf_counter() - started, 1e-9)
        telemetry = prefetcher.telemetry()
    finally:
        prefetcher.close()
    observed_hash = hashlib.sha256(
        "\n".join(sorted(observed_identities)).encode("utf-8")
    ).hexdigest()
    expected_hash = _workload_hash(refs)
    expected_origins = sum(int(ref.origins) for ref in refs)
    if blocks != len(refs) or origins != expected_origins or observed_hash != expected_hash:
        raise RuntimeError(
            "loader candidate did not consume the exact fixed workload: "
            f"blocks={blocks}/{len(refs)} origins={origins}/{expected_origins} "
            f"hash={observed_hash}/{expected_hash}"
        )
    return {
        "batches": len(waits),
        "blocks": blocks,
        "origins": origins,
        "workload_sha256": observed_hash,
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


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(float(value) for value in values))


def _run(
    candidate: Candidate,
    *,
    base: DataConfig,
    units: tuple[OfflineShardUnit, ...],
    warmup_refs: tuple[OfflineBlockRef, ...],
    measured_refs: tuple[OfflineBlockRef, ...],
    batch_size: int,
    repeats: int,
    device: torch.device,
) -> dict[str, object]:
    trials: list[dict[str, object]] = []
    for repeat in range(1, int(repeats) + 1):
        print(f"  repeat {repeat}/{repeats}: warming fixed block set", flush=True)
        warmup = _consume_fixed_refs(
            candidate,
            base=base,
            units=units,
            refs=warmup_refs,
            batch_size=batch_size,
            device=device,
        )
        measured = _consume_fixed_refs(
            candidate,
            base=base,
            units=units,
            refs=measured_refs,
            batch_size=batch_size,
            device=device,
        )
        trials.append({"repeat": repeat, "warmup_elapsed_seconds": warmup["elapsed_seconds"], **measured})
        print(
            f"  repeat {repeat}/{repeats}: {float(measured['origins_per_second']):,.0f} origins/s | "
            f"wait p95={float(measured['wait_p95_ms']):.1f} ms | "
            f"cold={float(measured['cold_start_seconds']):.1f}s",
            flush=True,
        )
    invariant_keys = ("blocks", "origins", "workload_sha256")
    for key in invariant_keys:
        if len({trial[key] for trial in trials}) != 1:
            raise RuntimeError(f"loader benchmark trial workload changed for {key}")
    median_keys = (
        "padded_origin_slots", "origin_slot_utilization", "elapsed_seconds", "cold_start_seconds",
        "origins_per_second", "blocks_per_second", "wait_p50_ms", "wait_p95_ms", "wait_max_ms",
        "host_cache_empty_reads", "device_stage_empty_waits", "device_staged_batches",
        "h2d_completed_batches", "h2d_seconds", "warmup_elapsed_seconds",
    )
    return {
        **dataclasses.asdict(candidate),
        "state": "passed",
        "repeats": int(repeats),
        "measured_batches": int(trials[0]["batches"]),
        "blocks": int(trials[0]["blocks"]),
        "origins": int(trials[0]["origins"]),
        "workload_sha256": str(trials[0]["workload_sha256"]),
        **{key: _median([float(trial[key]) for trial in trials]) for key in median_keys},
        "trials": trials,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if min(args.batch_size, args.warmup_batches, args.measured_batches, args.repeats) <= 0:
        raise ValueError("batch size, warmup batches, measured batches, and repeats must be positive")
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
    worker_values = _ints(args.workers)
    warmup_refs, measured_refs = _fixed_workloads(
        units,
        warmup_batches=int(args.warmup_batches),
        measured_batches=int(args.measured_batches),
        batch_size=int(args.batch_size),
        minimum_units=max(worker_values),
        seed=17,
    )
    measured_workload_hash = _workload_hash(measured_refs)
    measured_origins = sum(int(ref.origins) for ref in measured_refs)
    measured_units = len({ref.unit_key for ref in measured_refs})
    print(
        f"Fixed measured workload: {len(measured_refs):,} blocks | {measured_origins:,} origins | "
        f"{measured_units} units | sha256={measured_workload_hash}",
        flush=True,
    )
    candidates = tuple(
        Candidate(workers, prefetch, cache, bucket)
        for bucket in _ints(args.length_bucket_batches)
        for workers in worker_values
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
                warmup_refs=warmup_refs,
                measured_refs=measured_refs,
                batch_size=int(args.batch_size),
                repeats=int(args.repeats),
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
        "contract": "bar_gpt_v12_offline_loader_benchmark_v2",
        "scope": "loader_and_h2d_only_no_model_compute",
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "shard_root": str(root),
        "device": str(device),
        "batch_size": int(args.batch_size),
        "units": len(units),
        "workload": {
            "seed": 17,
            "warmup_batches": int(args.warmup_batches),
            "warmup_blocks": len(warmup_refs),
            "warmup_sha256": _workload_hash(warmup_refs),
            "measured_batches": int(args.measured_batches),
            "measured_blocks": len(measured_refs),
            "measured_origins": measured_origins,
            "measured_units": measured_units,
            "measured_sha256": measured_workload_hash,
            "repeats": int(args.repeats),
            "identity": "stable unit_index:block_offset set, identical for every candidate and repeat",
        },
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
