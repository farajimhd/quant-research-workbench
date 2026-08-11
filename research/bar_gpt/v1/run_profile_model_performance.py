from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import Iterable

import torch

from research.bar_gpt.v1.profile_train import (
    DEFAULT_OUTPUT_ROOT,
    ProfileReporter,
    _parse_candidates,
    main as profile_main,
    parse_args as parse_profile_args,
)
from research.bar_gpt.v1.run_train_model_comparison import COMPARISON_RUNS


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile one production BarGPT model shape with W&B completely disabled."
    )
    parser.add_argument("--model-size", choices=tuple(COMPARISON_RUNS), default="current")
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--measured-steps", type=int, default=10)
    parser.add_argument("--workers", type=int, default=16)
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
    run = COMPARISON_RUNS[str(args.model_size)]
    candidate = (
        f"{args.model_size}:4096:{run.microbatch}:{run.accumulation}:"
        f"{args.workers}:1:{int(args.compile_model)}"
    )
    return [
        "--data-source",
        "offline",
        "--offline-shard-root",
        r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v7",
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--candidates",
        candidate,
        "--ready-queue-blocks",
        "1024",
        "--worker-prefetch-batches",
        "8",
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
    if args.warmup_steps < 0 or args.measured_steps <= 0 or args.workers <= 0:
        raise ValueError("warm-up cannot be negative; measured steps and workers must be positive")
    resolved = profiler_argv(args)
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.profile_train", *resolved]
    print("W&B logging: disabled by profiler design", flush=True)
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    if args.print_config_only:
        profile_args = parse_profile_args(resolved)
        device_name = "cuda" if args.device == "auto" and torch.cuda.is_available() else (
            "cpu" if args.device == "auto" else args.device
        )
        device = torch.device(device_name)
        ProfileReporter(str(args.progress_layout)).configuration(
            profile_args,
            _parse_candidates(profile_args.candidates),
            device,
        )
        return 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    return int(profile_main(resolved) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
