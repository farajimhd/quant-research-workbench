from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v29.train import main as train_main  # noqa: E402
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "data_source": "sample_cache",
    "sample_cache_root": r"D:\market-data\prepared\event_sample_cache",
    "sample_cache_prefetch_shards": 2,
    "sample_cache_shuffle_records": True,
    "sample_cache_drop_last": True,
    "sample_cache_train_start_shard": 0,
    "sample_cache_train_max_shards": 10,
    "sample_cache_validation_split": "validation",
    "sample_cache_validation_start_shard": 0,
    "sample_cache_validation_max_shards": 8,
    "sample_cache_validation_batches_per_shard": 1,
    "sample_cache_interleave_shards": 1,
    "batch_size": 8192,
    "epochs": 4,
    "max_steps": 0,
    "input_representation": "bit",
    "d_byte": 40,
    "d_model": 256,
    "embedding_dim": 32,
    "n_heads": 8,
    "encoder_layers": 10,
    "decoder_layers": 4,
    "ffn_mult": 4,
    "dropout": 0.08,
    "event_mask_ratio": 0.70,
    "min_masked_events": 1,
    "header_bit_corruption_prob": 0.20,
    "header_bit_corruption_ratio": 0.05,
    "event_bit_corruption_prob": 0.30,
    "event_bit_corruption_ratio": 0.20,
    "learning_rate": 1e-3,
    "weight_decay": 1e-4,
    "scheduler": "cosine_warm_restarts",
    "scheduler_t_mult": 1,
    "scheduler_eta_min": 1e-6,
    "grad_clip_norm": 1.0,
    "logging_steps": 10,
    "detailed_metrics_steps": 0,
    "profile_first_steps": 5,
    "profile_training_every_steps": 1000,
    "profile_inference_every_steps": 0,
    "decoder_chunk_size": 0,
    "checkpoint_latest_steps": 25,
    "checkpoint_best_train": False,
    "checkpoint_best_val": True,
    "num_workers": 0,
    "progress_layout": "auto",
    "device": "cuda",
    "compile_model": True,
    "wandb_project": "June2026-event-token-mae-v29-mlp-decoder",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
    "wandb_run_name": "v29-mlpdecoder-medium-event-token-emb32-bs8192-10shards",
    "warm_start_checkpoint": "",
}

PROFILED_TRAINING_PATH = "v29 event-token MAE medium emb32 bs8192 compile-enabled, shard-cycle scheduler, no interleave"
VALIDATION_BATCHES = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train v29 event-token MAE on 10 sample-cache shards with one validation shard slice.")
    parser.add_argument("--cache-root", default=DEFAULTS["sample_cache_root"])
    parser.add_argument("--train-start-shard", "--sample-cache-train-start-shard", dest="train_start_shard", type=int, default=DEFAULTS["sample_cache_train_start_shard"])
    parser.add_argument("--train-shards", type=int, default=DEFAULTS["sample_cache_train_max_shards"])
    parser.add_argument("--validation-shard-index", type=int, default=DEFAULTS["sample_cache_validation_start_shard"])
    parser.add_argument("--validation-fraction", type=float, default=0.05)
    parser.add_argument("--interleave-shards", type=int, default=DEFAULTS["sample_cache_interleave_shards"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--device", default=DEFAULTS["device"])
    parser.add_argument("--run-name", default=DEFAULTS["wandb_run_name"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=DEFAULTS["compile_model"])
    parser.add_argument("--decoder-chunk-size", type=int, default=DEFAULTS["decoder_chunk_size"])
    parser.add_argument("--warm-start-checkpoint", nargs="?", const="", default=DEFAULTS["warm_start_checkpoint"])
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    cache_root = Path(args.cache_root)
    batch_size = max(1, int(args.batch_size))
    if int(args.interleave_shards) != 1:
        print(
            "WARN --interleave-shards is forced to 1. The interleaved sample-cache path can retain/duplicate "
            "large shard arrays across group transitions, causing RAM pressure and a persistent training slowdown.",
            flush=True,
        )
    interleave_shards = 1
    train_shards, validation_shards, validation_samples = resolve_shard_plan(
        cache_root=cache_root,
        train_start_shard=int(args.train_start_shard),
        train_shards=int(args.train_shards),
        validation_shard_index=int(args.validation_shard_index),
        validation_fraction=float(args.validation_fraction),
        batch_size=batch_size,
        max_validation_batches=VALIDATION_BATCHES,
    )
    steps_per_epoch = sum(shard.num_samples // batch_size for shard in train_shards)
    steps_per_shard = train_shards[0].num_samples // batch_size
    validation_batches = max(1, validation_samples // batch_size)
    values = dict(DEFAULTS)
    values.update(
        {
            "sample_cache_root": str(cache_root),
            "sample_cache_train_start_shard": int(args.train_start_shard),
            "sample_cache_train_max_shards": len(train_shards),
            "sample_cache_validation_split": "validation",
            "sample_cache_validation_start_shard": validation_shards[0].shard_index,
            "sample_cache_validation_max_shards": len(validation_shards),
            "sample_cache_validation_max_samples": validation_batches * batch_size,
            "sample_cache_validation_batches_per_shard": 1,
            # Keep interleave disabled until the loader is redesigned to avoid
            # transient copies of multiple full shard arrays. Interleave=2 caused
            # process RSS to jump by about 90 GiB and step time to degrade from
            # roughly 1.7s to 9.6s after shard-group transitions.
            "sample_cache_interleave_shards": interleave_shards,
            "batch_size": batch_size,
            "epochs": int(args.epochs),
            "pretrain_validation_frequency": steps_per_shard,
            "pretrain_validation_steps": validation_batches,
            "checkpoint_latest_steps": steps_per_shard,
            "checkpoint_archive_steps": steps_per_epoch,
            "scheduler_t0_steps": steps_per_shard,
            "device": args.device,
            "wandb_project": args.wandb_project,
            "wandb_mode": args.wandb_mode,
            "wandb_run_name": args.run_name,
            "progress_layout": args.progress_layout,
            "compile_model": bool(args.compile_model),
            "decoder_chunk_size": int(args.decoder_chunk_size),
            "warm_start_checkpoint": args.warm_start_checkpoint,
            "warm_start_load_optimizer": bool(args.warm_start_load_optimizer),
            "initial_validation": bool(args.initial_validation),
        }
    )
    argv = build_train_args(values)
    if args.dry_run:
        argv.append("--dry-run")
    if args.fresh_start:
        argv.append("--fresh-start")
    argv.extend(args.extra)
    print("=" * 96, flush=True)
    print("v29 medium event-token limited-shard training", flush=True)
    print(f"cache_root={cache_root}", flush=True)
    print(f"train_shards={train_shards[0].shard_index}..{train_shards[-1].shard_index} count={len(train_shards)}", flush=True)
    print(f"train_samples_per_epoch={sum(shard.num_samples for shard in train_shards):,}", flush=True)
    print(
        f"steps_per_epoch={steps_per_epoch:,} steps_per_shard={steps_per_shard:,} "
        f"batch_size={batch_size:,} epochs={args.epochs:,} interleave_shards={interleave_shards:,}",
        flush=True,
    )
    print(
        f"validation_split=validation shards={validation_shards[0].shard_index}..{validation_shards[-1].shard_index} "
        f"batches_per_shard=1 validation_samples={validation_batches * batch_size:,} validation_batches={validation_batches:,}",
        flush=True,
    )
    print(f"wandb_project={args.wandb_project} run={args.run_name}", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    print(f"compile_model={args.compile_model} decoder_chunk_size={args.decoder_chunk_size}", flush=True)
    print(f"warm_start_checkpoint={args.warm_start_checkpoint or '<none>'} load_optimizer={args.warm_start_load_optimizer}", flush=True)
    if not args.compile_model:
        print(
            "WARN this differs from the current profiled faster path; expected --compile-model.",
            flush=True,
        )
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    print("=" * 96, flush=True)
    if args.print_only:
        return
    train_main(argv)


def resolve_shard_plan(
    *,
    cache_root: Path,
    train_start_shard: int,
    train_shards: int,
    validation_shard_index: int,
    validation_fraction: float,
    batch_size: int,
    max_validation_batches: int,
):
    if train_shards <= 0:
        raise SystemExit("--train-shards must be positive")
    if train_start_shard < 0:
        raise SystemExit("--train-start-shard must be non-negative")
    if not 0.0 < validation_fraction <= 1.0:
        raise SystemExit("--validation-fraction must be in (0, 1]")
    train_config = EventSampleCacheDataConfig(cache_root=cache_root, split="train", max_shards=0)
    validation_config = EventSampleCacheDataConfig(cache_root=cache_root, split="validation", max_shards=0)
    shards = discover_event_sample_shards(train_config)
    validation_candidates = discover_event_sample_shards(validation_config)
    if len(shards) < train_start_shard + train_shards:
        raise SystemExit(
            f"Need train shard range {train_start_shard}..{train_start_shard + train_shards - 1}, "
            f"but only found {len(shards)} train shards under {cache_root}"
        )
    if len(validation_candidates) < validation_shard_index + max_validation_batches:
        raise SystemExit(
            f"Need validation shard range {validation_shard_index}..{validation_shard_index + max_validation_batches - 1}, "
            f"but only found {len(validation_candidates)} validation shards under {cache_root}"
        )
    selected_train = shards[train_start_shard : train_start_shard + train_shards]
    requested_validation = validation_candidates[validation_shard_index : validation_shard_index + max_validation_batches]
    validation_shards = []
    for shard in requested_validation:
        if shard.num_samples < batch_size:
            break
        validation_shards.append(shard)
    if not validation_shards:
        raise SystemExit(
            f"No validation shards with at least one full batch at batch_size={batch_size:,} "
            f"from {cache_root}"
        )
    validation_samples = len(validation_shards) * batch_size
    return selected_train, validation_shards, validation_samples


def build_train_args(values: dict[str, Any]) -> list[str]:
    argv: list[str] = []
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            argv.append(flag if value else "--no-" + key.replace("_", "-"))
        elif value is None or value == "":
            continue
        else:
            argv.extend([flag, str(value)])
    return argv


if __name__ == "__main__":
    main()
