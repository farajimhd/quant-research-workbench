from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import queue
import random
import signal
import sys
import threading
import time
import csv
import traceback
import _thread
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy, to_cpu_payload
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.model_artifacts import append_checkpoint_model_card, parameter_summary, write_model_artifacts, write_model_card
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb
from research.temporal_event_model.v3 import MODEL_FAMILY, MODEL_VERSION
from research.temporal_event_model.v3.config import BAR_FAMILIES, BAR_FEATURE_DIMS, ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, default_run_name, to_dict
from research.temporal_event_model.v3.data import TemporalBatch, batch_to_torch, loader_config_from_v3, make_dummy_temporal_batch, validation_loader_config_from_v3
from research.temporal_event_model.v3.losses import compute_loss
from research.temporal_event_model.v3.metrics import MetricWindow, cohort_metrics, fast_batch_metrics, prediction_metrics, wandb_metric_key
from research.temporal_event_model.v3.model import TemporalEventModelV3, build_model_mermaid
from research.temporal_event_model.v3.progress import TemporalProgressState, TemporalTrainingReporter

JOB_TYPE = "train"
_INTERRUPT_REQUESTED = False
_INTERRUPT_COUNT = 0
_INTERRUPT_REASON = "console interrupt"
_INTERRUPT_LOCK = threading.Lock()


def _handle_interrupt(signum: int, _frame: Any) -> None:
    global _INTERRUPT_REQUESTED, _INTERRUPT_COUNT
    with _INTERRUPT_LOCK:
        _INTERRUPT_REQUESTED = True
        _INTERRUPT_COUNT += 1
        count = int(_INTERRUPT_COUNT)
        reason = str(_INTERRUPT_REASON or f"signal {signum}")
    if count <= 1:
        print(f"\nInterrupt received ({reason}); cancelling training. Press Ctrl+C again to force exit.", file=sys.stderr, flush=True)
    else:
        print("\nSecond interrupt received; forcing process exit now.", file=sys.stderr, flush=True)
        os._exit(130)
    raise KeyboardInterrupt


def _install_interrupt_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)


def _set_interrupt_reason(reason: str) -> None:
    global _INTERRUPT_REASON
    with _INTERRUPT_LOCK:
        _INTERRUPT_REASON = str(reason)


def _start_stop_file_monitor(stop_paths: Sequence[Path], *, poll_seconds: float = 1.0) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()
    paths = tuple(Path(path) for path in stop_paths)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def monitor() -> None:
        while not stop_event.wait(max(0.1, float(poll_seconds))):
            for path in paths:
                if path.exists():
                    _set_interrupt_reason(f"stop file {path}")
                    print(f"\nStop file detected: {path}; interrupting training.", file=sys.stderr, flush=True)
                    _thread.interrupt_main()
                    return

    thread = threading.Thread(target=monitor, name="temporal-v3-stop-file-monitor", daemon=True)
    thread.start()
    return stop_event, thread


def parse_args() -> argparse.Namespace:
    default_model = ModelConfig()
    default_loader = LoaderConfig()
    default_train = TrainConfig()
    parser = argparse.ArgumentParser(description="Train temporal_event_model v3 on ticker-month rolling-cache batches.")
    parser.add_argument("--cache-root", default=str(default_loader.cache_root))
    parser.add_argument("--output-root", default=str(default_train.output_root))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--dataset-id", default=default_loader.dataset_id)
    parser.add_argument("--split", default="train")
    parser.add_argument("--val-split", default="validation")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--val-start-utc", default="")
    parser.add_argument("--val-end-utc", default="")
    parser.add_argument("--months", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--data-groups", default=",".join(default_loader.data_groups))
    parser.add_argument("--batch-size", type=int, default=default_loader.batch_size)
    parser.add_argument("--max-samples", type=int, default=default_train.max_samples, help="Stop after this many training samples. 0 means use all available samples for the configured epochs/cache cap.")
    parser.add_argument("--max-steps", type=int, default=default_train.max_steps, help=argparse.SUPPRESS)
    parser.add_argument("--epochs", type=int, default=default_train.epochs)
    parser.add_argument("--max-origins-per-epoch", type=int, default=default_loader.max_origins_per_epoch)
    parser.add_argument("--sample-fraction", type=float, default=1.0)
    parser.add_argument("--sample-hash-modulus", type=int, default=0)
    parser.add_argument("--sample-hash-buckets", default="")
    parser.add_argument("--val-sample-hash-buckets", default="")
    parser.add_argument("--training-days", default="", help="Comma-separated YYYY-MM-DD schedule. Empty discovers all cache days.")
    parser.add_argument("--validation-days", default="", help="Comma-separated YYYY-MM-DD validation days. Empty reserves from training schedule.")
    parser.add_argument("--validation-reserve-policy", choices=("last_n_days", "first_n_days", "random_n_days_seeded"), default=default_loader.validation_reserve_policy)
    parser.add_argument("--validation-reserve-days", type=int, default=default_loader.validation_reserve_days)
    parser.add_argument("--validation-origins-per-day", type=int, default=default_loader.validation_origins_per_day)
    parser.add_argument("--validation-random-ticker-count", type=int, default=default_loader.validation_random_ticker_count)
    parser.add_argument("--validation-liquid-tickers", default=",".join(default_loader.validation_liquid_tickers))
    parser.add_argument("--refresh-validation-plan", action="store_true")
    parser.add_argument("--read-workers", type=int, default=default_loader.read_workers)
    parser.add_argument("--materialize-workers", type=int, default=default_loader.materialize_workers)
    parser.add_argument("--loaded-parts-per-group", type=int, default=default_loader.loaded_parts_per_group)
    parser.add_argument("--materialize-chunk-size", type=int, default=default_loader.materialize_chunk_size)
    parser.add_argument("--prefetch-batches", type=int, default=default_loader.prefetch_batches, help="Raw CPU batches to keep prefetched ahead of GPU training. 0 disables training prefetch.")
    parser.add_argument("--chronological-replay", action=argparse.BooleanOptionalAction, default=default_loader.chronological_replay)
    parser.add_argument("--time-window-seconds", type=float, default=default_loader.time_window_seconds)
    parser.add_argument("--ticker-cache-capacity", type=int, default=default_loader.ticker_cache_capacity)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=default_loader.origin_cursor_chunk_rows)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=default_loader.warm_all_ticker_caches)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=default_loader.scanner_index_cache_entries)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=default_loader.prefetch_scanner_indexes)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=default_loader.scanner_prefetch_workers)
    parser.add_argument("--d-model", type=int, default=default_model.d_model)
    parser.add_argument("--fusion-d-model", type=int, default=default_model.fusion_d_model, help="Fusion token width. 0 means use --d-model.")
    parser.add_argument("--event-d-model", type=int, default=default_model.event_d_model, help="Event encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--bar-d-model", type=int, default=default_model.bar_d_model, help="Bar encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--text-d-model", type=int, default=default_model.text_d_model, help="Text encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--xbrl-d-model", type=int, default=default_model.xbrl_d_model, help="XBRL encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--corporate-action-d-model", type=int, default=default_model.corporate_action_d_model, help="Corporate-action encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--scanner-d-model", type=int, default=default_model.scanner_d_model, help="Scanner encoder output width before fusion adapter. 0 means use --d-model.")
    parser.add_argument("--event-layers", type=int, default=default_model.event_layers)
    parser.add_argument("--event-heads", type=int, default=default_model.event_heads)
    parser.add_argument("--event-encoder-type", choices=("latent", "transformer"), default=default_model.event_encoder_type)
    parser.add_argument("--event-item-dim", type=int, default=default_model.event_item_dim)
    parser.add_argument("--event-latents", type=int, default=default_model.event_latents)
    parser.add_argument("--event-latent-layers", type=int, default=default_model.event_latent_layers)
    parser.add_argument("--event-latent-heads", type=int, default=default_model.event_latent_heads)
    parser.add_argument("--fusion-layers", type=int, default=default_model.fusion_layers)
    parser.add_argument("--fusion-heads", type=int, default=default_model.fusion_heads)
    parser.add_argument("--side-encoder-dim", type=int, default=default_model.side_encoder_dim, help="Hidden width used inside side encoders. 0 means use d_model.")
    parser.add_argument("--dropout", type=float, default=default_model.dropout)
    parser.add_argument("--learning-rate", type=float, default=default_train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=default_train.weight_decay)
    parser.add_argument("--scheduler", choices=("none", "cosine"), default=default_train.scheduler)
    parser.add_argument("--scheduler-eta-min", type=float, default=default_train.scheduler_eta_min)
    parser.add_argument("--scheduler-t-max-samples", type=int, default=default_train.scheduler_t_max_samples, help="Legacy alias for --scheduler-cycle-samples when nonzero.")
    parser.add_argument("--scheduler-cycle-samples", type=int, default=default_train.scheduler_cycle_samples, help="Samples per cosine restart cycle.")
    parser.add_argument("--scheduler-decay-cycles", type=int, default=default_train.scheduler_decay_cycles, help="Number of completed restart cycles before decaying the peak LR.")
    parser.add_argument("--scheduler-decay-factor", type=float, default=default_train.scheduler_decay_factor, help="Peak LR multiplier after each decay cycle group.")
    parser.add_argument("--grad-clip-norm", type=float, default=default_train.grad_clip_norm)
    parser.add_argument("--seed", type=int, default=default_train.seed)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float16", "bfloat16", "float32"), default=default_train.amp_dtype)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=default_train.compile_model)
    parser.add_argument("--optimizer-foreach", action=argparse.BooleanOptionalAction, default=default_train.optimizer_foreach)
    parser.add_argument("--logging-samples", type=int, default=default_train.logging_samples)
    parser.add_argument("--fast-summary-samples", type=int, default=default_train.fast_summary_samples)
    parser.add_argument("--train-metric-window-samples", type=int, default=default_train.train_metric_window_samples)
    parser.add_argument("--validation-samples", type=int, default=default_train.validation_samples)
    parser.add_argument("--validation-batches", type=int, default=default_train.validation_batches)
    parser.add_argument("--disable-validation", action="store_true")
    parser.add_argument("--checkpoint-latest-samples", type=int, default=default_train.checkpoint_latest_samples)
    parser.add_argument("--checkpoint-archive-samples", type=int, default=default_train.checkpoint_archive_samples)
    parser.add_argument("--detail-profile-samples", type=int, default=default_train.detail_profile_samples)
    parser.add_argument("--logging-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--fast-summary-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--train-metric-window-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--validation-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint-latest-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=default_train.progress_layout)
    parser.add_argument("--loader-telemetry-log-seconds", type=float, default=default_train.loader_telemetry_log_seconds, help="Minimum seconds between loader cache telemetry JSONL snapshots. 0 logs every panel poll.")
    parser.add_argument("--cache-state-log-seconds", type=float, default=default_train.cache_state_log_seconds, help="Minimum seconds between cache-state JSONL snapshots. 0 logs every panel poll.")
    parser.add_argument("--wandb-project", default=default_train.wandb_project)
    parser.add_argument("--wandb-entity", default=default_train.wandb_entity)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=default_train.wandb_mode)
    parser.add_argument("--wandb-init-timeout", type=int, default=default_train.wandb_init_timeout)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--warm-start-checkpoint", default="")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dummy-data", action="store_true", help="Use deterministic synthetic batches for shape/model smoke checks.")
    return parser.parse_args()


def main() -> int:
    _install_interrupt_handlers()
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    config = config_from_args(args)
    if not args.dummy_data:
        _configure_day_schedules(config)
    _resolve_sample_limits(config, args)
    run_name = default_run_name(config)
    config.train.run_name = run_name
    run_root = Path(args.output_root) / run_name if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)
    paths = RunPaths.create(run_root)
    stop_monitor_stop, stop_monitor_thread = _start_stop_file_monitor((paths.logs_dir / "STOP", paths.run_root / "STOP"))
    _write_config(paths, config)
    set_seed(int(config.train.seed))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
    model = TemporalEventModelV3(config.model).to(device)
    model = maybe_compile_model(model, bool(config.train.compile_model))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config.train.learning_rate),
        weight_decay=float(config.train.weight_decay),
        foreach=bool(config.train.optimizer_foreach),
    )
    scheduler = build_scheduler(optimizer, config.train)
    scaler = torch.amp.GradScaler("cuda", enabled=bool(config.train.amp and config.train.amp_dtype in {"fp16", "float16"} and device.type == "cuda"))
    train_loader = None if args.dummy_data else _make_loader(config.loader, validation=False)
    validation_loader = None if args.dummy_data or args.disable_validation else _make_loader(config.loader, validation=True, optional=True)
    ledger = TrainingLedger(paths.run_root / "state", config=config, train_loader=train_loader)
    start_samples = _restore_if_requested(args, model, optimizer, scheduler, scaler, train_loader, validation_loader, device)
    if start_samples and args.resume_checkpoint:
        _restore_ledger_from_checkpoint(Path(args.resume_checkpoint), ledger, device)
    wandb_run = _init_wandb(args, config, paths)
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run, wandb_key_mapper=wandb_metric_key)
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=to_dict(config),
        data_roots={"cache_root": str(config.loader.cache_root)},
        output_root=paths.run_root,
        source_checkpoint=Path(args.warm_start_checkpoint) if args.warm_start_checkpoint else None,
        wandb_info={"project": args.wandb_project, "entity": args.wandb_entity, "run_name": run_name},
    )
    write_model_artifacts(
        model=_unwrap_model(model),
        artifact_dir=paths.artifacts_dir / "model",
        model_config=config.model,
        input_contract=_input_contract(config.model),
        output_contract=_output_contract(config.model),
        architecture_mermaid=build_model_mermaid(),
        summary_notes="Temporal v3 consumes ticker-month raw event streams, daily bars, cached Qwen embeddings, XBRL, and corporate-action context.",
        dummy_input_factory=lambda: ((), make_dummy_temporal_batch(model_config=config.model, batch_size=2, device=device).x),
        wandb_run=wandb_run,
    )
    checkpointer = AsyncCheckpointManager(
        paths.checkpoints_dir,
        paths.checkpoint_manifest_path,
        CheckpointPolicy(
            latest_steps=0,
            archive_steps=0,
            save_best_train=bool(config.train.checkpoint_best_train),
            save_best_val=bool(config.train.checkpoint_best_val),
            monitor_train_key="train/loss",
            monitor_val_key="val/loss",
            clock_name="sample",
            archive_prefix="checkpoint_sample",
        ),
    )
    progress = TemporalProgressState(
        run_name=run_name,
        dataset_id=config.loader.dataset_id,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(_unwrap_model(model))["total_parameters"]),
        batch_size=int(config.loader.batch_size),
        max_samples=int(config.train.max_samples),
    )
    reporter = None if config.train.progress_layout == "none" else TemporalTrainingReporter(layout=config.train.progress_layout, state=progress)
    loader_telemetry_logger = LoaderTelemetryJsonlLogger(paths.logs_dir / "loader_cache_telemetry.jsonl", interval_seconds=float(config.train.loader_telemetry_log_seconds))
    cache_state_logger = LoaderTelemetryJsonlLogger(paths.logs_dir / "cache_state.jsonl", interval_seconds=float(config.train.cache_state_log_seconds))
    train_window = MetricWindow(max_batches=32)
    train_iter = _batch_iterator(config, train_loader, device=device, dummy=bool(args.dummy_data))
    val_metrics: dict[str, float] = {}
    cadence = SampleCadence()
    samples_seen_total = int(start_samples)
    interrupted = False
    exit_code = 0
    try:
        with reporter if reporter is not None else _NullReporter() as active_reporter:
            checkpointer.set_message_callback(active_reporter.message)
            active_reporter.message("Trainer initialized; waiting for first training batch.")
            active_reporter.message(f"Graceful stop file: {paths.logs_dir / 'STOP'}")
            if train_loader is not None:
                initial_metrics = _scoped_loader_metrics(
                    role="train",
                    loader=train_loader,
                    iterator=train_iter,
                    batch_size=int(config.loader.batch_size),
                    trainer_phase="initializing",
                )
                initial_metrics.update(
                    _scoped_loader_metrics(
                        role="validation",
                        loader=validation_loader,
                        iterator=None,
                        batch_size=int(config.loader.batch_size),
                        trainer_phase="idle",
                    )
                )
                active_reporter.update(initial_metrics, step=0, validation_metrics=val_metrics)
                if hasattr(active_reporter, "set_loader_metrics_provider") and hasattr(train_loader, "telemetry_snapshot"):
                    active_reporter.set_loader_metrics_provider(
                        lambda train=train_loader, validation=validation_loader, iterator=train_iter, telemetry_logger=loader_telemetry_logger, cache_logger=cache_state_logger, reporter=active_reporter: _loader_telemetry_provider(
                            train_loader=train,
                            train_iterator=iterator,
                            validation_loader=validation,
                            telemetry_logger=telemetry_logger,
                            cache_state_logger=cache_logger,
                            reporter=reporter,
                            batch_size=int(config.loader.batch_size),
                        )
                    )
            update_count = int(train_loader.state.emitted_batches if train_loader is not None else 0)
            while True:
                if _INTERRUPT_REQUESTED:
                    raise KeyboardInterrupt
                prior_total_samples = int(samples_seen_total)
                if int(config.train.max_samples) > 0 and prior_total_samples >= int(config.train.max_samples):
                    break
                effective_summary = _effective_loader_summary(train_loader, train_iter)
                if int(config.train.max_samples) <= 0 and train_loader is not None and int(effective_summary.get("completed_epochs", 0) or 0) >= int(config.train.epochs):
                    break
                update_count += 1
                if update_count == 1:
                    active_reporter.message("Loading and materializing first training batch.")
                active_reporter.update({"train/status/phase": "waiting_for_batch", "train/update_count": float(update_count)}, step=prior_total_samples, validation_metrics=val_metrics, record_history=False)
                batch_start = time.perf_counter()
                loader_start = time.perf_counter()
                batch = next(train_iter)
                loader_wait = time.perf_counter() - loader_start
                if update_count <= 3 or update_count % 100 == 0:
                    active_reporter.message(f"Batch {update_count:,} ready; starting GPU training.")
                optimizer.zero_grad(set_to_none=True)
                amp_dtype = _amp_dtype(config.train.amp_dtype)
                prior_samples_seen = int(prior_total_samples)
                detail_profile_due = update_count == 1 or cadence.due("detail_profile", prior_samples_seen + int(batch.sample_count), int(config.train.detail_profile_samples))
                if train_loader is not None:
                    active_reporter.update(
                        {
                            "train/status/phase": "gpu_training",
                            "train/update_count": float(update_count),
                            "train/samples_clock": float(prior_samples_seen),
                            "train/gpu_memory_allocated_gib": _gpu_memory_gib(device),
                            "train/cpu_rss_gib": _rss_gib(),
                            **_loader_state_metrics(summary=_effective_loader_summary(train_loader, train_iter), batch_profile=batch.profile, batch_size=int(config.loader.batch_size)),
                            **_scoped_loader_metrics(
                                role="train",
                                loader=train_loader,
                                iterator=train_iter,
                                batch_size=int(config.loader.batch_size),
                                trainer_phase="gpu_training",
                                batch_profile=batch.profile,
                            ),
                        },
                        step=prior_samples_seen,
                        validation_metrics=val_metrics,
                        record_history=False,
                    )
                gpu_start = time.perf_counter()
                with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
                    if detail_profile_due and hasattr(model, "forward_with_timings"):
                        output, model_profile = model.forward_with_timings(batch.x, sync_cuda=device.type == "cuda")  # type: ignore[attr-defined]
                    else:
                        output = model(batch.x)
                        model_profile = {}
                    loss_result = compute_loss(output, batch)
                    loss = loss_result.loss
                amp_step_skipped = False
                if scaler.is_enabled():
                    scale_before = float(scaler.get_scale())
                    scaler.scale(loss).backward()
                    if float(config.train.grad_clip_norm) > 0:
                        scaler.unscale_(optimizer)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    scaler.step(optimizer)
                    scaler.update()
                    amp_step_skipped = float(scaler.get_scale()) < scale_before
                else:
                    loss.backward()
                    if float(config.train.grad_clip_norm) > 0:
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    optimizer.step()
                if device.type == "cuda":
                    torch.cuda.synchronize()
                gpu_seconds = time.perf_counter() - gpu_start
                samples_seen_total = int(prior_samples_seen) + int(batch.sample_count)
                scheduler_metrics: dict[str, float] = {}
                if scheduler is not None and not amp_step_skipped:
                    scheduler.step(samples_seen_total)
                    scheduler_metrics = scheduler.metrics()
                metrics = dict(loss_result.metrics)
                metrics.update(
                    {
                        "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                        "train/batch_seconds": time.perf_counter() - batch_start,
                        "train/loader_wait_seconds": float(loader_wait),
                        "train/gpu_batch_seconds": float(gpu_seconds),
                        "train/samples_per_second": float(batch.sample_count / max(time.perf_counter() - batch_start, 1e-9)),
                        "train/samples_seen_total": float(samples_seen_total),
                        "train/samples_clock": float(samples_seen_total),
                        "train/update_count": float(update_count),
                        "train/materialize_seconds": float(batch.profile.get("materialize_seconds", 0.0)),
                        "train/gpu_memory_allocated_gib": _gpu_memory_gib(device),
                        "train/cpu_rss_gib": _rss_gib(),
                        "train/amp_step_skipped": float(amp_step_skipped),
                    }
                )
                metrics.update(scheduler_metrics)
                metrics.update({f"profile/model/{key}_seconds": float(value) for key, value in model_profile.items()})
                if train_loader is not None:
                    summary = _effective_loader_summary(train_loader, train_iter)
                    ledger.update_batch(batch, loader_summary=summary)
                    metrics["train/samples_seen_total"] = float(samples_seen_total)
                    metrics["train/samples_clock"] = float(samples_seen_total)
                    metrics.update(
                        {
                            "loader/epoch": float(summary.get("epoch", 0)),
                            "loader/package_position": float(summary.get("package_position", 0)),
                            "loader/origin_cursor": float(summary.get("origin_cursor", 0)),
                            "loader/emitted_batches": float(summary.get("emitted_batches", 0)),
                            "loader/emitted_samples": float(summary.get("emitted_samples", 0)),
                            "loader/seen_origins_total": float(summary.get("seen_origins_total", 0)),
                            "loader/seen_origins_this_epoch": float(summary.get("seen_origins_this_epoch", 0)),
                            "schedule/day_index": float(ledger.current_day_index),
                            "schedule/day_count": float(max(ledger.day_count, 1)),
                            "schedule/current_day_samples_seen": float(ledger.current_day_seen),
                            "schedule/current_day_sample_count": float(max(ledger.current_day_total, 1)),
                        }
                    )
                    metrics.update(_loader_state_metrics(summary=summary, batch_profile=batch.profile, batch_size=int(config.loader.batch_size)))
                    metrics.update(
                        _scoped_loader_metrics(
                            role="train",
                            loader=train_loader,
                            iterator=train_iter,
                            batch_size=int(config.loader.batch_size),
                            trainer_phase="training",
                            batch_profile=batch.profile,
                        )
                    )
                if cadence.due("fast_summary", samples_seen_total, int(config.train.fast_summary_samples)):
                    metrics.update(fast_batch_metrics(batch, output, prefix="train"))
                if cadence.due("train_metric_window", samples_seen_total, int(config.train.train_metric_window_samples)):
                    train_window.add(prediction_metrics(batch, output, prefix="train"))
                    train_window.add(cohort_metrics(batch, output, prefix="train"))
                    metrics.update(train_window.mean())
                metric_logger.log(metrics, samples_seen_total)
                validation_due = cadence.due("validation", samples_seen_total, int(config.train.validation_samples))
                if validation_due and (validation_loader is not None or args.dummy_data):
                    active_reporter.update(
                        {
                            "train/status/phase": "validation",
                            "train/update_count": float(update_count),
                            **_scoped_loader_metrics(
                                role="validation",
                                loader=validation_loader,
                                iterator=None,
                                batch_size=int(config.loader.batch_size),
                                trainer_phase="validation",
                            ),
                        },
                        step=samples_seen_total,
                        validation_metrics=val_metrics,
                        record_history=False,
                    )
                    val_metrics = run_validation(model, config, validation_loader, device=device, dummy=bool(args.dummy_data), profile_detail=True)
                    metric_logger.log(val_metrics, samples_seen_total)
                    active_reporter.update(
                        _scoped_loader_metrics(
                            role="validation",
                            loader=validation_loader,
                            iterator=None,
                            batch_size=int(config.loader.batch_size),
                            trainer_phase="validation_complete",
                        ),
                        step=samples_seen_total,
                        validation_metrics=val_metrics,
                        record_history=False,
                    )
                if reporter is not None and cadence.due("logging", samples_seen_total, int(config.train.logging_samples)):
                    active_reporter.update(metrics, step=samples_seen_total, validation_metrics=val_metrics)
                checkpoint_due = cadence.checkpoint_reasons(samples_seen_total, latest=int(config.train.checkpoint_latest_samples), archive=int(config.train.checkpoint_archive_samples))
                if checkpoint_due:
                    save_checkpoint_reasons(
                        checkpointer,
                        step=samples_seen_total,
                        reasons=checkpoint_due,
                        payload_factory=lambda step=samples_seen_total, metrics=metrics, val_metrics=val_metrics: checkpoint_payload(
                            model=model,
                            optimizer=optimizer,
                            scheduler=scheduler,
                            scaler=scaler,
                            config=config,
                            step=step,
                            train_loader=train_loader,
                            validation_loader=validation_loader,
                            ledger=ledger,
                            train_metrics=metrics,
                            val_metrics=val_metrics,
                            run_paths=paths,
                            train_loader_state_override=_effective_loader_state(train_loader, train_iter),
                        ),
                        train_metrics=metrics,
                        val_metrics=val_metrics,
                    )
            final_metrics = val_metrics or {}
            final_samples = int(samples_seen_total)
            checkpointer.maybe_save(
                step=final_samples,
                payload_factory=lambda: checkpoint_payload(model, optimizer, scheduler, scaler, config, final_samples, train_loader, validation_loader, ledger, {}, final_metrics, paths, train_loader_state_override=_effective_loader_state(train_loader, train_iter)),
                train_metrics={"train/loss": progress.loss},
                val_metrics=final_metrics,
                force=True,
            )
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
        _cancel_loader(train_loader)
        _cancel_loader(validation_loader)
        _cancel_iterator(train_iter)
        samples_seen = int(samples_seen_total)
        message = f"Interrupt received; cancelling training at sample {samples_seen:,}."
        print(message, flush=True)
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "interrupted.txt").write_text(message + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "fatal_error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        raise
    finally:
        stop_monitor_stop.set()
        if stop_monitor_thread.is_alive():
            stop_monitor_thread.join(timeout=1.0)
        _cancel_loader(train_loader)
        _cancel_loader(validation_loader)
        _cancel_iterator(train_iter)
        checkpointer.close(wait=not interrupted, timeout=2.0 if interrupted else None)
        if wandb_run is not None:
            wandb_run.finish()
    return exit_code


@torch.no_grad()
def run_validation(model: torch.nn.Module, config: ExperimentConfig, validation_loader: Any, *, device: torch.device, dummy: bool, profile_detail: bool = False) -> dict[str, float]:
    if validation_loader is None and not dummy:
        return {}
    model.eval()
    metrics: list[dict[str, float]] = []
    iterator = _dummy_batch_iterator(config=config, device=device) if dummy else _synchronous_batch_iterator(config=config, loader=validation_loader, device=device)
    try:
        for _ in range(max(1, int(config.train.validation_batches))):
            batch = next(iterator)
            amp_dtype = _amp_dtype(config.train.amp_dtype)
            with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
                if profile_detail and hasattr(model, "forward_with_timings"):
                    output, model_profile = model.forward_with_timings(batch.x, sync_cuda=device.type == "cuda")  # type: ignore[attr-defined]
                else:
                    output = model(batch.x)
                    model_profile = {}
                loss = compute_loss(output, batch)
            row = {key.replace("train/", "val/"): value for key, value in loss.metrics.items()}
            row.update(prediction_metrics(batch, output, prefix="val"))
            row.update(cohort_metrics(batch, output, prefix="val"))
            row.update(fast_batch_metrics(batch, output, prefix="val"))
            row.update({f"profile/val_model/{key}_seconds": float(value) for key, value in model_profile.items()})
            metrics.append(row)
    finally:
        _cancel_iterator(iterator)
        model.train()
    return _mean_metrics(metrics)


def checkpoint_payload(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: SampleCosineScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    step: int,
    train_loader: Any,
    validation_loader: Any,
    ledger: "TrainingLedger",
    train_metrics: dict[str, float],
    val_metrics: dict[str, float],
    run_paths: RunPaths,
    train_loader_state_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fallback_batches_seen = (int(step) + max(1, int(config.loader.batch_size)) - 1) // max(1, int(config.loader.batch_size))
    train_loader_state = dict(train_loader_state_override or (train_loader.state_dict() if train_loader is not None else {}))
    model_card = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "sample_clock": int(step),
        "step": int(step),
        "dataset_id": config.loader.dataset_id,
        "cache_root": str(config.loader.cache_root),
        "period": {"start_utc": config.loader.start_utc, "end_utc": config.loader.end_utc, "months": list(config.loader.months)},
        "validation_period": {"start_utc": config.loader.val_start_utc, "end_utc": config.loader.val_end_utc},
        "samples_seen": int(step),
        "batches_seen": int(train_loader_state.get("emitted_batches", fallback_batches_seen) or fallback_batches_seen),
        "loader_state": train_loader_state,
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
        "training_schedule": ledger.schedule_payload(),
        "training_ledger": ledger.snapshot(),
        "training_ledger_path": str(ledger.ledger_path),
        "objective": {"loss": "unweighted active-task masked mean", "manual_loss_weights": "disabled_by_default"},
        "data_groups": list(config.loader.data_groups),
        "scheduler": {
            "type": config.train.scheduler,
            "eta_min": float(config.train.scheduler_eta_min),
            "cycle_samples": int(config.train.scheduler_cycle_samples),
            "t_max_samples": int(config.train.scheduler_t_max_samples),
            "decay_cycles": int(config.train.scheduler_decay_cycles),
            "decay_factor": float(config.train.scheduler_decay_factor),
            "last_progress": float(scheduler.last_progress) if scheduler is not None else 0.0,
            "last_cycle_index": int(scheduler.last_cycle_index) if scheduler is not None else 0,
            "last_decay_group": int(scheduler.last_decay_group) if scheduler is not None else 0,
            "last_peak_lr": float(scheduler.last_peak_lrs[0]) if scheduler is not None and scheduler.last_peak_lrs else 0.0,
            "last_lr": float(optimizer.param_groups[0]["lr"]) if optimizer.param_groups else 0.0,
        },
        "latest_train_metrics": dict(train_metrics),
        "latest_validation_metrics": dict(val_metrics),
        "run_root": str(run_paths.run_root),
    }
    write_model_card(run_paths.artifacts_dir / "latest_model_card.json", model_card)
    append_checkpoint_model_card(run_paths.artifacts_dir / "model_cards.jsonl", model_card)
    return {
        "model": _unwrap_model(model).state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "scaler": scaler.state_dict() if scaler.is_enabled() else None,
        "config": to_dict(config),
        "sample_clock": int(step),
        "step": int(step),
        "rng_state": checkpoint_rng_state(),
        "train_loader_state": train_loader_state,
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
        "training_schedule": ledger.schedule_payload(),
        "training_ledger": ledger.snapshot(),
        "model_card": model_card,
    }


def _loader_state_metrics(*, summary: Mapping[str, Any], batch_profile: Mapping[str, Any], batch_size: int = 0) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "loader/state/epoch": float(summary.get("epoch", 0) or 0),
        "loader/state/package_position": float(summary.get("package_position", 0) or 0),
        "loader/state/origin_cursor": float(summary.get("origin_cursor", 0) or 0),
        "loader/state/chronological_day_position": float(summary.get("chronological_day_position", 0) or 0),
        "loader/state/chronological_origin_cursor": float(summary.get("chronological_origin_cursor", 0) or 0),
        "loader/state/emitted_batches": float(summary.get("emitted_batches", 0) or 0),
        "loader/state/emitted_samples": float(summary.get("emitted_samples", 0) or 0),
        "loader/state/seen_origins_total": float(summary.get("seen_origins_total", 0) or 0),
        "loader/state/seen_origins_this_epoch": float(summary.get("seen_origins_this_epoch", 0) or 0),
        "loader/cache/part_count": float(summary.get("part_count", summary.get("package_count", 0)) or 0),
        "loader/cache/ticker_package_count": float(summary.get("ticker_package_count", 0) or 0),
        "loader/cache/ticker_count": float(summary.get("ticker_count", 0) or 0),
        "loader/cache/total_available_origins": float(summary.get("total_available_origins", 0) or 0),
    }
    summary_config = summary.get("config") if isinstance(summary.get("config"), Mapping) else {}
    if isinstance(summary_config, Mapping):
        metrics["loader/window/seconds"] = float(summary_config.get("time_window_seconds", 0.0) or 0.0)
    mapping = {
        "event_cache_ticker_states": "loader/cache/event_ticker_states",
        "event_cache_capacity": "loader/cache/event_ticker_capacity",
        "event_cache_evictions": "loader/cache/event_evictions",
        "event_cache_protected_tickers": "loader/cache/event_protected_tickers",
        "event_cache_stream_rows_per_ticker": "loader/cache/event_stream_rows_per_ticker",
        "event_cache_feature_count": "loader/cache/event_feature_count",
        "event_cache_estimated_bytes": "loader/cache/event_estimated_mib",
        "event_cache_warm_seconds": "loader/cache/event_warm_seconds",
        "event_cache_warm_tickers": "loader/cache/event_warm_tickers",
        "event_cache_warm_total_tickers": "loader/cache/event_warm_total_tickers",
        "event_cache_warm_rows": "loader/cache/event_warm_rows",
        "event_cache_warm_evictions": "loader/cache/event_warm_evictions",
        "event_cache_warm_rss_delta_mib": "loader/cache/event_warm_rss_delta_mib",
        "cache_first_day_warm_seconds": "loader/cache/day_warm_seconds",
        "cache_first_cursor_build_seconds": "loader/cache/cursor_build_seconds",
        "event_cache_warm_rebuilds": "loader/cache/event_warm_rebuilds",
        "event_cache_warm_appends": "loader/cache/event_warm_appends",
        "event_cache_warm_reused": "loader/cache/event_warm_reused",
        "cache_first_day_context_warm_seconds": "loader/cache/context_day_warm_seconds",
        "context_cache_warm_payload_tickers": "loader/cache/context_warm_tickers",
        "context_cache_warm_payload_total_tickers": "loader/cache/context_warm_total_tickers",
        "context_cache_warm_payload_rows": "loader/cache/context_warm_rows",
        "context_cache_warm_payload_bytes": "loader/cache/context_warm_mib",
        "context_cache_warm_payload_materialize_seconds": "loader/cache/context_warm_materialize_seconds",
        "context_cache_warm_payload_rss_delta_mib": "loader/cache/context_warm_rss_delta_mib",
        "raw_stream_rolling_state_copy_seconds": "loader/cache/event_state_copy_seconds",
        "raw_stream_rolling_stateful": "loader/cache/event_stateful",
        "raw_stream_rolling_reused": "loader/cache/event_state_reused",
        "rolling_text_cache_hits": "loader/cache/text_hits",
        "rolling_text_cache_misses": "loader/cache/text_misses",
        "rolling_text_cache_stale": "loader/cache/text_stale",
        "rolling_xbrl_cache_hits": "loader/cache/xbrl_hits",
        "rolling_xbrl_cache_misses": "loader/cache/xbrl_misses",
        "rolling_xbrl_cache_stale": "loader/cache/xbrl_stale",
        "rolling_corporate_action_cache_hits": "loader/cache/corporate_action_hits",
        "rolling_corporate_action_cache_misses": "loader/cache/corporate_action_misses",
        "rolling_corporate_action_cache_stale": "loader/cache/corporate_action_stale",
        "rolling_bar_cache_hits": "loader/cache/bar_hits",
        "rolling_bar_cache_misses": "loader/cache/bar_misses",
        "rolling_bar_cache_stale": "loader/cache/bar_stale",
        "rolling_intraday_bar_cache_hits": "loader/cache/intraday_bar_hits",
        "rolling_intraday_bar_cache_misses": "loader/cache/intraday_bar_misses",
        "rolling_intraday_bar_cache_stale": "loader/cache/intraday_bar_stale",
        "rolling_scanner_cache_hits": "loader/cache/scanner_hits",
        "rolling_scanner_cache_misses": "loader/cache/scanner_misses",
        "rolling_scanner_cache_stale": "loader/cache/scanner_stale",
        "origin_cursor_count": "loader/cache/origin_cursor_count",
        "origin_cursor_chunk_rows": "loader/cache/origin_cursor_chunk_rows",
        "origin_cursor_initial_seconds": "loader/cache/origin_cursor_initial_seconds",
        "origin_cursor_initial_chunks": "loader/cache/origin_cursor_initial_chunks",
        "origin_cursor_rows_loaded": "loader/cache/origin_cursor_rows_loaded",
        "origin_cursor_rows_loaded_for_window": "loader/cache/origin_cursor_rows_loaded_for_window",
        "origin_cursor_rss_delta_mib": "loader/cache/origin_cursor_rss_delta_mib",
        "origin_cache_parts": "loader/cache/origin_parts",
        "origin_cache_limit": "loader/cache/origin_limit",
        "origin_rows": "loader/cache/origin_rows",
        "origin_window_load_seconds": "loader/cache/origin_window_load_seconds",
        "origin_window_cursor_seconds": "loader/cache/origin_window_cursor_seconds",
        "origin_window_sort_seconds": "loader/cache/origin_window_sort_seconds",
        "origin_window_rss_delta_mib": "loader/cache/origin_window_rss_delta_mib",
        "payload_cache_parts": "loader/cache/payload_parts",
        "payload_cache_limit": "loader/cache/payload_limit",
        "part_count": "loader/cache/part_count",
        "package_count": "loader/cache/part_count",
        "ticker_package_count": "loader/cache/ticker_package_count",
        "ticker_count": "loader/cache/ticker_count",
        "total_available_origins": "loader/cache/total_available_origins",
        "ready_buffer_chunks": "loader/cache/ready_buffer_chunks",
        "ready_buffer_samples": "loader/cache/ready_buffer_samples",
        "materializer_text_index_cache_entries": "loader/cache/text_index_entries",
        "materializer_label_index_cache_entries": "loader/cache/label_index_entries",
        "materializer_scanner_index_cache_entries": "loader/cache/scanner_index_entries",
        "scanner_current_day_seconds": "loader/cache/scanner_current_day_seconds",
        "scanner_current_day_paths": "loader/cache/scanner_current_day_paths",
        "scanner_current_day_built": "loader/cache/scanner_current_day_built",
        "scanner_current_day_reused": "loader/cache/scanner_current_day_reused",
        "scanner_current_day_missing": "loader/cache/scanner_current_day_missing",
        "scanner_current_day_empty": "loader/cache/scanner_current_day_empty",
        "scanner_current_day_failed": "loader/cache/scanner_current_day_failed",
        "scanner_next_day_prefetch_paths": "loader/prefetch/scanner_next_day_paths",
        "scanner_next_day_prefetch_total_paths": "loader/prefetch/scanner_next_day_total_paths",
        "scanner_next_day_prefetch_done": "loader/prefetch/scanner_next_day_done",
        "scanner_next_day_prefetch_failed": "loader/prefetch/scanner_next_day_failed",
        "materializer_bar_index_cache_entries": "loader/cache/bar_index_entries",
        "materializer_xbrl_index_cache_entries": "loader/cache/xbrl_index_entries",
        "materializer_xbrl_category_cache_entries": "loader/cache/xbrl_category_entries",
        "materializer_corporate_action_index_cache_entries": "loader/cache/corporate_action_index_entries",
        "window_active_refs": "loader/window/active_refs",
        "window_active_parts": "loader/window/active_parts",
        "window_active_tickers": "loader/window/active_tickers",
        "window_start_timestamp_us": "loader/window/start_timestamp_us",
        "window_end_timestamp_us": "loader/window/end_timestamp_us",
        "day_package_count": "loader/window/day_package_count",
        "day_ticker_count": "loader/window/day_ticker_count",
        "day_refs_total": "loader/window/day_refs_total",
        "day_refs_remaining_before_window": "loader/window/day_refs_remaining_before_window",
        "chronological_time_window_seconds": "loader/window/seconds",
        "prefetch_materialize_max_pending_batches": "loader/prefetch/materialize_max_pending_batches",
        "prefetch_materialize_pending_batches": "loader/prefetch/materialize_pending_batches",
        "raw_prefetch_enabled": "loader/prefetch/raw_enabled",
        "raw_prefetch_queue_size": "loader/prefetch/raw_queue_size",
        "raw_prefetch_queue_limit": "loader/prefetch/raw_queue_limit",
        "raw_prefetch_produced_batches": "loader/prefetch/raw_produced_batches",
        "raw_prefetch_produced_samples": "loader/prefetch/raw_produced_samples",
        "raw_prefetch_consumed_batches": "loader/prefetch/raw_consumed_batches",
        "raw_prefetch_consumed_samples": "loader/prefetch/raw_consumed_samples",
        "raw_prefetch_thread_alive": "loader/prefetch/raw_thread_alive",
        "raw_prefetch_exception": "loader/prefetch/raw_exception",
    }
    for source, target in mapping.items():
        value = batch_profile.get(source)
        if not isinstance(value, (int, float)):
            continue
        metrics[target] = float(value) / (1024.0 * 1024.0) if source in {"event_cache_estimated_bytes", "context_cache_warm_payload_bytes"} else float(value)
    raw_ready_batches = metrics.get("loader/prefetch/raw_queue_size")
    ready_samples = metrics.get("loader/cache/ready_buffer_samples")
    if isinstance(raw_ready_batches, (int, float)):
        metrics["loader/cache/ready_batches"] = float(raw_ready_batches)
    elif isinstance(ready_samples, (int, float)) and int(batch_size) > 0:
        metrics["loader/cache/ready_batches"] = float(ready_samples) / float(max(1, int(batch_size)))
    status_mapping = {
        "loader_phase": "loader/status/phase",
        "current_source_date": "loader/status/current_day",
    }
    for source, target in status_mapping.items():
        value = batch_profile.get(source)
        if value is not None:
            metrics[target] = str(value)
    return metrics


def _cache_state_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    mapping = {
        "loader/cache/event_ticker_states": "cache/state/event_tickers",
        "loader/cache/event_ticker_capacity": "cache/state/event_ticker_capacity",
        "loader/cache/event_evictions": "cache/state/event_evictions",
        "loader/cache/event_protected_tickers": "cache/state/event_protected_tickers",
        "loader/cache/event_stream_rows_per_ticker": "cache/state/event_rows_per_ticker",
        "loader/cache/event_feature_count": "cache/state/event_feature_count",
        "loader/cache/event_estimated_mib": "cache/state/event_estimated_mib",
        "loader/cache/event_warm_seconds": "cache/state/event_warm_seconds",
        "loader/cache/event_warm_tickers": "cache/state/event_warm_tickers",
        "loader/cache/event_warm_total_tickers": "cache/state/event_warm_total_tickers",
        "loader/cache/event_warm_rows": "cache/state/event_warm_rows",
        "loader/cache/event_warm_evictions": "cache/state/event_warm_evictions",
        "loader/cache/event_warm_rss_delta_mib": "cache/state/event_warm_rss_delta_mib",
        "loader/cache/day_warm_seconds": "cache/state/day_warm_seconds",
        "loader/cache/cursor_build_seconds": "cache/state/cursor_build_seconds",
        "loader/cache/event_warm_rebuilds": "cache/state/event_warm_rebuilds",
        "loader/cache/event_warm_appends": "cache/state/event_warm_appends",
        "loader/cache/event_warm_reused": "cache/state/event_warm_reused",
        "loader/cache/context_day_warm_seconds": "cache/state/context_day_warm_seconds",
        "loader/cache/context_warm_tickers": "cache/state/context_warm_tickers",
        "loader/cache/context_warm_total_tickers": "cache/state/context_warm_total_tickers",
        "loader/cache/context_warm_rows": "cache/state/context_warm_rows",
        "loader/cache/context_warm_mib": "cache/state/context_warm_mib",
        "loader/cache/context_warm_materialize_seconds": "cache/state/context_warm_materialize_seconds",
        "loader/cache/context_warm_rss_delta_mib": "cache/state/context_warm_rss_delta_mib",
        "loader/cache/event_state_copy_seconds": "cache/state/event_state_copy_seconds",
        "loader/cache/event_stateful": "cache/state/event_stateful",
        "loader/cache/event_state_reused": "cache/state/event_state_reused",
        "loader/cache/text_hits": "cache/state/text_hits",
        "loader/cache/text_misses": "cache/state/text_misses",
        "loader/cache/text_stale": "cache/state/text_stale",
        "loader/cache/xbrl_hits": "cache/state/xbrl_hits",
        "loader/cache/xbrl_misses": "cache/state/xbrl_misses",
        "loader/cache/xbrl_stale": "cache/state/xbrl_stale",
        "loader/cache/corporate_action_hits": "cache/state/corporate_action_hits",
        "loader/cache/corporate_action_misses": "cache/state/corporate_action_misses",
        "loader/cache/corporate_action_stale": "cache/state/corporate_action_stale",
        "loader/cache/bar_hits": "cache/state/bar_hits",
        "loader/cache/bar_misses": "cache/state/bar_misses",
        "loader/cache/bar_stale": "cache/state/bar_stale",
        "loader/cache/intraday_bar_hits": "cache/state/intraday_bar_hits",
        "loader/cache/intraday_bar_misses": "cache/state/intraday_bar_misses",
        "loader/cache/intraday_bar_stale": "cache/state/intraday_bar_stale",
        "loader/cache/scanner_hits": "cache/state/scanner_hits",
        "loader/cache/scanner_misses": "cache/state/scanner_misses",
        "loader/cache/scanner_stale": "cache/state/scanner_stale",
        "loader/cache/origin_cursor_count": "cache/state/origin_cursor_count",
        "loader/cache/origin_cursor_chunk_rows": "cache/state/origin_cursor_chunk_rows",
        "loader/cache/origin_cursor_initial_seconds": "cache/state/origin_cursor_initial_seconds",
        "loader/cache/origin_cursor_initial_chunks": "cache/state/origin_cursor_initial_chunks",
        "loader/cache/origin_cursor_rows_loaded": "cache/state/origin_cursor_rows_loaded",
        "loader/cache/origin_cursor_rows_loaded_for_window": "cache/state/origin_cursor_rows_loaded_for_window",
        "loader/cache/origin_cursor_rss_delta_mib": "cache/state/origin_cursor_rss_delta_mib",
        "loader/cache/origin_parts": "cache/state/origin_parts",
        "loader/cache/origin_rows": "cache/state/origin_rows",
        "loader/cache/origin_window_cursor_seconds": "cache/state/origin_window_cursor_seconds",
        "loader/cache/origin_window_sort_seconds": "cache/state/origin_window_sort_seconds",
        "loader/cache/origin_window_rss_delta_mib": "cache/state/origin_window_rss_delta_mib",
        "loader/cache/payload_parts": "cache/state/payload_parts",
        "loader/cache/payload_limit": "cache/state/payload_limit",
        "loader/cache/ready_batches": "cache/state/ready_batches",
        "loader/cache/ready_buffer_chunks": "cache/state/ready_chunks",
        "loader/cache/ready_buffer_samples": "cache/state/ready_samples",
        "loader/cache/text_index_entries": "cache/state/text_index_entries",
        "loader/cache/label_index_entries": "cache/state/label_index_entries",
        "loader/cache/scanner_index_entries": "cache/state/scanner_index_entries",
        "loader/cache/scanner_current_day_seconds": "cache/state/scanner_current_day_seconds",
        "loader/cache/scanner_current_day_built": "cache/state/scanner_current_day_built",
        "loader/cache/scanner_current_day_reused": "cache/state/scanner_current_day_reused",
        "loader/cache/scanner_current_day_missing": "cache/state/scanner_current_day_missing",
        "loader/cache/scanner_current_day_failed": "cache/state/scanner_current_day_failed",
        "loader/cache/bar_index_entries": "cache/state/bar_index_entries",
        "loader/cache/xbrl_index_entries": "cache/state/xbrl_index_entries",
        "loader/cache/xbrl_category_entries": "cache/state/xbrl_category_entries",
        "loader/cache/corporate_action_index_entries": "cache/state/corporate_action_index_entries",
        "loader/prefetch/raw_queue_size": "cache/state/raw_ready_batches",
        "loader/prefetch/raw_queue_limit": "cache/state/raw_ready_limit",
        "loader/prefetch/raw_produced_batches": "cache/state/raw_produced_batches",
        "loader/prefetch/raw_consumed_batches": "cache/state/raw_consumed_batches",
        "loader/prefetch/raw_thread_alive": "cache/state/raw_thread_alive",
        "loader/prefetch/scanner_next_day_paths": "cache/state/scanner_next_day_paths",
        "loader/prefetch/scanner_next_day_total_paths": "cache/state/scanner_next_day_total_paths",
        "loader/prefetch/scanner_next_day_done": "cache/state/scanner_next_day_done",
        "loader/prefetch/scanner_next_day_failed": "cache/state/scanner_next_day_failed",
        "loader/prefetch/materialize_pending_batches": "cache/state/materialize_pending_batches",
        "loader/prefetch/materialize_max_pending_batches": "cache/state/materialize_max_pending_batches",
    }
    out = {target: metrics[source] for source, target in mapping.items() if source in metrics}
    for source, target in {
        "loader/status/phase": "cache/state/loader_phase",
        "loader/status/current_day": "cache/state/current_day",
        "train/status/phase": "cache/state/trainer_phase",
    }.items():
        if source in metrics:
            out[target] = metrics[source]
    return out


def _prefix_metrics(role: str, metrics: Mapping[str, Any]) -> dict[str, Any]:
    role_prefix = str(role).strip("/")
    return {f"{role_prefix}/{key}": value for key, value in metrics.items()}


def _scoped_loader_metrics(
    *,
    role: str,
    loader: Any,
    iterator: Any | None,
    batch_size: int,
    trainer_phase: str | None,
    batch_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    role_name = str(role).strip("/") or "loader"
    if loader is None:
        base = {
            "loader/status/phase": "disabled",
            "cache/state/loader_phase": "disabled",
            "cache/state/trainer_phase": str(trainer_phase or "idle"),
        }
        return _prefix_metrics(role_name, base)
    summary = _effective_loader_summary(loader, iterator) if iterator is not None else loader.summary()
    profile: dict[str, Any] = {}
    if hasattr(loader, "telemetry_snapshot"):
        try:
            profile.update(dict(loader.telemetry_snapshot()))
        except Exception as exc:  # noqa: BLE001
            profile["loader_phase"] = f"telemetry_error:{type(exc).__name__}"
    if batch_profile:
        profile.update(dict(batch_profile))
    base = _loader_state_metrics(summary=summary, batch_profile=profile, batch_size=int(batch_size))
    if "loader/status/phase" not in base:
        base["loader/status/phase"] = "idle"
    cache = _cache_state_metrics(base)
    cache.setdefault("cache/state/loader_phase", base.get("loader/status/phase", "idle"))
    cache["cache/state/trainer_phase"] = str(trainer_phase or "idle")
    return _prefix_metrics(role_name, {**base, **cache})


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    train_defaults = TrainConfig()
    model = ModelConfig(
        d_model=int(args.d_model),
        fusion_d_model=int(args.fusion_d_model),
        event_d_model=int(args.event_d_model),
        bar_d_model=int(args.bar_d_model),
        text_d_model=int(args.text_d_model),
        xbrl_d_model=int(args.xbrl_d_model),
        corporate_action_d_model=int(args.corporate_action_d_model),
        scanner_d_model=int(args.scanner_d_model),
        event_layers=int(args.event_layers),
        event_heads=int(args.event_heads),
        event_encoder_type=str(args.event_encoder_type),
        event_item_dim=int(args.event_item_dim),
        event_latents=int(args.event_latents),
        event_latent_layers=int(args.event_latent_layers),
        event_latent_heads=int(args.event_latent_heads),
        fusion_layers=int(args.fusion_layers),
        fusion_heads=int(args.fusion_heads),
        side_encoder_dim=int(args.side_encoder_dim),
        dropout=float(args.dropout),
    )
    loader = LoaderConfig(
        cache_root=Path(args.cache_root),
        split=str(args.split),
        val_split=str(args.val_split),
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        val_start_utc=str(args.val_start_utc),
        val_end_utc=str(args.val_end_utc),
        months=_split_csv(args.months),
        tickers=_split_csv(args.tickers),
        batch_size=int(args.batch_size),
        seed=int(args.seed),
        dataset_id=str(args.dataset_id),
        data_groups=_split_csv(args.data_groups),
        read_workers=int(args.read_workers),
        materialize_workers=int(args.materialize_workers),
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        materialize_chunk_size=int(args.materialize_chunk_size),
        prefetch_batches=int(args.prefetch_batches),
        chronological_replay=bool(args.chronological_replay),
        time_window_seconds=float(args.time_window_seconds),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(args.scanner_prefetch_workers),
        max_origins_per_epoch=int(args.max_origins_per_epoch),
        sample_fraction=float(args.sample_fraction),
        sample_hash_modulus=int(args.sample_hash_modulus),
        sample_hash_buckets=tuple(int(v) for v in _split_csv(args.sample_hash_buckets)),
        val_sample_hash_buckets=tuple(int(v) for v in _split_csv(args.val_sample_hash_buckets)),
        training_days=tuple(str(day)[:10] for day in _split_csv(args.training_days)),
        validation_days=tuple(str(day)[:10] for day in _split_csv(args.validation_days)),
        validation_reserve_policy=str(args.validation_reserve_policy),
        validation_reserve_days=int(args.validation_reserve_days),
        validation_origins_per_day=int(args.validation_origins_per_day),
        validation_random_ticker_count=int(args.validation_random_ticker_count),
        validation_liquid_tickers=_split_csv(args.validation_liquid_tickers),
        refresh_validation_plan=bool(args.refresh_validation_plan),
    )
    train = TrainConfig(
        run_name=str(args.run_name),
        output_root=Path(args.output_root),
        max_steps=int(args.max_steps),
        max_samples=max(0, int(args.max_samples)),
        epochs=int(args.epochs),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        scheduler=str(args.scheduler),
        scheduler_eta_min=max(0.0, float(args.scheduler_eta_min)),
        scheduler_t_max_samples=max(0, int(args.scheduler_t_max_samples)),
        scheduler_cycle_samples=max(0, int(args.scheduler_cycle_samples)),
        scheduler_decay_cycles=max(1, int(args.scheduler_decay_cycles)),
        scheduler_decay_factor=max(0.0, float(args.scheduler_decay_factor)),
        grad_clip_norm=float(args.grad_clip_norm),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        optimizer_foreach=bool(args.optimizer_foreach),
        seed=int(args.seed),
        logging_samples=_sample_arg(args.logging_samples, args.logging_steps, config_value=0, batch_size=int(args.batch_size)),
        fast_summary_samples=_sample_arg(args.fast_summary_samples, args.fast_summary_steps, config_value=train_defaults.fast_summary_samples, batch_size=int(args.batch_size)),
        train_metric_window_samples=_sample_arg(args.train_metric_window_samples, args.train_metric_window_steps, config_value=train_defaults.train_metric_window_samples, batch_size=int(args.batch_size)),
        validation_samples=_sample_arg(args.validation_samples, args.validation_steps, config_value=train_defaults.validation_samples, batch_size=int(args.batch_size)),
        validation_batches=int(args.validation_batches),
        checkpoint_latest_samples=_sample_arg(args.checkpoint_latest_samples, args.checkpoint_latest_steps, config_value=train_defaults.checkpoint_latest_samples, batch_size=int(args.batch_size)),
        checkpoint_archive_samples=_sample_arg(args.checkpoint_archive_samples, args.checkpoint_archive_steps, config_value=train_defaults.checkpoint_archive_samples, batch_size=int(args.batch_size)),
        detail_profile_samples=max(0, int(args.detail_profile_samples)),
        progress_layout=str(args.progress_layout),
        loader_telemetry_log_seconds=max(0.0, float(args.loader_telemetry_log_seconds)),
        cache_state_log_seconds=max(0.0, float(args.cache_state_log_seconds)),
        wandb_project=str(args.wandb_project),
        wandb_entity=str(args.wandb_entity),
        wandb_mode=str(args.wandb_mode),
        wandb_init_timeout=int(args.wandb_init_timeout),
        resume_checkpoint=str(args.resume_checkpoint),
        warm_start_checkpoint=str(args.warm_start_checkpoint),
        fresh_start=bool(args.fresh_start),
    )
    return ExperimentConfig(model=model, loader=loader, train=train)


def _sample_arg(sample_value: int, legacy_steps: int, *, config_value: int, batch_size: int) -> int:
    if int(legacy_steps or 0) > 0:
        return max(1, int(legacy_steps) * max(1, int(batch_size)))
    if int(sample_value or 0) > 0:
        return int(sample_value)
    return int(config_value)


def _resolve_sample_limits(config: ExperimentConfig, args: argparse.Namespace) -> None:
    max_samples = max(0, int(config.train.max_samples))
    legacy_steps = max(0, int(getattr(args, "max_steps", 0) or 0))
    if max_samples <= 0 and legacy_steps > 0:
        max_samples = legacy_steps * max(1, int(config.loader.batch_size))
    if max_samples <= 0 and int(config.loader.max_origins_per_epoch) > 0:
        max_samples = int(config.loader.max_origins_per_epoch) * max(1, int(config.train.epochs))
    config.train.max_samples = max(0, int(max_samples))
    if config.train.max_samples > 0 and (
        int(config.loader.max_origins_per_epoch) <= 0 or int(config.loader.max_origins_per_epoch) > int(config.train.max_samples)
    ):
        config.loader.max_origins_per_epoch = int(config.train.max_samples)
    if str(config.train.scheduler) != "none":
        if int(config.train.scheduler_cycle_samples) <= 0 and int(config.train.scheduler_t_max_samples) > 0:
            config.train.scheduler_cycle_samples = int(config.train.scheduler_t_max_samples)
        if int(config.train.scheduler_cycle_samples) <= 0:
            config.train.scheduler_cycle_samples = max(1, int(config.loader.batch_size)) * 1000
        config.train.scheduler_t_max_samples = int(config.train.scheduler_cycle_samples)


def _configure_day_schedules(config: ExperimentConfig) -> None:
    from research.mlops.rolling_loader.daily_index_dataset import DailyIndexCacheIndex

    probe = loader_config_from_v3(config.loader)
    index = DailyIndexCacheIndex(type(probe)(**{**asdict(probe), "days": ()}))
    discovered = sorted({str(plan.source_date)[:10] for plan in index.parts if str(plan.source_date).strip()})
    explicit_train = tuple(str(day)[:10] for day in config.loader.training_days if str(day).strip())
    train_days = [day for day in (explicit_train or tuple(discovered)) if day in set(discovered)]
    if not train_days:
        raise RuntimeError(f"No training days found in cache {config.loader.cache_root}.")
    explicit_val = tuple(str(day)[:10] for day in config.loader.validation_days if str(day).strip())
    if explicit_val:
        validation_days = [day for day in explicit_val if day in set(discovered)]
    else:
        validation_days = _reserve_validation_days(
            train_days,
            policy=str(config.loader.validation_reserve_policy),
            count=int(config.loader.validation_reserve_days),
            seed=int(config.loader.seed),
        )
        train_days = [day for day in train_days if day not in set(validation_days)]
    if not train_days:
        raise RuntimeError("Validation reservation consumed all training days; reduce --validation-reserve-days or pass --validation-days.")
    config.loader.training_days = tuple(train_days)
    config.loader.validation_days = tuple(validation_days)
    config.loader.shuffle_parts = False
    config.loader.shuffle_within_loaded_group = False


def _reserve_validation_days(days: Sequence[str], *, policy: str, count: int, seed: int) -> list[str]:
    days = list(dict.fromkeys(str(day)[:10] for day in days if str(day).strip()))
    count = max(0, min(int(count), len(days) - 1 if len(days) > 1 else 0))
    if count <= 0:
        return []
    if policy == "first_n_days":
        return days[:count]
    if policy == "random_n_days_seeded":
        selected = list(days)
        random.Random(int(seed)).shuffle(selected)
        return sorted(selected[:count])
    return days[-count:]


class SampleCadence:
    def __init__(self) -> None:
        self.last: dict[str, int] = {}

    def due(self, name: str, samples_seen: int, interval: int) -> bool:
        samples_seen = max(0, int(samples_seen))
        interval = int(interval)
        if interval <= 0:
            self.last[name] = samples_seen
            return True
        previous = int(self.last.get(name, 0))
        if samples_seen <= 0 or samples_seen < interval:
            return False
        if previous <= 0 or samples_seen // interval > previous // interval:
            self.last[name] = samples_seen
            return True
        return False

    def checkpoint_reasons(self, samples_seen: int, *, latest: int, archive: int) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.due("checkpoint_latest", samples_seen, latest):
            reasons.append("latest")
        if self.due("checkpoint_archive", samples_seen, archive):
            reasons.append("archive")
        return tuple(reasons)


class LoaderTelemetryJsonlLogger:
    def __init__(self, path: Path, *, interval_seconds: float) -> None:
        self.path = path
        self.interval_seconds = max(0.0, float(interval_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_log = 0.0
        self._lock = threading.Lock()

    def log(self, metrics: Mapping[str, Any], *, step: int, force: bool = False) -> None:
        now = time.perf_counter()
        with self._lock:
            if not force and self.interval_seconds > 0 and now - self._last_log < self.interval_seconds:
                return
            self._last_log = now
            row = {
                "step": int(step),
                "ts": datetime.now().isoformat(timespec="seconds"),
                **dict(metrics),
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _loader_telemetry_provider(
    *,
    train_loader: Any,
    train_iterator: Any,
    validation_loader: Any,
    telemetry_logger: LoaderTelemetryJsonlLogger,
    cache_state_logger: LoaderTelemetryJsonlLogger,
    reporter: TemporalTrainingReporter,
    batch_size: int,
) -> Mapping[str, Any]:
    trainer_phase = reporter.state.trainer_status.get("phase") if hasattr(reporter, "state") else None
    metrics = _scoped_loader_metrics(
        role="train",
        loader=train_loader,
        iterator=train_iterator,
        batch_size=int(batch_size),
        trainer_phase=str(trainer_phase or "idle"),
        batch_profile=_iterator_telemetry(train_iterator),
    )
    metrics.update(
        _scoped_loader_metrics(
            role="validation",
            loader=validation_loader,
            iterator=None,
            batch_size=int(batch_size),
            trainer_phase=str(trainer_phase or "idle"),
        )
    )
    step = int(getattr(reporter.state, "samples_clock", 0) or 0)
    telemetry_logger.log(metrics, step=step)
    cache_state_logger.log({key: value for key, value in metrics.items() if "/cache/state/" in key}, step=step)
    return metrics


def save_checkpoint_reasons(
    checkpointer: AsyncCheckpointManager,
    *,
    step: int,
    reasons: Sequence[str],
    payload_factory: Any,
    train_metrics: dict[str, float],
    val_metrics: dict[str, float],
) -> None:
    destinations: list[tuple[Path, str]] = []
    train_loss = train_metrics.get(checkpointer.policy.monitor_train_key)
    val_loss = val_metrics.get(checkpointer.policy.monitor_val_key)
    if train_loss is not None and checkpointer.policy.save_best_train and float(train_loss) < float(checkpointer.best_train_loss):
        checkpointer.best_train_loss = float(train_loss)
        destinations.append((checkpointer.checkpoint_dir / "checkpoint_best_train.pt", "best_train"))
    if val_loss is not None and checkpointer.policy.save_best_val and float(val_loss) < float(checkpointer.best_val_loss):
        checkpointer.best_val_loss = float(val_loss)
        destinations.append((checkpointer.checkpoint_dir / "checkpoint_best_val.pt", "best_val"))
    if "latest" in reasons:
        destinations.append((checkpointer.checkpoint_dir / "checkpoint_latest.pt", "latest"))
    if "archive" in reasons:
        destinations.append((checkpointer.checkpoint_dir / f"checkpoint_sample_{int(step):012d}.pt", "archive"))
    if not destinations:
        return
    if all(reason == "latest" for _, reason in destinations) and checkpointer.policy.skip_latest_if_busy and checkpointer.jobs.qsize() > 0:
        checkpointer._message(f"Skipped latest checkpoint at {checkpointer.policy.clock_name} {int(step)}; checkpoint writer is still busy.")  # noqa: SLF001
        return
    payload = payload_factory()
    checkpointer._enqueue(  # noqa: SLF001
        to_cpu_payload(payload),
        destinations,
        {
            "step": int(step),
            "samples_seen": int(step),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "train_loss": train_loss,
            "val_loss": val_loss,
        },
    )


class TrainingLedger:
    def __init__(self, state_dir: Path, *, config: ExperimentConfig, train_loader: Any) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "training_ledger_latest.csv"
        self.schedule_path = self.state_dir / "day_schedule.csv"
        self.validation_plan_path = self.state_dir / "validation_plan.csv"
        self.rows: dict[tuple[int, int, str], dict[str, Any]] = {}
        self.current_day_index = 0
        self.current_day_seen = 0
        self.current_day_total = 0
        self.train_days = tuple(config.loader.training_days)
        self._train_day_index = {day: index for index, day in enumerate(self.train_days)}
        self.validation_days = tuple(config.loader.validation_days)
        self.day_totals = self._day_totals(train_loader)
        self.day_count = len(self.train_days)
        self._write_schedule(config)
        self.flush()

    def update_batch(self, batch: TemporalBatch, *, loader_summary: Mapping[str, Any]) -> None:
        epoch = int(loader_summary.get("epoch", 0) or 0)
        keys = np.asarray(batch.identity.get("source_part_key", []), dtype=object)
        if keys.size <= 0:
            return
        day_counts: dict[str, int] = {}
        for key in keys.astype(str, copy=False):
            day = _day_from_part_key(str(key))
            if day:
                day_counts[day] = int(day_counts.get(day, 0)) + 1
        for day, count in day_counts.items():
            schedule_index = int(self._train_day_index.get(day, -1))
            row_key = (epoch, schedule_index, day)
            row = self.rows.setdefault(
                row_key,
                {
                    "epoch_index": epoch,
                    "schedule_index": schedule_index,
                    "day": day,
                    "day_sample_count": int(self.day_totals.get(day, 0)),
                    "visited_samples": 0,
                    "visit_count": 0,
                    "completed": False,
                },
            )
            row["visited_samples"] = int(row.get("visited_samples", 0)) + int(count)
            row["visit_count"] = int(row.get("visit_count", 0)) + 1
            row["completed"] = bool(int(row["day_sample_count"]) > 0 and int(row["visited_samples"]) >= int(row["day_sample_count"]))
            self.current_day_index = max(0, int(schedule_index))
            self.current_day_seen = int(row["visited_samples"])
            self.current_day_total = int(row["day_sample_count"])
        self.flush()

    def flush(self) -> None:
        fields = ("epoch_index", "schedule_index", "day", "day_sample_count", "visited_samples", "visit_count", "completed")
        with self.ledger_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for _, row in sorted(self.rows.items()):
                writer.writerow({field: row.get(field, "") for field in fields})

    def snapshot(self) -> dict[str, Any]:
        return {
            "ledger_path": str(self.ledger_path),
            "schedule_path": str(self.schedule_path),
            "validation_plan_path": str(self.validation_plan_path),
            "rows": [dict(row) for _, row in sorted(self.rows.items())],
        }

    def load_snapshot(self, value: Mapping[str, Any]) -> None:
        self.rows.clear()
        for row in value.get("rows") or ():
            day = str(row.get("day") or "")
            epoch = int(row.get("epoch_index") or 0)
            schedule_index = int(row.get("schedule_index") or -1)
            self.rows[(epoch, schedule_index, day)] = dict(row)
        self.flush()

    def schedule_payload(self) -> dict[str, Any]:
        return {
            "train_days": list(self.train_days),
            "validation_days": list(self.validation_days),
            "day_totals": dict(self.day_totals),
            "schedule_path": str(self.schedule_path),
            "validation_plan_path": str(self.validation_plan_path),
        }

    def _day_totals(self, train_loader: Any) -> dict[str, int]:
        totals: dict[str, int] = {day: 0 for day in self.train_days}
        if train_loader is None:
            return totals
        for plan in train_loader.index.parts:
            day = str(getattr(plan, "source_date", "") or "")[:10]
            if day in totals:
                totals[day] += int(plan.origin_count)
        return totals

    def _write_schedule(self, config: ExperimentConfig) -> None:
        with self.schedule_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("schedule_index", "day", "sample_count"))
            writer.writeheader()
            for index, day in enumerate(self.train_days):
                writer.writerow({"schedule_index": index, "day": day, "sample_count": int(self.day_totals.get(day, 0))})
        with self.validation_plan_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("validation_day", "origins_per_day", "liquid_tickers", "random_ticker_count", "seed"))
            writer.writeheader()
            for day in self.validation_days:
                writer.writerow(
                    {
                        "validation_day": day,
                        "origins_per_day": int(config.loader.validation_origins_per_day),
                        "liquid_tickers": ",".join(config.loader.validation_liquid_tickers),
                        "random_ticker_count": int(config.loader.validation_random_ticker_count),
                        "seed": int(config.loader.seed),
                    }
                )


def _restore_ledger_from_checkpoint(path: Path, ledger: TrainingLedger, device: torch.device) -> None:
    if not path.exists():
        return
    ckpt = torch.load(path, map_location=device, weights_only=False)
    if isinstance(ckpt.get("training_ledger"), Mapping):
        ledger.load_snapshot(ckpt["training_ledger"])


def _day_from_part_key(key: str) -> str:
    for token in str(key).split("|"):
        if len(token) >= 10 and token[4:5] == "-" and token[7:8] == "-":
            return token[:10]
    return ""


def _make_loader(config: LoaderConfig, *, validation: bool, optional: bool = False) -> Any:
    from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader

    try:
        return AsyncDailyIndexBatchLoader(validation_loader_config_from_v3(config) if validation else loader_config_from_v3(config))
    except FileNotFoundError:
        if optional:
            print("Validation cache split was not found; validation will be skipped.", flush=True)
            return None
        raise


def _batch_iterator(config: ExperimentConfig, loader: Any, *, device: torch.device, dummy: bool) -> Iterator[TemporalBatch]:
    prefetch_batches = 0 if dummy else int(config.loader.prefetch_batches)
    if prefetch_batches > 0:
        return _PrefetchingTemporalBatchIterator(config=config, loader=loader, device=device, max_prefetch=prefetch_batches)
    if dummy:
        return _dummy_batch_iterator(config=config, device=device)
    return _synchronous_batch_iterator(config=config, loader=loader, device=device)


def _dummy_batch_iterator(*, config: ExperimentConfig, device: torch.device) -> Iterator[TemporalBatch]:
    while True:
        yield make_dummy_temporal_batch(model_config=config.model, batch_size=config.loader.batch_size, device=device)


def _synchronous_batch_iterator(*, config: ExperimentConfig, loader: Any, device: torch.device) -> Iterator[TemporalBatch]:
    while True:
        assert loader is not None
        for raw in loader.iter_batches():
            yield batch_to_torch(raw, model_config=config.model, device=device)


class _PrefetchingTemporalBatchIterator:
    _SENTINEL = object()

    def __init__(self, *, config: ExperimentConfig, loader: Any, device: torch.device, max_prefetch: int) -> None:
        self.config = config
        self.loader = loader
        self.device = device
        self.max_prefetch = max(1, int(max_prefetch))
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=self.max_prefetch)
        self._stop = threading.Event()
        self._exception: BaseException | None = None
        self._produced_batches = 0
        self._produced_samples = 0
        self._consumed_batches = 0
        self._consumed_samples = 0
        self._last_consumed_loader_state: Mapping[str, Any] | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._produce, name="temporal-v3-raw-batch-prefetch", daemon=True)
        self._thread.start()

    def __iter__(self) -> "_PrefetchingTemporalBatchIterator":
        return self

    def __next__(self) -> TemporalBatch:
        while True:
            if _INTERRUPT_REQUESTED:
                self.close()
                raise KeyboardInterrupt
            try:
                item = self._queue.get(timeout=0.25)
                break
            except queue.Empty:
                if self._exception is not None and self._queue.empty():
                    raise self._exception
                if not self._thread.is_alive() and self._queue.empty():
                    raise StopIteration
        if item is self._SENTINEL:
            if self._exception is not None:
                raise self._exception
            raise StopIteration
        raw = item
        with self._lock:
            self._consumed_batches += 1
            self._consumed_samples += int(raw.sample_count)
            state = raw.profile.get("_loader_state_after_yield")
            if isinstance(state, Mapping):
                self._last_consumed_loader_state = dict(state)
        raw.profile.update(self.telemetry_snapshot())
        return batch_to_torch(raw, model_config=self.config.model, device=self.device)

    def close(self) -> None:
        self._stop.set()
        _cancel_loader(self.loader)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def loader_state_dict(self) -> Mapping[str, Any] | None:
        with self._lock:
            return dict(self._last_consumed_loader_state) if self._last_consumed_loader_state is not None else None

    def telemetry_snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "raw_prefetch_enabled": int(1),
                "raw_prefetch_queue_size": int(self._queue.qsize()),
                "raw_prefetch_queue_limit": int(self.max_prefetch),
                "raw_prefetch_produced_batches": int(self._produced_batches),
                "raw_prefetch_produced_samples": int(self._produced_samples),
                "raw_prefetch_consumed_batches": int(self._consumed_batches),
                "raw_prefetch_consumed_samples": int(self._consumed_samples),
                "raw_prefetch_thread_alive": int(self._thread.is_alive()),
                "raw_prefetch_exception": int(self._exception is not None),
            }

    def _produce(self) -> None:
        try:
            for raw in self.loader.iter_batches():
                if self._stop.is_set():
                    break
                raw.profile["_loader_state_after_yield"] = self.loader.state_dict()
                with self._lock:
                    self._produced_batches += 1
                    self._produced_samples += int(raw.sample_count)
                while not self._stop.is_set():
                    try:
                        self._queue.put(raw, timeout=0.25)
                        break
                    except queue.Full:
                        continue
        except BaseException as exc:  # noqa: BLE001
            self._exception = exc
        finally:
            while not self._stop.is_set():
                try:
                    self._queue.put(self._SENTINEL, timeout=0.25)
                    break
                except queue.Full:
                    continue


def _cancel_loader(loader: Any) -> None:
    if loader is None:
        return
    try:
        close = getattr(loader, "close", None)
        if callable(close):
            close()
            return
        cancel = getattr(loader, "cancel", None)
        if callable(cancel):
            cancel()
    except Exception:
        return


def _cancel_iterator(iterator: Any) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _iterator_telemetry(iterator: Any) -> Mapping[str, Any]:
    snapshot = getattr(iterator, "telemetry_snapshot", None)
    if callable(snapshot):
        return snapshot()
    return {}


def _effective_loader_state(loader: Any, iterator: Any) -> Mapping[str, Any]:
    state_fn = getattr(iterator, "loader_state_dict", None)
    if callable(state_fn):
        state = state_fn()
        if isinstance(state, Mapping):
            return dict(state)
    if loader is not None:
        return loader.state_dict()
    return {}


def _effective_loader_summary(loader: Any, iterator: Any) -> Mapping[str, Any]:
    summary = loader.summary() if loader is not None else {}
    state = _effective_loader_state(loader, iterator)
    if state:
        merged = dict(summary)
        merged.update(state)
        return merged
    return summary


def _restore_if_requested(
    args: argparse.Namespace,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: SampleCosineScheduler | None,
    scaler: torch.amp.GradScaler,
    train_loader: Any,
    validation_loader: Any,
    device: torch.device,
) -> int:
    resume_text = str(args.resume_checkpoint or "").strip()
    if args.fresh_start:
        resume_text = ""
    if not resume_text:
        warm_text = str(args.warm_start_checkpoint or "").strip()
        if warm_text:
            warm = Path(warm_text)
            if not warm.exists():
                raise FileNotFoundError(f"Missing warm-start checkpoint: {warm}")
            ckpt = torch.load(warm, map_location=device, weights_only=False)
            _unwrap_model(model).load_state_dict(ckpt["model"], strict=False)
        return 0
    path = Path(resume_text)
    if not path.exists():
        raise FileNotFoundError(f"Missing resume checkpoint: {path}")
    ckpt = torch.load(path, map_location=device, weights_only=False)
    _unwrap_model(model).load_state_dict(ckpt["model"])
    optimizer.load_state_dict(ckpt["optimizer"])
    samples_seen = int(ckpt.get("samples_seen", ckpt.get("step", 0)) or 0)
    if scheduler is not None:
        if ckpt.get("scheduler") is not None:
            scheduler.load_state_dict(ckpt["scheduler"])
        else:
            scheduler.step(samples_seen)
    if ckpt.get("scaler") and scaler.is_enabled():
        scaler.load_state_dict(ckpt["scaler"])
    restore_checkpoint_rng_state(ckpt.get("rng_state"))
    if train_loader is not None and ckpt.get("train_loader_state"):
        train_loader.load_state_dict(ckpt["train_loader_state"])
    if validation_loader is not None and ckpt.get("validation_loader_state"):
        validation_loader.load_state_dict(ckpt["validation_loader_state"])
    return samples_seen


def checkpoint_rng_state() -> dict[str, Any]:
    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def restore_checkpoint_rng_state(state: dict[str, Any] | None) -> None:
    if not state:
        return
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])
    if torch.cuda.is_available() and state.get("cuda"):
        torch.cuda.set_rng_state_all(state["cuda"])


def set_seed(seed: int) -> None:
    random.seed(int(seed))
    np.random.seed(int(seed))
    torch.manual_seed(int(seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(seed))


def _unwrap_model(model: torch.nn.Module) -> torch.nn.Module:
    return getattr(model, "_orig_mod", model)


def _init_wandb(args: argparse.Namespace, config: ExperimentConfig, paths: RunPaths) -> Any | None:
    return mlops_init_wandb(
        entity=args.wandb_entity,
        project=args.wandb_project,
        run_name=config.train.run_name,
        config=to_dict(config),
        run_dir=paths.wandb_dir,
        mode=args.wandb_mode,
        timeout_seconds=int(args.wandb_init_timeout),
    )


def _write_config(paths: RunPaths, config: ExperimentConfig) -> None:
    paths.run_root.mkdir(parents=True, exist_ok=True)
    (paths.run_root / "config.json").write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")


def _input_contract(config: ModelConfig) -> dict[str, Any]:
    return {
        "raw_event_stream": [None, config.event_stream_length, config.event_feature_count],
        "ticker_intraday_bars": {family: [None, config.intraday_horizons, dim] for family, dim in {"trade": 6, "quote_bid": 9, "quote_ask": 9}.items()},
        "ticker_daily_bars": {family: [None, config.ticker_bar_offsets, dim] for family, dim in {"trade": 6, "quote_bid": 9, "quote_ask": 9}.items()},
        "global_daily_bars": {family: [None, config.global_symbols, config.global_bar_offsets, dim] for family, dim in {"trade": 6, "quote_bid": 9, "quote_ask": 9}.items()},
        "ticker_news_embeddings": [None, config.ticker_news_items, config.ticker_news_chunks, config.text_embedding_dim],
        "market_news_embeddings": [None, config.market_news_items, config.market_news_chunks, config.text_embedding_dim],
        "sec_filing_embeddings": [None, config.sec_filing_items, config.sec_filing_chunks, config.text_embedding_dim],
        "xbrl_value": [None, config.xbrl_max_items],
        "corporate_actions": [None, config.corporate_action_max_items],
        "scanner_leader_values": [None, config.scanner_groups, config.scanner_top_k, config.scanner_horizons, len(BAR_FAMILIES), max(BAR_FEATURE_DIMS.values())],
        "scanner_origin_values": [None, config.scanner_groups, config.scanner_horizons, len(BAR_FAMILIES), max(BAR_FEATURE_DIMS.values())],
    }


def _output_contract(config: ModelConfig) -> dict[str, Any]:
    return {
        "future_bar_values": {family: [None, config.intraday_horizons, dim] for family, dim in {"trade": 6, "quote_bid": 9, "quote_ask": 9}.items()},
        "intraday_logits": [None, config.intraday_horizons],
        "corporate_action_logits": [None, len(config.corporate_action_days)],
    }


def _mean_metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = sorted({key for row in rows for key in row})
    return {key: float(np.mean([row[key] for row in rows if key in row])) for key in keys}


def _amp_dtype(value: str) -> torch.dtype:
    return torch.float16 if value in {"fp16", "float16"} else torch.bfloat16 if value in {"bf16", "bfloat16"} else torch.float32


def _gpu_memory_gib(device: torch.device) -> float:
    if device.type != "cuda":
        return 0.0
    return float(torch.cuda.memory_allocated(device) / (1024**3))


def _rss_gib() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process(os.getpid()).memory_info().rss / (1024**3))
    except Exception:  # noqa: BLE001
        return 0.0


class SampleCosineScheduler:
    """Cosine restart schedule keyed to samples_seen instead of optimizer step count."""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        *,
        cycle_samples: int,
        eta_min: float,
        decay_cycles: int,
        decay_factor: float,
    ) -> None:
        self.optimizer = optimizer
        self.base_lrs = [float(group.get("lr", 0.0)) for group in optimizer.param_groups]
        self.cycle_samples = max(1, int(cycle_samples))
        self.eta_min = max(0.0, float(eta_min))
        self.decay_cycles = max(1, int(decay_cycles))
        self.decay_factor = max(0.0, float(decay_factor))
        self.last_samples_seen = 0
        self.last_progress = 0.0
        self.last_cycle_index = 0
        self.last_cycle_position = 0
        self.last_decay_group = 0
        self.last_peak_lrs = list(self.base_lrs)
        self.last_lrs = list(self.base_lrs)
        self.step(0)

    def step(self, samples_seen: int) -> list[float]:
        self.last_samples_seen = max(0, int(samples_seen))
        self.last_cycle_index = self.last_samples_seen // self.cycle_samples
        self.last_cycle_position = self.last_samples_seen % self.cycle_samples
        self.last_decay_group = self.last_cycle_index // self.decay_cycles
        self.last_progress = min(1.0, self.last_cycle_position / max(1, self.cycle_samples))
        decay_multiplier = self.decay_factor ** self.last_decay_group
        cosine = 0.5 * (1.0 + math.cos(math.pi * self.last_progress))
        lrs: list[float] = []
        peak_lrs: list[float] = []
        for base_lr, group in zip(self.base_lrs, self.optimizer.param_groups):
            eta = min(float(base_lr), self.eta_min)
            peak_lr = max(eta, float(base_lr) * decay_multiplier)
            lr = eta + (peak_lr - eta) * cosine
            group["lr"] = float(lr)
            peak_lrs.append(float(peak_lr))
            lrs.append(float(lr))
        self.last_peak_lrs = peak_lrs
        self.last_lrs = lrs
        return lrs

    def state_dict(self) -> dict[str, Any]:
        return {
            "type": "sample_cosine_restarts",
            "base_lrs": list(self.base_lrs),
            "cycle_samples": int(self.cycle_samples),
            "t_max_samples": int(self.cycle_samples),
            "eta_min": float(self.eta_min),
            "decay_cycles": int(self.decay_cycles),
            "decay_factor": float(self.decay_factor),
            "last_samples_seen": int(self.last_samples_seen),
            "last_progress": float(self.last_progress),
            "last_cycle_index": int(self.last_cycle_index),
            "last_cycle_position": int(self.last_cycle_position),
            "last_decay_group": int(self.last_decay_group),
            "last_peak_lrs": list(self.last_peak_lrs),
            "last_lrs": list(self.last_lrs),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if state.get("base_lrs"):
            base_lrs = [float(value) for value in state.get("base_lrs", [])]
            if len(base_lrs) == len(self.optimizer.param_groups):
                self.base_lrs = base_lrs
        cycle_samples = int(state.get("cycle_samples", state.get("t_max_samples", 0)) or 0)
        if cycle_samples > 0:
            self.cycle_samples = max(1, int(cycle_samples))
        self.eta_min = max(0.0, float(state.get("eta_min", self.eta_min)))
        self.decay_cycles = max(1, int(state.get("decay_cycles", self.decay_cycles) or self.decay_cycles))
        self.decay_factor = max(0.0, float(state.get("decay_factor", self.decay_factor)))
        self.step(int(state.get("last_samples_seen", 0) or 0))

    def metrics(self) -> dict[str, float]:
        return {
            "train/lr_scheduler_progress": float(self.last_progress),
            "train/lr_scheduler_cycle_samples": float(self.cycle_samples),
            "train/lr_scheduler_t_max_samples": float(self.cycle_samples),
            "train/lr_scheduler_eta_min": float(self.eta_min),
            "train/lr_scheduler_cycle_index": float(self.last_cycle_index),
            "train/lr_scheduler_cycle_position_samples": float(self.last_cycle_position),
            "train/lr_scheduler_decay_cycles": float(self.decay_cycles),
            "train/lr_scheduler_decay_factor": float(self.decay_factor),
            "train/lr_scheduler_decay_group": float(self.last_decay_group),
            "train/lr_scheduler_peak_lr": float(self.last_peak_lrs[0]) if self.last_peak_lrs else 0.0,
        }


def build_scheduler(optimizer: torch.optim.Optimizer, train_config: TrainConfig) -> SampleCosineScheduler | None:
    if str(train_config.scheduler) == "none":
        return None
    if str(train_config.scheduler) != "cosine":
        raise ValueError(f"Unsupported scheduler: {train_config.scheduler!r}")
    return SampleCosineScheduler(
        optimizer,
        cycle_samples=max(1, int(train_config.scheduler_cycle_samples or train_config.scheduler_t_max_samples)),
        eta_min=max(0.0, float(train_config.scheduler_eta_min)),
        decay_cycles=max(1, int(train_config.scheduler_decay_cycles)),
        decay_factor=max(0.0, float(train_config.scheduler_decay_factor)),
    )


def maybe_compile_model(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if not enabled:
        return model
    if not hasattr(torch, "compile"):
        print("WARN --compile-model requested, but this PyTorch build does not expose torch.compile.", flush=True)
        return model
    if torch.cuda.is_available() and importlib.util.find_spec("triton") is None:
        print("WARN --compile-model requested, but Triton is unavailable; continuing without torch.compile.", flush=True)
        return model
    print("Compiling model with torch.compile...", flush=True)
    return torch.compile(model)  # type: ignore[return-value]


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


class _NullReporter:
    def __enter__(self) -> "_NullReporter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def message(self, text: str) -> None:
        print(text, flush=True)

    def update(self, metrics: dict[str, float], *, step: int, validation_metrics: dict[str, float] | None = None, record_history: bool = True) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
