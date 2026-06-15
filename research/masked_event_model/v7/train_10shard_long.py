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

from research.masked_event_model.v7.train import main as train_main  # noqa: E402
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "data_source": "sample_cache",
    "sample_cache_root": r"D:\market-data\prepared\event_sample_cache",
    "sample_cache_prefetch_shards": 2,
    "sample_cache_shuffle_records": True,
    "sample_cache_drop_last": True,
    "sample_cache_train_start_shard": 0,
    "sample_cache_train_max_shards": 10,
    "sample_cache_validation_split": "train",
    "sample_cache_validation_start_shard": 10,
    "sample_cache_validation_max_shards": 1,
    "sample_cache_interleave_shards": 1,
    "batch_size": 4096,
    "epochs": 10,
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
    "compile_model": False,
    "wandb_project": "June2026-event-token-mae-v7",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
    "wandb_run_name": "v7-medium-newdecoder-emb32-bs4096-10shards-guarded",
    "amp_initial_scale": 1024.0,
    "amp_overflow_fatal_threshold": 8,
    "warm_start_checkpoint": "",
}

VALIDATION_BATCHES = 20
PROFILED_TRAINING_PATH = (
    "v7 event-token MAE, masked-query cross-attention decoder, "
    "sample-cache shards, shard-cycle scheduler, no interleave"
)


class ShardPreview:
    def __init__(self, *, shard_index: int, num_samples: int) -> None:
        self.shard_index = int(shard_index)
        self.num_samples = int(num_samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Long v7 pretraining over 10 sample-cache shards. Defaults target the "
            "medium emb32 bs4096 setup; model-size arguments are explicit so the "
            "same launcher can run high/small variants after profiling."
        )
    )
    parser.add_argument("--cache-root", default=DEFAULTS["sample_cache_root"])
    parser.add_argument(
        "--train-start-shard",
        "--sample-cache-train-start-shard",
        dest="train_start_shard",
        type=int,
        default=DEFAULTS["sample_cache_train_start_shard"],
    )
    parser.add_argument("--train-shards", type=int, default=DEFAULTS["sample_cache_train_max_shards"])
    parser.add_argument("--validation-shard-index", type=int, default=DEFAULTS["sample_cache_validation_start_shard"])
    parser.add_argument("--validation-fraction", type=float, default=0.05)
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
    parser.add_argument("--event-mask-ratio", type=float, default=DEFAULTS["event_mask_ratio"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--device", default=DEFAULTS["device"])
    parser.add_argument("--run-name", default=DEFAULTS["wandb_run_name"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=DEFAULTS["compile_model"])
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
    cache_root = Path(args.cache_root)
    batch_size = max(1, int(args.batch_size))
    if args.skip_shard_discovery:
        if not args.print_only:
            raise SystemExit("--skip-shard-discovery is only allowed with --print-only")
        train_shards = [
            ShardPreview(shard_index=index, num_samples=0)
            for index in range(int(args.train_start_shard), int(args.train_start_shard) + int(args.train_shards))
        ]
        validation_shard = ShardPreview(shard_index=int(args.validation_shard_index), num_samples=0)
        steps_per_epoch = 0
        steps_per_shard = 1
        validation_batches = int(args.validation_batches)
    else:
        train_shards, validation_shard, validation_samples = resolve_shard_plan(
            cache_root=cache_root,
            train_start_shard=int(args.train_start_shard),
            train_shards=int(args.train_shards),
            validation_shard_index=int(args.validation_shard_index),
            validation_fraction=float(args.validation_fraction),
            batch_size=batch_size,
            max_validation_batches=int(args.validation_batches),
        )
        shard_batch_counts = [shard.num_samples // batch_size for shard in train_shards]
        if any(count <= 0 for count in shard_batch_counts):
            raise SystemExit(
                "At least one selected training shard has no full batches after drop-last: "
                f"batch_size={batch_size:,}, shard_batch_counts={shard_batch_counts}"
            )
        unique_shard_batch_counts = sorted(set(shard_batch_counts))
        if len(unique_shard_batch_counts) != 1:
            raise SystemExit(
                "Selected training shards have different full-batch counts. The long launcher "
                "intentionally uses one LR restart and one validation pass per shard, so unequal "
                f"shards would make the schedule ambiguous. batch_size={batch_size:,}, "
                f"shard_batch_counts={shard_batch_counts}"
            )
        steps_per_epoch = sum(shard_batch_counts)
        steps_per_shard = unique_shard_batch_counts[0]
        validation_batches = max(1, validation_samples // batch_size)
    values = dict(DEFAULTS)
    values.update(
        {
            "sample_cache_root": str(cache_root),
            "sample_cache_train_start_shard": int(args.train_start_shard),
            "sample_cache_train_max_shards": len(train_shards),
            "sample_cache_validation_start_shard": validation_shard.shard_index,
            "sample_cache_validation_max_samples": validation_batches * batch_size,
            # Keep interleave disabled. In v4/v7 testing, interleave retained
            # transient full-shard arrays and caused persistent RAM pressure and
            # large step-time regressions after shard transitions.
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
            "event_mask_ratio": float(args.event_mask_ratio),
            "learning_rate": float(args.learning_rate),
            # One scheduler cycle per shard keeps LR restarts aligned to the
            # actual data regime instead of the whole 10-shard epoch.
            "scheduler_t0_steps": steps_per_shard,
            "pretrain_validation_frequency": steps_per_shard,
            "pretrain_validation_steps": validation_batches,
            "checkpoint_archive_steps": max(1, steps_per_epoch),
            "device": args.device,
            "wandb_project": args.wandb_project,
            "wandb_mode": args.wandb_mode,
            "wandb_run_name": args.run_name,
            "progress_layout": args.progress_layout,
            "compile_model": bool(args.compile_model),
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
    print_plan(args, values, train_shards, validation_shard, validation_batches, steps_per_epoch, steps_per_shard, argv)
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
    if max_validation_batches <= 0:
        raise SystemExit("--validation-batches must be positive")
    if not 0.0 < validation_fraction <= 1.0:
        raise SystemExit("--validation-fraction must be in (0, 1]")
    shard_config = EventSampleCacheDataConfig(cache_root=cache_root, split="train", max_shards=0)
    shards = discover_event_sample_shards(shard_config)
    if len(shards) <= validation_shard_index:
        raise SystemExit(f"Need validation shard index {validation_shard_index}, but only found {len(shards)} train shards under {cache_root}")
    if len(shards) < train_start_shard + train_shards:
        raise SystemExit(
            f"Need train shard range {train_start_shard}..{train_start_shard + train_shards - 1}, "
            f"but only found {len(shards)} train shards under {cache_root}"
        )
    selected_train = shards[train_start_shard : train_start_shard + train_shards]
    validation_shard = shards[validation_shard_index]
    selected_ids = {shard.shard_index for shard in selected_train}
    if validation_shard.shard_index in selected_ids:
        raise SystemExit(
            f"Validation shard {validation_shard.shard_index} overlaps train shards "
            f"{selected_train[0].shard_index}..{selected_train[-1].shard_index}; choose a non-overlapping validation shard."
        )
    raw_validation_samples = int(math.floor(validation_shard.num_samples * validation_fraction))
    validation_samples = min(raw_validation_samples, max_validation_batches * batch_size)
    validation_samples = (validation_samples // batch_size) * batch_size
    if validation_samples <= 0:
        raise SystemExit(
            f"Validation slice is too small after drop-last: raw={raw_validation_samples:,}, batch_size={batch_size:,}"
        )
    return selected_train, validation_shard, validation_samples


def print_plan(
    args: argparse.Namespace,
    values: dict[str, Any],
    train_shards,
    validation_shard,
    validation_batches: int,
    steps_per_epoch: int,
    steps_per_shard: int,
    argv: list[str],
) -> None:
    print("=" * 104, flush=True)
    print("v7 long pretraining over sample-cache shards", flush=True)
    print(f"profiled_training_path={PROFILED_TRAINING_PATH}", flush=True)
    print(f"cache_root={values['sample_cache_root']}", flush=True)
    print(f"train_shards={train_shards[0].shard_index}..{train_shards[-1].shard_index} count={len(train_shards)}", flush=True)
    print(f"train_samples_per_epoch={sum(shard.num_samples for shard in train_shards):,}", flush=True)
    print(
        f"steps_per_epoch={steps_per_epoch:,} steps_per_shard={steps_per_shard:,} "
        f"batch_size={values['batch_size']:,} epochs={values['epochs']:,}",
        flush=True,
    )
    print(
        f"validation_shard={validation_shard.shard_index} requested_fraction={args.validation_fraction:.3f} "
        f"validation_batches={validation_batches:,} validation_frequency=every_shard",
        flush=True,
    )
    print(
        f"model=d{values['d_model']} emb{values['embedding_dim']} heads{values['n_heads']} "
        f"enc{values['encoder_layers']} dec{values['decoder_layers']} ffn_mult{values['ffn_mult']} dropout={values['dropout']}",
        flush=True,
    )
    print(
        f"optimizer=AdamW lr={values['learning_rate']} weight_decay={values['weight_decay']} "
        f"scheduler={values['scheduler']} t0_steps={values['scheduler_t0_steps']} eta_min={values['scheduler_eta_min']}",
        flush=True,
    )
    print(
        f"logging_steps={values['logging_steps']} profile_first_steps={values['profile_first_steps']} "
        f"profile_training_every_steps={values['profile_training_every_steps']} "
        f"checkpoint_latest_steps={values['checkpoint_latest_steps']} checkpoint_archive_steps={values['checkpoint_archive_steps']}",
        flush=True,
    )
    print(
        f"amp_initial_scale={values['amp_initial_scale']} "
        f"amp_overflow_fatal_threshold={values['amp_overflow_fatal_threshold']}",
        flush=True,
    )
    print(f"wandb_project={values['wandb_project']} run={values['wandb_run_name']} mode={values['wandb_mode']}", flush=True)
    print(f"warm_start_checkpoint={values['warm_start_checkpoint'] or '<none>'} load_optimizer={values['warm_start_load_optimizer']}", flush=True)
    print(f"compile_model={values['compile_model']} interleave_shards={values['sample_cache_interleave_shards']}", flush=True)
    if values["compile_model"]:
        print("WARN compile_model=True differs from the profiled stable path.", flush=True)
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    print("=" * 104, flush=True)


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

