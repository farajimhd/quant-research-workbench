from __future__ import annotations

import argparse
import math
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
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "data_source": "sample_cache",
    "sample_cache_root": r"D:\market-data\prepared\event_sample_cache\cache_20260611_195259",
    "sample_cache_validation_root": r"D:\market-data\prepared\event_sample_cache\cache_20260617_112833",
    "sample_cache_prefetch_shards": 2,
    "sample_cache_shuffle_records": True,
    "sample_cache_drop_last": True,
    "sample_cache_train_start_shard": 0,
    # 0 means all discovered train shards after the start shard.
    "sample_cache_train_max_shards": 0,
    "sample_cache_validation_split": "validation",
    "sample_cache_validation_start_shard": 0,
    "sample_cache_validation_max_shards": 8,
    "sample_cache_validation_batches_per_shard": 1,
    "sample_cache_interleave_shards": 1,
    "batch_size": 4096,
    "epochs": 3,
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
    "decoder_force_fp32": False,
    "event_mask_ratio": 0.70,
    "event_mask_schedule": "fixed",
    "min_masked_events": 1,
    "header_bit_corruption_prob": 0.20,
    "header_bit_corruption_ratio": 0.05,
    "event_bit_corruption_prob": 0.30,
    "event_bit_corruption_ratio": 0.20,
    "learning_rate": 1e-3,
    "weight_decay": 1e-4,
    "scheduler": "shard_decay_cosine",
    "scheduler_t_mult": 1,
    "scheduler_eta_min": 1e-6,
    "scheduler_epoch_decay_ratio": 0.80,
    "scheduler_shard_decay_fraction": 0.60,
    "grad_clip_norm": 1.0,
    "logging_steps": 10,
    "detailed_metrics_steps": 0,
    "profile_first_steps": 5,
    "profile_training_every_steps": 1000,
    "profile_inference_every_steps": 0,
    "decoder_chunk_size": 0,
    "checkpoint_latest_steps": 1000,
    "checkpoint_best_train": False,
    "checkpoint_best_val": True,
    "num_workers": 0,
    "progress_layout": "auto",
    "device": "cuda",
    "amp": True,
    "amp_dtype": "auto",
    "amp_growth_interval": 10000,
    "amp_max_scale": 2048.0,
    "compile_model": True,
    "wandb_project": "June2026-event-token-mae-v20-full-pretrain",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
    "wandb_run_name": "v20-fullpretrain-fixedmask070-emb32-bs4096-3epochs",
    "amp_initial_scale": 1024.0,
    "amp_overflow_fatal_threshold": 8,
    "float32_matmul_precision": "high",
    "warm_start_checkpoint": "",
}

VALIDATION_BATCHES = 8
PROFILED_TRAINING_PATH = (
    "v20 full pretraining, v12-style per-masked-event MLP decoder, fixed-grid bottleneck, "
    "full sample-cache shards, shard-decay cosine scheduler, no interleave, torch.compile enabled"
)


class ShardPreview:
    def __init__(self, *, shard_index: int, num_samples: int) -> None:
        self.shard_index = int(shard_index)
        self.num_samples = int(num_samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Full v20 pretraining over the larger event sample cache. By default it "
            "uses all discovered training shards and a bounded validation set."
        )
    )
    parser.add_argument("--cache-root", "--train-cache-root", dest="cache_root", default=DEFAULTS["sample_cache_root"])
    parser.add_argument("--validation-cache-root", default=DEFAULTS["sample_cache_validation_root"])
    parser.add_argument("--train-start-shard", "--sample-cache-train-start-shard", dest="train_start_shard", type=int, default=DEFAULTS["sample_cache_train_start_shard"])
    parser.add_argument("--train-shards", type=int, default=DEFAULTS["sample_cache_train_max_shards"], help="0 means all discovered train shards after start")
    parser.add_argument("--validation-shard-index", type=int, default=DEFAULTS["sample_cache_validation_start_shard"])
    parser.add_argument("--validation-batches", type=int, default=VALIDATION_BATCHES)
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--d-model", type=int, default=DEFAULTS["d_model"])
    parser.add_argument("--embedding-dim", type=int, default=DEFAULTS["embedding_dim"])
    parser.add_argument("--n-heads", type=int, default=DEFAULTS["n_heads"])
    parser.add_argument("--encoder-layers", type=int, default=DEFAULTS["encoder_layers"])
    parser.add_argument("--decoder-layers", type=int, default=DEFAULTS["decoder_layers"])
    parser.add_argument("--ffn-mult", type=int, default=DEFAULTS["ffn_mult"])
    parser.add_argument("--dropout", type=float, default=DEFAULTS["dropout"])
    parser.add_argument("--decoder-force-fp32", action=argparse.BooleanOptionalAction, default=DEFAULTS["decoder_force_fp32"])
    parser.add_argument("--event-mask-ratio", type=float, default=DEFAULTS["event_mask_ratio"])
    parser.add_argument("--event-mask-schedule", choices=("fixed", "mixed"), default=DEFAULTS["event_mask_schedule"])
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
    parser.add_argument("--amp-growth-interval", type=int, default=DEFAULTS["amp_growth_interval"])
    parser.add_argument("--amp-max-scale", type=float, default=DEFAULTS["amp_max_scale"])
    parser.add_argument("--float32-matmul-precision", choices=("highest", "high", "medium"), default=DEFAULTS["float32_matmul_precision"])
    parser.add_argument("--warm-start-checkpoint", nargs="?", const="", default=DEFAULTS["warm_start_checkpoint"])
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument(
        "--skip-shard-discovery",
        action="store_true",
        help="Only for local command inspection when the workstation sample cache is not mounted.",
    )
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    train_cache_root = Path(args.cache_root)
    validation_cache_root = Path(args.validation_cache_root)
    batch_size = max(1, int(args.batch_size))
    if args.skip_shard_discovery:
        if not args.print_only:
            raise SystemExit("--skip-shard-discovery is only allowed with --print-only")
        train_count = int(args.train_shards) if int(args.train_shards) > 0 else 1
        train_shards = [
            ShardPreview(shard_index=index, num_samples=0)
            for index in range(int(args.train_start_shard), int(args.train_start_shard) + train_count)
        ]
        validation_shards = [
            ShardPreview(shard_index=index, num_samples=0)
            for index in range(int(args.validation_shard_index), int(args.validation_shard_index) + int(args.validation_batches))
        ]
        steps_per_epoch = 0
        validation_batches = int(args.validation_batches)
        validation_batches_per_shard = max(1, math.ceil(validation_batches / max(1, len(validation_shards))))
    else:
        train_shards, validation_shards, validation_batches = resolve_shard_plan(
            train_cache_root=train_cache_root,
            validation_cache_root=validation_cache_root,
            train_start_shard=int(args.train_start_shard),
            train_shards=int(args.train_shards),
            validation_shard_index=int(args.validation_shard_index),
            batch_size=batch_size,
            max_validation_batches=int(args.validation_batches),
        )
        steps_per_epoch = sum(shard.num_samples // batch_size for shard in train_shards)
        validation_batches_per_shard = max(1, math.ceil(validation_batches / max(1, len(validation_shards))))
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
            "sample_cache_validation_batches_per_shard": validation_batches_per_shard,
            "sample_cache_interleave_shards": 1,
            "batch_size": batch_size,
            "epochs": int(args.epochs),
            "d_model": int(args.d_model),
            "embedding_dim": int(args.embedding_dim),
            "n_heads": int(args.n_heads),
            "encoder_layers": int(args.encoder_layers),
            "decoder_layers": int(args.decoder_layers),
            "ffn_mult": int(args.ffn_mult),
            "dropout": float(args.dropout),
            "decoder_force_fp32": bool(args.decoder_force_fp32),
            "event_mask_ratio": float(args.event_mask_ratio),
            "event_mask_schedule": args.event_mask_schedule,
            "learning_rate": float(args.learning_rate),
            "scheduler": "shard_decay_cosine",
            "scheduler_eta_min": float(args.scheduler_eta_min),
            "scheduler_epoch_decay_ratio": float(args.scheduler_epoch_decay_ratio),
            "scheduler_shard_decay_fraction": float(args.scheduler_shard_decay_fraction),
            # Validation runs at shard end for full pretraining because shard
            # sizes may differ and a single global frequency would drift.
            "pretrain_validation_frequency": 0,
            "pretrain_validation_steps": validation_batches,
            "checkpoint_latest_steps": int(args.checkpoint_latest_steps),
            "checkpoint_archive_steps": max(1, steps_per_epoch),
            "device": args.device,
            "wandb_project": args.wandb_project,
            "wandb_mode": args.wandb_mode,
            "wandb_run_name": args.run_name,
            "progress_layout": args.progress_layout,
            "compile_model": bool(args.compile_model),
            "amp_dtype": args.amp_dtype,
            "amp_growth_interval": int(args.amp_growth_interval),
            "amp_max_scale": float(args.amp_max_scale),
            "float32_matmul_precision": args.float32_matmul_precision,
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
    print_plan(values, train_shards, validation_shards, validation_batches, steps_per_epoch, argv)
    if args.print_only:
        return
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
    if max_validation_batches <= 0:
        raise SystemExit("--validation-batches must be positive")
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
    if validation_shard_index >= len(validation_candidates):
        raise SystemExit(
            f"Need validation start shard {validation_shard_index}, "
            f"but only found {len(validation_candidates)} validation shards under {validation_cache_root}"
        )
    available_validation = validation_candidates[validation_shard_index:]
    validation_shards = available_validation[: min(len(available_validation), max_validation_batches)]
    too_small = [shard.shard_index for shard in validation_shards if shard.num_samples < batch_size]
    if too_small:
        raise SystemExit(f"Validation shards have fewer than one full batch at batch_size={batch_size:,}: {too_small}")
    full_batch_counts = [shard.num_samples // batch_size for shard in selected_train]
    empty_train = [shard.shard_index for shard, count in zip(selected_train, full_batch_counts, strict=True) if count <= 0]
    if empty_train:
        raise SystemExit(f"Training shards have no full batches at batch_size={batch_size:,}: {empty_train}")
    return selected_train, validation_shards, max_validation_batches


def print_plan(values: dict[str, Any], train_shards, validation_shards, validation_batches: int, steps_per_epoch: int, argv: list[str]) -> None:
    epoch_lrs = compute_epoch_lr_table(values, len(train_shards))
    print("=" * 104, flush=True)
    print("v20 full pretraining over sample-cache shards", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    print(f"train_cache_root={values['sample_cache_root']}", flush=True)
    print(f"validation_cache_root={values['sample_cache_validation_root']}", flush=True)
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
    print(f"wandb_project={values['wandb_project']} run={values['wandb_run_name']} mode={values['wandb_mode']}", flush=True)
    print(f"compile_model={values['compile_model']} interleave_shards={values['sample_cache_interleave_shards']}", flush=True)
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    print("=" * 104, flush=True)


def compute_epoch_lr_table(values: dict[str, Any], shard_count: int) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    base_lr = max(0.0, float(values["learning_rate"]))
    eta_min = max(0.0, float(values["scheduler_eta_min"]))
    epoch_decay_ratio = max(0.0, float(values["scheduler_epoch_decay_ratio"]))
    shard_decay_fraction = min(max(0.0, float(values["scheduler_shard_decay_fraction"])), 1.0)
    effective_shards = max(1, int(shard_count))
    for epoch in range(1, max(1, int(values["epochs"])) + 1):
        epoch_peak_lr = max(eta_min, base_lr * (epoch_decay_ratio ** (epoch - 1)))
        shard_decay = max(0.0, (epoch_peak_lr - eta_min) * shard_decay_fraction / effective_shards)
        rows.append(
            {
                "epoch": float(epoch),
                "epoch_peak_lr": epoch_peak_lr,
                "shard_decay": shard_decay,
                "first_shard_peak": epoch_peak_lr,
                "last_shard_peak": max(eta_min, epoch_peak_lr - (effective_shards - 1) * shard_decay),
            }
        )
    return rows


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
