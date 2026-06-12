from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import traceback
import importlib.util
from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from research.masked_event_model.v4.config import DataConfig, ExperimentConfig, LossConfig, MaskConfig, ModelConfig, TrainConfig
from research.masked_event_model.v4.losses import BIT_WEIGHTS, byte_psnr_db, masked_byte_bce_loss, pack_bits, unpack_bits
from research.masked_event_model.v4.masking import ByteMaskBatch, build_byte_masks
from research.masked_event_model.v4.model import CompactByteMaskedAutoencoder
from research.masked_event_model.v4.progress import TrainingProgressState, TrainingReporter
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
MODEL_VERSION = "v4"
JOB_TYPE = "pretrain"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    model_defaults = ModelConfig()
    mask_defaults = MaskConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Train compact byte masked event autoencoder v4.")
    parser.add_argument("--data-source", choices=("clickhouse_events", "sample_cache", "precomputed", "canonical"), default=data_defaults.data_source)
    parser.add_argument("--canonical-root", default=str(data_defaults.canonical_root))
    parser.add_argument("--precomputed-chunk-root", default=str(data_defaults.precomputed_chunk_root or ""))
    parser.add_argument("--sample-cache-root", default=str(data_defaults.sample_cache_root or ""))
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
    parser.add_argument("--sample-cache-shuffle-records", action=argparse.BooleanOptionalAction, default=data_defaults.sample_cache_shuffle_records)
    parser.add_argument("--sample-cache-drop-last", action=argparse.BooleanOptionalAction, default=data_defaults.sample_cache_drop_last)
    parser.add_argument("--max-index-files", type=int, default=data_defaults.max_index_files)
    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--max-steps", type=int, default=train_defaults.max_steps)
    parser.add_argument("--epochs", type=int, default=train_defaults.epochs)
    parser.add_argument("--num-workers", type=int, default=train_defaults.num_workers)
    parser.add_argument("--prefetch-factor", type=int, default=train_defaults.prefetch_factor)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train_defaults.weight_decay)
    parser.add_argument("--scheduler", choices=("none", "cosine_warm_restarts"), default=train_defaults.scheduler)
    parser.add_argument("--scheduler-t0-steps", type=int, default=train_defaults.scheduler_t0_steps)
    parser.add_argument("--scheduler-t-mult", type=int, default=train_defaults.scheduler_t_mult)
    parser.add_argument("--scheduler-eta-min", type=float, default=train_defaults.scheduler_eta_min)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=train_defaults.logging_steps)
    parser.add_argument("--detailed-metrics-steps", type=int, default=train_defaults.detailed_metrics_steps)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=train_defaults.progress_layout)
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
    parser.add_argument("--mask-ratio", type=float, default=mask_defaults.mask_ratio)
    parser.add_argument("--header-mask-ratio", type=float, default=mask_defaults.header_mask_ratio)
    parser.add_argument("--input-representation", choices=("byte", "bit"), default=model_defaults.input_representation)
    parser.add_argument("--d-byte", type=int, default=model_defaults.d_byte)
    parser.add_argument("--d-model", type=int, default=model_defaults.d_model)
    parser.add_argument("--embedding-dim", type=int, default=model_defaults.embedding_dim)
    parser.add_argument("--n-heads", type=int, default=model_defaults.n_heads)
    parser.add_argument("--encoder-layers", type=int, default=model_defaults.encoder_layers)
    parser.add_argument("--decoder-layers", type=int, default=model_defaults.decoder_layers)
    parser.add_argument("--ffn-mult", type=int, default=model_defaults.ffn_mult)
    parser.add_argument("--dropout", type=float, default=model_defaults.dropout)
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-init-timeout", type=int, default=60)
    parser.add_argument("--compile-model", action="store_true", default=train_defaults.compile_model)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT))
    set_seed(args.seed)
    config = build_config(args)
    run_name = config.train.wandb_run_name or default_run_name(args)
    config.train.wandb_run_name = run_name
    output_dir = resolve_output_dir(config, args)
    run_paths = RunPaths.create(output_dir)
    install_fatal_exception_logger(run_paths)
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    print(f"Output directory: {output_dir}", flush=True)
    print(f"Device: {device}", flush=True)
    print(f"Input shape: header=[B,14] events=[B,{config.data.events_per_chunk},16]", flush=True)
    print(f"Input representation: {config.model.input_representation}", flush=True)

    model = CompactByteMaskedAutoencoder(events_per_chunk=config.data.events_per_chunk, config=config.model).to(device)
    model_parameters = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {model_parameters:,}", flush=True)
    if config.train.decoder_chunk_size > 0 and config.train.compile_model:
        print("WARN --compile-model is ignored while --decoder-chunk-size is enabled; chunked decoder uses custom backward.", flush=True)
    train_model = maybe_compile_model(model, config.train.compile_model and config.train.decoder_chunk_size <= 0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
    global_step = maybe_resume(model, optimizer, scheduler, output_dir, fresh_start=args.fresh_start)

    wandb_run = init_wandb(args, config, output_dir)
    metric_logger = JsonlMetricLogger(run_paths.metrics_path, wandb_run)
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

    if args.dry_run:
        if config.data.data_source == "sample_cache":
            sample_config = sample_cache_data_config(config, "train", args.seed)
            batch = next(iter_event_sample_cache_epoch_batches(sample_config, epoch=1, shards=discover_event_sample_shards(sample_config)))
        else:
            batch = next(iter(make_loader(config, "train", args.seed)))
        batch = move_batch(batch, device)
        masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
        with torch.no_grad():
            output = model(batch["header_uint8"], batch["events_uint8"], masks)
            result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses, include_diagnostics=True)
            embedding = model.encode(batch["header_uint8"], batch["events_uint8"])
        print(f"Dry run loss={float(result.loss):.6f} embedding={tuple(embedding.shape)}", flush=True)
        print(json.dumps(result.metrics, indent=2), flush=True)
        return

    validation_batches = build_validation_cache(config, args.seed + 50_000)
    if validation_batches:
        val_metrics = evaluate_validation(model, validation_batches, config, device, seed=args.seed + 90_000)
        metric_logger.log(val_metrics, global_step)
        print("VALIDATION " + format_metrics(global_step, val_metrics), flush=True)

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
            )
        checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), force=True)
        checkpointer.close()
        if wandb_run is not None and reporter is not None:
            reporter.message(f"W&B run: {getattr(wandb_run, 'url', '<unknown>')}")


def train_precomputed_epochs(
    *,
    model: CompactByteMaskedAutoencoder,
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
            )
            epoch_loss_sum += float(metrics.get("pretrain/loss_total", 0.0))
            shard_index = int(batch.get("shard_index", 0) or 0)
            shard_step = int(batch.get("shard_step", 0) or 0)
            shard_steps = max(1, int(batch.get("shard_steps", 1) or 1))
            metrics.update(
                {
                    "train/epoch": float(epoch),
                    "train/epoch_step": float(epoch_steps),
                    "train/epoch_progress_pct": 100.0 * ((max(0, shard_index - 1) + shard_step / shard_steps) / max(1, shard_count)),
                    "train/shard_index": float(shard_index),
                    "train/shards_per_epoch": float(shard_count),
                    "train/shard_step": float(shard_step),
                    "train/shard_steps": float(shard_steps),
                    "train/samples_seen_epoch": float(epoch_samples),
                    "train/samples_seen_total": float(samples_seen_total),
                }
            )
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
            )
            checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), train_metrics=metrics, val_metrics=val_metrics)
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


def train_sample_cache_epochs(
    *,
    model: CompactByteMaskedAutoencoder,
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
) -> int:
    data_config = sample_cache_data_config(config, "train", args.seed)
    train_shards = discover_event_sample_shards(data_config)
    shard_count = len(train_shards)
    total_samples = sum(shard.num_samples for shard in train_shards)
    emit_progress_message(
        reporter,
        f"Sample-cache training shards={shard_count:,} samples={total_samples:,} "
        f"epochs={config.train.epochs:,} batch_size={config.train.batch_size:,} max_steps={config.train.max_steps:,}",
    )
    samples_seen_total = 0
    stop_training = False
    for epoch in range(1, max(1, int(config.train.epochs)) + 1):
        epoch_started = time.perf_counter()
        epoch_steps = 0
        epoch_samples = 0
        epoch_loss_sum = 0.0
        iterator = iter_event_sample_cache_epoch_batches(data_config, epoch=epoch, shards=train_shards)
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
            )
            epoch_loss_sum += float(metrics.get("pretrain/loss_total", 0.0))
            shard_index = int(batch.get("shard_index", 0) or 0)
            shard_step = int(batch.get("shard_step", 0) or 0)
            shard_steps = max(1, int(batch.get("shard_steps", 1) or 1))
            metrics.update(
                {
                    "train/epoch": float(epoch),
                    "train/epoch_step": float(epoch_steps),
                    "train/epoch_progress_pct": 100.0 * ((max(0, shard_index - 1) + shard_step / shard_steps) / max(1, shard_count)),
                    "train/shard_index": float(shard_index),
                    "train/shards_per_epoch": float(shard_count),
                    "train/shard_step": float(shard_step),
                    "train/shard_steps": float(shard_steps),
                    "train/samples_seen_epoch": float(epoch_samples),
                    "train/samples_seen_total": float(samples_seen_total),
                }
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
            checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), train_metrics=metrics, val_metrics=val_metrics)
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
    model: CompactByteMaskedAutoencoder,
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
        checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), train_metrics=metrics, val_metrics=val_metrics)
    return global_step


def run_training_step(
    *,
    model: CompactByteMaskedAutoencoder,
    train_model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    batch: dict[str, Any],
    config: ExperimentConfig,
    device: torch.device,
    global_step: int,
    data_wait_seconds: float,
) -> dict[str, float]:
    if config.train.decoder_chunk_size > 0:
        return run_training_step_chunked_decoder(
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            batch=batch,
            config=config,
            device=device,
            global_step=global_step,
            data_wait_seconds=data_wait_seconds,
        )
    step_started = time.perf_counter()
    profile_step = should_profile_step(config, global_step)
    if profile_step and device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    transfer_started = time.perf_counter()
    batch = move_batch(batch, device)
    sync_if_cuda(device)
    transfer_seconds = time.perf_counter() - transfer_started
    mask_started = time.perf_counter()
    masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
    sync_if_cuda(device)
    mask_seconds = time.perf_counter() - mask_started
    forward_started = time.perf_counter()
    include_diagnostics = config.train.detailed_metrics_steps > 0 and global_step % config.train.detailed_metrics_steps == 0
    with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
        output = train_model(batch["header_uint8"], batch["events_uint8"], masks)
        result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses, include_diagnostics=include_diagnostics, profile_metrics=profile_step)
    sync_if_cuda(device)
    forward_loss_seconds = time.perf_counter() - forward_started
    backward_started = time.perf_counter()
    optimizer.zero_grad(set_to_none=True)
    scaler.scale(result.loss).backward()
    sync_if_cuda(device)
    backward_seconds = time.perf_counter() - backward_started
    optimizer_started = time.perf_counter()
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
    scaler.step(optimizer)
    scaler.update()
    if scheduler is not None:
        scheduler.step(global_step)
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
                "profile/metrics_seconds": result.metrics.get("profile/header_metrics_seconds", 0.0) + result.metrics.get("profile/event_metrics_seconds", 0.0),
                "profile/backward_seconds": backward_seconds,
                "profile/optimizer_seconds": optimizer_seconds,
                "profile/profile_active": 1.0,
                **{f"profile/{key}": float(value) for key, value in data_profile.items()},
                **resource_profile(device),
            }
        )
    if config.train.profile_inference_every_steps > 0 and global_step % config.train.profile_inference_every_steps == 0:
        metrics.update(profile_encode(model, batch, device))
    return metrics


def run_training_step_chunked_decoder(
    *,
    model: CompactByteMaskedAutoencoder,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    batch: dict[str, Any],
    config: ExperimentConfig,
    device: torch.device,
    global_step: int,
    data_wait_seconds: float,
) -> dict[str, float]:
    step_started = time.perf_counter()
    profile_step = should_profile_step(config, global_step)
    if profile_step and device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    transfer_started = time.perf_counter()
    batch = move_batch(batch, device)
    sync_if_cuda(device)
    transfer_seconds = time.perf_counter() - transfer_started

    mask_started = time.perf_counter()
    masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
    sync_if_cuda(device)
    mask_seconds = time.perf_counter() - mask_started

    optimizer.zero_grad(set_to_none=True)
    include_diagnostics = config.train.detailed_metrics_steps > 0 and global_step % config.train.detailed_metrics_steps == 0
    forward_started = time.perf_counter()
    with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
        encoded_tokens, _, _ = model.encode_tokens_for_training(batch["header_uint8"], batch["events_uint8"], masks)
    encoded_leaf = encoded_tokens.detach().requires_grad_(True)
    header_indices = masks.header_mask.nonzero(as_tuple=False)
    event_indices = masks.event_mask.nonzero(as_tuple=False)
    total_weight = chunked_total_weight(header_indices, event_indices, config.losses)
    chunk_size = max(1, int(config.train.decoder_chunk_size))
    header_group_bits = max(1, int(header_indices.shape[0]) * 8)
    event_group_bits = max(1, int(event_indices.shape[0]) * 8)

    decoder_backward_started = time.perf_counter()
    header_loss_sum, header_metrics, header_chunks, header_metrics_seconds = backward_masked_group_chunks(
        model=model,
        encoded_tokens=encoded_leaf,
        indices=header_indices,
        target_uint8=batch["header_uint8"],
        chunk_size=chunk_size,
        prefix="header",
        group_scale=float(config.losses.header_weight) / total_weight / header_group_bits,
        scaler=scaler,
        include_diagnostics=include_diagnostics,
        profile_metrics=profile_step,
    )
    event_loss_sum, event_metrics, event_chunks, event_metrics_seconds = backward_masked_group_chunks(
        model=model,
        encoded_tokens=encoded_leaf,
        indices=event_indices,
        target_uint8=batch["events_uint8"],
        chunk_size=chunk_size,
        prefix="event",
        group_scale=float(config.losses.event_weight) / total_weight / event_group_bits,
        scaler=scaler,
        include_diagnostics=include_diagnostics,
        profile_metrics=profile_step,
    )
    sync_if_cuda(device)
    decoder_backward_seconds = time.perf_counter() - decoder_backward_started
    forward_loss_seconds = time.perf_counter() - forward_started

    encoder_backward_started = time.perf_counter()
    if encoded_leaf.grad is not None:
        encoded_tokens.backward(encoded_leaf.grad)
    sync_if_cuda(device)
    encoder_backward_seconds = time.perf_counter() - encoder_backward_started
    backward_seconds = decoder_backward_seconds + encoder_backward_seconds

    optimizer_started = time.perf_counter()
    scaler.unscale_(optimizer)
    torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
    scaler.step(optimizer)
    scaler.update()
    if scheduler is not None:
        scheduler.step(global_step)
    sync_if_cuda(device)
    optimizer_seconds = time.perf_counter() - optimizer_started

    total_loss = 0.0
    if header_indices.numel() > 0:
        total_loss += float(config.losses.header_weight) * header_loss_sum / header_group_bits
    if event_indices.numel() > 0:
        total_loss += float(config.losses.event_weight) * event_loss_sum / event_group_bits
    total_loss /= total_weight
    metrics = {
        "pretrain/loss_total": total_loss,
        "pretrain/loss_header": header_loss_sum / header_group_bits if header_indices.numel() > 0 else 0.0,
        "pretrain/loss_event": event_loss_sum / event_group_bits if event_indices.numel() > 0 else 0.0,
        "mask/header_masked_bytes": header_metrics["pretrain/header_masked_bytes"],
        "mask/event_masked_bytes": event_metrics["pretrain/event_masked_bytes"],
        "mask/total_masked_bytes": header_metrics["pretrain/header_masked_bytes"] + event_metrics["pretrain/event_masked_bytes"],
        **header_metrics,
        **event_metrics,
    }
    step_seconds = time.perf_counter() - step_started
    batch_size = float(batch["header_uint8"].shape[0])
    metrics.update(
        {
            "train/lr": float(optimizer.param_groups[0]["lr"]),
            "train/step_seconds": step_seconds,
            "train/samples_per_second": batch_size / max(step_seconds, 1e-9),
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
                "profile/header_metrics_seconds": header_metrics_seconds,
                "profile/event_metrics_seconds": event_metrics_seconds,
                "profile/metrics_seconds": header_metrics_seconds + event_metrics_seconds,
                "profile/backward_seconds": backward_seconds,
                "profile/decoder_backward_seconds": decoder_backward_seconds,
                "profile/encoder_backward_seconds": encoder_backward_seconds,
                "profile/optimizer_seconds": optimizer_seconds,
                "profile/decoder_chunk_size": float(chunk_size),
                "profile/header_decoder_chunks": float(header_chunks),
                "profile/event_decoder_chunks": float(event_chunks),
                "profile/chunked_decoder_active": 1.0,
                "profile/profile_active": 1.0,
                **{f"profile/{key}": float(value) for key, value in data_profile.items()},
                **resource_profile(device),
            }
        )
    if config.train.profile_inference_every_steps > 0 and global_step % config.train.profile_inference_every_steps == 0:
        metrics.update(profile_encode(model, batch, device))
    return metrics


def should_profile_step(config: ExperimentConfig, global_step: int) -> bool:
    if config.train.profile_first_steps > 0 and global_step <= config.train.profile_first_steps:
        return True
    return config.train.profile_training_every_steps > 0 and global_step % config.train.profile_training_every_steps == 0


def chunked_total_weight(header_indices: torch.Tensor, event_indices: torch.Tensor, config: LossConfig) -> float:
    total = 0.0
    if header_indices.numel() > 0:
        total += float(config.header_weight)
    if event_indices.numel() > 0:
        total += float(config.event_weight)
    return max(total, 1.0)


def backward_masked_group_chunks(
    *,
    model: CompactByteMaskedAutoencoder,
    encoded_tokens: torch.Tensor,
    indices: torch.Tensor,
    target_uint8: torch.Tensor,
    chunk_size: int,
    prefix: str,
    group_scale: float,
    scaler: torch.amp.GradScaler,
    include_diagnostics: bool,
    profile_metrics: bool,
) -> tuple[float, dict[str, float], int, float]:
    if indices.numel() == 0:
        return 0.0, empty_chunk_metrics(prefix), 0, 0.0
    stats = ChunkMetricAccumulator(prefix)
    chunks = 0
    metrics_seconds = 0.0
    for start in range(0, int(indices.shape[0]), chunk_size):
        chunks += 1
        chunk_indices = indices[start : start + chunk_size]
        with torch.amp.autocast("cuda", enabled=encoded_tokens.is_cuda):
            probabilities = model.decode_header_indices(encoded_tokens, chunk_indices) if prefix == "header" else model.decode_event_indices(encoded_tokens, chunk_indices)
            target_bytes = target_uint8[tuple(chunk_indices.T)].long()
            target_bits = unpack_bits(target_bytes).to(dtype=probabilities.dtype, device=probabilities.device)
        if probabilities.is_cuda:
            with torch.amp.autocast("cuda", enabled=False):
                loss_sum = F.binary_cross_entropy(probabilities.float(), target_bits.float(), reduction="sum")
        else:
            loss_sum = F.binary_cross_entropy(probabilities, target_bits, reduction="sum")
        scaler.scale(loss_sum * group_scale).backward()
        metrics_started = time.perf_counter()
        stats.update(probabilities.detach(), target_bits.detach(), target_bytes.detach(), loss_sum.detach(), include_diagnostics=include_diagnostics)
        if profile_metrics:
            if probabilities.is_cuda:
                torch.cuda.synchronize(probabilities.device)
            metrics_seconds += time.perf_counter() - metrics_started
    return stats.loss_sum, stats.to_metrics(), chunks, metrics_seconds


class ChunkMetricAccumulator:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.loss_sum = 0.0
        self.byte_count = 0
        self.bit_count = 0
        self.bit_correct = 0.0
        self.zero_bit_correct = 0.0
        self.zero_bit_count = 0.0
        self.one_bit_correct = 0.0
        self.one_bit_count = 0.0
        self.pred_one_count = 0.0
        self.per_bit_correct = torch.zeros(8, dtype=torch.float64)
        self.per_bit_count = torch.zeros(8, dtype=torch.float64)
        self.per_bit_target_one_count = torch.zeros(8, dtype=torch.float64)
        self.per_bit_pred_one_count = torch.zeros(8, dtype=torch.float64)
        self.exact_correct = 0.0
        self.byte_mode_count = 0.0
        self.hard_abs_sum = 0.0
        self.soft_abs_sum = 0.0
        self.hard_sq_sum = 0.0
        self.soft_sq_sum = 0.0
        self.psnr_byte_count = 0.0
        self.conf_sum = 0.0
        self.conf_min = 1.0
        self.high_conf_correct = 0.0
        self.high_conf_count = 0.0
        self.low_conf_correct = 0.0
        self.low_conf_count = 0.0

    def update(
        self,
        probabilities: torch.Tensor,
        target_bits: torch.Tensor,
        target_bytes: torch.Tensor,
        loss_sum: torch.Tensor,
        *,
        include_diagnostics: bool,
    ) -> None:
        with torch.no_grad():
            hard_bits = probabilities >= 0.5
            target_bool = target_bits.bool()
            one_mask = target_bool
            zero_mask = ~target_bool
            hard_bytes = pack_bits(hard_bits)
            target_float = target_bytes.float()
            soft_bytes = (probabilities.float() * BIT_WEIGHTS.to(probabilities.device)).sum(dim=-1)
            confidence = (probabilities - 0.5).abs() * 2.0
            self.loss_sum += float(loss_sum.float().cpu())
            self.byte_count += int(target_bytes.numel())
            self.bit_count += int(target_bits.numel())
            self.bit_correct += float((hard_bits == target_bool).float().sum().cpu())
            self.zero_bit_correct += float((hard_bits[zero_mask] == target_bool[zero_mask]).float().sum().cpu()) if zero_mask.any() else 0.0
            self.zero_bit_count += float(zero_mask.sum().cpu())
            self.one_bit_correct += float((hard_bits[one_mask] == target_bool[one_mask]).float().sum().cpu()) if one_mask.any() else 0.0
            self.one_bit_count += float(one_mask.sum().cpu())
            self.pred_one_count += float(hard_bits.float().sum().cpu())
            self.per_bit_correct += (hard_bits == target_bool).float().sum(dim=0).double().cpu()
            self.per_bit_count += torch.full((8,), float(target_bits.shape[0]), dtype=torch.float64)
            self.per_bit_target_one_count += target_bits.float().sum(dim=0).double().cpu()
            self.per_bit_pred_one_count += hard_bits.float().sum(dim=0).double().cpu()
            self.exact_correct += float((hard_bytes == target_bytes).float().sum().cpu())
            self.byte_mode_count += float(torch.bincount(target_bytes, minlength=256).max().cpu())
            self.hard_abs_sum += float((hard_bytes.float() - target_float).abs().sum().cpu())
            self.soft_abs_sum += float((soft_bytes - target_float).abs().sum().cpu())
            self.conf_sum += float(confidence.float().sum().cpu())
            self.conf_min = min(self.conf_min, float(confidence.min().float().cpu()))
            if include_diagnostics:
                if self.prefix == "event":
                    self.hard_sq_sum += float((hard_bytes.float() - target_float).pow(2).sum().cpu())
                    self.soft_sq_sum += float((soft_bytes - target_float).pow(2).sum().cpu())
                    self.psnr_byte_count += float(target_bytes.numel())
                high_conf = confidence >= 0.8
                if high_conf.any():
                    self.high_conf_correct += float((hard_bits[high_conf] == target_bool[high_conf]).float().sum().cpu())
                    self.high_conf_count += float(high_conf.sum().cpu())
                low_conf = confidence <= 0.2
                if low_conf.any():
                    self.low_conf_correct += float((hard_bits[low_conf] == target_bool[low_conf]).float().sum().cpu())
                    self.low_conf_count += float(low_conf.sum().cpu())

    def to_metrics(self) -> dict[str, float]:
        bit_acc = 100.0 * self.bit_correct / max(1.0, float(self.bit_count))
        zero_acc = 100.0 * self.zero_bit_correct / max(1.0, float(self.zero_bit_count))
        one_acc = 100.0 * self.one_bit_correct / max(1.0, float(self.one_bit_count))
        balanced_bit_acc = 0.5 * (zero_acc + one_acc) if self.zero_bit_count > 0 and self.one_bit_count > 0 else bit_acc
        target_one_rate = 100.0 * self.one_bit_count / max(1.0, float(self.bit_count))
        pred_one_rate = 100.0 * self.pred_one_count / max(1.0, float(self.bit_count))
        majority_baseline = max(target_one_rate, 100.0 - target_one_rate)
        byte_exact = 100.0 * self.exact_correct / max(1.0, float(self.byte_count))
        byte_mode_baseline = 100.0 * self.byte_mode_count / max(1.0, float(self.byte_count))
        metrics = {
            f"pretrain/{self.prefix}_masked_bytes": float(self.byte_count),
            f"pretrain/{self.prefix}_bit_acc_pct": bit_acc,
            f"pretrain/{self.prefix}_bit_majority_baseline_pct": majority_baseline,
            f"pretrain/{self.prefix}_bit_acc_lift_pct": bit_acc - majority_baseline,
            f"pretrain/{self.prefix}_balanced_bit_acc_pct": balanced_bit_acc,
            f"pretrain/{self.prefix}_zero_bit_acc_pct": zero_acc,
            f"pretrain/{self.prefix}_one_bit_acc_pct": one_acc,
            f"pretrain/{self.prefix}_target_one_rate_pct": target_one_rate,
            f"pretrain/{self.prefix}_pred_one_rate_pct": pred_one_rate,
            f"pretrain/{self.prefix}_byte_exact_acc_pct": byte_exact,
            f"pretrain/{self.prefix}_byte_mode_baseline_pct": byte_mode_baseline,
            f"pretrain/{self.prefix}_byte_exact_lift_pct": byte_exact - byte_mode_baseline,
            f"pretrain/{self.prefix}_hard_byte_mae": self.hard_abs_sum / max(1.0, float(self.byte_count)),
            f"pretrain/{self.prefix}_soft_byte_mae": self.soft_abs_sum / max(1.0, float(self.byte_count)),
            f"pretrain/{self.prefix}_bit_conf_mean": self.conf_sum / max(1.0, float(self.bit_count)),
            f"pretrain/{self.prefix}_bit_conf_min": self.conf_min if self.byte_count else 0.0,
        }
        if self.high_conf_count > 0:
            metrics[f"pretrain/{self.prefix}_high_conf_bit_acc_pct"] = 100.0 * self.high_conf_correct / self.high_conf_count
        if self.low_conf_count > 0:
            metrics[f"pretrain/{self.prefix}_low_conf_bit_acc_pct"] = 100.0 * self.low_conf_correct / self.low_conf_count
        if self.prefix == "event" and self.psnr_byte_count > 0:
            hard_mse = torch.tensor(self.hard_sq_sum / self.psnr_byte_count, dtype=torch.float32)
            soft_mse = torch.tensor(self.soft_sq_sum / self.psnr_byte_count, dtype=torch.float32)
            metrics[f"pretrain/{self.prefix}_hard_byte_psnr_db"] = float(byte_psnr_db(hard_mse))
            metrics[f"pretrain/{self.prefix}_soft_byte_psnr_db"] = float(byte_psnr_db(soft_mse))
        for bit_index in range(8):
            count = max(1.0, float(self.per_bit_count[bit_index]))
            metrics[f"pretrain/{self.prefix}_bit{bit_index}_acc_pct"] = 100.0 * float(self.per_bit_correct[bit_index]) / count
            metrics[f"pretrain/{self.prefix}_bit{bit_index}_target_one_rate_pct"] = 100.0 * float(self.per_bit_target_one_count[bit_index]) / count
            metrics[f"pretrain/{self.prefix}_bit{bit_index}_pred_one_rate_pct"] = 100.0 * float(self.per_bit_pred_one_count[bit_index]) / count
        return metrics


def empty_chunk_metrics(prefix: str) -> dict[str, float]:
    return {
        f"pretrain/{prefix}_masked_bytes": 0.0,
        f"pretrain/{prefix}_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_bit_majority_baseline_pct": 0.0,
        f"pretrain/{prefix}_bit_acc_lift_pct": 0.0,
        f"pretrain/{prefix}_balanced_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_zero_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_one_bit_acc_pct": 0.0,
        f"pretrain/{prefix}_target_one_rate_pct": 0.0,
        f"pretrain/{prefix}_pred_one_rate_pct": 0.0,
        f"pretrain/{prefix}_byte_exact_acc_pct": 0.0,
        f"pretrain/{prefix}_byte_mode_baseline_pct": 0.0,
        f"pretrain/{prefix}_byte_exact_lift_pct": 0.0,
        f"pretrain/{prefix}_hard_byte_mae": 0.0,
        f"pretrain/{prefix}_soft_byte_mae": 0.0,
        f"pretrain/{prefix}_bit_conf_mean": 0.0,
        f"pretrain/{prefix}_bit_conf_min": 0.0,
        **{f"pretrain/{prefix}_bit{bit_index}_acc_pct": 0.0 for bit_index in range(8)},
        **{f"pretrain/{prefix}_bit{bit_index}_target_one_rate_pct": 0.0 for bit_index in range(8)},
        **{f"pretrain/{prefix}_bit{bit_index}_pred_one_rate_pct": 0.0 for bit_index in range(8)},
    }


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


def maybe_log_train_and_validation(
    *,
    model: CompactByteMaskedAutoencoder,
    config: ExperimentConfig,
    device: torch.device,
    args: argparse.Namespace,
    global_step: int,
    metrics: dict[str, float],
    validation_batches: list[dict[str, Any]],
    metric_logger: JsonlMetricLogger,
    reporter: TrainingReporter | None = None,
) -> dict[str, float] | None:
    if global_step % config.train.logging_steps == 0:
        metric_logger.log(metrics, global_step)
        if reporter is None:
            print(format_metrics(global_step, metrics), flush=True)
    val_metrics = None
    if validation_batches and config.train.pretrain_validation_frequency > 0 and global_step % config.train.pretrain_validation_frequency == 0:
        val_metrics = evaluate_validation(model, validation_batches, config, device, seed=args.seed + 90_000)
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
            sample_cache_shuffle_records=args.sample_cache_shuffle_records,
            sample_cache_drop_last=args.sample_cache_drop_last,
            max_index_files=args.max_index_files,
        ),
        masks=MaskConfig(mask_ratio=args.mask_ratio, header_mask_ratio=args.header_mask_ratio),
        model=ModelConfig(input_representation=args.input_representation, d_byte=args.d_byte, d_model=args.d_model, embedding_dim=args.embedding_dim, n_heads=args.n_heads, encoder_layers=args.encoder_layers, decoder_layers=args.decoder_layers, ffn_mult=args.ffn_mult, dropout=args.dropout),
        losses=LossConfig(),
        train=TrainConfig(batch_size=args.batch_size, max_steps=args.max_steps, epochs=args.epochs, learning_rate=args.learning_rate, weight_decay=args.weight_decay, scheduler=args.scheduler, scheduler_t0_steps=args.scheduler_t0_steps, scheduler_t_mult=args.scheduler_t_mult, scheduler_eta_min=args.scheduler_eta_min, grad_clip_norm=args.grad_clip_norm, logging_steps=args.logging_steps, detailed_metrics_steps=args.detailed_metrics_steps, progress_layout=args.progress_layout, profile_first_steps=args.profile_first_steps, profile_training_every_steps=args.profile_training_every_steps, profile_inference_every_steps=args.profile_inference_every_steps, decoder_chunk_size=args.decoder_chunk_size, pretrain_validation_frequency=args.pretrain_validation_frequency, pretrain_validation_steps=args.pretrain_validation_steps, checkpoint_latest_steps=args.checkpoint_latest_steps, checkpoint_archive_steps=args.checkpoint_archive_steps, checkpoint_best_train=args.checkpoint_best_train, checkpoint_best_val=args.checkpoint_best_val, num_workers=args.num_workers, prefetch_factor=args.prefetch_factor, seed=args.seed, compile_model=args.compile_model, output_root=Path(args.output_root), wandb_project=args.wandb_project, wandb_entity=args.wandb_entity, wandb_run_name=args.wandb_run_name),
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
        print("DataLoader workers disabled on Windows for prebatched compact byte tensors.", flush=True)
        workers = 0
    kwargs: dict[str, Any] = {"batch_size": None, "num_workers": workers, "pin_memory": False}
    if workers > 0:
        kwargs.update({"prefetch_factor": max(1, int(config.train.prefetch_factor)), "persistent_workers": True})
    return DataLoader(CompactEventIterableDataset(dataset_config), **kwargs)


def build_validation_cache(config: ExperimentConfig, seed: int) -> list[dict[str, Any]]:
    if config.train.pretrain_validation_frequency <= 0 or config.train.pretrain_validation_steps <= 0:
        return []
    print(f"Building fixed validation cache batches={config.train.pretrain_validation_steps}", flush=True)
    if config.data.data_source == "precomputed":
        batches = build_fixed_precomputed_validation_batches(
            precomputed_data_config(config, "validation", seed),
            batch_count=config.train.pretrain_validation_steps,
            seed=seed,
        )
        for index, batch in enumerate(batches, start=1):
            print(
                f"validation cache batch {index}/{len(batches)} size={batch['header_uint8'].shape[0]} "
                f"shard={batch.get('shard_index')}/{batch.get('shard_count')}",
                flush=True,
            )
        return batches
    if config.data.data_source == "sample_cache":
        batches: list[dict[str, Any]] = []
        data_config = sample_cache_data_config(config, "validation", seed)
        shards = discover_event_sample_shards(data_config)
        iterator = iter_event_sample_cache_epoch_batches(data_config, epoch=1, shards=shards)
        for index in range(config.train.pretrain_validation_steps):
            batch = next(iterator)
            batches.append(batch)
            print(
                f"validation cache batch {index + 1}/{config.train.pretrain_validation_steps} "
                f"size={batch['header_uint8'].shape[0]} shard={batch.get('shard_index')}/{batch.get('shard_count')}",
                flush=True,
            )
        return batches
    loader = make_loader(config, "validation", seed)
    batches: list[dict[str, Any]] = []
    iterator = iter(loader)
    for index in range(config.train.pretrain_validation_steps):
        batch = next(iterator)
        batches.append(batch)
        print(f"validation cache batch {index + 1}/{config.train.pretrain_validation_steps} size={batch['header_uint8'].shape[0]}", flush=True)
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
    cache_split = split
    start_shard_index = 0
    max_shards = data.max_index_files
    max_samples = 0
    if split == "train":
        start_shard_index = data.sample_cache_train_start_shard
        max_shards = data.sample_cache_train_max_shards or data.max_index_files
    elif split == "validation":
        cache_split = data.sample_cache_validation_split
        start_shard_index = data.sample_cache_validation_start_shard
        max_shards = data.sample_cache_validation_max_shards or data.max_index_files
        max_samples = data.sample_cache_validation_max_samples
    return EventSampleCacheDataConfig(
        cache_root=data.sample_cache_root,
        split=cache_split,
        batch_size=config.train.batch_size,
        events_per_chunk=data.events_per_chunk,
        seed=seed,
        prefetch_shards=data.sample_cache_prefetch_shards,
        start_shard_index=start_shard_index,
        max_shards=max_shards,
        max_samples=max_samples,
        shuffle_records=data.sample_cache_shuffle_records,
        drop_last=data.sample_cache_drop_last,
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


def evaluate_validation(model: CompactByteMaskedAutoencoder, batches: list[dict[str, Any]], config: ExperimentConfig, device: torch.device, *, seed: int) -> dict[str, float]:
    was_training = model.training
    model.eval()
    totals: dict[str, float] = {}
    started = time.perf_counter()
    with torch.no_grad():
        for index, cpu_batch in enumerate(batches):
            torch.manual_seed(seed + index)
            batch = move_batch(cpu_batch, device)
            masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
            with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
                output = model(batch["header_uint8"], batch["events_uint8"], masks)
                result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses, include_diagnostics=True)
            for key, value in result.metrics.items():
                totals["validation/" + key] = totals.get("validation/" + key, 0.0) + float(value)
    count = max(1, len(batches))
    averaged = {key: value / count for key, value in totals.items()}
    averaged["validation/pretrain/batches"] = float(len(batches))
    averaged["validation/pretrain/seconds"] = time.perf_counter() - started
    if was_training:
        model.train()
    return averaged


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved = dict(batch)
    moved["header_uint8"] = batch["header_uint8"].to(device, non_blocking=True)
    moved["events_uint8"] = batch["events_uint8"].to(device, non_blocking=True)
    moved["origin_timestamp_ns"] = batch["origin_timestamp_ns"].to(device, non_blocking=True)
    return moved


def profile_encode(model: CompactByteMaskedAutoencoder, batch: dict[str, Any], device: torch.device) -> dict[str, float]:
    was_training = model.training
    model.eval()
    sync_if_cuda(device)
    started = time.perf_counter()
    with torch.no_grad(), torch.amp.autocast("cuda", enabled=device.type == "cuda"):
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
    return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=max(1, train_config.scheduler_t0_steps), T_mult=max(1, train_config.scheduler_t_mult), eta_min=max(0.0, train_config.scheduler_eta_min))


def maybe_compile_model(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if enabled and hasattr(torch, "compile"):
        if importlib.util.find_spec("triton") is None:
            print("WARN --compile-model requested, but Triton is unavailable; continuing without torch.compile.", flush=True)
            return model
        print("Compiling model with torch.compile...", flush=True)
        return torch.compile(model)
    return model


def maybe_resume(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: torch.optim.lr_scheduler.LRScheduler | None, output_dir: Path, *, fresh_start: bool) -> int:
    path = output_dir / "checkpoints" / "checkpoint_latest.pt"
    if fresh_start or not path.exists():
        return 0
    checkpoint = torch.load(path, map_location="cpu")
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    if scheduler is not None and checkpoint.get("scheduler") is not None:
        scheduler.load_state_dict(checkpoint["scheduler"])
    print(f"Resumed checkpoint: {path} step={checkpoint.get('step', 0)}", flush=True)
    return int(checkpoint.get("step", 0))


def checkpoint_payload(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: torch.optim.lr_scheduler.LRScheduler | None, step: int, config: ExperimentConfig, args: argparse.Namespace) -> dict[str, Any]:
    return {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict() if scheduler is not None else None, "step": step, "config": dataclass_tree(config), "args": vars(args)}


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace) -> Path:
    if args.run_root:
        return Path(args.run_root)
    if args.output_root:
        return Path(args.output_root) / config.train.wandb_run_name
    return default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, config.train.wandb_run_name)


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path) -> Any | None:
    return mlops_init_wandb(entity=args.wandb_entity, project=args.wandb_project, run_name=config.train.wandb_run_name, config=dataclass_tree(config), run_dir=output_dir / "wandb", mode=args.wandb_mode, timeout_seconds=args.wandb_init_timeout)


def save_model_artifacts(
    model: CompactByteMaskedAutoencoder,
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
        "decoder_layers": config.model.decoder_layers,
        "ffn_mult": config.model.ffn_mult,
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
        "CompactByteMaskedAutoencoder v4",
        "=" * 80,
        f"Input header_uint8: [B, 14]",
        f"Input events_uint8: [B, {details['events_per_chunk']}, 16]",
        f"Output header bit probabilities: [masked_header_bytes, 8]",
        f"Output event bit probabilities: [masked_event_bytes, 8]",
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
    return f"""flowchart TD
    H[\"header_uint8<br/>B x 14\"] --> HB[\"byte embedding + header byte position\"]
    E[\"events_uint8<br/>B x {events} x 16\"] --> EB[\"byte embedding + event byte position\"]
    HB --> HP[\"header projection<br/>14*d_byte -> d_model\"]
    EB --> EP[\"event projection<br/>16*d_byte -> d_model\"]
    EP --> POS[\"event position + token type\"]
    HP --> TOK[\"token sequence<br/>CLS + header + {events} events\"]
    POS --> TOK
    TOK --> ENC[\"Transformer encoder<br/>{config.model.encoder_layers} layers, d={config.model.d_model}, heads={config.model.n_heads}\"]
    ENC --> NORM[\"LayerNorm\"]
    NORM --> EMB[\"chunk embedding<br/>B x {config.model.embedding_dim}\"]
    NORM --> DECUP[\"decoder up projection\"]
    DECUP --> DEC[\"masked-byte decoder<br/>{config.model.decoder_layers} MLP blocks\"]
    DEC --> BIT[\"bit head + sigmoid<br/>8 probabilities per masked byte\"]
    BIT --> OH[\"header_bit_probs\"]
    BIT --> OE[\"event_bit_probs\"]
"""


def try_optional_torchinfo_summary(model: torch.nn.Module, config: ExperimentConfig, device: torch.device, artifact_dir: Path) -> None:
    path = artifact_dir / "model_summary_torchinfo.txt"
    error_path = artifact_dir / "model_summary_torchinfo_error.txt"
    try:
        from torchinfo import summary

        wrapper = TorchInfoSummaryWrapper(model, config.data.events_per_chunk).to(device)
        header = torch.zeros((1, 14), dtype=torch.uint8, device=device)
        events = torch.zeros((1, config.data.events_per_chunk, 16), dtype=torch.uint8, device=device)
        text = str(summary(wrapper, input_data=(header, events), depth=8, col_names=("input_size", "output_size", "num_params", "trainable"), verbose=0))
        path.write_text(text + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        error_path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")


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

        wrapper = MaskedSummaryWrapper(model, config.data.events_per_chunk).to(device)
        header = torch.zeros((1, 14), dtype=torch.uint8, device=device)
        events = torch.zeros((1, config.data.events_per_chunk, 16), dtype=torch.uint8, device=device)
        graph = draw_graph(wrapper, input_data=(header, events), expand_nested=True, save_graph=False)
        if hasattr(graph, "visual_graph"):
            graph.visual_graph.attr(dpi="180")
            graph.visual_graph.render(filename=str(png_path.with_suffix("")), directory=str(png_path.parent), format="png", cleanup=True)
            graph.visual_graph.render(filename=str(svg_path.with_suffix("")), directory=str(svg_path.parent), format="svg", cleanup=True)
    except Exception as exc:  # noqa: BLE001
        error_path.write_text(repr(exc) + "\n", encoding="utf-8")


class MaskedSummaryWrapper(torch.nn.Module):
    def __init__(self, model: torch.nn.Module, events_per_chunk: int) -> None:
        super().__init__()
        self.model = model
        self.events_per_chunk = int(events_per_chunk)

    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        header_mask = torch.zeros_like(header_uint8, dtype=torch.bool)
        event_mask = torch.zeros_like(events_uint8, dtype=torch.bool)
        header_mask[:, 0] = True
        event_mask[:, 0, 0] = True
        output = self.model(header_uint8, events_uint8, ByteMaskBatch(header_mask=header_mask, event_mask=event_mask))
        return output.header_bit_probs, output.event_bit_probs, output.chunk_embedding


class TorchInfoSummaryWrapper(MaskedSummaryWrapper):
    def forward(self, header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> torch.Tensor:
        header_probs, event_probs, chunk_embedding = super().forward(header_uint8, events_uint8)
        return torch.cat([header_probs.flatten(), event_probs.flatten(), chunk_embedding.flatten()]).unsqueeze(0)


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
    return f"mem-v4-{args.input_representation}-bce-emb{args.embedding_dim}-d{args.d_model}-e{args.encoder_layers}-mask{int(args.mask_ratio * 100)}-events{args.events_per_chunk}"


def format_metrics(step: int, metrics: dict[str, float]) -> str:
    keys = [
        "pretrain/loss_total",
        "pretrain/event_bit_acc_pct",
        "pretrain/event_bit_acc_lift_pct",
        "pretrain/event_balanced_bit_acc_pct",
        "pretrain/event_byte_exact_acc_pct",
        "pretrain/event_byte_exact_lift_pct",
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
