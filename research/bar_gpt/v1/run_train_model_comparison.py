from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Iterable

from research.bar_gpt.v1.config import BAR_GPT_WANDB_PROJECT
from research.bar_gpt.v1.profile_train import MODEL_SIZE_PRESETS
from research.bar_gpt.v1.run_train import default_argv
from research.bar_gpt.v1.train import main as train_main


@dataclass(frozen=True, slots=True)
class ComparisonRun:
    model_size: str
    microbatch: int
    accumulation: int

    @property
    def effective_blocks(self) -> int:
        return self.microbatch * self.accumulation


# These are the fit/throughput winners selected from the workstation sweep.
# Large uses the 8x4 configuration rather than the 16x2 near-capacity result.
COMPARISON_RUNS: dict[str, ComparisonRun] = {
    "current": ComparisonRun("current", microbatch=32, accumulation=1),
    "medium": ComparisonRun("medium", microbatch=16, accumulation=2),
    "large": ComparisonRun("large", microbatch=8, accumulation=4),
}
DEFAULT_WANDB_MODE = "online"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or execute one of the three one-epoch BarGPT model comparisons."
    )
    parser.add_argument("--model-size", choices=("all", *COMPARISON_RUNS), default="all")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--run-stamp",
        default="",
        help="Optional shared run suffix. Defaults to the current YYYYmmdd-HHMMSS timestamp.",
    )
    parser.add_argument(
        "--wandb-mode",
        choices=("auto", "online", "offline", "disabled"),
        default=DEFAULT_WANDB_MODE,
        help="override the normal online W&B logging mode",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def comparison_run_name(model_size: str, run_stamp: str) -> str:
    run = COMPARISON_RUNS[model_size]
    return (
        f"bar-gpt-v1-epoch1-{model_size}-micro{run.microbatch}-"
        f"accum{run.accumulation}-{run_stamp}"
    )


def trainer_argv(
    model_size: str,
    *,
    run_stamp: str,
    wandb_mode: str = DEFAULT_WANDB_MODE,
) -> list[str]:
    run = COMPARISON_RUNS[model_size]
    model = MODEL_SIZE_PRESETS[model_size]
    argv = [
        *default_argv(),
        "--run-name",
        comparison_run_name(model_size, run_stamp),
        "--wandb-project",
        BAR_GPT_WANDB_PROJECT,
        "--epochs",
        "1",
        "--batch-size",
        str(run.microbatch),
        "--gradient-accumulation-steps",
        str(run.accumulation),
        "--d-model",
        str(model["d_model"]),
        "--n-layers",
        str(model["n_layers"]),
        "--n-heads",
        str(model["n_heads"]),
        "--n-kv-heads",
        str(model["n_kv_heads"]),
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        argv.extend(("--wandb-mode", wandb_mode))
    return argv


def _launcher_command(model_size: str, *, run_stamp: str, wandb_mode: str, execute: bool) -> list[str]:
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.run_train_model_comparison",
        "--model-size",
        model_size,
        "--run-stamp",
        run_stamp,
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        command.extend(("--wandb-mode", wandb_mode))
    if execute:
        command.append("--execute")
    return command


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.execute and args.model_size == "all":
        raise SystemExit("--execute requires one explicit --model-size; long runs are never started in a chain")
    run_stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
    selected = tuple(COMPARISON_RUNS) if args.model_size == "all" else (args.model_size,)
    print(f"W&B project: {BAR_GPT_WANDB_PROJECT}", flush=True)
    for model_size in selected:
        run = COMPARISON_RUNS[model_size]
        model = MODEL_SIZE_PRESETS[model_size]
        print(
            f"{model_size}: d_model={model['d_model']} layers={model['n_layers']} "
            f"heads={model['n_heads']} kv_heads={model['n_kv_heads']} "
            f"microbatch={run.microbatch} accumulation={run.accumulation} "
            f"effective_blocks={run.effective_blocks}",
            flush=True,
        )
        print(
            "Command: "
            + " ".join(
                shlex.quote(item)
                for item in _launcher_command(
                    model_size,
                    run_stamp=run_stamp,
                    wandb_mode=args.wandb_mode,
                    execute=True,
                )
            ),
            flush=True,
        )
    if not args.execute:
        return 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    resolved = trainer_argv(selected[0], run_stamp=run_stamp, wandb_mode=args.wandb_mode)
    equivalent = [sys.executable, "-B", "-m", "research.bar_gpt.v1.train", *resolved]
    print("Equivalent trainer command: " + " ".join(shlex.quote(item) for item in equivalent), flush=True)
    return int(train_main(resolved) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
