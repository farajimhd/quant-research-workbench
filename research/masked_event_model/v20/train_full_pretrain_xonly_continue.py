from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()),
    Path(__file__).resolve().parents[3],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v20.train import main as train_main  # noqa: E402
from research.masked_event_model.v20.train_full_pretrain import (  # noqa: E402
    DEFAULTS as FULL_PRETRAIN_DEFAULTS,
    PROFILED_TRAINING_PATH,
    build_train_args,
    cache_split_dir_for_display,
    compute_epoch_lr_table,
    resolve_explicit_cache_root,
    validate_required_warm_start,
)
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards  # noqa: E402


XONLY_TRAIN_CACHE = r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\event_sample_cache\cache_pretrain_xonly_20260621_140813\train"
VALIDATION_CACHE = r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\event_sample_cache\cache_20260617_112833\validation"
MIXED_RUN_CHECKPOINT = (
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\masked_event_model\v20\pretrain"
    r"\v20-fullpretrain-mixedcache-fixedmask070-emb32-bs8192-lr4e4-3epochs-freshrng-from-step260352"
    r"\checkpoints\checkpoint_latest.pt"
)


DEFAULTS: dict[str, Any] = dict(FULL_PRETRAIN_DEFAULTS)
DEFAULTS.update(
    {
        "sample_cache_root": XONLY_TRAIN_CACHE,
        "sample_cache_validation_root": VALIDATION_CACHE,
        "sample_cache_train_start_shard": 0,
        "sample_cache_train_max_shards": 100,
        "warm_start_checkpoint": MIXED_RUN_CHECKPOINT,
        "warm_start_load_optimizer": False,
        "epochs": 3,
        "learning_rate": 4e-4,
        "repeatable_randomness": False,
        "wandb_project": "June2026-event-token-mae-full",
        "wandb_run_name": "v20-fullpretrain-xonly100-fixedmask070-emb32-bs8192-lr4e4-3epochs-freshrng-from-mixed-latest",
    }
)


class ShardPreview:
    def __init__(self, *, shard_index: int, num_samples: int) -> None:
        self.shard_index = int(shard_index)
        self.num_samples = int(num_samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continue v20 full pretraining from the latest mixed-cache checkpoint, "
            "using only cache_pretrain_xonly_20260621_140813 train shards."
        )
    )
    parser.add_argument("--cache-root", "--train-cache-root", dest="cache_root", default=DEFAULTS["sample_cache_root"])
    parser.add_argument("--validation-cache-root", default=DEFAULTS["sample_cache_validation_root"])
    parser.add_argument("--train-start-shard", "--sample-cache-train-start-shard", dest="train_start_shard", type=int, default=DEFAULTS["sample_cache_train_start_shard"])
    parser.add_argument("--train-shards", type=int, default=DEFAULTS["sample_cache_train_max_shards"], help="Default 100; use 0 for all discovered x-only shards")
    parser.add_argument("--validation-shard-index", type=int, default=DEFAULTS["sample_cache_validation_start_shard"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["sample_cache_validation_max_shards"], help="One shuffled validation batch per selected validation shard")
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--scheduler-eta-min", type=float, default=DEFAULTS["scheduler_eta_min"])
    parser.add_argument("--scheduler-epoch-decay-ratio", type=float, default=DEFAULTS["scheduler_epoch_decay_ratio"])
    parser.add_argument("--scheduler-shard-decay-fraction", type=float, default=DEFAULTS["scheduler_shard_decay_fraction"])
    parser.add_argument("--checkpoint-latest-steps", type=int, default=DEFAULTS["checkpoint_latest_steps"])
    parser.add_argument("--device", default=DEFAULTS["device"])
    parser.add_argument("--run-name", default=DEFAULTS["wandb_run_name"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=DEFAULTS["compile_model"])
    parser.add_argument("--amp-dtype", choices=("auto", "bf16", "fp16"), default=DEFAULTS["amp_dtype"])
    parser.add_argument("--warm-start-checkpoint", nargs="?", const="", default=DEFAULTS["warm_start_checkpoint"])
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fresh-start", action="store_true", default=True)
    parser.add_argument("--no-fresh-start", action="store_false", dest="fresh_start")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument(
        "--skip-shard-discovery",
        action="store_true",
        help="Only for command inspection when the workstation sample cache is not mounted.",
    )
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    train_cache_root_arg = Path(args.cache_root)
    validation_cache_root_arg = Path(args.validation_cache_root)
    batch_size = max(1, int(args.batch_size))
    if args.skip_shard_discovery:
        if not args.print_only:
            raise SystemExit("--skip-shard-discovery is only allowed with --print-only")
        train_cache_root = train_cache_root_arg
        validation_cache_root = validation_cache_root_arg
        train_count = int(args.train_shards) if int(args.train_shards) > 0 else 1
        validation_count = int(args.validation_batches) if int(args.validation_batches) > 0 else 1
        train_shards = [
            ShardPreview(shard_index=index, num_samples=0)
            for index in range(int(args.train_start_shard), int(args.train_start_shard) + train_count)
        ]
        validation_shards = [
            ShardPreview(shard_index=index, num_samples=0)
            for index in range(int(args.validation_shard_index), int(args.validation_shard_index) + validation_count)
        ]
        steps_per_epoch = 0
        steps_per_shard = 1
        validation_batches = validation_count
    else:
        train_cache_root = resolve_explicit_cache_root(train_cache_root_arg, split="train")
        validation_cache_root = resolve_explicit_cache_root(validation_cache_root_arg, split="validation")
        train_shards, validation_shards, validation_batches = resolve_shard_plan(
            train_cache_root=train_cache_root,
            validation_cache_root=validation_cache_root,
            train_start_shard=int(args.train_start_shard),
            train_shards=int(args.train_shards),
            validation_shard_index=int(args.validation_shard_index),
            batch_size=batch_size,
            max_validation_batches=int(args.validation_batches),
        )
        shard_batch_counts = [shard.num_samples // batch_size for shard in train_shards]
        positive_shard_batch_counts = [count for count in shard_batch_counts if count > 0]
        steps_per_epoch = sum(shard_batch_counts)
        steps_per_shard = max(1, min(positive_shard_batch_counts))

    checkpoint_latest_steps = int(args.checkpoint_latest_steps) if int(args.checkpoint_latest_steps) > 0 else steps_per_shard
    values = dict(DEFAULTS)
    values.update(
        {
            "sample_cache_root": str(train_cache_root),
            "sample_cache_validation_root": str(validation_cache_root),
            "sample_cache_train_start_shard": int(args.train_start_shard),
            "sample_cache_train_max_shards": len(train_shards),
            "sample_cache_validation_split": "validation",
            "sample_cache_validation_start_shard": validation_shards[0].shard_index,
            "sample_cache_validation_max_shards": len(validation_shards),
            "sample_cache_validation_max_samples": validation_batches * batch_size,
            "sample_cache_validation_batches_per_shard": 1,
            "sample_cache_interleave_shards": 1,
            "batch_size": batch_size,
            "epochs": int(args.epochs),
            "learning_rate": float(args.learning_rate),
            "scheduler": "shard_decay_cosine",
            "scheduler_eta_min": float(args.scheduler_eta_min),
            "scheduler_epoch_decay_ratio": float(args.scheduler_epoch_decay_ratio),
            "scheduler_shard_decay_fraction": float(args.scheduler_shard_decay_fraction),
            "checkpoint_latest_steps": checkpoint_latest_steps,
            "checkpoint_archive_steps": max(1, steps_per_epoch),
            "device": args.device,
            "wandb_project": args.wandb_project,
            "wandb_mode": args.wandb_mode,
            "wandb_run_name": args.run_name,
            "progress_layout": args.progress_layout,
            "compile_model": bool(args.compile_model),
            "amp_dtype": args.amp_dtype,
            "warm_start_checkpoint": args.warm_start_checkpoint,
            "warm_start_load_optimizer": bool(args.warm_start_load_optimizer),
            "repeatable_randomness": bool(args.repeatable_randomness),
            "initial_validation": bool(args.initial_validation),
        }
    )
    argv = build_train_args(values)
    if args.dry_run:
        argv.append("--dry-run")
    if args.fresh_start:
        argv.append("--fresh-start")
    argv.extend(args.extra)
    print_plan(values, train_shards, validation_shards, validation_batches, steps_per_epoch, argv)
    if args.print_only:
        return
    validate_required_warm_start(values)
    train_main(argv)


def resolve_shard_plan(
    *,
    train_cache_root: Path,
    validation_cache_root: Path,
    train_start_shard: int,
    train_shards: int,
    validation_shard_index: int,
    batch_size: int,
    max_validation_batches: int,
):
    if train_start_shard < 0:
        raise SystemExit("--train-start-shard must be non-negative")
    train_config = EventSampleCacheDataConfig(cache_root=train_cache_root, split="train", max_shards=0)
    validation_config = EventSampleCacheDataConfig(cache_root=validation_cache_root, split="validation", max_shards=0)
    shards = discover_event_sample_shards(train_config)
    validation_candidates = discover_event_sample_shards(validation_config)
    if train_start_shard >= len(shards):
        raise SystemExit(f"Need train start shard {train_start_shard}, but only found {len(shards)} train shards under {train_cache_root}")
    available_train = shards[train_start_shard:]
    selected_train = available_train[:train_shards] if train_shards > 0 else available_train
    if not selected_train:
        raise SystemExit(f"No training shards selected from {train_cache_root}")
    if any(shard.num_samples < batch_size for shard in selected_train):
        too_small = [shard.shard_index for shard in selected_train if shard.num_samples < batch_size]
        raise SystemExit(f"Selected train shards smaller than batch_size={batch_size}: {too_small[:16]}")
    if validation_shard_index >= len(validation_candidates):
        raise SystemExit(
            f"Need validation start shard {validation_shard_index}, "
            f"but only found {len(validation_candidates)} validation shards under {validation_cache_root}"
        )
    available_validation = validation_candidates[validation_shard_index:]
    validation_shards = available_validation[: min(len(available_validation), max_validation_batches)]
    validation_shards = [shard for shard in validation_shards if shard.num_samples >= batch_size]
    if not validation_shards:
        raise SystemExit(f"No validation shards with at least batch_size={batch_size} samples under {validation_cache_root}")
    return selected_train, validation_shards, len(validation_shards)


def print_plan(values: dict[str, Any], train_shards, validation_shards, validation_batches: int, steps_per_epoch: int, argv: list[str]) -> None:
    epoch_lrs = compute_epoch_lr_table(values, len(train_shards))
    print("=" * 104, flush=True)
    print("v20 x-only full pretraining continuation", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    print(f"train_cache_root={values['sample_cache_root']}", flush=True)
    print(f"train_cache_split_dir={cache_split_dir_for_display(Path(values['sample_cache_root']), 'train')}", flush=True)
    print(f"validation_cache_root={values['sample_cache_validation_root']}", flush=True)
    print(f"validation_cache_split_dir={cache_split_dir_for_display(Path(values['sample_cache_validation_root']), 'validation')}", flush=True)
    print(f"train_shards={train_shards[0].shard_index}..{train_shards[-1].shard_index} count={len(train_shards)}", flush=True)
    print(f"train_samples_per_epoch={sum(shard.num_samples for shard in train_shards):,}", flush=True)
    print(f"steps_per_epoch={steps_per_epoch:,} batch_size={values['batch_size']:,} epochs={values['epochs']:,}", flush=True)
    print(
        f"validation_split=validation shards={validation_shards[0].shard_index}..{validation_shards[-1].shard_index} "
        f"selected_shards={len(validation_shards):,} validation_batches={validation_batches:,} validation_frequency=end_of_each_shard",
        flush=True,
    )
    print(
        f"scheduler=shard_decay_cosine base_lr={values['learning_rate']} eta_min={values['scheduler_eta_min']} "
        f"epoch_decay_ratio={values['scheduler_epoch_decay_ratio']} shard_decay_fraction={values['scheduler_shard_decay_fraction']}",
        flush=True,
    )
    for row in epoch_lrs:
        print(
            f"lr_epoch={row['epoch']} epoch_peak={row['epoch_peak_lr']:.8g} "
            f"shard_decay={row['shard_decay']:.8g} first_shard_peak={row['first_shard_peak']:.8g} "
            f"last_shard_peak={row['last_shard_peak']:.8g}",
            flush=True,
        )
    print(
        f"model=d{values['d_model']} emb{values['embedding_dim']} heads{values['n_heads']} "
        f"enc{values['encoder_layers']} decoder=per_masked_event_mlp ffn_mult{values['ffn_mult']} dropout={values['dropout']}",
        flush=True,
    )
    print(f"warm_start_checkpoint={values.get('warm_start_checkpoint') or '<none>'}", flush=True)
    print(f"warm_start_load_optimizer={values.get('warm_start_load_optimizer', False)}", flush=True)
    print(f"repeatable_randomness={values['repeatable_randomness']} seed_mode={'fixed' if values['repeatable_randomness'] else 'fresh_per_run'}", flush=True)
    print(f"wandb_project={values['wandb_project']} run={values['wandb_run_name']} mode={values['wandb_mode']}", flush=True)
    print(f"compile_model={values['compile_model']} interleave_shards={values['sample_cache_interleave_shards']}", flush=True)
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    print("=" * 104, flush=True)


if __name__ == "__main__":
    main()
