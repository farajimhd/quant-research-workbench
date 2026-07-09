from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
import csv
import traceback
from dataclasses import asdict
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


def _handle_interrupt(signum: int, _frame: Any) -> None:
    global _INTERRUPT_REQUESTED
    _INTERRUPT_REQUESTED = True
    raise KeyboardInterrupt


def _install_interrupt_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)


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
    parser.add_argument("--scanner-index-cache-entries", type=int, default=default_loader.scanner_index_cache_entries)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=default_loader.prefetch_scanner_indexes)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=default_loader.scanner_prefetch_workers)
    parser.add_argument("--d-model", type=int, default=default_model.d_model)
    parser.add_argument("--event-layers", type=int, default=default_model.event_layers)
    parser.add_argument("--event-heads", type=int, default=default_model.event_heads)
    parser.add_argument("--fusion-layers", type=int, default=default_model.fusion_layers)
    parser.add_argument("--fusion-heads", type=int, default=default_model.fusion_heads)
    parser.add_argument("--side-encoder-dim", type=int, default=default_model.side_encoder_dim, help="Hidden width used inside side encoders. 0 means use d_model.")
    parser.add_argument("--dropout", type=float, default=default_model.dropout)
    parser.add_argument("--learning-rate", type=float, default=default_train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=default_train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=default_train.grad_clip_norm)
    parser.add_argument("--seed", type=int, default=default_train.seed)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float16", "bfloat16", "float32"), default=default_train.amp_dtype)
    parser.add_argument("--compile-model", action="store_true")
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
    _write_config(paths, config)
    set_seed(int(config.train.seed))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
    model = TemporalEventModelV3(config.model).to(device)
    if bool(config.train.compile_model):
        model = torch.compile(model)  # type: ignore[assignment]
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.train.learning_rate), weight_decay=float(config.train.weight_decay))
    scaler = torch.amp.GradScaler("cuda", enabled=bool(config.train.amp and config.train.amp_dtype in {"fp16", "float16"} and device.type == "cuda"))
    train_loader = None if args.dummy_data else _make_loader(config.loader, validation=False)
    validation_loader = None if args.dummy_data or args.disable_validation else _make_loader(config.loader, validation=True, optional=True)
    ledger = TrainingLedger(paths.run_root / "state", config=config, train_loader=train_loader)
    start_samples = _restore_if_requested(args, model, optimizer, scaler, train_loader, validation_loader, device)
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
    train_window = MetricWindow(max_batches=32)
    train_iter = _batch_iterator(config, train_loader, device=device, dummy=bool(args.dummy_data))
    val_metrics: dict[str, float] = {}
    cadence = SampleCadence()
    interrupted = False
    exit_code = 0
    try:
        with reporter if reporter is not None else _NullReporter() as active_reporter:
            checkpointer.set_message_callback(active_reporter.message)
            active_reporter.message("Trainer initialized; waiting for first training batch.")
            update_count = int(train_loader.state.emitted_batches if train_loader is not None else 0)
            while True:
                if _INTERRUPT_REQUESTED:
                    raise KeyboardInterrupt
                prior_total_samples = int(train_loader.state.seen_origins_total if train_loader is not None else update_count * int(config.loader.batch_size))
                if int(config.train.max_samples) > 0 and prior_total_samples >= int(config.train.max_samples):
                    break
                if int(config.train.max_samples) <= 0 and train_loader is not None and int(train_loader.state.completed_epochs) >= int(config.train.epochs):
                    break
                update_count += 1
                if update_count == 1:
                    active_reporter.message("Loading and materializing first training batch.")
                batch_start = time.perf_counter()
                loader_start = time.perf_counter()
                batch = next(train_iter)
                loader_wait = time.perf_counter() - loader_start
                optimizer.zero_grad(set_to_none=True)
                amp_dtype = _amp_dtype(config.train.amp_dtype)
                prior_samples_seen = int(prior_total_samples)
                detail_profile_due = update_count == 1 or cadence.due("detail_profile", prior_samples_seen + int(batch.sample_count), int(config.train.detail_profile_samples))
                gpu_start = time.perf_counter()
                with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
                    if detail_profile_due and hasattr(model, "forward_with_timings"):
                        output, model_profile = model.forward_with_timings(batch.x, sync_cuda=device.type == "cuda")  # type: ignore[attr-defined]
                    else:
                        output = model(batch.x)
                        model_profile = {}
                    loss_result = compute_loss(output, batch)
                    loss = loss_result.loss
                if scaler.is_enabled():
                    scaler.scale(loss).backward()
                    if float(config.train.grad_clip_norm) > 0:
                        scaler.unscale_(optimizer)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    if float(config.train.grad_clip_norm) > 0:
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    optimizer.step()
                if device.type == "cuda":
                    torch.cuda.synchronize()
                gpu_seconds = time.perf_counter() - gpu_start
                samples_seen_total = int(train_loader.state.seen_origins_total if train_loader is not None else update_count * int(config.loader.batch_size))
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
                    }
                )
                metrics.update({f"profile/model/{key}_seconds": float(value) for key, value in model_profile.items()})
                if train_loader is not None:
                    summary = train_loader.summary()
                    ledger.update_batch(batch, loader_summary=summary)
                    samples_seen_total = int(summary.get("seen_origins_total", samples_seen_total))
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
                if cadence.due("fast_summary", samples_seen_total, int(config.train.fast_summary_samples)):
                    metrics.update(fast_batch_metrics(batch, output, prefix="train"))
                if cadence.due("train_metric_window", samples_seen_total, int(config.train.train_metric_window_samples)):
                    train_window.add(prediction_metrics(batch, output, prefix="train"))
                    train_window.add(cohort_metrics(batch, output, prefix="train"))
                    metrics.update(train_window.mean())
                metric_logger.log(metrics, samples_seen_total)
                validation_due = cadence.due("validation", samples_seen_total, int(config.train.validation_samples))
                if validation_due and (validation_loader is not None or args.dummy_data):
                    val_metrics = run_validation(model, config, validation_loader, device=device, dummy=bool(args.dummy_data), profile_detail=True)
                    metric_logger.log(val_metrics, samples_seen_total)
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
                        scaler=scaler,
                        config=config,
                        step=step,
                        train_loader=train_loader,
                        validation_loader=validation_loader,
                        ledger=ledger,
                        train_metrics=metrics,
                        val_metrics=val_metrics,
                        run_paths=paths,
                    ),
                        train_metrics=metrics,
                        val_metrics=val_metrics,
                    )
            final_metrics = val_metrics or {}
            final_samples = int(train_loader.state.seen_origins_total if train_loader is not None else update_count * int(config.loader.batch_size))
            checkpointer.maybe_save(
                step=final_samples,
                payload_factory=lambda: checkpoint_payload(model, optimizer, scaler, config, final_samples, train_loader, validation_loader, ledger, {}, final_metrics, paths),
                train_metrics={"train/loss": progress.loss},
                val_metrics=final_metrics,
                force=True,
            )
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
        _cancel_loader(train_loader)
        _cancel_loader(validation_loader)
        samples_seen = int(train_loader.state.seen_origins_total if train_loader is not None else 0)
        message = f"Interrupt received; cancelling training at sample {samples_seen:,}."
        print(message, flush=True)
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "interrupted.txt").write_text(message + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "fatal_error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        raise
    finally:
        _cancel_loader(train_loader)
        _cancel_loader(validation_loader)
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
    iterator = _batch_iterator(config, validation_loader, device=device, dummy=dummy)
    for _ in range(max(1, int(config.train.validation_batches))):
        batch = next(iterator)
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
    model.train()
    return _mean_metrics(metrics)


def checkpoint_payload(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    step: int,
    train_loader: Any,
    validation_loader: Any,
    ledger: "TrainingLedger",
    train_metrics: dict[str, float],
    val_metrics: dict[str, float],
    run_paths: RunPaths,
) -> dict[str, Any]:
    fallback_batches_seen = (int(step) + max(1, int(config.loader.batch_size)) - 1) // max(1, int(config.loader.batch_size))
    model_card = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "sample_clock": int(step),
        "step": int(step),
        "dataset_id": config.loader.dataset_id,
        "cache_root": str(config.loader.cache_root),
        "period": {"start_utc": config.loader.start_utc, "end_utc": config.loader.end_utc, "months": list(config.loader.months)},
        "validation_period": {"start_utc": config.loader.val_start_utc, "end_utc": config.loader.val_end_utc},
        "samples_seen": int(train_loader.state.seen_origins_total if train_loader is not None else step),
        "batches_seen": int(train_loader.state.emitted_batches if train_loader is not None else fallback_batches_seen),
        "loader_state": train_loader.state_dict() if train_loader is not None else {},
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
        "training_schedule": ledger.schedule_payload(),
        "training_ledger": ledger.snapshot(),
        "training_ledger_path": str(ledger.ledger_path),
        "objective": {"loss": "unweighted active-task masked mean", "manual_loss_weights": "disabled_by_default"},
        "data_groups": list(config.loader.data_groups),
        "latest_train_metrics": dict(train_metrics),
        "latest_validation_metrics": dict(val_metrics),
        "run_root": str(run_paths.run_root),
    }
    write_model_card(run_paths.artifacts_dir / "latest_model_card.json", model_card)
    append_checkpoint_model_card(run_paths.artifacts_dir / "model_cards.jsonl", model_card)
    return {
        "model": _unwrap_model(model).state_dict(),
        "optimizer": optimizer.state_dict(),
        "scaler": scaler.state_dict() if scaler.is_enabled() else None,
        "config": to_dict(config),
        "sample_clock": int(step),
        "step": int(step),
        "rng_state": checkpoint_rng_state(),
        "train_loader_state": train_loader.state_dict() if train_loader is not None else {},
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
        "training_schedule": ledger.schedule_payload(),
        "training_ledger": ledger.snapshot(),
        "model_card": model_card,
    }


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    model = ModelConfig(
        d_model=int(args.d_model),
        event_layers=int(args.event_layers),
        event_heads=int(args.event_heads),
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
        grad_clip_norm=float(args.grad_clip_norm),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        seed=int(args.seed),
        logging_samples=_sample_arg(args.logging_samples, args.logging_steps, config_value=0, batch_size=int(args.batch_size)),
        fast_summary_samples=_sample_arg(args.fast_summary_samples, args.fast_summary_steps, config_value=TrainConfig.fast_summary_samples, batch_size=int(args.batch_size)),
        train_metric_window_samples=_sample_arg(args.train_metric_window_samples, args.train_metric_window_steps, config_value=TrainConfig.train_metric_window_samples, batch_size=int(args.batch_size)),
        validation_samples=_sample_arg(args.validation_samples, args.validation_steps, config_value=TrainConfig.validation_samples, batch_size=int(args.batch_size)),
        validation_batches=int(args.validation_batches),
        checkpoint_latest_samples=_sample_arg(args.checkpoint_latest_samples, args.checkpoint_latest_steps, config_value=TrainConfig.checkpoint_latest_samples, batch_size=int(args.batch_size)),
        checkpoint_archive_samples=_sample_arg(args.checkpoint_archive_samples, args.checkpoint_archive_steps, config_value=TrainConfig.checkpoint_archive_samples, batch_size=int(args.batch_size)),
        detail_profile_samples=max(0, int(args.detail_profile_samples)),
        progress_layout=str(args.progress_layout),
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
        if samples_seen <= 0:
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
    if dummy:
        while True:
            yield make_dummy_temporal_batch(model_config=config.model, batch_size=config.loader.batch_size, device=device)
    while True:
        assert loader is not None
        for raw in loader.iter_batches():
            yield batch_to_torch(raw, model_config=config.model, device=device)


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


def _restore_if_requested(args: argparse.Namespace, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scaler: torch.amp.GradScaler, train_loader: Any, validation_loader: Any, device: torch.device) -> int:
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
    if ckpt.get("scaler") and scaler.is_enabled():
        scaler.load_state_dict(ckpt["scaler"])
    restore_checkpoint_rng_state(ckpt.get("rng_state"))
    if train_loader is not None and ckpt.get("train_loader_state"):
        train_loader.load_state_dict(ckpt["train_loader_state"])
    if validation_loader is not None and ckpt.get("validation_loader_state"):
        validation_loader.load_state_dict(ckpt["validation_loader_state"])
    return int(ckpt.get("samples_seen", ckpt.get("step", 0)) or 0)


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


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


class _NullReporter:
    def __enter__(self) -> "_NullReporter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def message(self, text: str) -> None:
        print(text, flush=True)

    def update(self, metrics: dict[str, float], *, step: int, validation_metrics: dict[str, float] | None = None) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
