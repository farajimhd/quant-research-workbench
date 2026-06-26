from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import secrets
import shutil
import sys
import time
import traceback
import importlib.util
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
import numpy as np
from torch.utils.data import DataLoader

from research.masked_event_model.v22.config import DataConfig, ExperimentConfig, LossConfig, MaskConfig, ModelConfig, TrainConfig
from research.masked_event_model.v22.losses import masked_event_bce_loss
from research.masked_event_model.v22.masking import EventMaskBatch, build_event_masks
from research.masked_event_model.v22.model import EventTokenMaskedAutoencoder
from research.masked_event_model.v22.progress import TrainingProgressState, TrainingReporter
from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.compact_events import (
    CompactEventDataConfig,
    CompactEventIterableDataset,
    PrecomputedChunkDataConfig,
    PrecomputedV4ChunkIterableDataset,
    build_fixed_precomputed_validation_batches,
    discover_precomputed_chunk_shards,
    iter_precomputed_epoch_batches,
)
from research.mlops.clickhouse_events import ClickHouseEventsChunkIterableDataset, ClickHouseEventsDataConfig
from research.mlops.event_sample_cache import (
    EventSampleCacheDataConfig,
    EventSampleShard,
    discover_event_sample_shards,
    iter_event_sample_cache_epoch_batches,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb


MODEL_FAMILY = "masked_event_model"
MODEL_VERSION = "v22"
JOB_TYPE = "pretrain"
MAX_NUMPY_SAFE_BASE_SEED = (2**32) - 1_000_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    model_defaults = ModelConfig()
    mask_defaults = MaskConfig()
    loss_defaults = LossConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Train v22 event-token MAE on compact event sample-cache batches.")
    parser.add_argument("--data-source", choices=("clickhouse_events", "sample_cache", "precomputed", "canonical"), default=data_defaults.data_source)
    parser.add_argument("--canonical-root", default=str(data_defaults.canonical_root))
    parser.add_argument("--precomputed-chunk-root", default=str(data_defaults.precomputed_chunk_root or ""))
    parser.add_argument("--sample-cache-root", default=str(data_defaults.sample_cache_root or ""))
    parser.add_argument("--sample-cache-validation-root", default=str(data_defaults.sample_cache_validation_root or ""))
    parser.add_argument("--sample-cache-train-roots", default="")
    parser.add_argument("--sample-cache-train-root-max-shards", default="")
    parser.add_argument("--reference-dir", default=str(data_defaults.reference_dir))
    parser.add_argument("--clickhouse-url", default=data_defaults.clickhouse_url)
    parser.add_argument("--clickhouse-database", default=data_defaults.clickhouse_database)
    parser.add_argument("--events-table", default=data_defaults.events_table)
    parser.add_argument("--train-index-table", default=data_defaults.train_index_table)
    parser.add_argument("--validation-index-table", default=data_defaults.validation_index_table)
    parser.add_argument("--index-table", default=data_defaults.index_table)
    parser.add_argument("--output-root", default="")
    parser.add_argument("--run-root", default="")
    parser.add_argument("--train-start-date", default=data_defaults.train_start_date)
    parser.add_argument("--train-end-date", default=data_defaults.train_end_date)
    parser.add_argument("--validation-start-date", default=data_defaults.validation_start_date)
    parser.add_argument("--validation-end-date", default=data_defaults.validation_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--events-per-chunk", type=int, default=data_defaults.events_per_chunk)
    parser.add_argument("--num-spans", type=int, default=data_defaults.num_spans)
    parser.add_argument("--origins-per-span", type=int, default=data_defaults.origins_per_span)
    parser.add_argument("--min-origin-stride", type=int, default=data_defaults.min_origin_stride)
    parser.add_argument("--max-origin-stride", type=int, default=data_defaults.max_origin_stride)
    parser.add_argument("--query-bundle-spans", type=int, default=data_defaults.query_bundle_spans)
    parser.add_argument("--clickhouse-max-threads", type=int, default=data_defaults.clickhouse_max_threads)
    parser.add_argument("--clickhouse-max-memory-usage", default=data_defaults.clickhouse_max_memory_usage)
    parser.add_argument("--month-cache-size", type=int, default=data_defaults.month_cache_size)
    parser.add_argument("--sample-cache-prefetch-shards", type=int, default=data_defaults.sample_cache_prefetch_shards)
    parser.add_argument("--sample-cache-train-start-shard", type=int, default=data_defaults.sample_cache_train_start_shard)
    parser.add_argument("--sample-cache-train-max-shards", type=int, default=data_defaults.sample_cache_train_max_shards)
    parser.add_argument("--sample-cache-validation-split", default=data_defaults.sample_cache_validation_split)
    parser.add_argument("--sample-cache-validation-start-shard", type=int, default=data_defaults.sample_cache_validation_start_shard)
    parser.add_argument("--sample-cache-validation-max-shards", type=int, default=data_defaults.sample_cache_validation_max_shards)
    parser.add_argument("--sample-cache-validation-max-samples", type=int, default=data_defaults.sample_cache_validation_max_samples)
    parser.add_argument("--sample-cache-validation-batches-per-shard", type=int, default=data_defaults.sample_cache_validation_batches_per_shard)
    parser.add_argument("--sample-cache-shuffle-records", action=argparse.BooleanOptionalAction, default=data_defaults.sample_cache_shuffle_records)
    parser.add_argument("--sample-cache-drop-last", action=argparse.BooleanOptionalAction, default=data_defaults.sample_cache_drop_last)
    parser.add_argument("--sample-cache-interleave-shards", type=int, default=data_defaults.sample_cache_interleave_shards)
    parser.add_argument("--max-index-files", type=int, default=data_defaults.max_index_files)
    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--max-steps", type=int, default=train_defaults.max_steps)
    parser.add_argument("--epochs", type=int, default=train_defaults.epochs)
    parser.add_argument("--num-workers", type=int, default=train_defaults.num_workers)
    parser.add_argument("--prefetch-factor", type=int, default=train_defaults.prefetch_factor)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=train_defaults.repeatable_randomness)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train_defaults.weight_decay)
    parser.add_argument("--scheduler", choices=("none", "cosine_warm_restarts", "shard_decay_cosine"), default=train_defaults.scheduler)
    parser.add_argument("--scheduler-t0-steps", type=int, default=train_defaults.scheduler_t0_steps)
    parser.add_argument("--scheduler-t-mult", type=int, default=train_defaults.scheduler_t_mult)
    parser.add_argument("--scheduler-eta-min", type=float, default=train_defaults.scheduler_eta_min)
    parser.add_argument("--scheduler-epoch-decay-ratio", type=float, default=train_defaults.scheduler_epoch_decay_ratio)
    parser.add_argument("--scheduler-shard-decay-fraction", type=float, default=train_defaults.scheduler_shard_decay_fraction)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=train_defaults.logging_steps)
    parser.add_argument("--detailed-metrics-steps", type=int, default=train_defaults.detailed_metrics_steps)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=train_defaults.progress_layout)
    parser.add_argument("--loss-objective", choices=("weighted", "unweighted"), default=loss_defaults.objective)
    parser.add_argument("--profile-first-steps", type=int, default=train_defaults.profile_first_steps)
    parser.add_argument("--profile-training-every-steps", type=int, default=train_defaults.profile_training_every_steps)
    parser.add_argument("--profile-inference-every-steps", type=int, default=train_defaults.profile_inference_every_steps)
    parser.add_argument("--decoder-chunk-size", type=int, default=train_defaults.decoder_chunk_size)
    parser.add_argument("--pretrain-validation-frequency", type=int, default=train_defaults.pretrain_validation_frequency)
    parser.add_argument("--pretrain-validation-steps", type=int, default=train_defaults.pretrain_validation_steps)
    parser.add_argument("--checkpoint-latest-steps", type=int, default=train_defaults.checkpoint_latest_steps)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=train_defaults.checkpoint_archive_steps)
    parser.add_argument("--checkpoint-best-train", action=argparse.BooleanOptionalAction, default=train_defaults.checkpoint_best_train)
    parser.add_argument("--checkpoint-best-val", action=argparse.BooleanOptionalAction, default=train_defaults.checkpoint_best_val)
    parser.add_argument("--event-mask-ratio", type=float, default=mask_defaults.event_mask_ratio)
    parser.add_argument("--event-mask-schedule", choices=("fixed", "mixed"), default=mask_defaults.event_mask_schedule)
    parser.add_argument("--event-mask-high-probability", type=float, default=mask_defaults.event_mask_high_probability)
    parser.add_argument("--event-mask-zero-probability", type=float, default=mask_defaults.event_mask_zero_probability)
    parser.add_argument("--event-mask-low-probability", type=float, default=mask_defaults.event_mask_low_probability)
    parser.add_argument("--event-mask-high-min", type=float, default=mask_defaults.event_mask_high_min)
    parser.add_argument("--event-mask-high-max", type=float, default=mask_defaults.event_mask_high_max)
    parser.add_argument("--event-mask-low-min", type=float, default=mask_defaults.event_mask_low_min)
    parser.add_argument("--event-mask-low-max", type=float, default=mask_defaults.event_mask_low_max)
    parser.add_argument("--min-masked-events", type=int, default=mask_defaults.min_masked_events)
    parser.add_argument("--header-bit-corruption-prob", type=float, default=mask_defaults.header_bit_corruption_prob)
    parser.add_argument("--header-bit-corruption-ratio", type=float, default=mask_defaults.header_bit_corruption_ratio)
    parser.add_argument("--event-bit-corruption-prob", type=float, default=mask_defaults.event_bit_corruption_prob)
    parser.add_argument("--event-bit-corruption-ratio", type=float, default=mask_defaults.event_bit_corruption_ratio)
    parser.add_argument("--input-representation", choices=("byte", "bit"), default=model_defaults.input_representation)
    parser.add_argument("--d-byte", type=int, default=model_defaults.d_byte)
    parser.add_argument("--d-model", type=int, default=model_defaults.d_model)
    parser.add_argument("--embedding-dim", type=int, default=model_defaults.embedding_dim)
    parser.add_argument("--n-heads", type=int, default=model_defaults.n_heads)
    parser.add_argument("--encoder-layers", type=int, default=model_defaults.encoder_layers)
    parser.add_argument("--decoder-layers", type=int, default=model_defaults.decoder_layers)
    parser.add_argument("--ffn-mult", type=int, default=model_defaults.ffn_mult)
    parser.add_argument("--dropout", type=float, default=model_defaults.dropout)
    parser.add_argument(
        "--decoder-force-fp32",
        action=argparse.BooleanOptionalAction,
        default=model_defaults.decoder_force_fp32,
        help=(
            "Run the disposable reconstruction decoder outside AMP. Use "
            "--no-decoder-force-fp32 with --amp-dtype fp16 to test full-FP16 "
            "v22 decoder training."
        ),
    )
    parser.add_argument(
        "--bottleneck-force-fp32",
        action=argparse.BooleanOptionalAction,
        default=model_defaults.bottleneck_force_fp32,
        help="Run the exported fixed-grid chunk embedding bottleneck outside AMP for precision diagnostics.",
    )
    parser.add_argument("--bottleneck-branch-hidden-dim", type=int, default=model_defaults.bottleneck_branch_hidden_dim)
    parser.add_argument("--bottleneck-branch-embedding-dim", type=int, default=model_defaults.bottleneck_branch_embedding_dim)
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-init-timeout", type=int, default=60)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=train_defaults.amp)
    parser.add_argument("--amp-dtype", choices=("auto", "bf16", "fp16"), default=train_defaults.amp_dtype)
    parser.add_argument("--amp-initial-scale", type=float, default=train_defaults.amp_initial_scale)
    parser.add_argument("--amp-growth-interval", type=int, default=train_defaults.amp_growth_interval)
    parser.add_argument("--amp-max-scale", type=float, default=train_defaults.amp_max_scale)
    parser.add_argument("--amp-overflow-fatal-threshold", type=int, default=train_defaults.amp_overflow_fatal_threshold)
    parser.add_argument(
        "--float32-matmul-precision",
        choices=("highest", "high", "medium"),
        default=train_defaults.float32_matmul_precision,
        help="Controls PyTorch FP32 matmul precision; 'high' enables TF32 tensor cores on NVIDIA GPUs.",
    )
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=train_defaults.compile_model)
    parser.add_argument("--warm-start-checkpoint", default="")
    parser.add_argument("--warm-start-load-optimizer", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--initial-validation", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def resolve_effective_seed(requested_seed: int, *, repeatable_randomness: bool) -> int:
    if repeatable_randomness:
        return int(requested_seed)
    # NumPy's legacy global RNG rejects seeds outside uint32. Validation adds
    # deterministic offsets to the run seed, so keep enough headroom while still
    # drawing a fresh value for every non-repeatable run.
    return int(secrets.randbelow(MAX_NUMPY_SAFE_BASE_SEED))


def validation_mask_seed(run_seed: int, global_step: int) -> int:
    """Return a NumPy-safe validation seed that changes every validation call."""

    safe_span = MAX_NUMPY_SAFE_BASE_SEED - 100_000
    return int((int(run_seed) + 90_000 + int(global_step) * 1_009) % safe_span)


@contextmanager
def isolated_rng_state(device: torch.device):
    """Run code that samples masks without advancing the caller's RNG streams."""

    py_state = random.getstate()
    np_state = np.random.get_state()
    torch_state = torch.get_rng_state()
    cuda_states = torch.cuda.get_rng_state_all() if device.type == "cuda" and torch.cuda.is_available() else None
    try:
        yield
    finally:
        random.setstate(py_state)
        np.random.set_state(np_state)
        torch.set_rng_state(torch_state)
        if cuda_states is not None:
            torch.cuda.set_rng_state_all(cuda_states)


def checkpoint_rng_state() -> dict[str, Any]:
    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }


def restore_checkpoint_rng_state(state: dict[str, Any] | None) -> None:
    if not state:
        return
    if state.get("python") is not None:
        random.setstate(state["python"])
    if state.get("numpy") is not None:
        np.random.set_state(state["numpy"])
    if state.get("torch") is not None:
        torch.set_rng_state(state["torch"])
    if state.get("cuda") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda"])


def parse_path_tuple(value: str) -> tuple[Path, ...]:
    parts = [part.strip().strip('"') for part in str(value or "").replace("\n", ";").split(";")]
    return tuple(Path(part) for part in parts if part)


def parse_int_tuple(value: str) -> tuple[int, ...]:
    parts = [part.strip() for part in str(value or "").replace(";", ",").split(",")]
    return tuple(int(part) for part in parts if part)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT))
    requested_seed = int(args.seed)
    args.requested_seed = requested_seed
    args.seed = resolve_effective_seed(requested_seed, repeatable_randomness=bool(args.repeatable_randomness))
    config = build_config(args)
    set_seed(config.train.seed)
    run_name = config.train.wandb_run_name or default_run_name(args)
    config.train.wandb_run_name = run_name
    output_dir = resolve_output_dir(config, args)
    if args.fresh_start:
        clean_run_output_dir(output_dir, keep_paths=[Path(args.warm_start_checkpoint)] if args.warm_start_checkpoint else [])
    run_paths = RunPaths.create(output_dir)
    install_fatal_exception_logger(run_paths)
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    print(f"Output directory: {output_dir}", flush=True)
    print(f"Device: {device}", flush=True)
    print(
        f"Randomness: repeatable={config.train.repeatable_randomness} "
        f"requested_seed={requested_seed} effective_seed={config.train.seed}",
        flush=True,
    )
    configure_float32_matmul_precision(config.train, device)
    print(f"Input shape: header=[B,14] events=[B,{config.data.events_per_chunk},16]", flush=True)
    print(f"Input representation: {config.model.input_representation}", flush=True)

    model = EventTokenMaskedAutoencoder(events_per_chunk=config.data.events_per_chunk, config=config.model).to(device)
    model_parameters = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {model_parameters:,}", flush=True)
    if config.train.decoder_chunk_size > 0:
        print("WARN --decoder-chunk-size is ignored by v22; reconstruction uses a per-masked-event MLP decoder.", flush=True)
    if config.model.decoder_layers != ModelConfig().decoder_layers:
        print("WARN --decoder-layers is ignored by v22; reconstruction uses a per-masked-event MLP decoder.", flush=True)
    train_model = maybe_compile_model(model, config.train.compile_model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    amp_dtype = resolve_amp_dtype(config.train, device)
    scaler_enabled = amp_dtype == torch.float16
    scaler = torch.amp.GradScaler(
        "cuda",
        init_scale=max(1.0, float(config.train.amp_initial_scale)),
        growth_interval=max(1, int(config.train.amp_growth_interval)),
        enabled=scaler_enabled,
    )
    print(f"AMP: enabled={amp_dtype is not None} dtype={amp_dtype_name(amp_dtype)} grad_scaler={scaler.is_enabled()}", flush=True)
    global_step = maybe_resume_or_warm_start(
        model,
        optimizer,
        scheduler,
        scaler,
        output_dir,
        fresh_start=args.fresh_start,
        warm_start_checkpoint=Path(args.warm_start_checkpoint) if args.warm_start_checkpoint else None,
        warm_start_load_optimizer=bool(args.warm_start_load_optimizer),
    )

    wandb_run = init_wandb(args, config, output_dir)
    metric_logger = JsonlMetricLogger(run_paths.metrics_path, wandb_run, wandb_key_mapper=v22_wandb_metric_key)
    write_run_manifest(
        run_paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=dataclass_tree(config),
        data_roots={
            "data_source": config.data.data_source,
            "canonical_root": str(config.data.canonical_root),
            "precomputed_chunk_root": str(config.data.precomputed_chunk_root or ""),
            "sample_cache_root": str(config.data.sample_cache_root or ""),
            "reference_dir": str(config.data.reference_dir),
            "clickhouse_url": config.data.clickhouse_url,
            "clickhouse_database": config.data.clickhouse_database,
            "events_table": config.data.events_table,
            "train_index_table": config.data.train_index_table,
            "validation_index_table": config.data.validation_index_table,
        },
        output_root=output_dir,
        wandb_info={"project": args.wandb_project, "entity": args.wandb_entity, "run_name": run_name},
    )
    (output_dir / "config.json").write_text(json.dumps(dataclass_tree(config), indent=2, default=str), encoding="utf-8")
    save_model_artifacts(model, config, run_paths, wandb_run, device)
    checkpointer = AsyncCheckpointManager(
        run_paths.checkpoints_dir,
        run_paths.checkpoint_manifest_path,
        CheckpointPolicy(
            latest_steps=args.checkpoint_latest_steps,
            archive_steps=args.checkpoint_archive_steps,
            save_best_train=args.checkpoint_best_train,
            save_best_val=args.checkpoint_best_val,
        ),
    )
    failure_debug_dir = run_paths.artifacts_dir / "failure_debug"

    if args.dry_run:
        if config.data.data_source == "sample_cache":
            sample_config = sample_cache_data_config(config, "train", args.seed)
            batch = next(iter_event_sample_cache_epoch_batches(
                sample_config,
                epoch=1,
                shards=discover_train_sample_cache_shards(config, sample_config),
            ))
        else:
            batch = next(iter(make_loader(config, "train", args.seed)))
        batch = move_batch(batch, device)
        masks = build_event_masks(batch["events_uint8"], config.masks)
        with torch.no_grad():
            output = model(batch["header_uint8"], batch["events_uint8"], masks, config.masks)
            result = masked_event_bce_loss(output, config.losses, header_uint8=batch["header_uint8"], include_diagnostics=True)
            embedding = model.encode(batch["header_uint8"], batch["events_uint8"])
        print(f"Dry run loss={float(result.loss):.6f} embedding={tuple(embedding.shape)}", flush=True)
        print(json.dumps(result.metrics, indent=2), flush=True)
        return

    reporter_state = TrainingProgressState(
        run_name=run_name,
        device=str(device),
        data_source=config.data.data_source,
        batch_size=config.train.batch_size,
        max_steps=config.train.max_steps,
        epochs=config.train.epochs,
        model_parameters=model_parameters,
        output_dir=str(output_dir),
    )
    reporter_context: Any
    reporter_context = TrainingReporter(layout=args.progress_layout, state=reporter_state) if args.progress_layout != "none" else nullcontext()
    with reporter_context as reporter:
        checkpointer.set_message_callback(reporter.message if reporter is not None else None)
        if reporter is not None:
            reporter.message(f"Training started. Output: {output_dir}")
        validation_batches = build_validation_cache(config, args.seed + 50_000, reporter=reporter)
        if validation_batches and args.initial_validation:
            val_metrics = evaluate_validation(
                model,
                validation_batches,
                config,
                device,
                seed=validation_mask_seed(args.seed, global_step),
            )
            metric_logger.log(val_metrics, global_step)
            emit_progress_message(reporter, "Initial validation " + format_metrics(global_step, val_metrics))
            if reporter is not None:
                reporter.update({}, step=global_step, validation_metrics=val_metrics)
        try:
            if config.data.data_source == "sample_cache":
                global_step = train_sample_cache_epochs(
                    model=model,
                    train_model=train_model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    scaler=scaler,
                    config=config,
                    args=args,
                    device=device,
                    global_step=global_step,
                    validation_batches=validation_batches,
                    metric_logger=metric_logger,
                    checkpointer=checkpointer,
                    reporter=reporter,
                    failure_debug_dir=failure_debug_dir,
                )
            elif config.data.data_source == "precomputed":
                global_step = train_precomputed_epochs(
                    model=model,
                    train_model=train_model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    scaler=scaler,
                    config=config,
                    args=args,
                    device=device,
                    global_step=global_step,
                    validation_batches=validation_batches,
                    metric_logger=metric_logger,
                    checkpointer=checkpointer,
                    reporter=reporter,
                    failure_debug_dir=failure_debug_dir,
                )
            else:
                global_step = train_streaming_loader(
                    model=model,
                    train_model=train_model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    scaler=scaler,
                    config=config,
                    args=args,
                    device=device,
                    global_step=global_step,
                    validation_batches=validation_batches,
                    metric_logger=metric_logger,
                    checkpointer=checkpointer,
                    reporter=reporter,
                    failure_debug_dir=failure_debug_dir,
                )
            checkpointer.maybe_save(
                step=global_step,
                payload_factory=lambda step=global_step: checkpoint_payload(model, optimizer, scheduler, step, config, args, scaler=scaler),
                force=True,
            )
        finally:
            checkpointer.close()
        if wandb_run is not None and reporter is not None:
            reporter.message(f"W&B run: {getattr(wandb_run, 'url', '<unknown>')}")


def train_precomputed_epochs(
    *,
    model: EventTokenMaskedAutoencoder,
    train_model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    args: argparse.Namespace,
    device: torch.device,
    global_step: int,
    validation_batches: list[dict[str, Any]],
    metric_logger: JsonlMetricLogger,
    checkpointer: AsyncCheckpointManager,
    reporter: TrainingReporter | None,
    failure_debug_dir: Path,
) -> int:
    data_config = precomputed_data_config(config, "train", args.seed)
    train_shards = discover_precomputed_chunk_shards(data_config)
    shard_count = len(train_shards)
    emit_progress_message(
        reporter,
        f"Precomputed training shards={shard_count:,} epochs={config.train.epochs:,} "
        f"batch_size={config.train.batch_size:,} max_steps={config.train.max_steps:,}",
    )
    samples_seen_total = 0
    stop_training = False
    for epoch in range(1, max(1, int(config.train.epochs)) + 1):
        epoch_started = time.perf_counter()
        epoch_steps = 0
        epoch_samples = 0
        epoch_loss_sum = 0.0
        iterator = iter_precomputed_epoch_batches(data_config, epoch=epoch, shards=train_shards)
        while True:
            if config.train.max_steps > 0 and global_step >= config.train.max_steps:
                stop_training = True
                break
            data_wait_started = time.perf_counter()
            try:
                batch = next(iterator)
            except StopIteration:
                break
            data_wait_seconds = time.perf_counter() - data_wait_started
            global_step += 1
            shard_index = int(batch.get("shard_index", 0) or 0)
            shard_position = int(batch.get("shard_position", shard_index) or shard_index)
            shard_step = int(batch.get("shard_step", 0) or 0)
            shard_steps = max(1, int(batch.get("shard_steps", 1) or 1))
            run_validation = should_validate_step(config, global_step, shard_step=shard_step, shard_steps=shard_steps)
            lr_metrics = apply_sample_cache_step_lr(
                optimizer=optimizer,
                train_config=config.train,
                epoch=epoch,
                shard_position=shard_position,
                shard_count=int(batch.get("shard_count", shard_count) or shard_count),
                shard_step=shard_step,
                shard_steps=shard_steps,
            )
            batch_size = int(batch["header_uint8"].shape[0])
            samples_seen_total += batch_size
            epoch_samples += batch_size
            epoch_steps += 1
            metrics = run_training_step(
                model=model,
                train_model=train_model,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                batch=batch,
                config=config,
                device=device,
                global_step=global_step,
                data_wait_seconds=data_wait_seconds,
                force_diagnostics=run_validation,
                failure_debug_dir=failure_debug_dir,
                args=args,
            )
            epoch_loss_sum += float(metrics.get("pretrain/loss_total", 0.0))
            effective_shard_count = int(batch.get("shard_count", shard_count) or shard_count)
            metrics.update(
                {
                    "train/epoch": float(epoch),
                    "train/epoch_step": float(epoch_steps),
                    "train/epoch_progress_pct": 100.0 * ((max(0, shard_position - 1) + shard_step / shard_steps) / max(1, effective_shard_count)),
                    "train/shard_index": float(shard_index),
                    "train/shard_position": float(shard_position),
                    "train/shards_per_epoch": float(effective_shard_count),
                    "train/shard_step": float(shard_step),
                    "train/shard_steps": float(shard_steps),
                    "train/samples_seen_epoch": float(epoch_samples),
                    "train/samples_seen_total": float(samples_seen_total),
                }
            )
            metrics.update(lr_metrics)
            if shard_step == shard_steps:
                metrics["train/shard_completed"] = 1.0
            val_metrics = maybe_log_train_and_validation(
                model=model,
                config=config,
                device=device,
                args=args,
                global_step=global_step,
                metrics=metrics,
                validation_batches=validation_batches,
                metric_logger=metric_logger,
                reporter=reporter,
                force_validation=run_validation,
            )
            checkpointer.maybe_save(
                step=global_step,
                payload_factory=lambda step=global_step: checkpoint_payload(model, optimizer, scheduler, step, config, args, scaler=scaler),
                train_metrics=metrics,
                val_metrics=val_metrics,
            )
        epoch_metrics = {
            "train/epoch": float(epoch),
            "train/epoch_seconds": time.perf_counter() - epoch_started,
            "train/epoch_steps": float(epoch_steps),
            "train/epoch_samples": float(epoch_samples),
            "train/epoch_loss_mean": epoch_loss_sum / max(1, epoch_steps),
        }
        metric_logger.log(epoch_metrics, global_step)
        emit_progress_message(reporter, "EPOCH " + format_metrics(global_step, epoch_metrics))
        if stop_training:
            break
    return global_step


def shuffled_epoch_sample_cache_shards(
    shards: list[Any],
    *,
    seed: int,
    epoch: int,
) -> list[Any]:
    """Return a deterministic per-epoch shuffle of the selected shard files.

    Record order is still shuffled inside each loaded shard by the shared
    sample-cache iterator. This outer shuffle changes which shard files are
    loaded first every epoch, so the epoch is not tied to the filesystem order.
    """

    epoch_shards = list(shards)
    rng = random.Random(int(seed) + int(epoch) * 1_000_003)
    rng.shuffle(epoch_shards)
    return epoch_shards


def sample_cache_shard_step_count(shard: Any, config: EventSampleCacheDataConfig) -> int:
    """Return the number of emitted batches for one sample-cache shard."""

    usable_samples = int(shard.num_samples)
    if config.max_batches_per_shard > 0:
        usable_samples = min(usable_samples, int(config.max_batches_per_shard) * max(1, int(config.batch_size)))
    if config.drop_last:
        usable_samples = (usable_samples // max(1, int(config.batch_size))) * max(1, int(config.batch_size))
    if usable_samples <= 0:
        return 0
    return int(math.ceil(usable_samples / max(1, int(config.batch_size))))


def discover_train_sample_cache_shards(config: ExperimentConfig, data_config: EventSampleCacheDataConfig) -> list[EventSampleShard]:
    roots = tuple(config.data.sample_cache_train_roots)
    if not roots:
        return discover_event_sample_shards(data_config)

    max_shards_by_root = tuple(config.data.sample_cache_train_root_max_shards)
    mixed: list[EventSampleShard] = []
    for root_index, root in enumerate(roots):
        root_max_shards = max_shards_by_root[root_index] if root_index < len(max_shards_by_root) else 0
        root_config = replace(
            data_config,
            cache_root=Path(root),
            start_shard_index=0,
            max_shards=max(0, int(root_max_shards)),
        )
        root_shards = discover_event_sample_shards(root_config)
        for shard in root_shards:
            # Mixed roots often reuse local shard_index values. Assign a unique
            # logical index so per-shard record shuffles and progress reporting
            # do not collide, while keeping the actual shard path untouched.
            mixed.append(
                EventSampleShard(
                    split=shard.split,
                    shard_index=len(mixed),
                    path=shard.path,
                    meta_path=shard.meta_path,
                    num_samples=shard.num_samples,
                    sample_bytes=shard.sample_bytes,
                    sha256=shard.sha256,
                    byte_size=shard.byte_size,
                )
            )
    if data_config.start_shard_index > 0:
        mixed = mixed[int(data_config.start_shard_index) :]
    if data_config.max_shards > 0:
        mixed = mixed[: int(data_config.max_shards)]
    if not mixed:
        raise RuntimeError(f"No mixed sample-cache train shards found from roots={roots}")
    return mixed


def train_sample_cache_epochs(
    *,
    model: EventTokenMaskedAutoencoder,
    train_model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    args: argparse.Namespace,
    device: torch.device,
    global_step: int,
    validation_batches: list[dict[str, Any]],
    metric_logger: JsonlMetricLogger,
    checkpointer: AsyncCheckpointManager,
    reporter: TrainingReporter | None,
    failure_debug_dir: Path,
) -> int:
    data_config = sample_cache_data_config(config, "train", args.seed)
    train_shards = discover_train_sample_cache_shards(config, data_config)
    shard_count = len(train_shards)
    total_samples = sum(shard.num_samples for shard in train_shards)
    planned_shard_steps = [sample_cache_shard_step_count(shard, data_config) for shard in train_shards]
    planned_epoch_steps = sum(planned_shard_steps)
    planned_total_steps = planned_epoch_steps * max(1, int(config.train.epochs))
    emit_progress_message(
        reporter,
        f"Sample-cache training shards={shard_count:,} samples={total_samples:,} "
        f"epochs={config.train.epochs:,} batch_size={config.train.batch_size:,} "
        f"steps_per_epoch={planned_epoch_steps:,} max_steps={config.train.max_steps:,}",
    )
    if config.data.sample_cache_train_roots:
        root_text = ", ".join(str(root) for root in config.data.sample_cache_train_roots)
        limit_text = ",".join(str(value) for value in config.data.sample_cache_train_root_max_shards) or "all"
        emit_progress_message(reporter, f"Mixed sample-cache train roots={root_text} root_max_shards={limit_text}")
    samples_seen_total = 0
    stop_training = False
    for epoch in range(1, max(1, int(config.train.epochs)) + 1):
        epoch_started = time.perf_counter()
        epoch_steps = 0
        epoch_samples = 0
        epoch_loss_sum = 0.0
        epoch_shards = shuffled_epoch_sample_cache_shards(train_shards, seed=args.seed, epoch=epoch)
        emit_progress_message(
            reporter,
            "Sample-cache epoch "
            f"{epoch}/{config.train.epochs} shuffled_shards="
            f"{epoch_shards[0].shard_index}..{epoch_shards[min(len(epoch_shards), 5) - 1].shard_index}"
            f"{'...' if len(epoch_shards) > 5 else ''}",
        )
        iterator = iter_event_sample_cache_epoch_batches(data_config, epoch=epoch, shards=epoch_shards)
        while True:
            if config.train.max_steps > 0 and global_step >= config.train.max_steps:
                stop_training = True
                break
            data_wait_started = time.perf_counter()
            try:
                batch = next(iterator)
            except StopIteration:
                break
            data_wait_seconds = time.perf_counter() - data_wait_started
            global_step += 1
            shard_index = int(batch.get("shard_index", 0) or 0)
            shard_position = int(batch.get("shard_position", shard_index) or shard_index)
            shard_step = int(batch.get("shard_step", 0) or 0)
            shard_steps = max(1, int(batch.get("shard_steps", 1) or 1))
            run_validation = should_validate_step(config, global_step, shard_step=shard_step, shard_steps=shard_steps)
            lr_metrics = apply_sample_cache_step_lr(
                optimizer=optimizer,
                train_config=config.train,
                epoch=epoch,
                shard_position=shard_position,
                shard_count=int(batch.get("shard_count", shard_count) or shard_count),
                shard_step=shard_step,
                shard_steps=shard_steps,
            )
            batch_size = int(batch["header_uint8"].shape[0])
            samples_seen_total += batch_size
            epoch_samples += batch_size
            epoch_steps += 1
            metrics = run_training_step(
                model=model,
                train_model=train_model,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                batch=batch,
                config=config,
                device=device,
                global_step=global_step,
                data_wait_seconds=data_wait_seconds,
                force_diagnostics=run_validation,
                failure_debug_dir=failure_debug_dir,
                args=args,
            )
            epoch_loss_sum += float(metrics.get("pretrain/loss_total", 0.0))
            effective_shard_count = int(batch.get("shard_count", shard_count) or shard_count)
            metrics.update(
                {
                    "train/epoch": float(epoch),
                    "train/epoch_step": float(epoch_steps),
                    "train/epoch_steps_planned": float(planned_epoch_steps),
                    "train/total_steps_planned": float(planned_total_steps),
                    "train/epoch_progress_pct": 100.0 * (epoch_steps / max(1, planned_epoch_steps)),
                    "train/shard_index": float(shard_index),
                    "train/shard_position": float(shard_position),
                    "train/shards_per_epoch": float(effective_shard_count),
                    "train/shard_step": float(shard_step),
                    "train/shard_steps": float(shard_steps),
                    "train/samples_seen_epoch": float(epoch_samples),
                    "train/samples_seen_total": float(samples_seen_total),
                }
            )
            metrics.update(lr_metrics)
            val_metrics = maybe_log_train_and_validation(
                model=model,
                config=config,
                device=device,
                args=args,
                global_step=global_step,
                metrics=metrics,
                validation_batches=validation_batches,
                metric_logger=metric_logger,
                reporter=reporter,
                force_validation=run_validation,
            )
            checkpointer.maybe_save(
                step=global_step,
                payload_factory=lambda step=global_step: checkpoint_payload(model, optimizer, scheduler, step, config, args, scaler=scaler),
                train_metrics=metrics,
                val_metrics=val_metrics,
            )
        epoch_metrics = {
            "train/epoch": float(epoch),
            "train/epoch_seconds": time.perf_counter() - epoch_started,
            "train/epoch_steps": float(epoch_steps),
            "train/epoch_samples": float(epoch_samples),
            "train/epoch_loss_mean": epoch_loss_sum / max(1, epoch_steps),
        }
        metric_logger.log(epoch_metrics, global_step)
        emit_progress_message(reporter, "EPOCH " + format_metrics(global_step, epoch_metrics))
        if stop_training:
            break
    return global_step


def train_streaming_loader(
    *,
    model: EventTokenMaskedAutoencoder,
    train_model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    args: argparse.Namespace,
    device: torch.device,
    global_step: int,
    validation_batches: list[dict[str, Any]],
    metric_logger: JsonlMetricLogger,
    checkpointer: AsyncCheckpointManager,
    reporter: TrainingReporter | None,
    failure_debug_dir: Path,
) -> int:
    loader = make_loader(config, "train", args.seed)
    loader_iter = iter(loader)
    while config.train.max_steps <= 0 or global_step < config.train.max_steps:
        data_wait_started = time.perf_counter()
        batch = next(loader_iter)
        data_wait_seconds = time.perf_counter() - data_wait_started
        global_step += 1
        metrics = run_training_step(
            model=model,
            train_model=train_model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            batch=batch,
            config=config,
            device=device,
            global_step=global_step,
            data_wait_seconds=data_wait_seconds,
            failure_debug_dir=failure_debug_dir,
            args=args,
        )
        val_metrics = maybe_log_train_and_validation(
            model=model,
            config=config,
            device=device,
            args=args,
            global_step=global_step,
            metrics=metrics,
            validation_batches=validation_batches,
            metric_logger=metric_logger,
            reporter=reporter,
        )
        checkpointer.maybe_save(
            step=global_step,
            payload_factory=lambda step=global_step: checkpoint_payload(model, optimizer, scheduler, step, config, args, scaler=scaler),
            train_metrics=metrics,
            val_metrics=val_metrics,
        )
    return global_step


def run_training_step(
    *,
    model: EventTokenMaskedAutoencoder,
    train_model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    batch: dict[str, Any],
    config: ExperimentConfig,
    device: torch.device,
    global_step: int,
    data_wait_seconds: float,
    force_diagnostics: bool = False,
    failure_debug_dir: Path | None = None,
    args: argparse.Namespace | None = None,
) -> dict[str, float]:
    step_started = time.perf_counter()
    profile_step = should_profile_step(config, global_step)
    if profile_step and device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    transfer_started = time.perf_counter()
    batch = move_batch(batch, device)
    if profile_step:
        sync_if_cuda(device)
    transfer_seconds = time.perf_counter() - transfer_started
    mask_started = time.perf_counter()
    masks = build_event_masks(batch["events_uint8"], config.masks)
    if profile_step:
        sync_if_cuda(device)
    mask_seconds = time.perf_counter() - mask_started
    forward_started = time.perf_counter()
    include_diagnostics = force_diagnostics or (config.train.detailed_metrics_steps > 0 and global_step % config.train.detailed_metrics_steps == 0)
    # Detailed reconstruction metrics touch large masked-byte tensors. Most
    # steps keep the same BCE objective but skip that extra metric work so the
    # training loop measures model learning instead of metric bookkeeping.
    metric_level = "cheap" if force_diagnostics else "loss_only"
    if include_diagnostics:
        metric_level = "standard"
    amp_dtype = resolve_amp_dtype(config.train, device)
    with torch.amp.autocast("cuda", enabled=amp_dtype is not None, dtype=amp_dtype):
        output = train_model(batch["header_uint8"], batch["events_uint8"], masks, config.masks)
        result = masked_event_bce_loss(
            output,
            config.losses,
            header_uint8=batch["header_uint8"],
            include_diagnostics=include_diagnostics,
            profile_metrics=profile_step,
            metric_level=metric_level,
        )
    if not torch.isfinite(result.loss).item():
        debug_path = save_failure_debug_bundle(
            reason="nonfinite_forward_loss",
            failure_debug_dir=failure_debug_dir,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            config=config,
            args=args,
            global_step=global_step,
            batch=batch,
            masks=masks,
            output=output,
            metrics=result.metrics,
        )
        raise_nonfinite_training_error(output, result.metrics, global_step, debug_path=debug_path)
    if profile_step:
        sync_if_cuda(device)
    forward_loss_seconds = time.perf_counter() - forward_started
    backward_started = time.perf_counter()
    optimizer.zero_grad(set_to_none=True)
    try:
        scaler.scale(result.loss).backward()
        if profile_step:
            sync_if_cuda(device)
        backward_seconds = time.perf_counter() - backward_started
        optimizer_started = time.perf_counter()
        scaler.unscale_(optimizer)
        old_scale = scaler.get_scale()
        nonfinite_gradient = find_first_nonfinite_gradient(model) if force_diagnostics else None
        if nonfinite_gradient is not None and scaler.is_enabled():
            amp_overflow_streak = int(getattr(scaler, "_qresearch_amp_overflow_streak", 0)) + 1
            setattr(scaler, "_qresearch_amp_overflow_streak", amp_overflow_streak)
            scaler.update(new_scale=max(1.0, float(old_scale) * 0.5))
            clamp_grad_scaler_(scaler, config.train)
            amp_step_skipped = True
            grad_norm = torch.tensor(float("nan"), device=device)
            if amp_overflow_streak >= max(1, int(config.train.amp_overflow_fatal_threshold)):
                debug_path = save_failure_debug_bundle(
                    reason="amp_repeated_nonfinite_gradient",
                    failure_debug_dir=failure_debug_dir,
                    model=model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    scaler=scaler,
                    config=config,
                    args=args,
                    global_step=global_step,
                    batch=batch,
                    masks=masks,
                    output=output,
                    metrics=result.metrics
                    | {
                        "debug/nonfinite_gradient": nonfinite_gradient,
                        "debug/amp_old_scale": old_scale,
                        "debug/amp_new_scale": scaler.get_scale(),
                        "debug/amp_overflow_streak": amp_overflow_streak,
                    },
                )
                raise FloatingPointError(
                    "AMP repeatedly produced non-finite gradients at "
                    f"step={global_step}: {nonfinite_gradient}. "
                    f"streak={amp_overflow_streak}. Debug bundle: {debug_path}"
                )
            optimizer.zero_grad(set_to_none=True)
        elif nonfinite_gradient is not None:
            debug_path = save_failure_debug_bundle(
                reason="nonfinite_gradient",
                failure_debug_dir=failure_debug_dir,
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                config=config,
                args=args,
                global_step=global_step,
                batch=batch,
                masks=masks,
                output=output,
                metrics=result.metrics | {"debug/nonfinite_gradient": nonfinite_gradient},
            )
            raise FloatingPointError(f"Non-finite gradient without AMP recovery path: {nonfinite_gradient}. Debug bundle: {debug_path}")
        else:
            setattr(scaler, "_qresearch_amp_overflow_streak", 0)
            grad_norm = torch.nn.utils.clip_grad_norm_(
                model.parameters(),
                config.train.grad_clip_norm,
                error_if_nonfinite=force_diagnostics,
            )
            scaler.step(optimizer)
            scaler.update()
            clamp_grad_scaler_(scaler, config.train)
            amp_step_skipped = bool(scaler.is_enabled() and scaler.get_scale() < old_scale)
            if scheduler is not None and not amp_step_skipped:
                scheduler.step(global_step)
            nonfinite_parameter = find_first_nonfinite_parameter(model) if force_diagnostics else None
            if nonfinite_parameter is not None:
                debug_path = save_failure_debug_bundle(
                    reason="nonfinite_model_parameter",
                    failure_debug_dir=failure_debug_dir,
                    model=model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    scaler=scaler,
                    config=config,
                    args=args,
                    global_step=global_step,
                    batch=batch,
                    masks=masks,
                    output=output,
                    metrics=result.metrics | {"debug/nonfinite_parameter": nonfinite_parameter},
                )
                raise FloatingPointError(f"Non-finite model parameter after optimizer step: {nonfinite_parameter}. Debug bundle: {debug_path}")
    except Exception as exc:
        if "Debug bundle:" in str(exc):
            raise
        debug_path = save_failure_debug_bundle(
            reason="backward_or_optimizer_failure",
            failure_debug_dir=failure_debug_dir,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            config=config,
            args=args,
            global_step=global_step,
            batch=batch,
            masks=masks,
            output=output,
            metrics=result.metrics | {"debug/exception": repr(exc)},
        )
        raise RuntimeError(f"Training step failed after debug bundle was saved to {debug_path}") from exc
    if profile_step:
        sync_if_cuda(device)
    optimizer_seconds = time.perf_counter() - optimizer_started
    metrics = dict(result.metrics)
    step_seconds = time.perf_counter() - step_started
    batch_size = float(batch["header_uint8"].shape[0])
    metrics.update(
        {
            "train/lr": float(optimizer.param_groups[0]["lr"]),
            "train/step_seconds": step_seconds,
            "train/samples_per_second": batch_size / max(step_seconds, 1e-9),
            "train/grad_norm": float(grad_norm.detach().cpu()),
            "train/amp_step_skipped": float(amp_step_skipped),
            "train/amp_scale": float(scaler.get_scale()) if scaler.is_enabled() else 0.0,
            "train/amp_dtype_id": amp_dtype_id(amp_dtype),
            "train/amp_overflow_streak": float(getattr(scaler, "_qresearch_amp_overflow_streak", 0)),
            "profile/batch_size": batch_size,
        }
    )
    if profile_step:
        data_profile = batch.get("profile", {})
        metrics.update(
            {
                "profile/data_wait_seconds": data_wait_seconds,
                "profile/transfer_seconds": transfer_seconds,
                "profile/mask_seconds": mask_seconds,
                "profile/forward_loss_seconds": forward_loss_seconds,
                "profile/metrics_seconds": result.metrics.get("profile/metrics_seconds", result.metrics.get("profile/event_metrics_seconds", 0.0)),
                "profile/backward_seconds": backward_seconds,
                "profile/optimizer_seconds": optimizer_seconds,
                "profile/profile_active": 1.0,
                **{f"profile/{key}": float(value) for key, value in data_profile.items()},
                **resource_profile(device),
            }
        )
    if config.train.profile_inference_every_steps > 0 and global_step % config.train.profile_inference_every_steps == 0:
        metrics.update(profile_encode(model, batch, device, amp_dtype=amp_dtype))
    return metrics


def raise_nonfinite_training_error(output: Any, metrics: dict[str, float], global_step: int, *, debug_path: Path | None = None) -> None:
    logits = output.event_bit_logits.detach().float()
    finite = torch.isfinite(logits)
    finite_logits = logits[finite]
    if finite_logits.numel() > 0:
        logit_min = float(finite_logits.min().cpu())
        logit_max = float(finite_logits.max().cpu())
        logit_mean = float(finite_logits.mean().cpu())
    else:
        logit_min = float("nan")
        logit_max = float("nan")
        logit_mean = float("nan")
    bad_count = int((~finite).sum().cpu())
    total_count = int(logits.numel())
    raise FloatingPointError(
        "Non-finite training loss at "
        f"step={global_step}, loss={metrics.get('pretrain/loss_total')}, "
        f"unweighted_loss={metrics.get('pretrain/loss_event_unweighted')}, "
        f"masked_events={metrics.get('mask/event_masked_events')}, "
        f"requested_mask_pct={metrics.get('mask/event_requested_mask_ratio_pct')}, "
        f"logit_bad={bad_count}/{total_count}, "
        f"finite_logit_min={logit_min}, finite_logit_max={logit_max}, finite_logit_mean={logit_mean}, "
        f"debug_bundle={debug_path}"
    )


def save_failure_debug_bundle(
    *,
    reason: str,
    failure_debug_dir: Path | None,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    args: argparse.Namespace | None,
    global_step: int,
    batch: dict[str, Any],
    masks: EventMaskBatch,
    output: Any,
    metrics: dict[str, Any],
) -> Path | None:
    if failure_debug_dir is None:
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    debug_dir = failure_debug_dir / f"step_{global_step:09d}_{reason}_{timestamp}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": reason,
        "global_step": int(global_step),
        "metrics": metrics,
        "config": dataclass_tree(config),
        "args": vars(args) if args is not None else {},
        "model": to_cpu_debug(model.state_dict()),
        "gradients": to_cpu_debug({name: parameter.grad for name, parameter in model.named_parameters() if parameter.grad is not None}),
        "optimizer": to_cpu_debug(optimizer.state_dict()),
        "scheduler": to_cpu_debug(scheduler.state_dict()) if scheduler is not None else None,
        "scaler": to_cpu_debug(scaler.state_dict()),
        "batch": to_cpu_debug({key: value for key, value in batch.items() if key != "profile"}),
        "masks": to_cpu_debug(
            {
                "visible_event_indices": masks.visible_event_indices,
                "masked_event_indices": masks.masked_event_indices,
                "visible_count": masks.visible_count,
                "masked_count": masks.masked_count,
                "event_count": masks.event_count,
                "requested_mask_ratio": masks.requested_mask_ratio,
                "actual_mask_ratio": masks.actual_mask_ratio,
                "mask_policy_id": masks.mask_policy_id,
                "mask_policy_name": masks.mask_policy_name,
            }
        ),
        "output": to_cpu_debug(
            {
                "event_bit_logits": output.event_bit_logits,
                "target_events_uint8": output.target_events_uint8,
                "chunk_embedding": output.chunk_embedding,
                "masked_event_indices": output.masked_event_indices,
            }
        ),
    }
    torch.save(payload, debug_dir / "failure_debug.pt")
    summary = {
        "reason": reason,
        "global_step": int(global_step),
        "metrics": metrics,
        "path": str(debug_dir / "failure_debug.pt"),
    }
    (debug_dir / "failure_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return debug_dir / "failure_debug.pt"


def to_cpu_debug(value: Any) -> Any:
    if torch.is_tensor(value):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: to_cpu_debug(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_cpu_debug(item) for item in value]
    if isinstance(value, tuple):
        return tuple(to_cpu_debug(item) for item in value)
    return value


def find_first_nonfinite_parameter(model: torch.nn.Module) -> str | None:
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if not bool(torch.isfinite(parameter).all()):
                return name
    return None


def find_first_nonfinite_gradient(model: torch.nn.Module) -> str | None:
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all()):
                return name
    return None


def should_profile_step(config: ExperimentConfig, global_step: int) -> bool:
    if config.train.profile_first_steps > 0 and global_step <= config.train.profile_first_steps:
        return True
    return config.train.profile_training_every_steps > 0 and global_step % config.train.profile_training_every_steps == 0


def should_validate_step(config: ExperimentConfig, global_step: int, *, shard_step: int, shard_steps: int) -> bool:
    if config.train.pretrain_validation_frequency > 0:
        return global_step > 0 and global_step % config.train.pretrain_validation_frequency == 0
    return shard_steps > 0 and shard_step == shard_steps


def resource_profile(device: torch.device) -> dict[str, float]:
    metrics: dict[str, float] = {}
    try:
        import psutil

        process = psutil.Process(os.getpid())
        metrics["profile/process_rss_gib"] = process.memory_info().rss / 1024**3
        vm = psutil.virtual_memory()
        metrics["profile/system_memory_used_gib"] = (vm.total - vm.available) / 1024**3
        metrics["profile/system_memory_available_gib"] = vm.available / 1024**3
    except Exception:  # noqa: BLE001
        pass
    if device.type == "cuda":
        metrics.update(
            {
                "profile/gpu_allocated_gib": torch.cuda.memory_allocated(device) / 1024**3,
                "profile/gpu_reserved_gib": torch.cuda.memory_reserved(device) / 1024**3,
                "profile/gpu_peak_allocated_gib": torch.cuda.max_memory_allocated(device) / 1024**3,
                "profile/gpu_peak_reserved_gib": torch.cuda.max_memory_reserved(device) / 1024**3,
            }
        )
        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info(device)
            metrics["profile/gpu_free_gib"] = free_bytes / 1024**3
            metrics["profile/gpu_total_gib"] = total_bytes / 1024**3
        except Exception:  # noqa: BLE001
            pass
    return metrics


def configure_float32_matmul_precision(train_config: TrainConfig, device: torch.device) -> None:
    precision = str(train_config.float32_matmul_precision).lower().strip()
    if precision not in {"highest", "high", "medium"}:
        raise ValueError(
            f"Unsupported float32_matmul_precision={train_config.float32_matmul_precision!r}; "
            "expected highest, high, or medium."
        )
    torch.set_float32_matmul_precision(precision)
    if device.type == "cuda":
        # Use the new PyTorch matmul-precision API instead of manually toggling
        # backend flags. On NVIDIA GPUs, `high` enables TF32 matmuls for the
        # FP32 decoder path; replay tests kept the stable FP32 gradients while
        # improving step time versus full FP32 matmuls.
        print(
            "Float32 matmul precision: "
            f"{torch.get_float32_matmul_precision()} "
            f"(cuda.matmul.allow_tf32={torch.backends.cuda.matmul.allow_tf32})",
            flush=True,
        )
    else:
        print(f"Float32 matmul precision: {torch.get_float32_matmul_precision()} (device={device})", flush=True)


def resolve_amp_dtype(train_config: TrainConfig, device: torch.device) -> torch.dtype | None:
    if not train_config.amp or device.type != "cuda":
        return None
    requested = str(train_config.amp_dtype).lower().strip()
    if requested == "bf16":
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("Requested --amp-dtype bf16, but this CUDA device does not report BF16 support.")
        return torch.bfloat16
    if requested == "fp16":
        return torch.float16
    if requested != "auto":
        raise ValueError(f"Unsupported amp_dtype={train_config.amp_dtype!r}; expected auto, bf16, or fp16.")
    return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16


def amp_dtype_name(dtype: torch.dtype | None) -> str:
    if dtype is None:
        return "off"
    if dtype == torch.bfloat16:
        return "bf16"
    if dtype == torch.float16:
        return "fp16"
    return str(dtype)


def amp_dtype_id(dtype: torch.dtype | None) -> float:
    if dtype == torch.bfloat16:
        return 2.0
    if dtype == torch.float16:
        return 1.0
    return 0.0


def clamp_grad_scaler_(scaler: torch.amp.GradScaler, train_config: TrainConfig) -> None:
    if not scaler.is_enabled():
        return
    max_scale = float(train_config.amp_max_scale)
    if max_scale <= 0:
        return
    current = float(scaler.get_scale())
    if current > max_scale:
        scaler.update(new_scale=max_scale)


def maybe_log_train_and_validation(
    *,
    model: EventTokenMaskedAutoencoder,
    config: ExperimentConfig,
    device: torch.device,
    args: argparse.Namespace,
    global_step: int,
    metrics: dict[str, float],
    validation_batches: list[dict[str, Any]],
    metric_logger: JsonlMetricLogger,
    reporter: TrainingReporter | None = None,
    force_validation: bool = False,
) -> dict[str, float] | None:
    if global_step % config.train.logging_steps == 0 or force_validation:
        metric_logger.log(metrics, global_step)
        if reporter is None:
            print(format_metrics(global_step, metrics), flush=True)
    val_metrics = None
    run_validation = force_validation or (
        validation_batches
        and config.train.pretrain_validation_frequency > 0
        and global_step > 0
        and global_step % config.train.pretrain_validation_frequency == 0
    )
    if validation_batches and run_validation:
        val_metrics = evaluate_validation(
            model,
            validation_batches,
            config,
            device,
            seed=validation_mask_seed(args.seed, global_step),
        )
        metric_logger.log(val_metrics, global_step)
        if reporter is None:
            print("VALIDATION " + format_metrics(global_step, val_metrics), flush=True)
        else:
            reporter.message("Validation " + format_metrics(global_step, val_metrics))
    if reporter is not None:
        reporter.update(metrics, step=global_step, validation_metrics=val_metrics)
    return val_metrics


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    return ExperimentConfig(
        data=DataConfig(
            data_source=args.data_source,
            canonical_root=Path(args.canonical_root),
            precomputed_chunk_root=Path(args.precomputed_chunk_root) if args.precomputed_chunk_root else None,
            sample_cache_root=Path(args.sample_cache_root) if args.sample_cache_root else None,
            sample_cache_validation_root=Path(args.sample_cache_validation_root) if args.sample_cache_validation_root else None,
            sample_cache_train_roots=parse_path_tuple(args.sample_cache_train_roots),
            sample_cache_train_root_max_shards=parse_int_tuple(args.sample_cache_train_root_max_shards),
            reference_dir=Path(args.reference_dir),
            clickhouse_url=args.clickhouse_url,
            clickhouse_database=args.clickhouse_database,
            events_table=args.events_table,
            train_index_table=args.train_index_table,
            validation_index_table=args.validation_index_table,
            index_table=args.index_table,
            train_start_date=args.train_start_date,
            train_end_date=args.train_end_date,
            validation_start_date=args.validation_start_date,
            validation_end_date=args.validation_end_date,
            tickers=tuple(part.strip().upper() for part in args.tickers.split(",") if part.strip()) or ("ALL",),
            events_per_chunk=args.events_per_chunk,
            num_spans=args.num_spans,
            origins_per_span=args.origins_per_span,
            min_origin_stride=args.min_origin_stride,
            max_origin_stride=args.max_origin_stride,
            query_bundle_spans=args.query_bundle_spans,
            clickhouse_max_threads=args.clickhouse_max_threads,
            clickhouse_max_memory_usage=args.clickhouse_max_memory_usage,
            month_cache_size=args.month_cache_size,
            sample_cache_prefetch_shards=args.sample_cache_prefetch_shards,
            sample_cache_train_start_shard=args.sample_cache_train_start_shard,
            sample_cache_train_max_shards=args.sample_cache_train_max_shards,
            sample_cache_validation_split=args.sample_cache_validation_split,
            sample_cache_validation_start_shard=args.sample_cache_validation_start_shard,
            sample_cache_validation_max_shards=args.sample_cache_validation_max_shards,
            sample_cache_validation_max_samples=args.sample_cache_validation_max_samples,
            sample_cache_validation_batches_per_shard=args.sample_cache_validation_batches_per_shard,
            sample_cache_shuffle_records=args.sample_cache_shuffle_records,
            sample_cache_drop_last=args.sample_cache_drop_last,
            sample_cache_interleave_shards=args.sample_cache_interleave_shards,
            max_index_files=args.max_index_files,
        ),
        masks=MaskConfig(
            event_mask_ratio=args.event_mask_ratio,
            event_mask_schedule=args.event_mask_schedule,
            event_mask_high_probability=args.event_mask_high_probability,
            event_mask_zero_probability=args.event_mask_zero_probability,
            event_mask_low_probability=args.event_mask_low_probability,
            event_mask_high_min=args.event_mask_high_min,
            event_mask_high_max=args.event_mask_high_max,
            event_mask_low_min=args.event_mask_low_min,
            event_mask_low_max=args.event_mask_low_max,
            min_masked_events=args.min_masked_events,
            header_bit_corruption_prob=args.header_bit_corruption_prob,
            header_bit_corruption_ratio=args.header_bit_corruption_ratio,
            event_bit_corruption_prob=args.event_bit_corruption_prob,
            event_bit_corruption_ratio=args.event_bit_corruption_ratio,
        ),
        model=ModelConfig(
            input_representation=args.input_representation,
            d_byte=args.d_byte,
            d_model=args.d_model,
            embedding_dim=args.embedding_dim,
            n_heads=args.n_heads,
            encoder_layers=args.encoder_layers,
            decoder_layers=args.decoder_layers,
            ffn_mult=args.ffn_mult,
            dropout=args.dropout,
            decoder_force_fp32=args.decoder_force_fp32,
            bottleneck_force_fp32=args.bottleneck_force_fp32,
            bottleneck_branch_hidden_dim=args.bottleneck_branch_hidden_dim,
            bottleneck_branch_embedding_dim=args.bottleneck_branch_embedding_dim,
        ),
        losses=LossConfig(objective=args.loss_objective),
        train=TrainConfig(batch_size=args.batch_size, max_steps=args.max_steps, epochs=args.epochs, learning_rate=args.learning_rate, weight_decay=args.weight_decay, scheduler=args.scheduler, scheduler_t0_steps=args.scheduler_t0_steps, scheduler_t_mult=args.scheduler_t_mult, scheduler_eta_min=args.scheduler_eta_min, scheduler_epoch_decay_ratio=args.scheduler_epoch_decay_ratio, scheduler_shard_decay_fraction=args.scheduler_shard_decay_fraction, grad_clip_norm=args.grad_clip_norm, logging_steps=args.logging_steps, detailed_metrics_steps=args.detailed_metrics_steps, progress_layout=args.progress_layout, profile_first_steps=args.profile_first_steps, profile_training_every_steps=args.profile_training_every_steps, profile_inference_every_steps=args.profile_inference_every_steps, decoder_chunk_size=args.decoder_chunk_size, pretrain_validation_frequency=args.pretrain_validation_frequency, pretrain_validation_steps=args.pretrain_validation_steps, checkpoint_latest_steps=args.checkpoint_latest_steps, checkpoint_archive_steps=args.checkpoint_archive_steps, checkpoint_best_train=args.checkpoint_best_train, checkpoint_best_val=args.checkpoint_best_val, num_workers=args.num_workers, prefetch_factor=args.prefetch_factor, seed=args.seed, repeatable_randomness=args.repeatable_randomness, amp=args.amp, amp_dtype=args.amp_dtype, amp_initial_scale=args.amp_initial_scale, amp_growth_interval=args.amp_growth_interval, amp_max_scale=args.amp_max_scale, amp_overflow_fatal_threshold=args.amp_overflow_fatal_threshold, float32_matmul_precision=args.float32_matmul_precision, compile_model=args.compile_model, output_root=Path(args.output_root), wandb_project=args.wandb_project, wandb_entity=args.wandb_entity, wandb_run_name=args.wandb_run_name),
    )


def make_loader(config: ExperimentConfig, split: str, seed: int) -> DataLoader:
    data = config.data
    start, end = (data.train_start_date, data.train_end_date) if split == "train" else (data.validation_start_date, data.validation_end_date)
    if data.data_source == "clickhouse_events":
        dataset = ClickHouseEventsChunkIterableDataset(clickhouse_events_data_config(config, split, seed))
        return DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=False)
    if data.data_source == "sample_cache":
        raise ValueError("sample_cache data is finite per sampled epoch; use train_sample_cache_epochs instead of make_loader")
    if data.data_source == "precomputed":
        dataset = PrecomputedV4ChunkIterableDataset(precomputed_data_config(config, split, seed))
        return DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=False)
    if data.data_source != "canonical":
        raise ValueError(f"Unsupported data_source={data.data_source!r}")
    dataset_config = CompactEventDataConfig(
        canonical_root=data.canonical_root,
        reference_dir=data.reference_dir,
        start_date=start,
        end_date=end,
        tickers=data.tickers,
        events_per_chunk=data.events_per_chunk,
        batch_size=config.train.batch_size,
        seed=seed,
        month_cache_size=data.month_cache_size,
        max_index_files=data.max_index_files,
        strict_lossless=data.strict_lossless,
    )
    workers = max(0, int(config.train.num_workers))
    if workers > 0 and platform.system().lower().startswith("win"):
        print("DataLoader workers disabled on Windows for prebatched compact event tensors.", flush=True)
        workers = 0
    kwargs: dict[str, Any] = {"batch_size": None, "num_workers": workers, "pin_memory": False}
    if workers > 0:
        kwargs.update({"prefetch_factor": max(1, int(config.train.prefetch_factor)), "persistent_workers": True})
    return DataLoader(CompactEventIterableDataset(dataset_config), **kwargs)


def build_validation_cache(config: ExperimentConfig, seed: int, reporter: TrainingReporter | None = None) -> list[dict[str, Any]]:
    # A validation frequency of 0 is meaningful for sample-cache full
    # pretraining: it means validation is triggered by the shard boundary
    # instead of by a fixed global-step interval. Validation batches must still
    # be built when pretrain_validation_steps is positive.
    if config.train.pretrain_validation_steps <= 0:
        return []
    if config.data.data_source == "precomputed":
        emit_progress_message(reporter, f"Building fixed validation cache batches={config.train.pretrain_validation_steps:,}")
        batches = build_fixed_precomputed_validation_batches(
            precomputed_data_config(config, "validation", seed),
            batch_count=config.train.pretrain_validation_steps,
            seed=seed,
        )
        for index, batch in enumerate(batches, start=1):
            emit_progress_message(
                reporter,
                f"validation cache batch {index}/{len(batches)} size={batch['header_uint8'].shape[0]} "
                f"shard={batch.get('shard_index')}/{batch.get('shard_count')}",
            )
        return batches
    if config.data.data_source == "sample_cache":
        batches: list[dict[str, Any]] = []
        data_config = sample_cache_data_config(config, "validation", seed)
        shards = discover_event_sample_shards(data_config)
        one_batch_config = replace(
            data_config,
            prefetch_shards=1,
            max_samples=config.train.batch_size,
            max_batches_per_shard=1,
            drop_last=True,
            interleave_shards=1,
        )
        emit_progress_message(
            reporter,
            f"Building sample-cache validation from {len(shards):,} shard(s), one shuffled batch per shard",
        )
        for shard in shards:
            iterator = iter_event_sample_cache_epoch_batches(one_batch_config, epoch=1, shards=[shard])
            try:
                batch = next(iterator)
            except StopIteration:
                emit_progress_message(
                    reporter,
                    f"validation cache skipped shard={shard.shard_index} because it has fewer than one full batch",
                )
                continue
            batches.append(batch)
            emit_progress_message(
                reporter,
                f"validation cache batch {len(batches)}/{len(shards)} "
                f"size={batch['header_uint8'].shape[0]} shard={batch.get('shard_index')}/{batch.get('shard_count')}",
            )
        if not batches:
            raise RuntimeError(f"No validation batches could be built from sample cache {data_config.cache_root}")
        return batches
    emit_progress_message(reporter, f"Building fixed validation cache batches={config.train.pretrain_validation_steps:,}")
    loader = make_loader(config, "validation", seed)
    batches: list[dict[str, Any]] = []
    iterator = iter(loader)
    for index in range(config.train.pretrain_validation_steps):
        batch = next(iterator)
        batches.append(batch)
        emit_progress_message(
            reporter,
            f"validation cache batch {index + 1}/{config.train.pretrain_validation_steps} size={batch['header_uint8'].shape[0]}",
        )
    return batches


def precomputed_data_config(config: ExperimentConfig, split: str, seed: int) -> PrecomputedChunkDataConfig:
    data = config.data
    start, end = (data.train_start_date, data.train_end_date) if split == "train" else (data.validation_start_date, data.validation_end_date)
    if data.precomputed_chunk_root is None:
        raise ValueError("precomputed_data_config requires data.precomputed_chunk_root")
    return PrecomputedChunkDataConfig(
        chunk_root=data.precomputed_chunk_root,
        start_date=start,
        end_date=end,
        tickers=data.tickers,
        batch_size=config.train.batch_size,
        events_per_chunk=data.events_per_chunk,
        seed=seed,
        shard_cache_size=data.month_cache_size,
        max_shards=data.max_index_files,
    )


def sample_cache_data_config(config: ExperimentConfig, split: str, seed: int) -> EventSampleCacheDataConfig:
    data = config.data
    if data.sample_cache_root is None:
        raise ValueError("sample_cache_data_config requires data.sample_cache_root")
    cache_root = data.sample_cache_root
    cache_split = split
    start_shard_index = 0
    max_shards = data.max_index_files
    max_samples = 0
    max_batches_per_shard = 0
    if split == "train":
        start_shard_index = data.sample_cache_train_start_shard
        max_shards = data.sample_cache_train_max_shards or data.max_index_files
    elif split == "validation":
        cache_root = data.sample_cache_validation_root or data.sample_cache_root
        cache_split = data.sample_cache_validation_split
        start_shard_index = data.sample_cache_validation_start_shard
        max_shards = data.sample_cache_validation_max_shards or data.max_index_files
        max_samples = data.sample_cache_validation_max_samples
        max_batches_per_shard = data.sample_cache_validation_batches_per_shard
    return EventSampleCacheDataConfig(
        cache_root=cache_root,
        split=cache_split,
        batch_size=config.train.batch_size,
        events_per_chunk=data.events_per_chunk,
        seed=seed,
        prefetch_shards=data.sample_cache_prefetch_shards,
        start_shard_index=start_shard_index,
        max_shards=max_shards,
        max_samples=max_samples,
        max_batches_per_shard=max_batches_per_shard,
        shuffle_records=data.sample_cache_shuffle_records,
        drop_last=data.sample_cache_drop_last,
        interleave_shards=data.sample_cache_interleave_shards if split == "train" else 1,
    )


def clickhouse_events_data_config(config: ExperimentConfig, split: str, seed: int) -> ClickHouseEventsDataConfig:
    data = config.data
    return ClickHouseEventsDataConfig(
        clickhouse_url=data.clickhouse_url,
        database=data.clickhouse_database,
        events_table=data.events_table,
        train_index_table=data.train_index_table,
        validation_index_table=data.validation_index_table,
        index_table=data.index_table,
        split=split,
        tickers=data.tickers,
        events_per_chunk=data.events_per_chunk,
        batch_size=config.train.batch_size,
        num_spans=data.num_spans,
        origins_per_span=data.origins_per_span,
        min_origin_stride=data.min_origin_stride,
        max_origin_stride=data.max_origin_stride,
        query_bundle_spans=data.query_bundle_spans,
        max_threads=data.clickhouse_max_threads,
        max_memory_usage=data.clickhouse_max_memory_usage,
        seed=seed,
        max_index_rows=data.max_index_files,
        strict_lossless=data.strict_lossless,
    )


def evaluate_validation(model: EventTokenMaskedAutoencoder, batches: list[dict[str, Any]], config: ExperimentConfig, device: torch.device, *, seed: int) -> dict[str, float]:
    was_training = model.training
    model.eval()
    totals: dict[str, float] = {}
    started = time.perf_counter()
    amp_dtype = resolve_amp_dtype(config.train, device)
    with torch.no_grad(), isolated_rng_state(device):
        for index, cpu_batch in enumerate(batches):
            if config.train.repeatable_randomness:
                set_seed(seed + index)
            batch = move_batch(cpu_batch, device)
            masks = build_event_masks(batch["events_uint8"], config.masks)
            with torch.amp.autocast("cuda", enabled=amp_dtype is not None, dtype=amp_dtype):
                output = model(batch["header_uint8"], batch["events_uint8"], masks, config.masks)
                result = masked_event_bce_loss(output, config.losses, header_uint8=batch["header_uint8"], include_diagnostics=False, metric_level="standard")
            if not torch.isfinite(result.loss).item():
                raise_nonfinite_training_error(output, result.metrics, global_step=-1)
            for key, value in result.metrics.items():
                validation_key = validation_metric_key(key)
                totals[validation_key] = totals.get(validation_key, 0.0) + float(value)
    count = max(1, len(batches))
    averaged = {key: value / count for key, value in totals.items()}
    averaged["validation/pretrain/batches"] = float(len(batches))
    averaged["validation/pretrain/seconds"] = time.perf_counter() - started
    if was_training:
        model.train()
    return averaged


def validation_metric_key(train_key: str) -> str:
    """Map train metric keys into validation keys without burying semantic metrics.

    Internal metrics keep the same shape expected by the Rich reporter and
    checkpoint monitors. W&B receives a second mapping in `v22_wandb_metric_key`
    so semantic metrics show in their own requested panel groups.
    """

    if train_key.startswith("semantic/train/"):
        return "semantic/validation/" + train_key[len("semantic/train/") :]
    return "validation/" + train_key


def v22_wandb_metric_key(metric_key: str) -> str:
    """Put v22 pretraining metrics into explicit W&B panel groups."""

    if metric_key.startswith("semantic/train/"):
        return "training_pretrain_semantic/" + metric_key[len("semantic/train/") :]
    if metric_key.startswith("semantic/validation/"):
        return "validation_pretrain_semantic/" + metric_key[len("semantic/validation/") :]
    if metric_key.startswith("validation/pretrain/"):
        return "validation_pretrain/" + metric_key[len("validation/pretrain/") :]
    if metric_key.startswith("pretrain/"):
        return "training_pretrain/" + metric_key[len("pretrain/") :]
    return metric_key


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved = dict(batch)
    moved["header_uint8"] = batch["header_uint8"].to(device, non_blocking=True)
    moved["events_uint8"] = batch["events_uint8"].to(device, non_blocking=True)
    moved["origin_timestamp_ns"] = batch["origin_timestamp_ns"].to(device, non_blocking=True)
    return moved


def profile_encode(model: EventTokenMaskedAutoencoder, batch: dict[str, Any], device: torch.device, *, amp_dtype: torch.dtype | None) -> dict[str, float]:
    was_training = model.training
    model.eval()
    sync_if_cuda(device)
    started = time.perf_counter()
    with torch.no_grad(), torch.amp.autocast("cuda", enabled=amp_dtype is not None, dtype=amp_dtype):
        embedding = model.encode(batch["header_uint8"], batch["events_uint8"])
    sync_if_cuda(device)
    elapsed = time.perf_counter() - started
    if was_training:
        model.train()
    batch_size = float(batch["header_uint8"].shape[0])
    return {"profile/inference_encode_seconds": elapsed, "profile/inference_encode_ms_per_sample": elapsed * 1000.0 / max(batch_size, 1.0), "profile/inference_encode_output_elements": float(embedding.numel())}


def build_scheduler(optimizer: torch.optim.Optimizer, train_config: TrainConfig) -> torch.optim.lr_scheduler.LRScheduler | None:
    if train_config.scheduler == "none":
        return None
    if train_config.scheduler == "shard_decay_cosine":
        return None
    return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=max(1, train_config.scheduler_t0_steps), T_mult=max(1, train_config.scheduler_t_mult), eta_min=max(0.0, train_config.scheduler_eta_min))


def apply_sample_cache_step_lr(
    *,
    optimizer: torch.optim.Optimizer,
    train_config: TrainConfig,
    epoch: int,
    shard_position: int,
    shard_count: int,
    shard_step: int,
    shard_steps: int,
) -> dict[str, float]:
    """Apply the full-pretraining shard-decay schedule before a sample-cache step.

    The epoch starts from `learning_rate * epoch_decay_ratio ** (epoch - 1)`.
    Across the epoch, the start LR of each shard walks down by a fixed
    `shard_decay`, computed from the greater 60% (configurable) of the current
    epoch range between the epoch peak and `eta_min`. Within a shard, LR follows
    a cosine from that shard's start LR toward `eta_min`.
    """

    if train_config.scheduler != "shard_decay_cosine":
        return {}
    base_lr = max(0.0, float(train_config.learning_rate))
    eta_min = max(0.0, float(train_config.scheduler_eta_min))
    epoch_decay_ratio = max(0.0, float(train_config.scheduler_epoch_decay_ratio))
    shard_decay_fraction = min(max(0.0, float(train_config.scheduler_shard_decay_fraction)), 1.0)
    epoch_index = max(0, int(epoch) - 1)
    epoch_peak_lr = max(eta_min, base_lr * (epoch_decay_ratio ** epoch_index))
    effective_shard_count = max(1, int(shard_count))
    shard_decay = max(0.0, (epoch_peak_lr - eta_min) * shard_decay_fraction / effective_shard_count)
    shard_index = max(0, min(effective_shard_count - 1, int(shard_position) - 1))
    shard_peak_lr = max(eta_min, epoch_peak_lr - shard_index * shard_decay)
    step_index = max(0, min(max(1, int(shard_steps)) - 1, int(shard_step) - 1))
    progress = step_index / max(1, int(shard_steps) - 1)
    cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
    lr = eta_min + (shard_peak_lr - eta_min) * cosine
    for group in optimizer.param_groups:
        group["lr"] = lr
    return {
        "train/lr_epoch_peak": float(epoch_peak_lr),
        "train/lr_shard_peak": float(shard_peak_lr),
        "train/lr_shard_decay": float(shard_decay),
        "train/lr_shard_cosine_progress": float(progress),
    }


def maybe_compile_model(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if enabled and hasattr(torch, "compile"):
        if importlib.util.find_spec("triton") is None:
            print("WARN --compile-model requested, but Triton is unavailable; continuing without torch.compile.", flush=True)
            return model
        print("Compiling model with torch.compile...", flush=True)
        return torch.compile(model)
    return model


def maybe_resume_or_warm_start(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    output_dir: Path,
    *,
    fresh_start: bool,
    warm_start_checkpoint: Path | None,
    warm_start_load_optimizer: bool,
) -> int:
    path = output_dir / "checkpoints" / "checkpoint_latest.pt"
    if not fresh_start and path.exists():
        checkpoint = load_trusted_training_checkpoint(path, map_location="cpu")
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        if scheduler is not None and checkpoint.get("scheduler") is not None:
            scheduler.load_state_dict(checkpoint["scheduler"])
        if checkpoint.get("scaler") is not None:
            scaler.load_state_dict(checkpoint["scaler"])
        else:
            print("WARN resumed checkpoint has no AMP scaler state; starting with a fresh GradScaler.", flush=True)
        if checkpoint.get("rng_state") is not None:
            restore_checkpoint_rng_state(checkpoint.get("rng_state"))
        else:
            print("WARN resumed checkpoint has no RNG state; continuing from the current run RNG stream.", flush=True)
        print(f"Resumed checkpoint: {path} step={checkpoint.get('step', 0)}", flush=True)
        return int(checkpoint.get("step", 0))
    if warm_start_checkpoint is not None and str(warm_start_checkpoint) and warm_start_checkpoint.exists():
        checkpoint = load_trusted_training_checkpoint(warm_start_checkpoint, map_location="cpu")
        model.load_state_dict(checkpoint["model"])
        if warm_start_load_optimizer and checkpoint.get("optimizer") is not None:
            optimizer.load_state_dict(checkpoint["optimizer"])
            for group in optimizer.param_groups:
                group["lr"] = group.get("initial_lr", group["lr"])
            if checkpoint.get("scaler") is not None:
                scaler.load_state_dict(checkpoint["scaler"])
            else:
                print("WARN warm-start checkpoint has no AMP scaler state; starting with a fresh GradScaler.", flush=True)
        print(
            f"Warm-started model from checkpoint: {warm_start_checkpoint} "
            f"source_step={checkpoint.get('step', 0)} load_optimizer={warm_start_load_optimizer}",
            flush=True,
        )
    elif warm_start_checkpoint is not None and str(warm_start_checkpoint):
        print(f"WARN warm-start checkpoint not found: {warm_start_checkpoint}", flush=True)
    return 0


def load_trusted_training_checkpoint(path: Path, *, map_location: str | torch.device) -> dict[str, Any]:
    """Load one of our full local training checkpoints under PyTorch 2.6+.

    PyTorch 2.6 defaults `torch.load` to `weights_only=True`, which rejects
    optimizer, scheduler, RNG, and NumPy-backed metadata stored in our own
    checkpoint payloads. These checkpoint paths are explicit local/workstation
    files from our training pipeline, so warm-start and resume need the full
    trusted payload.
    """

    try:
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
    except TypeError:
        checkpoint = torch.load(path, map_location=map_location)
    if not isinstance(checkpoint, dict):
        raise RuntimeError(f"Checkpoint payload is not a dict: {path}")
    return checkpoint


def clean_run_output_dir(output_dir: Path, *, keep_paths: list[Path] | None = None) -> None:
    keep_resolved = {path.resolve() for path in keep_paths or [] if str(path)}
    def should_keep(path: Path) -> bool:
        resolved = path.resolve()
        if resolved in keep_resolved:
            return True
        return any(keep == resolved or keep.is_relative_to(resolved) for keep in keep_resolved)

    if not output_dir.exists():
        return
    for child_name in ("metrics.jsonl", "config.json", "run_manifest.json"):
        child = output_dir / child_name
        if child.exists() and not should_keep(child):
            child.unlink()
    for child_name in ("checkpoints", "logs", "wandb", "artifacts"):
        child = output_dir / child_name
        if child.exists() and not should_keep(child):
            shutil.rmtree(child)


def checkpoint_payload(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    step: int,
    config: ExperimentConfig,
    args: argparse.Namespace,
    *,
    scaler: torch.amp.GradScaler | None = None,
) -> dict[str, Any]:
    return {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "scaler": scaler.state_dict() if scaler is not None else None,
        "step": step,
        "config": dataclass_tree(config),
        "args": vars(args),
        "rng_state": checkpoint_rng_state(),
    }


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace) -> Path:
    if args.run_root:
        return Path(args.run_root)
    if args.output_root:
        return Path(args.output_root) / config.train.wandb_run_name
    return default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, config.train.wandb_run_name)


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path) -> Any | None:
    return mlops_init_wandb(entity=args.wandb_entity, project=args.wandb_project, run_name=config.train.wandb_run_name, config=dataclass_tree(config), run_dir=output_dir / "wandb", mode=args.wandb_mode, timeout_seconds=args.wandb_init_timeout)


def save_model_artifacts(
    model: EventTokenMaskedAutoencoder,
    config: ExperimentConfig,
    run_paths: RunPaths,
    wandb_run: Any | None,
    device: torch.device,
) -> None:
    artifact_dir = run_paths.artifacts_dir / "model"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    details_path = artifact_dir / "model_details.json"
    params_path = artifact_dir / "model_parameters.jsonl"
    summary_path = artifact_dir / "model_summary.txt"
    mermaid_path = artifact_dir / "model_architecture.mmd"
    diagram_md_path = artifact_dir / "model_architecture.md"
    torchview_path = artifact_dir / "model_architecture_torchview.png"
    torchview_svg_path = artifact_dir / "model_architecture_torchview.svg"
    torchview_error_path = artifact_dir / "model_architecture_torchview_error.txt"

    parameters = []
    total_params = 0
    trainable_params = 0
    for name, param in model.named_parameters():
        count = int(param.numel())
        total_params += count
        if param.requires_grad:
            trainable_params += count
        parameters.append(
            {
                "name": name,
                "shape": list(param.shape),
                "num_params": count,
                "trainable": bool(param.requires_grad),
                "dtype": str(param.dtype),
            }
        )
    details = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "events_per_chunk": config.data.events_per_chunk,
        "header_shape": [config.train.batch_size, 14],
        "events_shape": [config.train.batch_size, config.data.events_per_chunk, 16],
        "embedding_dim": config.model.embedding_dim,
        "d_model": config.model.d_model,
        "d_byte": config.model.d_byte,
        "n_heads": config.model.n_heads,
        "encoder_layers": config.model.encoder_layers,
        "decoder": "per_masked_event_mlp",
        "decoder_layers_arg_ignored": config.model.decoder_layers,
        "ffn_mult": config.model.ffn_mult,
        "event_mask_ratio": config.masks.event_mask_ratio,
        "visible_events_train": config.data.events_per_chunk - max(config.masks.min_masked_events, int(round(config.data.events_per_chunk * config.masks.event_mask_ratio))),
        "total_params": total_params,
        "trainable_params": trainable_params,
        "non_trainable_params": total_params - trainable_params,
    }
    details_path.write_text(json.dumps(details, indent=2), encoding="utf-8")
    with params_path.open("w", encoding="utf-8") as handle:
        for row in parameters:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    summary_path.write_text(build_model_summary_text(model, details, parameters), encoding="utf-8")
    mermaid = build_model_mermaid(config)
    mermaid_path.write_text(mermaid, encoding="utf-8")
    diagram_md_path.write_text("```mermaid\n" + mermaid + "\n```\n", encoding="utf-8")
    try_optional_torchinfo_summary(model, config, device, artifact_dir)
    try_optional_torchview_diagram(model, config, device, torchview_path, torchview_svg_path, torchview_error_path)
    if wandb_run is not None:
        for path in artifact_dir.iterdir():
            if path.is_file():
                try:
                    wandb_run.save(str(path), base_path=str(artifact_dir))
                except Exception as exc:  # noqa: BLE001
                    print(f"WARN could not save W&B artifact {path}: {exc!r}", flush=True)
    print(f"Model artifacts saved: {artifact_dir}", flush=True)


def build_model_summary_text(model: torch.nn.Module, details: dict[str, Any], parameters: list[dict[str, Any]]) -> str:
    lines = [
        "EventTokenMaskedAutoencoder v22",
        "=" * 80,
        f"Input header_uint8: [B, 14]",
        f"Input events_uint8: [B, {details['events_per_chunk']}, 16]",
        f"Training encoder tokens: [B, 2 + visible_events, d_model]",
        f"Production encoder tokens: [B, 2 + {details['events_per_chunk']}, d_model]",
        f"Chunk bottleneck: zero-initialized fixed grid [B, 130, d_model] -> [B, {details['embedding_dim']}]",
        f"Decoder: projected chunk embedding + masked event position embedding -> per-masked-event logits",
        f"Output event bit logits: [B, masked_events, 16, 8]",
        f"Embedding: [B, {details['embedding_dim']}]",
        "",
        f"Total params: {details['total_params']:,}",
        f"Trainable params: {details['trainable_params']:,}",
        f"Non-trainable params: {details['non_trainable_params']:,}",
        "",
        f"{'Layer/Parameter':70} {'Shape':24} {'Params':>14} {'Trainable':>10}",
        "-" * 124,
    ]
    for row in parameters:
        lines.append(f"{row['name'][:70]:70} {str(row['shape'])[:24]:24} {row['num_params']:14,} {str(row['trainable']):>10}")
    lines.extend(["", "Module repr", "-" * 80, repr(model)])
    return "\n".join(lines) + "\n"


def build_model_mermaid(config: ExperimentConfig) -> str:
    events = config.data.events_per_chunk
    masked = max(config.masks.min_masked_events, int(round(events * config.masks.event_mask_ratio)))
    visible = events - masked
    return f"""flowchart TD
    H[\"header_uint8<br/>B x 14\"] --> HC[\"optional low-rate bit corruption\"]
    E[\"events_uint8<br/>B x {events} x 16\"] --> M[\"event token mask<br/>{visible} visible / {masked} masked\"]
    M --> VE[\"visible events<br/>B x {visible} x 16\"]
    VE --> EC[\"optional visible-event bit corruption\"]
    HC --> HP[\"header bits projection<br/>112 -> d_model\"]
    EC --> EP[\"event bits projection<br/>128 -> d_model\"]
    EP --> POS[\"event position + token type\"]
    HP --> TOK[\"encoder tokens<br/>CLS + header + visible events\"]
    POS --> TOK
    TOK --> ENC[\"Transformer encoder<br/>{config.model.encoder_layers} layers, d={config.model.d_model}, heads={config.model.n_heads}\"]
    ENC --> GRID[\"zero-initialized fixed grid bottleneck<br/>CLS + header + visible event slots\"]
    GRID --> EMB[\"chunk embedding<br/>B x {config.model.embedding_dim}\"]
    EMB --> MEM[\"project chunk embedding<br/>B x 1 x d_model\"]
    M --> MPOS[\"decoder-only masked event position embedding<br/>B x {masked} x d_model\"]
    MEM --> ADD[\"chunk memory + masked positions\"]
    MPOS --> ADD
    ADD --> HEAD[\"per-masked-event MLP<br/>B x {masked} x 16 x 8 logits\"]
    HEAD --> LOSS[\"BCE on masked event bits only\"]
"""


def try_optional_torchinfo_summary(model: torch.nn.Module, config: ExperimentConfig, device: torch.device, artifact_dir: Path) -> None:
    encoder_path = artifact_dir / "model_summary_torchinfo.txt"
    encoder_error_path = artifact_dir / "model_summary_torchinfo_error.txt"
    training_path = artifact_dir / "model_summary_training_torchinfo.txt"
    training_error_path = artifact_dir / "model_summary_training_torchinfo_error.txt"
    try:
        from torchinfo import summary

        wrapper = EncoderSummaryWrapper(model).to(device)
        header = torch.zeros((1, 14), dtype=torch.uint8, device=device)
        events = torch.zeros((1, config.data.events_per_chunk, 16), dtype=torch.uint8, device=device)
        text = str(summary(wrapper, input_data=(header, events), depth=8, col_names=("input_size", "output_size", "num_params", "trainable"), verbose=0))
        encoder_path.write_text(text + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        encoder_error_path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
    try:
        from torchinfo import summary

        wrapper = MaskedTrainingSummaryWrapper(model, config.data.events_per_chunk).to(device)
        header = torch.zeros((1, 14), dtype=torch.uint8, device=device)
        events = torch.zeros((1, config.data.events_per_chunk, 16), dtype=torch.uint8, device=device)
        text = str(summary(wrapper, input_data=(header, events), depth=8, col_names=("input_size", "output_size", "num_params", "trainable"), verbose=0))
        training_path.write_text(text + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        training_error_path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")


def try_optional_torchview_diagram(
    model: torch.nn.Module,
    config: ExperimentConfig,
    device: torch.device,
    png_path: Path,
    svg_path: Path,
    error_path: Path,
) -> None:
    try:
        from torchview import draw_graph

        wrapper = MaskedTrainingSummaryWrapper(model, config.data.events_per_chunk).to(device)
        header = torch.zeros((1, 14), dtype=torch.uint8, device=device)
        events = torch.zeros((1, config.data.events_per_chunk, 16), dtype=torch.uint8, device=device)
        graph = draw_graph(wrapper, input_data=(header, events), expand_nested=True, save_graph=False)
        if hasattr(graph, "visual_graph"):
            graph.visual_graph.attr(dpi="180")
            graph.visual_graph.render(filename=str(png_path.with_suffix("")), directory=str(png_path.parent), format="png", cleanup=True)
            graph.visual_graph.render(filename=str(svg_path.with_suffix("")), directory=str(svg_path.parent), format="svg", cleanup=True)
    except Exception as exc:  # noqa: BLE001
        error_path.write_text(repr(exc) + "\n", encoding="utf-8")


class EncoderSummaryWrapper(torch.nn.Module):
    """Expose the independently exportable production encoder as a single-output graph."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.reusable_event_chunk_encoder = model.build_encoder_model()

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        return self.reusable_event_chunk_encoder(header_uint8, events_uint8)


class MaskedTrainingSummaryWrapper(torch.nn.Module):
    """Expose the MAE graph with both exported embedding and reconstruction outputs."""

    def __init__(self, model: torch.nn.Module, events_per_chunk: int) -> None:
        super().__init__()
        self.events_per_chunk = int(events_per_chunk)
        self.visible_event_token_selector = model.visible_event_token_selector
        self.header_token_encoder = model.header_token_encoder
        self.global_event_position_embedding = model.global_event_position_embedding
        self.visible_event_token_encoder = model.visible_event_token_encoder
        self.encoder_sequence_builder = model.encoder_sequence_builder
        self.visible_context_transformer_encoder = model.visible_context_transformer_encoder
        self.encoded_token_output_layer_norm = model.encoded_token_output_layer_norm
        self.chunk_embedding_bottleneck = model.chunk_embedding_bottleneck
        self.per_masked_event_mlp_decoder = model.per_masked_event_mlp_decoder

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        masked_count = max(1, int(round(self.events_per_chunk * 0.70)))
        visible_count = self.events_per_chunk - masked_count
        visible = torch.arange(visible_count, device=events_uint8.device).view(1, -1).expand(events_uint8.shape[0], -1)
        masked = torch.arange(visible_count, self.events_per_chunk, device=events_uint8.device).view(1, -1).expand(events_uint8.shape[0], -1)
        masks = EventMaskBatch(
            visible_event_indices=visible,
            masked_event_indices=masked,
            visible_count=visible_count,
            masked_count=masked_count,
            event_count=self.events_per_chunk,
            requested_mask_ratio=float(masked_count / max(1, self.events_per_chunk)),
            actual_mask_ratio=float(masked_count / max(1, self.events_per_chunk)),
            mask_policy_id=-1,
            mask_policy_name="summary",
        )
        selected_events_uint8, selected_event_indices = self.visible_event_token_selector(events_uint8, masks.visible_event_indices)
        header_token = self.header_token_encoder(header_uint8)
        visible_event_tokens = self.visible_event_token_encoder(
            selected_events_uint8,
            selected_event_indices,
            self.global_event_position_embedding,
        )
        encoder_input_tokens = self.encoder_sequence_builder(header_token, visible_event_tokens)
        encoded_tokens = self.encoded_token_output_layer_norm(self.visible_context_transformer_encoder(encoder_input_tokens))
        chunk_embedding = self.chunk_embedding_bottleneck(
            encoded_tokens,
            selected_event_indices,
        )
        event_bit_logits = self.per_masked_event_mlp_decoder(chunk_embedding, masks.masked_event_indices)
        return chunk_embedding, event_bit_logits


def install_fatal_exception_logger(run_paths: RunPaths) -> None:
    previous_hook = sys.excepthook

    def log_exception(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        path = run_paths.logs_dir / "fatal_error.txt"
        try:
            path.write_text(text, encoding="utf-8")
            print(f"FATAL error log saved: {path}", flush=True)
        except Exception as log_exc:  # noqa: BLE001
            print(f"WARN could not save fatal error log: {log_exc!r}", flush=True)
        previous_hook(exc_type, exc, tb)

    sys.excepthook = log_exception


def default_run_name(args: argparse.Namespace) -> str:
    return f"mem-v22-eventmae-emb{args.embedding_dim}-d{args.d_model}-e{args.encoder_layers}-mask{int(args.event_mask_ratio * 100)}-events{args.events_per_chunk}"


def format_metrics(step: int, metrics: dict[str, float]) -> str:
    keys = [
        "pretrain/loss_total",
        "pretrain/event_bit_acc_pct",
        "pretrain/event_bit_acc_lift_pct",
        "pretrain/event_balanced_bit_acc_pct",
        "pretrain/event_byte_exact_acc_pct",
        "pretrain/event_byte_exact_lift_pct",
        "mask/event_mask_ratio_pct",
        "mask/event_visible_events",
        "mask/event_masked_events",
        "train/epoch",
        "train/epoch_progress_pct",
        "train/shard_index",
        "train/shard_step",
        "profile/inference_encode_ms_per_sample",
        "train/step_seconds",
        "train/epoch_loss_mean",
    ]
    parts = [f"step={step}"]
    for key in keys:
        if key in metrics:
            parts.append(f"{key.split('/')[-1]}={metrics[key]:.4f}")
    return " | ".join(parts)


def emit_progress_message(reporter: TrainingReporter | None, text: str) -> None:
    if reporter is not None:
        reporter.message(text)
    else:
        print(text, flush=True)


def dataclass_tree(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_tree(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: dataclass_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dataclass_tree(item) for item in value]
    return value


def sync_if_cuda(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


if __name__ == "__main__":
    main()
