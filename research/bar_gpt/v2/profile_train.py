from __future__ import annotations

import argparse
import datetime as dt
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
import torch.nn.functional as F

from research.bar_gpt.v2 import LEARNING_CONTRACT
from research.bar_gpt.v2.config import (
    OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES,
    OFFLINE_PRODUCTION_LOADER_WORKERS,
    OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
    BarGPTConfig,
    DataConfig,
    MODEL_SIZE_PRESETS,
    TrainConfig,
)
from research.bar_gpt.v2.data import BarGPTBatch, PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME
from research.bar_gpt.v2.loader import (
    BarGPTIterableDataset,
    ClickHouseBarStreamConfig,
    make_dataloader,
)
from research.bar_gpt.v2.offline_shards import (
    OfflineShardDataset,
    discover_offline_units,
    hydrate_offline_runtime_config,
    make_offline_dataloader,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v2.model import BarGPTV2
from research.bar_gpt.v2.model_discovery import (
    load_discovery_manifest,
    panel_refs,
)
from research.bar_gpt.v2.metrics import ValidationAccumulator
from research.bar_gpt.v2.objectives import compute_loss
from research.bar_gpt.v2.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v2.train import preflight
from research.bar_gpt.v2.targets import (
    AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT,
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    RETURN_CLASS_COUNT,
    RETURN_TARGET_COUNT,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v2\profile_train")
SDPA_PROBE_MAX_LENGTH = 512
PROFILE_MEMORY_LIMIT = 0.90
# Post-fusion measurements track close to linear microbatch scaling. Retain a
# bounded 3% projection cushion so safe intermediate shapes such as Current=24
# are measured, while the 90% hard eligibility gate still rejects paging-prone
# tails from selection even if a projection underestimates them.
PROFILE_MEMORY_PROJECTION_MARGIN = 1.03

# The joint default deliberately uses one microbatch per optimizer step. It
# revisits the three practical comparison sizes after projection fusion and
# crosses each model shape with bounded length-bucketing windows. Candidate
# microbatches ascend independently within each model/bucket lane, allowing the
# profiler's 90% projected-memory guard to stop only an unsafe tail. XLarge
# remains explicit-only because its cost is disproportionate for this sweep.
DEFAULT_PROFILE_LENGTH_BUCKET_BATCHES: tuple[int, ...] = (4, 16, 32)
DEFAULT_PROFILE_MICROBATCHES: dict[str, tuple[int, ...]] = {
    "current": (16, 20, 24, 28),
    "medium": (8, 10, 12, 14),
    "large": (6, 8, 10),
}
DEFAULT_JOINT_CANDIDATES = ",".join(
    f"{model}:4096:{microbatch}:1:{OFFLINE_PRODUCTION_LOADER_WORKERS}:1:0:"
    f"{bucket_batches}"
    for bucket_batches in DEFAULT_PROFILE_LENGTH_BUCKET_BATCHES
    for model, microbatches in DEFAULT_PROFILE_MICROBATCHES.items()
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
    length_bucket_batches: int | None = None

    @property
    def name(self) -> str:
        return (
            f"model-{self.model_size}_origins-{self.origin_bars}_micro-{self.microbatch}_accum-{self.accumulation}_"
            f"workers-{self.workers}_cuda-prefetch-{int(self.cuda_prefetch)}_compile-{int(self.compile_model)}_"
            f"length-bucket-{self.length_bucket_batches if self.length_bucket_batches is not None else 'default'}"
        )


@dataclass(frozen=True, slots=True)
class ProfileResult:
    candidate: ProfileCandidate
    state: str
    optimizer_steps: int
    origins: int
    encoded_tokens: int
    valid_encoded_tokens: int
    encoded_padding_fraction: float
    encoded_tokens_by_view: dict[str, int]
    valid_encoded_tokens_by_view: dict[str, int]
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
    forward_seconds: float = 0.0
    backward_seconds: float = 0.0
    optimizer_seconds: float = 0.0
    h2d_seconds: float = 0.0
    h2d_completed_batches: int = 0
    host_cache_empty_reads: int = 0
    device_stage_empty_waits: int = 0
    metric_seconds: float = 0.0
    projected_metric_overhead_fraction: float = 0.0
    sdpa_audit_state: str = "not_run"
    sdpa_backend_counts: tuple[tuple[str, int], ...] = ()
    sdpa_expected_calls: int = 0
    sdpa_audit_seconds: float = 0.0
    sdpa_audit_message: str = ""
    message: str = ""


def _projected_memory_fraction(
    previous: ProfileResult,
    candidate: ProfileCandidate,
) -> float:
    """Conservatively project peak memory before launching a larger batch."""
    if previous.candidate.microbatch <= 0:
        return 0.0
    ratio = candidate.microbatch / previous.candidate.microbatch
    return previous.memory_fraction * ratio * PROFILE_MEMORY_PROJECTION_MARGIN


def _sdpa_backend(key: str) -> str | None:
    """Classify the concrete forward SDPA operator emitted by torch.profiler."""
    lowered = key.lower()
    if "backward" in lowered:
        return None
    if "scaled_dot_product" not in lowered and "flash_attention" not in lowered:
        return None
    if "flash" in lowered:
        return "flash"
    if "efficient" in lowered:
        return "memory_efficient"
    if "cudnn" in lowered:
        return "cudnn"
    if "math" in lowered:
        return "math"
    # The public dispatcher is always present and does not identify the
    # selected implementation, so exclude it rather than double counting.
    if lowered.endswith("scaled_dot_product_attention"):
        return None
    return "other"


def _profile_sdpa_backends(
    batch: BarGPTBatch,
    data: DataConfig,
    model_config: BarGPTConfig,
    *,
    device: torch.device,
) -> tuple[str, tuple[tuple[str, int], ...], int, float, str]:
    """Audit bounded representative causal/local SDPA calls after timing."""
    if device.type != "cuda":
        return "not_run", (), 0, 0.0, "CUDA is required for an SDPA kernel audit"
    view_lengths = {name: int(value.shape[1]) for name, value in batch.views.items()}
    local_lengths = [
        length
        for name, length in view_lengths.items()
        if int(data.attention_window_by_name[name]) < length
    ]
    causal_lengths = [
        length
        for name, length in view_lengths.items()
        if int(data.attention_window_by_name[name]) >= length
    ]
    probes: list[tuple[str, int]] = []
    if local_lengths:
        probes.append(("local_mask", min(SDPA_PROBE_MAX_LENGTH, max(local_lengths))))
    if causal_lengths:
        probes.append(("causal", min(SDPA_PROBE_MAX_LENGTH, max(causal_lengths))))
    counts: dict[str, int] = {}
    started = time.perf_counter()
    try:
        head_dim = model_config.d_model // model_config.n_heads
        for mode, length in probes:
            kv_heads = model_config.n_heads if mode == "local_mask" else model_config.n_kv_heads
            query = torch.randn(
                (1, model_config.n_heads, length, head_dim),
                device=device,
                dtype=torch.bfloat16,
                requires_grad=True,
            )
            key = torch.randn(
                (1, kv_heads, length, head_dim),
                device=device,
                dtype=torch.bfloat16,
                requires_grad=True,
            )
            value = torch.randn_like(key, requires_grad=True)
            mask = None
            if mode == "local_mask":
                positions = torch.arange(length, device=device)
                mask = (positions[None, :] <= positions[:, None]) & (
                    positions[None, :] > positions[:, None] - min(128, length)
                )
            with torch.profiler.profile(
                activities=[torch.profiler.ProfilerActivity.CPU, torch.profiler.ProfilerActivity.CUDA],
            ) as trace:
                audited_output = F.scaled_dot_product_attention(
                    query,
                    key,
                    value,
                    attn_mask=mask,
                    dropout_p=float(model_config.dropout),
                    is_causal=mode == "causal",
                    enable_gqa=mode == "causal" and model_config.n_kv_heads != model_config.n_heads,
                )
            torch.cuda.synchronize(device)
            del audited_output, query, key, value, mask
            for event in trace.key_averages():
                backend = _sdpa_backend(str(event.key))
                if backend is not None:
                    label = f"{mode}/{backend}"
                    counts[label] = counts.get(label, 0) + int(event.count)
    except (RuntimeError, OSError) as exc:
        return "failed", (), len(probes), time.perf_counter() - started, str(exc)
    elapsed = time.perf_counter() - started
    if not counts:
        return (
            "unreported",
            (),
            len(probes),
            elapsed,
            "torch.profiler emitted no concrete SDPA backend operator",
        )
    lengths = ", ".join(f"{mode}={length}" for mode, length in probes)
    return "passed", tuple(sorted(counts.items())), len(probes), elapsed, f"bounded probe lengths: {lengths}"


def _parse_candidates(value: str) -> tuple[ProfileCandidate, ...]:
    candidates = []
    for item in value.split(","):
        raw_parts = item.split(":")
        if raw_parts[0].isdigit():
            if len(raw_parts) not in (5, 6, 7):
                raise ValueError(
                    "legacy profile candidates use origin:microbatch:accumulation:workers:"
                    "prefetch[:compile[:length_bucket_batches]]"
                )
            model_size = "current"
            parts = tuple(int(part) for part in raw_parts)
        else:
            if len(raw_parts) not in (6, 7, 8):
                raise ValueError(
                    "joint profile candidates use model:origin:microbatch:accumulation:workers:"
                    "prefetch[:compile[:length_bucket_batches]]"
                )
            model_size = raw_parts[0].strip().lower()
            if model_size not in MODEL_SIZE_PRESETS:
                raise ValueError(
                    f"unknown model size {model_size!r}; expected one of {tuple(MODEL_SIZE_PRESETS)}"
                )
            parts = tuple(int(part) for part in raw_parts[1:])
        origin, micro, accumulation, workers, prefetch = parts[:5]
        compile_model = bool(parts[5]) if len(parts) == 6 else False
        if len(parts) >= 7:
            compile_model = bool(parts[5])
            length_bucket_batches = int(parts[6])
        else:
            length_bucket_batches = None
        if min(origin, micro, accumulation, workers) <= 0:
            raise ValueError("origin, microbatch, accumulation, and workers must be positive")
        if length_bucket_batches is not None and length_bucket_batches <= 0:
            raise ValueError("length_bucket_batches must be positive")
        candidates.append(
            ProfileCandidate(
                origin,
                micro,
                accumulation,
                workers,
                bool(prefetch),
                compile_model,
                model_size,
                length_bucket_batches,
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
            "model:origin:microbatch:accumulation:workers:cuda_prefetch"
            "[:compile[:length_bucket_batches]] entries; "
            "the legacy format without model remains current-size compatible"
        ),
    )
    parser.add_argument("--warmup-steps", type=int, default=1)
    parser.add_argument("--measured-steps", type=int, default=8)
    parser.add_argument(
        "--ready-queue-blocks",
        type=int,
        default=OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    )
    parser.add_argument(
        "--worker-prefetch-batches",
        type=int,
        default=OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
    )
    parser.add_argument(
        "--offline-length-bucket-batches",
        type=int,
        default=OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES,
    )
    parser.add_argument("--target-effective-blocks", type=int, default=32)
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=1)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--sdpa-audit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--data-source", choices=("offline", "clickhouse"), default="offline")
    parser.add_argument("--offline-shard-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
    parser.add_argument(
        "--experiment-manifest",
        default="",
        help=(
            "optional fixed-panel manifest; when set, profiling samples its "
            "explicit block population instead of a date-prefix stream"
        ),
    )
    parser.add_argument("--experiment-panel", choices=("train", "monitor", "validation"), default="train")
    return parser.parse_args(list(argv) if argv is not None else None)


def _device(value: str) -> torch.device:
    if value == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    return torch.device("cuda" if value == "auto" and torch.cuda.is_available() else ("cpu" if value == "auto" else value))


def _data(args: argparse.Namespace, candidate: ProfileCandidate) -> DataConfig:
    tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    runtime = DataConfig(
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
        offline_length_bucket_batches=(
            int(args.offline_length_bucket_batches)
            if candidate.length_bucket_batches is None
            else int(candidate.length_bucket_batches)
        ),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        coverage_mode="sequential",
        coverage_blocks_per_unit=16,
    )
    if str(args.data_source) != "offline":
        return runtime
    storage = hydrate_offline_runtime_config(Path(args.offline_shard_root), runtime)
    if int(storage.origin_bars_1s) != int(candidate.origin_bars):
        raise ValueError(
            "profile candidate origin geometry conflicts with the immutable shard manifest: "
            f"candidate={candidate.origin_bars} shard={storage.origin_bars_1s}"
        )
    return storage


def _model_config(candidate: ProfileCandidate) -> BarGPTConfig:
    return BarGPTConfig(**MODEL_SIZE_PRESETS[candidate.model_size])


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _runtime_evidence(device: torch.device) -> dict[str, str]:
    cuda_available = device.type == "cuda" and torch.cuda.is_available()
    return {
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda or "unavailable"),
        "device_name": torch.cuda.get_device_name(device) if cuda_available else str(device),
    }


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

    def configuration(
        self,
        args: argparse.Namespace,
        candidates: tuple[ProfileCandidate, ...],
        device: torch.device,
    ) -> None:
        if self.layout == "none":
            return
        train = TrainConfig()
        data = DataConfig()
        model_defaults = BarGPTConfig()
        intraday_context = ", ".join(f"{name}={bars}" for name, bars in data.intraday_context_bars)
        calendar_context = ", ".join(f"{name}={bars}" for name, bars in data.calendar_context_bars)
        horizons = ", ".join(f"{value // 1_000_000}s" for value in data.horizons_us)
        runtime = _runtime_evidence(device)
        audit_description = (
            "up to two bounded representative probes after timing; excluded from throughput"
            if bool(args.sdpa_audit)
            else "disabled by --no-sdpa-audit"
        )
        if self.rich:
            from rich.console import Console
            from rich.table import Table

            console = Console()
            run = Table(title="BarGPT performance profile", show_header=False)
            run.add_column("Setting", style="bold cyan")
            run.add_column("Value")
            run.add_row("W&B", "disabled; no initialization, recording, or upload")
            run.add_row("Device", str(device))
            run.add_row(
                "Runtime",
                f"{runtime['device_name']} | PyTorch {runtime['torch_version']} | CUDA {runtime['cuda_version']}",
            )
            run.add_row("Data", f"{args.data_source} | [{args.start_date}, {args.end_date})")
            run.add_row("Shard root", str(args.offline_shard_root))
            run.add_row("Warm-up / measured", f"{args.warmup_steps} / {args.measured_steps} optimizer updates")
            run.add_row(
                "Metric-cost audit",
                f"one isolated reduction; amortized across {train.training_metrics_interval_samples:,} origins",
            )
            run.add_row(
                "SDPA kernel audit",
                audit_description,
            )
            run.add_row("Precision", "BF16 autocast")
            run.add_row("Intraday context", intraday_context)
            run.add_row("Calendar context", calendar_context)
            run.add_row("Prediction horizons", horizons)
            run.add_row(
                "Target contract",
                f"physical {CONTINUOUS_TARGET_COUNT}+{AVAILABILITY_TARGET_COUNT}={model_defaults.target_dim} | "
                f"AR {AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT}+{AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT}="
                f"{model_defaults.autoregressive_target_dim} | return classes "
                f"{RETURN_TARGET_COUNT}x{RETURN_CLASS_COUNT}",
            )
            run.add_row(
                "Shared model settings",
                f"dropout {model_defaults.dropout:g} | FF multiplier {model_defaults.ff_multiplier:.4g} | "
                f"horizon rank {model_defaults.horizon_rank} | quantiles {', '.join(map(str, model_defaults.quantiles))}",
            )
            run.add_row("Optimizer", f"AdamW | peak LR {train.learning_rate:g} | weight decay {train.weight_decay:g}")
            run.add_row("Gradient clipping", f"global norm {train.grad_clip_norm:g}")
            run.add_row("Loss contract", "independently averaged per target, then summed without coefficients")
            run.add_row(
                "Scheduler in trainer",
                f"sample-clock linear warm-up {train.warmup_fraction:.1%} from LR "
                f"{train.minimum_learning_rate:g} to {train.learning_rate:g} | then cosine warm restarts | "
                f"cycle {train.cosine_cycle_samples:,} post-warm-up origins | restart decay {train.cosine_restart_decay:g}",
            )
            console.print(run)
            candidates_table = Table(title="Candidates")
            for name in (
                "Model", "Width", "Layers", "Heads", "KV", "Micro", "Accum",
                "Effective", "Workers", "Bucket", "Prefetch", "Compile",
            ):
                candidates_table.add_column(name, justify="right" if name != "Model" else "left")
            for candidate in candidates:
                model = MODEL_SIZE_PRESETS[candidate.model_size]
                candidates_table.add_row(
                    candidate.model_size,
                    str(model["d_model"]),
                    str(model["n_layers"]),
                    str(model["n_heads"]),
                    str(model["n_kv_heads"]),
                    str(candidate.microbatch),
                    str(candidate.accumulation),
                    str(candidate.microbatch * candidate.accumulation),
                    str(candidate.workers),
                    str(
                        args.offline_length_bucket_batches
                        if candidate.length_bucket_batches is None
                        else candidate.length_bucket_batches
                    ),
                    "yes" if candidate.cuda_prefetch else "no",
                    "yes" if candidate.compile_model else "no",
                )
            console.print(candidates_table)
            return
        print("BarGPT performance profile", flush=True)
        print("  W&B                 disabled; no initialization, recording, or upload", flush=True)
        print(f"  Device              {device}", flush=True)
        print(
            f"  Runtime             {runtime['device_name']}, PyTorch {runtime['torch_version']}, "
            f"CUDA {runtime['cuda_version']}",
            flush=True,
        )
        print(f"  Data                {args.data_source} [{args.start_date}, {args.end_date})", flush=True)
        print(f"  Shard root          {args.offline_shard_root}", flush=True)
        print(f"  Warm-up / measured  {args.warmup_steps} / {args.measured_steps} optimizer updates", flush=True)
        print(
            f"  Metric-cost audit   one isolated reduction, amortized across "
            f"{train.training_metrics_interval_samples:,} origins",
            flush=True,
        )
        print(
            f"  SDPA kernel audit   {audit_description}",
            flush=True,
        )
        print("  Precision           BF16 autocast", flush=True)
        print(f"  Intraday context    {intraday_context}", flush=True)
        print(f"  Calendar context    {calendar_context}", flush=True)
        print(f"  Horizons            {horizons}", flush=True)
        print(
            f"  Target contract     physical {CONTINUOUS_TARGET_COUNT}+{AVAILABILITY_TARGET_COUNT}="
            f"{model_defaults.target_dim}, AR {AUTOREGRESSIVE_CONTINUOUS_TARGET_COUNT}+"
            f"{AUTOREGRESSIVE_AVAILABILITY_TARGET_COUNT}={model_defaults.autoregressive_target_dim}, "
            f"return classes {RETURN_TARGET_COUNT}x{RETURN_CLASS_COUNT}",
            flush=True,
        )
        print(
            f"  Model settings      dropout {model_defaults.dropout:g}, FF multiplier {model_defaults.ff_multiplier:.4g}, "
            f"horizon rank {model_defaults.horizon_rank}, quantiles {', '.join(map(str, model_defaults.quantiles))}",
            flush=True,
        )
        print(
            f"  Optimizer           AdamW, peak LR {train.learning_rate:g}, weight decay {train.weight_decay:g}, "
            f"gradient norm clip {train.grad_clip_norm:g}",
            flush=True,
        )
        print("  Loss contract       independently averaged per target, then summed without coefficients", flush=True)
        print(
            f"  Scheduler           linear warm-up {train.warmup_fraction:.1%} from LR "
            f"{train.minimum_learning_rate:g} to {train.learning_rate:g}, then cosine warm restarts, "
            f"cycle {train.cosine_cycle_samples:,} post-warm-up origins, restart decay {train.cosine_restart_decay:g}",
            flush=True,
        )
        print("Candidates", flush=True)
        for candidate in candidates:
            model = MODEL_SIZE_PRESETS[candidate.model_size]
            print(
                f"  {candidate.model_size:<8} width={model['d_model']} layers={model['n_layers']} "
                f"heads={model['n_heads']} kv={model['n_kv_heads']} micro={candidate.microbatch} "
                f"accum={candidate.accumulation} effective={candidate.microbatch * candidate.accumulation} "
                f"workers={candidate.workers} bucket="
                f"{args.offline_length_bucket_batches if candidate.length_bucket_batches is None else candidate.length_bucket_batches} "
                f"prefetch={'yes' if candidate.cuda_prefetch else 'no'} "
                f"compile={'yes' if candidate.compile_model else 'no'}",
                flush=True,
            )

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

    def audit(self, candidate: ProfileCandidate) -> None:
        message = f"timed measurement complete; auditing bounded SDPA probes: {candidate.name}"
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, description=message)
        elif self.layout != "none":
            print(message, flush=True)

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
            table.add_column("valid tokens/s", justify="right")
            table.add_column("padding", justify="right")
            table.add_column("GPU memory", justify="right")
            table.add_column("forward", justify="right")
            table.add_column("backward", justify="right")
            table.add_column("optimizer", justify="right")
            table.add_column("metric amortized", justify="right")
            table.add_column("SDPA backend", justify="right")
            for result in results:
                steps = max(1, result.optimizer_steps)
                table.add_row(
                    result.candidate.name,
                    result.state,
                    f"{result.origins_per_second:,.0f}",
                    f"{result.encoded_tokens_per_second:,.0f}",
                    f"{result.valid_encoded_tokens / max(result.elapsed_seconds, 1e-9):,.0f}",
                    f"{result.encoded_padding_fraction * 100:,.1f}%",
                    f"{result.memory_fraction * 100:,.1f}%",
                    f"{result.forward_seconds * 1000 / steps:,.1f} ms",
                    f"{result.backward_seconds * 1000 / steps:,.1f} ms",
                    f"{result.optimizer_seconds * 1000 / steps:,.1f} ms",
                    f"{result.projected_metric_overhead_fraction * 100:,.3f}%",
                    (
                        ", ".join(f"{name}:{count}" for name, count in result.sdpa_backend_counts)
                        + f" ({sum(count for _name, count in result.sdpa_backend_counts)}/{result.sdpa_expected_calls})"
                        if result.sdpa_backend_counts
                        else result.sdpa_audit_state
                    ),
                )
            Console().print(table)
        for result in results:
            steps = max(1, result.optimizer_steps)
            print(
                f"result {result.candidate.model_size}: state={result.state} "
                f"origins/s={result.origins_per_second:,.0f} "
                f"tokens/s={result.encoded_tokens_per_second:,.0f} "
                f"valid_tokens/s={result.valid_encoded_tokens / max(result.elapsed_seconds, 1e-9):,.0f} "
                f"padding={result.encoded_padding_fraction * 100:.1f}% "
                f"memory={result.memory_fraction * 100:.1f}% "
                f"forward={result.forward_seconds * 1000 / steps:.1f}ms/update "
                f"backward={result.backward_seconds * 1000 / steps:.1f}ms/update "
                f"optimizer={result.optimizer_seconds * 1000 / steps:.1f}ms/update "
                f"metric_raw={result.metric_seconds * 1000:.1f}ms "
                f"metric_amortized={result.projected_metric_overhead_fraction * 100:.3f}% "
                f"loader_wait={result.loader_wait_seconds * 1000 / steps:.1f}ms/update "
                f"parameters={result.model_parameters:,} "
                f"sdpa={','.join(f'{name}:{count}' for name, count in result.sdpa_backend_counts) or result.sdpa_audit_state} "
                f"sdpa_calls={sum(count for _name, count in result.sdpa_backend_counts)}/{result.sdpa_expected_calls} "
                f"sdpa_audit={result.sdpa_audit_seconds * 1000:.1f}ms",
                flush=True,
            )
            if result.sdpa_audit_message:
                print(f"  SDPA audit detail: {result.sdpa_audit_message}", flush=True)
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
        manifest = (
            load_discovery_manifest(
                Path(args.experiment_manifest),
                shard_root=Path(args.offline_shard_root),
                config=data,
            )
            if str(args.experiment_manifest)
            else None
        )
        refs = (
            panel_refs(manifest, str(args.experiment_panel))
            if manifest is not None
            else ()
        )
        discovery_tickers = (
            tuple(sorted({ref.ticker for ref in refs}))
            if refs
            else data.training_tickers
        )
        discovery_start = min(ref.local_date for ref in refs) if refs else data.start_date
        discovery_end = (
            (
                dt.date.fromisoformat(max(ref.local_date for ref in refs))
                + dt.timedelta(days=1)
            ).isoformat()
            if refs
            else data.end_date
        )
        units = discover_offline_units(
            Path(args.offline_shard_root), data, tickers=discovery_tickers,
            start_date=discovery_start, end_date=discovery_end,
        )
        dataset = OfflineShardDataset(
            units,
            seed=17,
            shuffle_units=True,
            block_refs=refs,
            batch_size=data.batch_size,
            length_bucket_batches=data.offline_length_bucket_batches,
        )
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
    model = BarGPTV2(model_config).to(device)
    model_parameters = sum(parameter.numel() for parameter in model.parameters())
    if candidate.compile_model:
        if not hasattr(torch, "compile"):
            raise RuntimeError("torch.compile is unavailable in this PyTorch build")
        model = torch.compile(model, dynamic=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.1, foreach=device.type == "cuda")
    train_config = TrainConfig(gradient_accumulation_steps=candidate.accumulation, cuda_prefetch=candidate.cuda_prefetch)
    horizon_ids = torch.arange(len(data.horizons_us), device=device)
    prefetcher = DeviceBatchPrefetcher(
        loader,
        device,
        enabled=candidate.cuda_prefetch,
        host_cache_batches=max(1, math.ceil(data.ready_queue_blocks / data.batch_size)),
    )
    total_steps = int(args.warmup_steps) + int(args.measured_steps)
    measured_origins = measured_tokens = measured_valid_tokens = 0
    measured_tokens_by_view: dict[str, int] = {}
    measured_valid_tokens_by_view: dict[str, int] = {}
    measured_loader = measured_gpu = 0.0
    measured_forward = measured_backward = measured_optimizer = 0.0
    measured_started = 0.0
    measured_finished = 0.0
    measured_metric = 0.0
    sdpa_audit_state = "not_run"
    sdpa_backend_counts: tuple[tuple[str, int], ...] = ()
    sdpa_expected_calls = 0
    sdpa_audit_seconds = 0.0
    sdpa_audit_message = ""
    measured_allocated = measured_reserved = total_device = 0
    try:
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        for step in range(total_steps):
            if step == int(args.warmup_steps):
                measured_started = time.perf_counter()
                if device.type == "cuda":
                    torch.cuda.reset_peak_memory_stats(device)
            optimizer.zero_grad(set_to_none=True)
            step_origins = step_tokens = step_valid_tokens = 0
            step_tokens_by_view: dict[str, int] = {}
            step_valid_tokens_by_view: dict[str, int] = {}
            step_loader = step_gpu = 0.0
            step_forward = step_backward = step_optimizer = 0.0
            forward_events: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
            backward_events: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
            for _micro in range(candidate.accumulation):
                batch, wait = prefetcher.next()
                forward_started = time.perf_counter()
                forward_start_event = forward_end_event = None
                if device.type == "cuda":
                    forward_start_event = torch.cuda.Event(enable_timing=True)
                    forward_end_event = torch.cuda.Event(enable_timing=True)
                    forward_start_event.record()
                with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
                    output = model(
                        batch.views,
                        timeframe_us=TIMEFRAME_US_BY_NAME,
                        pathway_ids=PATHWAY_ID_BY_NAME,
                        base_view="1s",
                        origin_indices=batch.origin_indices,
                        asof_indices=batch.asof_indices,
                        attention_windows=data.attention_window_by_name,
                        view_masks={name: batch.view_mask[name] for name in batch.masked_context_views},
                        horizon_ids=horizon_ids,
                    )
                    loss_result = compute_loss(
                        output,
                        batch,
                        train_config,
                        model_config.quantiles,
                        collect_target_stats=False,
                    )
                    loss = loss_result.loss / candidate.accumulation
                if forward_end_event is not None and forward_start_event is not None:
                    forward_end_event.record()
                    forward_events.append((forward_start_event, forward_end_event))
                else:
                    step_forward += time.perf_counter() - forward_started
                backward_started = time.perf_counter()
                backward_start_event = backward_end_event = None
                if device.type == "cuda":
                    backward_start_event = torch.cuda.Event(enable_timing=True)
                    backward_end_event = torch.cuda.Event(enable_timing=True)
                    backward_start_event.record()
                loss.backward()
                if backward_end_event is not None and backward_start_event is not None:
                    backward_end_event.record()
                    backward_events.append((backward_start_event, backward_end_event))
                else:
                    step_backward += time.perf_counter() - backward_started
                step_loader += wait
                step_origins += batch.origin_count
                for name, value in batch.views.items():
                    allocated = int(value.shape[0] * value.shape[1])
                    valid = int(batch.valid_view_token_counts.get(name, allocated))
                    step_tokens += allocated
                    step_valid_tokens += valid
                    step_tokens_by_view[name] = step_tokens_by_view.get(name, 0) + allocated
                    step_valid_tokens_by_view[name] = (
                        step_valid_tokens_by_view.get(name, 0) + valid
                    )
            optimizer_started = time.perf_counter()
            step_start_event = step_end_event = None
            if device.type == "cuda":
                step_start_event = torch.cuda.Event(enable_timing=True)
                step_end_event = torch.cuda.Event(enable_timing=True)
                step_start_event.record()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            if step_end_event is not None and step_start_event is not None:
                step_end_event.record()
                step_end_event.synchronize()
                step_forward += sum(start.elapsed_time(end) / 1_000.0 for start, end in forward_events)
                step_backward += sum(start.elapsed_time(end) / 1_000.0 for start, end in backward_events)
                step_optimizer += step_start_event.elapsed_time(step_end_event) / 1_000.0
            else:
                step_optimizer += time.perf_counter() - optimizer_started
            step_gpu = step_forward + step_backward + step_optimizer
            if step >= int(args.warmup_steps):
                measured_origins += step_origins
                measured_tokens += step_tokens
                measured_valid_tokens += step_valid_tokens
                for name, value in step_tokens_by_view.items():
                    measured_tokens_by_view[name] = measured_tokens_by_view.get(name, 0) + value
                for name, value in step_valid_tokens_by_view.items():
                    measured_valid_tokens_by_view[name] = (
                        measured_valid_tokens_by_view.get(name, 0) + value
                    )
                measured_loader += step_loader
                measured_gpu += step_gpu
                measured_forward += step_forward
                measured_backward += step_backward
                measured_optimizer += step_optimizer
                reporter.step(candidate, step - int(args.warmup_steps) + 1, int(args.measured_steps))
        measured_finished = time.perf_counter()
        # Profile periodic training metrics separately from the throughput
        # window using the final existing forward output. Scale the reduction
        # portion by accumulation; finalization is paid once per metric update.
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        metric_accumulator = ValidationAccumulator(
            data.horizons_us,
            model_config.quantiles,
            namespace="train",
            include_loss_metrics=False,
            include_confidence_metrics=False,
        )
        metric_started = time.perf_counter()
        metric_accumulator.update(output, batch, loss_result)
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        reduction_seconds = time.perf_counter() - metric_started
        metric_started = time.perf_counter()
        metric_accumulator.finalize()
        finalization_seconds = time.perf_counter() - metric_started
        measured_metric = reduction_seconds * candidate.accumulation + finalization_seconds
        if device.type == "cuda":
            measured_allocated = int(torch.cuda.max_memory_allocated(device))
            measured_reserved = int(torch.cuda.max_memory_reserved(device))
            total_device = int(torch.cuda.get_device_properties(device).total_memory)
        optimizer.zero_grad(set_to_none=True)
        del output, loss_result, loss
        if bool(args.sdpa_audit):
            reporter.audit(candidate)
            (
                sdpa_audit_state,
                sdpa_backend_counts,
                sdpa_expected_calls,
                sdpa_audit_seconds,
                sdpa_audit_message,
            ) = _profile_sdpa_backends(batch, data, model_config, device=device)
        else:
            sdpa_audit_state = "disabled"
        observed_sdpa_calls = sum(count for _name, count in sdpa_backend_counts)
        if sdpa_audit_state == "passed" and observed_sdpa_calls != sdpa_expected_calls:
            sdpa_audit_state = "partial"
            sdpa_audit_message = (
                f"observed {observed_sdpa_calls} concrete SDPA calls; expected {sdpa_expected_calls}"
            )
        prefetch_telemetry = prefetcher.telemetry()
    finally:
        prefetcher.close()
    elapsed = max(measured_finished - measured_started, 1e-9)
    baseline_update_seconds = elapsed / max(1, int(args.measured_steps))
    metric_interval_updates = max(
        1,
        math.ceil(
            TrainConfig().training_metrics_interval_samples
            / max(1, candidate.microbatch * candidate.accumulation * candidate.origin_bars)
        ),
    )
    projected_metric_overhead = (
        (measured_metric / metric_interval_updates) / max(baseline_update_seconds, 1e-9)
    )
    return ProfileResult(
        candidate=candidate,
        state="passed",
        optimizer_steps=int(args.measured_steps),
        origins=measured_origins,
        encoded_tokens=measured_tokens,
        valid_encoded_tokens=measured_valid_tokens,
        encoded_padding_fraction=(
            1.0 - measured_valid_tokens / measured_tokens if measured_tokens else 0.0
        ),
        encoded_tokens_by_view=measured_tokens_by_view,
        valid_encoded_tokens_by_view=measured_valid_tokens_by_view,
        elapsed_seconds=elapsed,
        loader_wait_seconds=measured_loader,
        gpu_seconds=measured_gpu,
        origins_per_second=measured_origins / elapsed,
        encoded_tokens_per_second=measured_tokens / elapsed,
        peak_allocated_bytes=measured_allocated,
        peak_reserved_bytes=measured_reserved,
        total_device_bytes=total_device,
        memory_fraction=measured_reserved / total_device if total_device else 0.0,
        model_parameters=model_parameters,
        effective_blocks_per_update=candidate.microbatch * candidate.accumulation,
        recommended_accumulation=max(1, math.ceil(int(args.target_effective_blocks) / candidate.microbatch)),
        forward_seconds=measured_forward,
        backward_seconds=measured_backward,
        optimizer_seconds=measured_optimizer,
        h2d_seconds=float(prefetch_telemetry["h2d_seconds"]),
        h2d_completed_batches=int(prefetch_telemetry["h2d_completed_batches"]),
        host_cache_empty_reads=int(prefetch_telemetry["host_cache_empty_reads"]),
        device_stage_empty_waits=int(prefetch_telemetry["device_stage_empty_waits"]),
        metric_seconds=measured_metric,
        projected_metric_overhead_fraction=projected_metric_overhead,
        sdpa_audit_state=sdpa_audit_state,
        sdpa_backend_counts=sdpa_backend_counts,
        sdpa_expected_calls=sdpa_expected_calls,
        sdpa_audit_seconds=sdpa_audit_seconds,
        sdpa_audit_message=sdpa_audit_message,
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
    reporter.configuration(args, candidates, device)
    results: list[ProfileResult] = []
    oom_microbatch: dict[tuple[str, int, int, bool, int], int] = {}
    last_passed: dict[tuple[str, int, int, bool, int], ProfileResult] = {}
    jsonl = run_root / "profile.jsonl"
    # Candidates vary only loader/model shape; the authority/schema audit is
    # invariant.  Run it once so the measured sweep does not pay the same
    # ClickHouse metadata cost for every batch-size candidate.
    first_data = _data(args, candidates[0])
    first_data.validate()
    if args.data_source == "offline":
        verify_shard_catalog_lock(Path(args.offline_shard_root))
    if args.data_source == "clickhouse":
        preflight(
            ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()),
            first_data,
        )
    for index, candidate in enumerate(candidates, start=1):
        reporter.start(candidate, index, len(candidates))
        bucket_batches = (
            int(args.offline_length_bucket_batches)
            if candidate.length_bucket_batches is None
            else int(candidate.length_bucket_batches)
        )
        oom_key = (
            candidate.model_size,
            candidate.origin_bars,
            candidate.workers,
            candidate.compile_model,
            bucket_batches,
        )
        threshold = oom_microbatch.get(oom_key)
        previous = last_passed.get(oom_key)
        projected_memory = (
            _projected_memory_fraction(previous, candidate)
            if previous is not None and candidate.microbatch > previous.candidate.microbatch
            else 0.0
        )
        if projected_memory > PROFILE_MEMORY_LIMIT:
            assert previous is not None
            result = ProfileResult(
                candidate=candidate,
                state="skipped_projected_memory",
                optimizer_steps=0,
                origins=0,
                encoded_tokens=0,
                valid_encoded_tokens=0,
                encoded_padding_fraction=0.0,
                encoded_tokens_by_view={},
                valid_encoded_tokens_by_view={},
                elapsed_seconds=0.0,
                loader_wait_seconds=0.0,
                gpu_seconds=0.0,
                origins_per_second=0.0,
                encoded_tokens_per_second=0.0,
                peak_allocated_bytes=0,
                peak_reserved_bytes=0,
                total_device_bytes=previous.total_device_bytes,
                memory_fraction=projected_memory,
                model_parameters=previous.model_parameters,
                effective_blocks_per_update=candidate.microbatch * candidate.accumulation,
                recommended_accumulation=max(1, math.ceil(int(args.target_effective_blocks) / candidate.microbatch)),
                message=(
                    f"skipped before allocation: projected memory {projected_memory:.1%} exceeds "
                    f"the {PROFILE_MEMORY_LIMIT:.0%} profiling limit from microbatch "
                    f"{previous.candidate.microbatch}"
                ),
            )
        elif threshold is not None and candidate.microbatch >= threshold:
            result = ProfileResult(
                candidate=candidate,
                state="skipped_after_oom",
                optimizer_steps=0,
                origins=0,
                encoded_tokens=0,
                valid_encoded_tokens=0,
                encoded_padding_fraction=0.0,
                encoded_tokens_by_view={},
                valid_encoded_tokens_by_view={},
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
                    valid_encoded_tokens=0,
                    encoded_padding_fraction=0.0,
                    encoded_tokens_by_view={},
                    valid_encoded_tokens_by_view={},
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
        if result.state == "passed":
            last_passed[oom_key] = result
        results.append(result)
        with jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), default=str, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()
    eligible = [
        result for result in results
        if result.state == "passed" and result.memory_fraction <= PROFILE_MEMORY_LIMIT
    ]
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
        "learning_contract": LEARNING_CONTRACT,
        "device": str(device),
        "runtime": _runtime_evidence(device),
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
