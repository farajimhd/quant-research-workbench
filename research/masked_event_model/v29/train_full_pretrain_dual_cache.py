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

from research.masked_event_model.v29.train import main as train_main  # noqa: E402
from research.masked_event_model.v29.train_full_pretrain import (  # noqa: E402
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


DEFAULTS: dict[str, Any] = dict(FULL_PRETRAIN_DEFAULTS)
DEFAULTS.update(
    {
        "sample_cache_root": PRIMARY_TRAIN_CACHE,
        "sample_cache_train_roots": (PRIMARY_TRAIN_CACHE, SECONDARY_TRAIN_CACHE),
        # 0 means all discovered shards for that root.
        "sample_cache_train_root_max_shards": (0, 0),
        "sample_cache_train_max_shards": 0,
        "sample_cache_validation_root": VALIDATION_CACHE,
        "sample_cache_validation_split": "validation",
        "sample_cache_validation_start_shard": 0,
        "sample_cache_validation_batches_per_shard": 1,
        "batch_size": 8192,
        "epochs": 1,
        "embedding_dim": 32,
        "learning_rate": 4e-4,
        "scheduler": "shard_decay_cosine",
        "scheduler_eta_min": 1e-6,
        "scheduler_epoch_decay_ratio": 0.90,
        "scheduler_shard_decay_fraction": 0.95,
        "warm_start_checkpoint": "",
        "warm_start_load_optimizer": False,
        "repeatable_randomness": False,
        "wandb_project": "June2026-event-token-mae-full",
        "wandb_run_name": "v29-fullpretrain-dualcache-headerrecon-emb32-bs8192-lr4e4-1epoch-freshrng",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run v29 full pretraining over all shards from cache_20260611_195259 "
            "and cache_pretrain_xonly_20260621_140813, validating against "
            "cache_20260617_112833."
        )
    )
    parser.add_argument("--primary-train-cache", default=PRIMARY_TRAIN_CACHE)
    parser.add_argument("--secondary-train-cache", default=SECONDARY_TRAIN_CACHE)
    parser.add_argument("--primary-train-shards", type=int, default=0, help="0 means all primary train shards")
    parser.add_argument("--secondary-train-shards", type=int, default=0, help="0 means all secondary train shards")
    parser.add_argument("--validation-cache-root", default=VALIDATION_CACHE)
    parser.add_argument("--validation-batches", type=int, default=0, help="0 means one batch from every discovered validation shard")
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--embedding-dim", type=int, default=DEFAULTS["embedding_dim"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--scheduler-eta-min", type=float, default=DEFAULTS["scheduler_eta_min"])
    parser.add_argument("--scheduler-epoch-decay-ratio", type=float, default=DEFAULTS["scheduler_epoch_decay_ratio"])
    parser.add_argument("--scheduler-shard-decay-fraction", type=float, default=DEFAULTS["scheduler_shard_decay_fraction"])
    parser.add_argument("--header-loss-weight", type=float, default=DEFAULTS["header_loss_weight"])
    parser.add_argument("--checkpoint-latest-steps", type=int, default=DEFAULTS["checkpoint_latest_steps"])
    parser.add_argument("--device", default=DEFAULTS["device"])
    parser.add_argument("--run-name", default=DEFAULTS["wandb_run_name"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=DEFAULTS["compile_model"])
    parser.add_argument("--amp-dtype", choices=("auto", "bf16", "fp16"), default=DEFAULTS["amp_dtype"])
    parser.add_argument("--warm-start-checkpoint", nargs="?", const="", default="")
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fresh-start", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--skip-shard-discovery", action="store_true")
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    primary_root_arg = Path(args.primary_train_cache)
    secondary_root_arg = Path(args.secondary_train_cache)
    validation_root_arg = Path(args.validation_cache_root)

    if args.skip_shard_discovery:
        if not args.print_only:
            raise SystemExit("--skip-shard-discovery is only allowed with --print-only")
        primary_root = primary_root_arg
        secondary_root = secondary_root_arg
        validation_root = validation_root_arg
        primary_count = max(1, int(args.primary_train_shards) or 1)
        secondary_count = max(1, int(args.secondary_train_shards) or 1)
        validation_count = max(1, int(args.validation_batches) or 1)
        steps_per_epoch = 0
        steps_per_shard = 1
    else:
        primary_root = resolve_explicit_cache_root(primary_root_arg, split="train")
        secondary_root = resolve_explicit_cache_root(secondary_root_arg, split="train")
        validation_root = resolve_explicit_cache_root(validation_root_arg, split="validation")
        primary_shards = discover_event_sample_shards(
            EventSampleCacheDataConfig(cache_root=primary_root, split="train", max_shards=max(0, int(args.primary_train_shards)))
        )
        secondary_shards = discover_event_sample_shards(
            EventSampleCacheDataConfig(cache_root=secondary_root, split="train", max_shards=max(0, int(args.secondary_train_shards)))
        )
        validation_shards = discover_event_sample_shards(
            EventSampleCacheDataConfig(cache_root=validation_root, split="validation", max_shards=max(0, int(args.validation_batches)))
        )
        primary_count = len(primary_shards)
        secondary_count = len(secondary_shards)
        validation_count = len(validation_shards)
        batch_size = max(1, int(args.batch_size))
        steps_per_shard_values = [(shard.num_samples // batch_size) for shard in [*primary_shards, *secondary_shards]]
        positive_steps = [count for count in steps_per_shard_values if count > 0]
        if not positive_steps:
            raise SystemExit("No train shard can emit a full batch at the requested batch size.")
        steps_per_epoch = sum(steps_per_shard_values)
        steps_per_shard = max(1, min(positive_steps))

    checkpoint_latest_steps = int(args.checkpoint_latest_steps) if int(args.checkpoint_latest_steps) > 0 else steps_per_shard
    values = dict(DEFAULTS)
    values.update(
        {
            "sample_cache_root": str(primary_root),
            "sample_cache_train_roots": (str(primary_root), str(secondary_root)),
            "sample_cache_train_root_max_shards": (max(0, int(args.primary_train_shards)), max(0, int(args.secondary_train_shards))),
            "sample_cache_train_max_shards": 0,
            "sample_cache_validation_root": str(validation_root),
            "sample_cache_validation_split": "validation",
            "sample_cache_validation_start_shard": 0,
            "sample_cache_validation_max_shards": validation_count,
            "sample_cache_validation_max_samples": validation_count * int(args.batch_size),
            "sample_cache_validation_batches_per_shard": 1,
            "batch_size": int(args.batch_size),
            "epochs": int(args.epochs),
            "embedding_dim": int(args.embedding_dim),
            "learning_rate": float(args.learning_rate),
            "scheduler": "shard_decay_cosine",
            "scheduler_eta_min": float(args.scheduler_eta_min),
            "scheduler_epoch_decay_ratio": float(args.scheduler_epoch_decay_ratio),
            "scheduler_shard_decay_fraction": float(args.scheduler_shard_decay_fraction),
            "header_loss_weight": float(args.header_loss_weight),
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

    argv = build_train_args_for_dual_cache(values)
    if args.dry_run:
        argv.append("--dry-run")
    if bool(args.fresh_start):
        argv.append("--fresh-start")
    argv.extend(args.extra)
    print_plan(values, primary_count, secondary_count, validation_count, steps_per_epoch, argv)
    validate_required_warm_start(values)
    if args.print_only:
        return
    train_main(argv)


def build_train_args_for_dual_cache(values: dict[str, Any]) -> list[str]:
    values = dict(values)
    train_roots = values.pop("sample_cache_train_roots")
    train_root_limits = values.pop("sample_cache_train_root_max_shards")
    argv = build_train_args(values)
    argv.extend(["--sample-cache-train-roots", ";".join(str(root) for root in train_roots)])
    argv.extend(["--sample-cache-train-root-max-shards", ",".join(str(value) for value in train_root_limits)])
    return argv


def print_plan(
    values: dict[str, Any],
    primary_count: int,
    secondary_count: int,
    validation_count: int,
    steps_per_epoch: int,
    argv: list[str],
) -> None:
    epoch_lrs = compute_epoch_lr_table(values, primary_count + secondary_count)
    roots = tuple(values["sample_cache_train_roots"])
    limits = tuple(values["sample_cache_train_root_max_shards"])
    print("=" * 104, flush=True)
    print("v29 dual-cache full pretraining", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    print(f"train_root_1={roots[0]} max_shards={_limit_label(limits[0])} discovered_shards={primary_count:,}", flush=True)
    print(f"train_root_2={roots[1]} max_shards={_limit_label(limits[1])} discovered_shards={secondary_count:,}", flush=True)
    print(f"train_shards_total={primary_count + secondary_count:,} steps_per_epoch={steps_per_epoch:,}", flush=True)
    print(f"validation_cache_root={values['sample_cache_validation_root']}", flush=True)
    print(f"validation_cache_split_dir={cache_split_dir_for_display(Path(values['sample_cache_validation_root']), 'validation')}", flush=True)
    print(f"validation_shards={validation_count:,} validation_frequency=end_of_each_shard", flush=True)
    print(
        f"model=d{values['d_model']} emb{values['embedding_dim']} heads{values['n_heads']} "
        f"enc{values['encoder_layers']} event_decoder=per_masked_event_mlp header_decoder=mlp "
        f"header_loss_weight={values['header_loss_weight']}",
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


def _limit_label(value: int) -> str:
    return "all" if int(value) <= 0 else str(int(value))


if __name__ == "__main__":
    main()
