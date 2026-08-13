from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Iterable

from research.bar_gpt.v2.config import (
    BAR_GPT_FULL_TRAINING_WANDB_PROJECT,
    MODEL_SIZE_PRESETS,
    OFFLINE_PRODUCTION_LOADER_WORKERS,
    OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
    PRODUCTION_MODEL_TRAINING_PRESETS,
)
from research.bar_gpt.v2.full_chunk_training import (
    FULL_CHUNK_MANIFEST_NAME,
    FULL_CHUNK_STOPPING_VALIDATION_ORIGINS,
    FULL_CHUNK_TARGET_ORIGINS,
    build_full_chunk_manifest,
    load_full_chunk_manifest,
)
from research.bar_gpt.v2.model_discovery import (
    DEFAULT_SHARD_ROOT,
    discovery_data_config,
)
from research.bar_gpt.v2.run_train import default_argv
from research.bar_gpt.v2.train import main as train_main


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v2\full_training")
DEFAULT_MODEL_SIZE = "medium"
DEFAULT_EPOCHS = 10
DEFAULT_MAX_CHUNK_EPOCHS = 20
DEFAULT_CHUNK_EARLY_STOPPING_PATIENCE = 1
DEFAULT_CHUNK_EARLY_STOPPING_MIN_RELATIVE_DELTA = 0.001
DEFAULT_EARLY_STOPPING_PATIENCE = 2
DEFAULT_EARLY_STOPPING_MIN_RELATIVE_DELTA = 0.001
DEFAULT_EPOCH_LR_DECAY = 0.95
DEFAULT_MANIFEST_INDEX_WORKERS = 4


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train BarGPT v2 over every eligible 2019-2025 shard block using "
            "randomized replayable chunks and fixed per-chunk 2026 validation panels."
        )
    )
    parser.add_argument(
        "--model-size",
        choices=("current", "medium", "large"),
        default=DEFAULT_MODEL_SIZE,
    )
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--chunk-target-origins", type=int, default=FULL_CHUNK_TARGET_ORIGINS)
    parser.add_argument(
        "--chunk-validation-origins",
        type=int,
        default=FULL_CHUNK_STOPPING_VALIDATION_ORIGINS,
    )
    parser.add_argument("--max-chunk-epochs", type=int, default=DEFAULT_MAX_CHUNK_EPOCHS)
    parser.add_argument(
        "--chunk-early-stopping-patience",
        type=int,
        default=DEFAULT_CHUNK_EARLY_STOPPING_PATIENCE,
    )
    parser.add_argument(
        "--chunk-early-stopping-min-relative-delta",
        type=float,
        default=DEFAULT_CHUNK_EARLY_STOPPING_MIN_RELATIVE_DELTA,
    )
    parser.add_argument(
        "--early-stopping-patience",
        type=int,
        default=DEFAULT_EARLY_STOPPING_PATIENCE,
    )
    parser.add_argument(
        "--early-stopping-min-relative-delta",
        type=float,
        default=DEFAULT_EARLY_STOPPING_MIN_RELATIVE_DELTA,
    )
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--manifest", default="")
    parser.add_argument(
        "--manifest-index-workers",
        type=int,
        default=DEFAULT_MANIFEST_INDEX_WORKERS,
        help="bounded metadata-only shard-index worker processes",
    )
    parser.add_argument(
        "--run-stamp",
        default="production",
        help=(
            "stable run identity suffix; rerunning the same command resumes its "
            "latest checkpoint (choose a new value for an independent run)"
        ),
    )
    parser.add_argument(
        "--wandb-project", default=BAR_GPT_FULL_TRAINING_WANDB_PROJECT
    )
    parser.add_argument(
        "--wandb-mode",
        choices=("auto", "online", "offline", "disabled"),
        default="online",
    )
    parser.add_argument("--prepare-manifest-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def manifest_path(args: argparse.Namespace) -> Path:
    return (
        Path(args.manifest)
        if str(args.manifest)
        else Path(args.output_root) / FULL_CHUNK_MANIFEST_NAME
    )


def ensure_full_training_manifest(args: argparse.Namespace) -> Path:
    shard_root = Path(args.shard_root)
    output = manifest_path(args)
    config = discovery_data_config(shard_root)
    if output.is_file():
        manifest = load_full_chunk_manifest(
            output,
            shard_root=shard_root,
            config=config,
        )
        print(f"Reusing verified full-training manifest: {output}", flush=True)
    else:
        print(
            "Building complete 2019-2025 training and disjoint 2026 evaluation authority...",
            flush=True,
        )
        print(
            f"Shard index: metadata-only loading with "
            f"{args.manifest_index_workers} bounded worker processes; cache is resumable",
            flush=True,
        )
        manifest = build_full_chunk_manifest(
            shard_root=shard_root,
            output_path=output,
            seed=17,
            index_workers=args.manifest_index_workers,
        )
    for name in ("train", "epoch_train", "monitor_pool", "validation", "locked_test"):
        summary = manifest["summaries"][name]
        print(
            f"{name}: origins={int(summary['origins']):,} "
            f"blocks={int(summary['blocks']):,} tickers={int(summary['tickers']):,}",
            flush=True,
        )
    return output


def _override_options(argv: list[str], values: dict[str, object]) -> list[str]:
    """Replace launcher defaults so the printed runnable command is unambiguous."""
    resolved = list(argv)
    for option, value in values.items():
        while option in resolved:
            index = resolved.index(option)
            del resolved[index:index + 2]
        resolved.extend((option, str(value)))
    return resolved


def trainer_argv(args: argparse.Namespace, *, resolved_manifest: Path) -> list[str]:
    model = MODEL_SIZE_PRESETS[str(args.model_size)]
    profile = PRODUCTION_MODEL_TRAINING_PRESETS[str(args.model_size)]
    run_stamp = str(args.run_stamp) or time.strftime("%Y%m%d-%H%M%S")
    run_name = (
        f"bar-gpt-v2-full-{args.model_size}-chunks{int(args.chunk_target_origins) // 1_000_000}m-"
        f"epoch{args.epochs}-"
        f"chunkepochs{args.max_chunk_epochs}-"
        f"chunkcosine-decay{int(DEFAULT_EPOCH_LR_DECAY * 100)}-"
        f"micro{profile.microbatch}-accum{profile.accumulation}-"
        f"bucket{profile.length_bucket_batches}-{run_stamp}"
    )
    argv = _override_options(
        default_argv(),
        {
            "--experiment-manifest": resolved_manifest,
            "--output-root": args.output_root,
            "--run-name": run_name,
            "--d-model": model["d_model"],
            "--n-layers": model["n_layers"],
            "--n-heads": model["n_heads"],
            "--n-kv-heads": model["n_kv_heads"],
            "--batch-size": profile.microbatch,
            "--gradient-accumulation-steps": profile.accumulation,
            "--offline-length-bucket-batches": profile.length_bucket_batches,
            "--loader-workers": OFFLINE_PRODUCTION_LOADER_WORKERS,
            "--ready-queue-blocks": OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
            "--worker-prefetch-batches": OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
            "--epochs": args.epochs,
            "--chunk-target-origins": args.chunk_target_origins,
            "--chunk-validation-origins": args.chunk_validation_origins,
            "--max-chunk-epochs": args.max_chunk_epochs,
            "--chunk-early-stopping-patience": args.chunk_early_stopping_patience,
            "--chunk-early-stopping-min-relative-delta": (
                args.chunk_early_stopping_min_relative_delta
            ),
            "--outer-early-stopping-patience": args.early_stopping_patience,
            "--outer-early-stopping-min-relative-delta": (
                args.early_stopping_min_relative_delta
            ),
            "--monitor-evaluation-origins": args.chunk_validation_origins,
            "--warmup-samples": 4_000_000,
            "--scheduler-mode": "epoch-chunk-cosine",
            "--cosine-restart-decay": DEFAULT_EPOCH_LR_DECAY,
            "--wandb-project": args.wandb_project,
            "--wandb-mode": args.wandb_mode,
        },
    )
    argv.extend(("--full-chunk-training", "--no-full-validation-final-epoch-only"))
    checkpoint = Path(args.output_root) / run_name / "checkpoints" / "checkpoint_latest.pt"
    if checkpoint.is_file():
        argv.extend(("--resume-checkpoint", str(checkpoint)))
    return argv


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.epochs <= 0:
        raise ValueError("epochs must be positive")
    if args.chunk_target_origins <= 0 or args.chunk_validation_origins <= 0:
        raise ValueError("chunk origin targets must be positive")
    if args.max_chunk_epochs <= 0 or args.chunk_early_stopping_patience <= 0:
        raise ValueError("chunk epochs and early-stopping patience must be positive")
    if args.manifest_index_workers <= 0:
        raise ValueError("manifest-index-workers must be positive")
    if args.early_stopping_patience < 0:
        raise ValueError("early-stopping patience cannot be negative")
    planned_manifest = manifest_path(args)
    print(f"Model size: {args.model_size} (default: medium)", flush=True)
    print(
        f"Training: every 2019-2025 block belongs to one chunk per outer epoch; "
        f"approximately {args.chunk_target_origins:,} origins/chunk; "
        f"maximum {args.epochs} epochs; patience={args.early_stopping_patience}",
        flush=True,
    )
    print(
        "Sampling: a new deterministic exact block partition each outer epoch; "
        "the next epoch plan is prepared concurrently; W&B step remains samples_seen",
        flush=True,
    )
    print(
        f"Chunk adaptation: up to {args.max_chunk_epochs} exact repetitions; fixed "
        f"{args.chunk_validation_origins:,}-origin validation; patience="
        f"{args.chunk_early_stopping_patience}",
        flush=True,
    )
    print(
        "Schedule: 4M-origin warmup; one cosine cycle spans the chunk's maximum "
        f"{args.max_chunk_epochs} repetitions; restart only when the chunk changes; "
        f"outer-epoch peak decay={DEFAULT_EPOCH_LR_DECAY:.2f}",
        flush=True,
    )
    preview_args = trainer_argv(args, resolved_manifest=planned_manifest)
    print(
        "Command: "
        + " ".join(
            shlex.quote(item)
            for item in (sys.executable, "-B", "-m", "research.bar_gpt.v2.train", *preview_args)
        ),
        flush=True,
    )
    if not args.execute and not args.prepare_manifest_only:
        print(
            "Preview only. Use --prepare-manifest-only once, then --execute; "
            "--execute also prepares a missing manifest.",
            flush=True,
        )
        return 0
    resolved_manifest = ensure_full_training_manifest(args)
    if args.prepare_manifest_only:
        return 0
    return train_main(trainer_argv(args, resolved_manifest=resolved_manifest))


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
