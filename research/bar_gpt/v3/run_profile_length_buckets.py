from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import Iterable

import torch

from research.bar_gpt.v3.config import (
    OFFLINE_PRODUCTION_LOADER_WORKERS,
    OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
)
from research.bar_gpt.v3.profile_train import (
    DEFAULT_OUTPUT_ROOT,
    MODEL_SIZE_PRESETS,
    ProfileReporter,
    _parse_candidates,
    main as profile_main,
    parse_args as parse_profile_args,
)


def _positive_ints(value: str) -> tuple[int, ...]:
    result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not result or any(item <= 0 for item in result):
        raise ValueError("length-bucket candidates must be positive integers")
    if len(set(result)) != len(result):
        raise ValueError("length-bucket candidates must be unique")
    return result


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile one fixed BarGPT model and microbatch across length-bucketing "
            "windows with W&B completely disabled."
        )
    )
    parser.add_argument("--model-size", choices=tuple(MODEL_SIZE_PRESETS), default="current")
    parser.add_argument("--microbatch", type=int, default=20)
    parser.add_argument("--accumulation", type=int, default=1)
    parser.add_argument("--length-bucket-batches", default="4,8,16")
    parser.add_argument("--workers", type=int, default=OFFLINE_PRODUCTION_LOADER_WORKERS)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--measured-steps", type=int, default=10)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cuda")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--sdpa-audit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--print-config-only", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def profiler_argv(args: argparse.Namespace) -> list[str]:
    buckets = _positive_ints(str(args.length_bucket_batches))
    candidates = ",".join(
        f"{args.model_size}:4096:{args.microbatch}:{args.accumulation}:"
        f"{args.workers}:1:{int(args.compile_model)}:{bucket}"
        for bucket in buckets
    )
    return [
        "--data-source",
        "offline",
        "--offline-shard-root",
        r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12",
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--candidates",
        candidates,
        "--ready-queue-blocks",
        str(OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS),
        "--worker-prefetch-batches",
        str(OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES),
        "--target-effective-blocks",
        "32",
        "--warmup-steps",
        str(args.warmup_steps),
        "--measured-steps",
        str(args.measured_steps),
        "--output-root",
        str(args.output_root),
        "--device",
        str(args.device),
        "--progress-layout",
        str(args.progress_layout),
        "--sdpa-audit" if args.sdpa_audit else "--no-sdpa-audit",
    ]


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if (
        args.microbatch <= 0
        or args.accumulation <= 0
        or args.workers <= 0
        or args.warmup_steps < 0
        or args.measured_steps <= 0
    ):
        raise ValueError(
            "microbatch, accumulation, workers, and measured steps must be positive; "
            "warm-up cannot be negative"
        )
    resolved = profiler_argv(args)
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v3.profile_train", *resolved]
    print("W&B logging: disabled by profiler design", flush=True)
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    if args.print_config_only:
        profile_args = parse_profile_args(resolved)
        device_name = "cuda" if args.device == "auto" and torch.cuda.is_available() else (
            "cpu" if args.device == "auto" else args.device
        )
        ProfileReporter(str(args.progress_layout)).configuration(
            profile_args,
            _parse_candidates(profile_args.candidates),
            torch.device(device_name),
        )
        return 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    return int(profile_main(resolved) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
