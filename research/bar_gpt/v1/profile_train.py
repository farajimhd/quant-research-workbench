from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, TrainConfig
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME
from research.bar_gpt.v1.loader import (
    BarGPTIterableDataset,
    ClickHouseBarStreamConfig,
    make_dataloader,
)
from research.bar_gpt.v1.offline_shards import OfflineShardDataset, discover_offline_units, make_offline_dataloader
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.objectives import compute_loss
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


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\profile_train")

MODEL_SIZE_PRESETS: dict[str, dict[str, int]] = {
    "current": {"d_model": 384, "n_layers": 8, "n_heads": 8, "n_kv_heads": 4},
    "medium": {"d_model": 512, "n_layers": 12, "n_heads": 8, "n_kv_heads": 4},
    "large": {"d_model": 768, "n_layers": 12, "n_heads": 12, "n_kv_heads": 4},
    "xlarge": {"d_model": 1024, "n_layers": 16, "n_heads": 16, "n_kv_heads": 8},
}

# The joint default deliberately uses one microbatch per optimizer step. This
# bounds the fit/throughput sweep across four architectures; the result records
# the accumulation needed to recover the production target effective batch.
DEFAULT_JOINT_CANDIDATES = ",".join(
    f"{model}:4096:{microbatch}:1:16:1:0"
    for model, microbatches in (
        ("current", (8, 16, 24, 32)),
        ("medium", (4, 8, 12, 16)),
        ("large", (2, 4, 8, 12)),
        ("xlarge", (1, 2, 4, 8)),
    )
    for microbatch in microbatches
)


@dataclass(frozen=True, slots=True)
class ProfileCandidate:
    origin_bars: int
    microbatch: int
    accumulation: int
    workers: int
    cuda_prefetch: bool
    compile_model: bool = False
    model_size: str = "current"

    @property
    def name(self) -> str:
        return (
            f"model-{self.model_size}_origins-{self.origin_bars}_micro-{self.microbatch}_accum-{self.accumulation}_"
            f"workers-{self.workers}_cuda-prefetch-{int(self.cuda_prefetch)}_compile-{int(self.compile_model)}"
        )


@dataclass(frozen=True, slots=True)
class ProfileResult:
    candidate: ProfileCandidate
    state: str
    optimizer_steps: int
    origins: int
    encoded_tokens: int
    elapsed_seconds: float
    loader_wait_seconds: float
    gpu_seconds: float
    origins_per_second: float
    encoded_tokens_per_second: float
    peak_allocated_bytes: int
    peak_reserved_bytes: int
    total_device_bytes: int
    memory_fraction: float
    model_parameters: int
    effective_blocks_per_update: int
    recommended_accumulation: int
    message: str = ""


def _parse_candidates(value: str) -> tuple[ProfileCandidate, ...]:
    candidates = []
    for item in value.split(","):
        raw_parts = item.split(":")
        if raw_parts[0].isdigit():
            if len(raw_parts) not in (5, 6):
                raise ValueError(
                    "legacy profile candidates use origin:microbatch:accumulation:workers:prefetch[:compile]"
                )
            model_size = "current"
            parts = tuple(int(part) for part in raw_parts)
        else:
            if len(raw_parts) not in (6, 7):
                raise ValueError(
                    "joint profile candidates use model:origin:microbatch:accumulation:workers:prefetch[:compile]"
                )
            model_size = raw_parts[0].strip().lower()
            if model_size not in MODEL_SIZE_PRESETS:
                raise ValueError(
                    f"unknown model size {model_size!r}; expected one of {tuple(MODEL_SIZE_PRESETS)}"
                )
            parts = tuple(int(part) for part in raw_parts[1:])
        origin, micro, accumulation, workers, prefetch = parts[:5]
        compile_model = bool(parts[5]) if len(parts) == 6 else False
        if min(origin, micro, accumulation, workers) <= 0:
            raise ValueError("origin, microbatch, accumulation, and workers must be positive")
        candidates.append(
            ProfileCandidate(
                origin,
                micro,
                accumulation,
                workers,
                bool(prefetch),
                compile_model,
                model_size,
            )
        )
    if not candidates:
        raise ValueError("at least one profiler candidate is required")
    return tuple(candidates)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the complete BarGPT loader, target, model, backward and optimizer path.")
    parser.add_argument("--start-date", default="2025-10-01")
    parser.add_argument("--end-date", default="2026-01-01")
    parser.add_argument("--tickers", default=",".join(DataConfig().training_tickers))
    parser.add_argument(
        "--candidates",
        default=DEFAULT_JOINT_CANDIDATES,
        help=(
            "model:origin:microbatch:accumulation:workers:cuda_prefetch[:compile] entries; "
            "the legacy format without model remains current-size compatible"
        ),
    )
    parser.add_argument("--warmup-steps", type=int, default=1)
    parser.add_argument("--measured-steps", type=int, default=8)
    parser.add_argument("--ready-queue-blocks", type=int, default=512)
    parser.add_argument("--worker-prefetch-batches", type=int, default=4)
    parser.add_argument("--target-effective-blocks", type=int, default=32)
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=1)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--data-source", choices=("offline", "clickhouse"), default="offline")
    parser.add_argument("--offline-shard-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v2")
    return parser.parse_args(list(argv) if argv is not None else None)


def _device(value: str) -> torch.device:
    if value == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    return torch.device("cuda" if value == "auto" and torch.cuda.is_available() else ("cpu" if value == "auto" else value))


def _data(args: argparse.Namespace, candidate: ProfileCandidate) -> DataConfig:
    tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    return DataConfig(
        loader_stream_contract_version=4 if args.data_source == "offline" else 3,
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=str(args.end_date),
        validation_slices=(),
        origin_bars_1s=candidate.origin_bars,
        batch_size=candidate.microbatch,
        loader_workers=candidate.workers,
        ready_queue_blocks=int(args.ready_queue_blocks),
        worker_prefetch_batches=int(args.worker_prefetch_batches),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        coverage_mode="sequential",
        coverage_blocks_per_unit=16,
    )


def _model_config(candidate: ProfileCandidate) -> BarGPTConfig:
    return BarGPTConfig(**MODEL_SIZE_PRESETS[candidate.model_size])


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


class ProfileReporter:
    def __init__(self, layout: str) -> None:
        self.layout = layout
        self.rich = layout == "rich" or (layout == "auto" and sys.stdout.isatty())
        self._progress = None
        self._task = None
        if self.rich:
            from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

            self._progress = Progress(
                SpinnerColumn(), TextColumn("[bold cyan]{task.description}"), BarColumn(),
                MofNCompleteColumn(), TimeElapsedColumn(), transient=False,
            )
            self._progress.start()

    def start(self, candidate: ProfileCandidate, index: int, total: int) -> None:
        if self._progress is not None:
            if self._task is None:
                self._task = self._progress.add_task(f"candidate {index}/{total}: {candidate.name}", total=1)
            else:
                self._progress.reset(self._task, total=1, completed=0, description=f"candidate {index}/{total}: {candidate.name}")
        elif self.layout != "none":
            print(f"profile {index}/{total} active: {candidate.name}", flush=True)

    def step(self, candidate: ProfileCandidate, completed: int, total: int) -> None:
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, total=total, completed=completed, description=f"measuring: {candidate.name}")
        elif self.layout == "text" and (completed == 1 or completed == total):
            print(f"profile measured: {candidate.name} {completed}/{total} updates", flush=True)

    def final(self, results: list[ProfileResult], selected: ProfileResult | None) -> None:
        if self.layout == "none":
            return
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        if self.rich:
            from rich.console import Console
            from rich.table import Table
            table = Table(title="BarGPT end-to-end training profile")
            table.add_column("candidate")
            table.add_column("state")
            table.add_column("origins/s", justify="right")
            table.add_column("tokens/s", justify="right")
            table.add_column("GPU memory", justify="right")
            for result in results:
                table.add_row(
                    result.candidate.name,
                    result.state,
                    f"{result.origins_per_second:,.0f}",
                    f"{result.encoded_tokens_per_second:,.0f}",
                    f"{result.memory_fraction * 100:,.1f}%",
                )
            Console().print(table)
        for result in results:
            print(json.dumps(asdict(result), default=str, sort_keys=True), flush=True)
        print(f"selected={selected.candidate.name if selected else 'none'}", flush=True)


def _profile_candidate(
    args: argparse.Namespace,
    candidate: ProfileCandidate,
    *,
    device: torch.device,
    reporter: ProfileReporter,
    preflight_complete: bool,
) -> ProfileResult:
    data = _data(args, candidate)
    data.validate()
    if candidate.workers > len(data.training_tickers):
        raise ValueError(
            "worker-owned profiling cannot use more workers than training tickers: "
            f"workers={candidate.workers}, training_tickers={len(data.training_tickers)}"
        )
    if args.data_source == "offline":
        units = discover_offline_units(
            Path(args.offline_shard_root), data, tickers=data.training_tickers,
            start_date=data.start_date, end_date=data.end_date,
        )
        dataset = OfflineShardDataset(units, seed=17, shuffle_units=True)
        loader = make_offline_dataloader(dataset, data, drop_last=False)
    else:
        url, user, password = default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
        clickhouse = ClickHouseHttpClient(url, user, password)
        if not preflight_complete:
            preflight(clickhouse, data)
        stream = ClickHouseBarStreamConfig(
            url=url, user=user, password=password, database=data.database,
            table=data.one_second_table, max_threads=data.clickhouse_max_threads_per_worker,
            max_block_size=data.clickhouse_max_block_size, max_memory_usage=data.clickhouse_max_memory_usage,
            query_days=data.clickhouse_query_days,
            max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
            retry_attempts=data.clickhouse_retry_attempts,
            retry_initial_seconds=data.clickhouse_retry_initial_seconds,
            retry_max_seconds=data.clickhouse_retry_max_seconds,
        )
        dataset = BarGPTIterableDataset(data_config=data, stream_config=stream, split="train", seed=17)
        loader = make_dataloader(dataset, data, drop_last=True)
    model_config = _model_config(candidate)
    model = BarGPTV1(model_config).to(device)
    model_parameters = sum(parameter.numel() for parameter in model.parameters())
    if candidate.compile_model:
        if not hasattr(torch, "compile"):
            raise RuntimeError("torch.compile is unavailable in this PyTorch build")
        model = torch.compile(model, dynamic=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.1, foreach=device.type == "cuda")
    train_config = TrainConfig(gradient_accumulation_steps=candidate.accumulation, cuda_prefetch=candidate.cuda_prefetch)
    prefetcher = DeviceBatchPrefetcher(
        loader,
        device,
        enabled=candidate.cuda_prefetch,
        host_cache_batches=max(1, math.ceil(data.ready_queue_blocks / data.batch_size)),
    )
    total_steps = int(args.warmup_steps) + int(args.measured_steps)
    measured_origins = measured_tokens = 0
    measured_loader = measured_gpu = 0.0
    measured_started = 0.0
    try:
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        for step in range(total_steps):
            if step == int(args.warmup_steps):
                measured_started = time.perf_counter()
                if device.type == "cuda":
                    torch.cuda.reset_peak_memory_stats(device)
            optimizer.zero_grad(set_to_none=True)
            step_origins = step_tokens = 0
            step_loader = step_gpu = 0.0
            gpu_event_pairs: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
            for _micro in range(candidate.accumulation):
                batch, wait = prefetcher.next()
                started = time.perf_counter()
                gpu_start_event = gpu_end_event = None
                if device.type == "cuda":
                    gpu_start_event = torch.cuda.Event(enable_timing=True)
                    gpu_end_event = torch.cuda.Event(enable_timing=True)
                    gpu_start_event.record()
                with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
                    output = model(
                        batch.views,
                        timeframe_us=TIMEFRAME_US_BY_NAME,
                        pathway_ids=PATHWAY_ID_BY_NAME,
                        base_view="1s",
                        origin_indices=batch.origin_indices,
                        asof_indices=batch.asof_indices,
                        horizon_ids=torch.arange(len(data.horizons_us), device=device),
                    )
                    loss = compute_loss(output, batch, train_config, model_config.quantiles).loss / candidate.accumulation
                loss.backward()
                if gpu_end_event is not None and gpu_start_event is not None:
                    gpu_end_event.record()
                    gpu_event_pairs.append((gpu_start_event, gpu_end_event))
                else:
                    step_gpu += time.perf_counter() - started
                step_loader += wait
                step_origins += batch.origin_count
                step_tokens += sum(int(value.shape[0] * value.shape[1]) for value in batch.views.values())
            started = time.perf_counter()
            step_start_event = step_end_event = None
            if device.type == "cuda":
                step_start_event = torch.cuda.Event(enable_timing=True)
                step_end_event = torch.cuda.Event(enable_timing=True)
                step_start_event.record()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            if step_end_event is not None and step_start_event is not None:
                step_end_event.record()
                gpu_event_pairs.append((step_start_event, step_end_event))
                step_end_event.synchronize()
                step_gpu += sum(start.elapsed_time(end) / 1_000.0 for start, end in gpu_event_pairs)
            else:
                step_gpu += time.perf_counter() - started
            if step >= int(args.warmup_steps):
                measured_origins += step_origins
                measured_tokens += step_tokens
                measured_loader += step_loader
                measured_gpu += step_gpu
                reporter.step(candidate, step - int(args.warmup_steps) + 1, int(args.measured_steps))
    finally:
        prefetcher.close()
    elapsed = max(time.perf_counter() - measured_started, 1e-9)
    if device.type == "cuda":
        allocated = int(torch.cuda.max_memory_allocated(device))
        reserved = int(torch.cuda.max_memory_reserved(device))
        total_device = int(torch.cuda.get_device_properties(device).total_memory)
    else:
        allocated = reserved = total_device = 0
    return ProfileResult(
        candidate=candidate,
        state="passed",
        optimizer_steps=int(args.measured_steps),
        origins=measured_origins,
        encoded_tokens=measured_tokens,
        elapsed_seconds=elapsed,
        loader_wait_seconds=measured_loader,
        gpu_seconds=measured_gpu,
        origins_per_second=measured_origins / elapsed,
        encoded_tokens_per_second=measured_tokens / elapsed,
        peak_allocated_bytes=allocated,
        peak_reserved_bytes=reserved,
        total_device_bytes=total_device,
        memory_fraction=reserved / total_device if total_device else 0.0,
        model_parameters=model_parameters,
        effective_blocks_per_update=candidate.microbatch * candidate.accumulation,
        recommended_accumulation=max(1, math.ceil(int(args.target_effective_blocks) / candidate.microbatch)),
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.warmup_steps < 0 or args.measured_steps <= 0 or args.target_effective_blocks <= 0:
        raise ValueError("warmup steps cannot be negative and measured steps must be positive")
    if args.data_source == "clickhouse":
        load_env_files(discover_clickhouse_env_files(), verbose=True)
    device = _device(str(args.device))
    candidates = _parse_candidates(str(args.candidates))
    output_root = Path(args.output_root)
    run_root = output_root / time.strftime("%Y%m%d_%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    reporter = ProfileReporter(str(args.progress_layout))
    results: list[ProfileResult] = []
    oom_microbatch: dict[tuple[str, int, int, bool], int] = {}
    jsonl = run_root / "profile.jsonl"
    # Candidates vary only loader/model shape; the authority/schema audit is
    # invariant.  Run it once so the measured sweep does not pay the same
    # ClickHouse metadata cost for every batch-size candidate.
    first_data = _data(args, candidates[0])
    first_data.validate()
    if args.data_source == "clickhouse":
        preflight(
            ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()),
            first_data,
        )
    for index, candidate in enumerate(candidates, start=1):
        reporter.start(candidate, index, len(candidates))
        oom_key = (candidate.model_size, candidate.origin_bars, candidate.workers, candidate.compile_model)
        threshold = oom_microbatch.get(oom_key)
        if threshold is not None and candidate.microbatch >= threshold:
            result = ProfileResult(
                candidate=candidate,
                state="skipped_after_oom",
                optimizer_steps=0,
                origins=0,
                encoded_tokens=0,
                elapsed_seconds=0.0,
                loader_wait_seconds=0.0,
                gpu_seconds=0.0,
                origins_per_second=0.0,
                encoded_tokens_per_second=0.0,
                peak_allocated_bytes=0,
                peak_reserved_bytes=0,
                total_device_bytes=0,
                memory_fraction=0.0,
                model_parameters=0,
                effective_blocks_per_update=candidate.microbatch * candidate.accumulation,
                recommended_accumulation=max(1, math.ceil(int(args.target_effective_blocks) / candidate.microbatch)),
                message=f"skipped because microbatch {threshold} already exhausted this model/device shape",
            )
        else:
            try:
                result = _profile_candidate(
                    args,
                    candidate,
                    device=device,
                    reporter=reporter,
                    preflight_complete=True,
                )
            except (torch.cuda.OutOfMemoryError, RuntimeError, OSError) as exc:
                state = "oom" if "out of memory" in str(exc).lower() else "failed"
                if state == "oom":
                    oom_microbatch[oom_key] = candidate.microbatch
                result = ProfileResult(
                    candidate=candidate,
                    state=state,
                    optimizer_steps=0,
                    origins=0,
                    encoded_tokens=0,
                    elapsed_seconds=0.0,
                    loader_wait_seconds=0.0,
                    gpu_seconds=0.0,
                    origins_per_second=0.0,
                    encoded_tokens_per_second=0.0,
                    peak_allocated_bytes=0,
                    peak_reserved_bytes=0,
                    total_device_bytes=0,
                    memory_fraction=0.0,
                    model_parameters=0,
                    effective_blocks_per_update=candidate.microbatch * candidate.accumulation,
                    recommended_accumulation=max(1, math.ceil(int(args.target_effective_blocks) / candidate.microbatch)),
                    message=str(exc),
                )
        results.append(result)
        with jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), default=str, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()
    eligible = [result for result in results if result.state == "passed" and result.memory_fraction <= 0.90]
    selected = max(eligible, key=lambda result: result.origins_per_second, default=None)
    selected_by_model = {
        model_size: asdict(best)
        for model_size in MODEL_SIZE_PRESETS
        if (best := max(
            (result for result in eligible if result.candidate.model_size == model_size),
            key=lambda result: result.origins_per_second,
            default=None,
        )) is not None
    }
    summary = {
        "device": str(device),
        "args": vars(args),
        "selected": asdict(selected) if selected else None,
        "selected_by_model": selected_by_model,
        "results": [asdict(result) for result in results],
    }
    _atomic_json(run_root / "summary.json", summary)
    _atomic_json(output_root / "selected_profile.json", summary)
    reporter.final(results, selected)
    return 0 if selected is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
