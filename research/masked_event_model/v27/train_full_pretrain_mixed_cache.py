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

from research.masked_event_model.v27.train import main as train_main  # noqa: E402
from research.masked_event_model.v27.train_full_pretrain import (  # noqa: E402
    DEFAULTS as FULL_PRETRAIN_DEFAULTS,
    PROFILED_TRAINING_PATH,
    build_train_args,
    cache_split_dir_for_display,
    compute_epoch_lr_table,
    resolve_explicit_cache_root,
    validate_required_warm_start,
)
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards  # noqa: E402


PRIMARY_TRAIN_CACHE = r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\event_sample_cache\cache_20260611_195259\train"
SECONDARY_TRAIN_CACHE = r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\event_sample_cache\cache_pretrain_xonly_20260621_140813\train"
VALIDATION_CACHE = r"\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\event_sample_cache\cache_20260617_112833\validation"
SOURCE_CHECKPOINT = (
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\masked_event_model\\v27\pretrain"
    r"\v27-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-lr1e4-epoch09-shard095-continue5epochs"
    r"\checkpoints\checkpoint_step_000260352.pt"
)


DEFAULTS: dict[str, Any] = dict(FULL_PRETRAIN_DEFAULTS)
DEFAULTS.update(
    {
        "sample_cache_root": PRIMARY_TRAIN_CACHE,
        "sample_cache_train_roots": (PRIMARY_TRAIN_CACHE, SECONDARY_TRAIN_CACHE),
        # 0 means all shards from the first cache; 54 means shard_000000..000053
        # from the x-only cache that was ready when this launcher was created.
        "sample_cache_train_root_max_shards": (0, 54),
        "sample_cache_validation_root": VALIDATION_CACHE,
        "warm_start_checkpoint": SOURCE_CHECKPOINT,
        "warm_start_load_optimizer": False,
        "epochs": 3,
        "learning_rate": 4e-4,
        "repeatable_randomness": False,
        "wandb_project": "June2026-event-token-mae-full",
        "wandb_run_name": "v27-fullpretrain-mixedcache-fixedmask070-emb32-bs8192-lr4e4-3epochs-freshrng-from-step260352",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start v27 full pretraining from the step-260352 checkpoint over a "
            "mixed train shard list: all cache_20260611_195259 train shards plus "
            "the first 54 cache_pretrain_xonly_20260621_140813 train shards."
        )
    )
    parser.add_argument("--primary-train-cache", default=PRIMARY_TRAIN_CACHE)
    parser.add_argument("--secondary-train-cache", default=SECONDARY_TRAIN_CACHE)
    parser.add_argument("--secondary-train-shards", type=int, default=54)
    parser.add_argument("--validation-cache-root", default=VALIDATION_CACHE)
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
    parser.add_argument("--warm-start-checkpoint", nargs="?", const="", default=SOURCE_CHECKPOINT)
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fresh-start", action="store_true", default=True)
    parser.add_argument("--no-fresh-start", action="store_false", dest="fresh_start")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--skip-shard-discovery", action="store_true")
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    if int(args.secondary_train_shards) <= 0:
        raise SystemExit("--secondary-train-shards must be positive for this mixed-cache launcher")

    primary_root_arg = Path(args.primary_train_cache)
    secondary_root_arg = Path(args.secondary_train_cache)
    validation_root_arg = Path(args.validation_cache_root)
    if args.skip_shard_discovery:
        if not args.print_only:
            raise SystemExit("--skip-shard-discovery is only allowed with --print-only")
        primary_root = primary_root_arg
        secondary_root = secondary_root_arg
        validation_root = validation_root_arg
        primary_count = 1
        secondary_count = int(args.secondary_train_shards)
        validation_count = int(DEFAULTS["sample_cache_validation_max_shards"])
        steps_per_epoch = 0
        steps_per_shard = 1
    else:
        primary_root = resolve_explicit_cache_root(primary_root_arg, split="train")
        secondary_root = resolve_explicit_cache_root(secondary_root_arg, split="train")
        validation_root = resolve_explicit_cache_root(validation_root_arg, split="validation")
        primary_shards = discover_event_sample_shards(EventSampleCacheDataConfig(cache_root=primary_root, split="train", max_shards=0))
        secondary_shards = discover_event_sample_shards(
            EventSampleCacheDataConfig(cache_root=secondary_root, split="train", max_shards=int(args.secondary_train_shards))
        )
        validation_shards = discover_event_sample_shards(
            EventSampleCacheDataConfig(
                cache_root=validation_root,
                split="validation",
                max_shards=int(DEFAULTS["sample_cache_validation_max_shards"]),
            )
        )
        primary_count = len(primary_shards)
        secondary_count = len(secondary_shards)
        validation_count = len(validation_shards)
        batch_size = max(1, int(args.batch_size))
        steps_per_shard_values = [(shard.num_samples // batch_size) for shard in [*primary_shards, *secondary_shards]]
        steps_per_epoch = sum(steps_per_shard_values)
        steps_per_shard = max(1, min(count for count in steps_per_shard_values if count > 0))

    checkpoint_latest_steps = int(args.checkpoint_latest_steps) if int(args.checkpoint_latest_steps) > 0 else steps_per_shard
    values = dict(DEFAULTS)
    values.update(
        {
            "sample_cache_root": str(primary_root),
            "sample_cache_train_roots": (str(primary_root), str(secondary_root)),
            "sample_cache_train_root_max_shards": (0, int(args.secondary_train_shards)),
            "sample_cache_train_max_shards": 0,
            "sample_cache_validation_root": str(validation_root),
            "sample_cache_validation_split": "validation",
            "sample_cache_validation_start_shard": 0,
            "sample_cache_validation_max_shards": validation_count,
            "sample_cache_validation_max_samples": validation_count * int(args.batch_size),
            "sample_cache_validation_batches_per_shard": 1,
            "batch_size": int(args.batch_size),
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
    argv = build_train_args_for_mixed(values)
    if args.dry_run:
        argv.append("--dry-run")
    if args.fresh_start:
        argv.append("--fresh-start")
    argv.extend(args.extra)
    print_plan(values, primary_count, secondary_count, validation_count, steps_per_epoch, argv)
    validate_required_warm_start(values)
    if args.print_only:
        return
    train_main(argv)


def print_plan(
    values: dict[str, Any],
    primary_count: int,
    secondary_count: int,
    validation_count: int,
    steps_per_epoch: int,
    argv: list[str],
) -> None:
    epoch_lrs = compute_epoch_lr_table(values, primary_count + secondary_count)
    print("=" * 104, flush=True)
    print("v27 mixed-cache full pretraining", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    roots = tuple(values["sample_cache_train_roots"])
    limits = tuple(values["sample_cache_train_root_max_shards"])
    print(f"train_root_1={roots[0]} max_shards=all discovered_shards={primary_count:,}", flush=True)
    print(f"train_root_2={roots[1]} max_shards={limits[1]} discovered_shards={secondary_count:,}", flush=True)
    print(f"train_shards_total={primary_count + secondary_count:,} steps_per_epoch={steps_per_epoch:,}", flush=True)
    print(f"validation_cache_root={values['sample_cache_validation_root']}", flush=True)
    print(f"validation_cache_split_dir={cache_split_dir_for_display(Path(values['sample_cache_validation_root']), 'validation')}", flush=True)
    print(f"validation_shards={validation_count:,} validation_frequency=end_of_each_shard", flush=True)
    print(
        f"model=d{values['d_model']} emb{values['embedding_dim']} heads{values['n_heads']} "
        f"enc{values['encoder_layers']} decoder=per_masked_event_mlp ffn_mult{values['ffn_mult']} dropout={values['dropout']}",
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
    print(f"warm_start_checkpoint={values.get('warm_start_checkpoint') or '<none>'}", flush=True)
    print(f"warm_start_load_optimizer={values.get('warm_start_load_optimizer', False)}", flush=True)
    print(f"repeatable_randomness={values['repeatable_randomness']} seed_mode={'fixed' if values['repeatable_randomness'] else 'fresh_per_run'}", flush=True)
    print(f"wandb_project={values['wandb_project']} run={values['wandb_run_name']} mode={values['wandb_mode']}", flush=True)
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    print("=" * 104, flush=True)


def build_train_args_for_mixed(values: dict[str, Any]) -> list[str]:
    values = dict(values)
    train_roots = values.pop("sample_cache_train_roots")
    train_root_limits = values.pop("sample_cache_train_root_max_shards")
    argv = build_train_args(values)
    argv.extend(["--sample-cache-train-roots", ";".join(str(root) for root in train_roots)])
    argv.extend(["--sample-cache-train-root-max-shards", ",".join(str(value) for value in train_root_limits)])
    return argv


if __name__ == "__main__":
    main()
