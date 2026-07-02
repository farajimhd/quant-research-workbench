from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.model_artifacts import append_checkpoint_model_card, parameter_summary, write_model_artifacts, write_model_card
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb
from research.temporal_event_model.v3 import MODEL_FAMILY, MODEL_VERSION
from research.temporal_event_model.v3.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, default_run_name, to_dict
from research.temporal_event_model.v3.data import TemporalBatch, batch_to_torch, loader_config_from_v3, make_dummy_temporal_batch, validation_loader_config_from_v3
from research.temporal_event_model.v3.losses import compute_loss
from research.temporal_event_model.v3.metrics import MetricWindow, fast_batch_metrics, prediction_metrics, wandb_metric_key
from research.temporal_event_model.v3.model import TemporalEventModelV3, build_model_mermaid
from research.temporal_event_model.v3.progress import TemporalProgressState, TemporalTrainingReporter

JOB_TYPE = "train"


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
    parser.add_argument("--max-steps", type=int, default=default_train.max_steps)
    parser.add_argument("--epochs", type=int, default=default_train.epochs)
    parser.add_argument("--max-origins-per-epoch", type=int, default=default_loader.max_origins_per_epoch)
    parser.add_argument("--sample-fraction", type=float, default=1.0)
    parser.add_argument("--sample-hash-modulus", type=int, default=0)
    parser.add_argument("--sample-hash-buckets", default="")
    parser.add_argument("--val-sample-hash-buckets", default="")
    parser.add_argument("--read-workers", type=int, default=default_loader.read_workers)
    parser.add_argument("--materialize-workers", type=int, default=default_loader.materialize_workers)
    parser.add_argument("--loaded-parts-per-group", type=int, default=default_loader.loaded_parts_per_group)
    parser.add_argument("--materialize-chunk-size", type=int, default=default_loader.materialize_chunk_size)
    parser.add_argument("--d-model", type=int, default=default_model.d_model)
    parser.add_argument("--event-layers", type=int, default=default_model.event_layers)
    parser.add_argument("--event-heads", type=int, default=default_model.event_heads)
    parser.add_argument("--fusion-layers", type=int, default=default_model.fusion_layers)
    parser.add_argument("--fusion-heads", type=int, default=default_model.fusion_heads)
    parser.add_argument("--dropout", type=float, default=default_model.dropout)
    parser.add_argument("--learning-rate", type=float, default=default_train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=default_train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=default_train.grad_clip_norm)
    parser.add_argument("--seed", type=int, default=default_train.seed)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float16", "bfloat16", "float32"), default=default_train.amp_dtype)
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--logging-steps", type=int, default=default_train.logging_steps)
    parser.add_argument("--fast-summary-steps", type=int, default=default_train.fast_summary_steps)
    parser.add_argument("--train-metric-window-steps", type=int, default=default_train.train_metric_window_steps)
    parser.add_argument("--validation-steps", type=int, default=default_train.validation_steps)
    parser.add_argument("--validation-batches", type=int, default=default_train.validation_batches)
    parser.add_argument("--disable-validation", action="store_true")
    parser.add_argument("--checkpoint-latest-steps", type=int, default=default_train.checkpoint_latest_steps)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=default_train.checkpoint_archive_steps)
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
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    config = config_from_args(args)
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
    start_step = _restore_if_requested(args, model, optimizer, scaler, train_loader, validation_loader, device)
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
            latest_steps=int(config.train.checkpoint_latest_steps),
            archive_steps=int(config.train.checkpoint_archive_steps),
            save_best_train=bool(config.train.checkpoint_best_train),
            save_best_val=bool(config.train.checkpoint_best_val),
            monitor_train_key="train/loss",
            monitor_val_key="val/loss",
        ),
    )
    progress = TemporalProgressState(
        run_name=run_name,
        dataset_id=config.loader.dataset_id,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(_unwrap_model(model))["total_parameters"]),
    )
    reporter = None if config.train.progress_layout == "none" else TemporalTrainingReporter(layout=config.train.progress_layout, state=progress)
    train_window = MetricWindow(max_batches=32)
    train_iter = _batch_iterator(config, train_loader, device=device, dummy=bool(args.dummy_data))
    val_metrics: dict[str, float] = {}
    try:
        with reporter if reporter is not None else _NullReporter() as active_reporter:
            checkpointer.set_message_callback(active_reporter.message)
            for step in range(int(start_step) + 1, int(config.train.max_steps) + 1):
                step_start = time.perf_counter()
                loader_start = time.perf_counter()
                batch = next(train_iter)
                loader_wait = time.perf_counter() - loader_start
                optimizer.zero_grad(set_to_none=True)
                amp_dtype = _amp_dtype(config.train.amp_dtype)
                gpu_start = time.perf_counter()
                with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
                    output = model(batch.x)
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
                metrics = dict(loss_result.metrics)
                metrics.update(
                    {
                        "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                        "train/step_seconds": time.perf_counter() - step_start,
                        "train/loader_wait_seconds": float(loader_wait),
                        "train/gpu_step_seconds": float(gpu_seconds),
                        "train/samples_per_second": float(batch.sample_count / max(time.perf_counter() - step_start, 1e-9)),
                        "train/samples_seen_total": float(step * int(config.loader.batch_size)),
                        "train/materialize_seconds": float(batch.profile.get("materialize_seconds", 0.0)),
                        "train/gpu_memory_allocated_gib": _gpu_memory_gib(device),
                        "train/cpu_rss_gib": _rss_gib(),
                    }
                )
                if train_loader is not None:
                    summary = train_loader.summary()
                    metrics.update(
                        {
                            "loader/epoch": float(summary.get("epoch", 0)),
                            "loader/package_position": float(summary.get("package_position", 0)),
                            "loader/origin_cursor": float(summary.get("origin_cursor", 0)),
                            "loader/emitted_batches": float(summary.get("emitted_batches", 0)),
                            "loader/emitted_samples": float(summary.get("emitted_samples", 0)),
                            "loader/seen_origins_total": float(summary.get("seen_origins_total", 0)),
                            "loader/seen_origins_this_epoch": float(summary.get("seen_origins_this_epoch", 0)),
                        }
                    )
                if step % int(config.train.fast_summary_steps) == 0:
                    metrics.update(fast_batch_metrics(batch, output, prefix="train"))
                if step % int(config.train.train_metric_window_steps) == 0:
                    train_window.add(prediction_metrics(batch, output, prefix="train"))
                    metrics.update(train_window.mean())
                metric_logger.log(metrics, step)
                if step % int(config.train.validation_steps) == 0 and (validation_loader is not None or args.dummy_data):
                    val_metrics = run_validation(model, config, validation_loader, device=device, dummy=bool(args.dummy_data))
                    metric_logger.log(val_metrics, step)
                if reporter is not None and step % int(config.train.logging_steps) == 0:
                    active_reporter.update(metrics, step=step, validation_metrics=val_metrics)
                checkpointer.maybe_save(
                    step=step,
                    payload_factory=lambda step=step, metrics=metrics, val_metrics=val_metrics: checkpoint_payload(
                        model=model,
                        optimizer=optimizer,
                        scaler=scaler,
                        config=config,
                        step=step,
                        train_loader=train_loader,
                        validation_loader=validation_loader,
                        train_metrics=metrics,
                        val_metrics=val_metrics,
                        run_paths=paths,
                    ),
                    train_metrics=metrics,
                    val_metrics=val_metrics,
                )
            final_metrics = val_metrics or {}
            checkpointer.maybe_save(
                step=int(config.train.max_steps),
                payload_factory=lambda: checkpoint_payload(model, optimizer, scaler, config, int(config.train.max_steps), train_loader, validation_loader, {}, final_metrics, paths),
                train_metrics={"train/loss": progress.loss},
                val_metrics=final_metrics,
                force=True,
            )
    except Exception as exc:  # noqa: BLE001
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "fatal_error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        raise
    finally:
        checkpointer.close()
        if wandb_run is not None:
            wandb_run.finish()
    return 0


@torch.no_grad()
def run_validation(model: torch.nn.Module, config: ExperimentConfig, validation_loader: Any, *, device: torch.device, dummy: bool) -> dict[str, float]:
    if validation_loader is None and not dummy:
        return {}
    model.eval()
    metrics: list[dict[str, float]] = []
    iterator = _batch_iterator(config, validation_loader, device=device, dummy=dummy)
    for _ in range(max(1, int(config.train.validation_batches))):
        batch = next(iterator)
        output = model(batch.x)
        loss = compute_loss(output, batch)
        row = {key.replace("train/", "val/"): value for key, value in loss.metrics.items()}
        row.update(prediction_metrics(batch, output, prefix="val"))
        row.update(fast_batch_metrics(batch, output, prefix="val"))
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
    train_metrics: dict[str, float],
    val_metrics: dict[str, float],
    run_paths: RunPaths,
) -> dict[str, Any]:
    model_card = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "step": int(step),
        "dataset_id": config.loader.dataset_id,
        "cache_root": str(config.loader.cache_root),
        "period": {"start_utc": config.loader.start_utc, "end_utc": config.loader.end_utc, "months": list(config.loader.months)},
        "validation_period": {"start_utc": config.loader.val_start_utc, "end_utc": config.loader.val_end_utc},
        "samples_seen": int(train_loader.state.seen_origins_total if train_loader is not None else step * config.loader.batch_size),
        "batches_seen": int(train_loader.state.emitted_batches if train_loader is not None else step),
        "loader_state": train_loader.state_dict() if train_loader is not None else {},
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
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
        "step": int(step),
        "rng_state": checkpoint_rng_state(),
        "train_loader_state": train_loader.state_dict() if train_loader is not None else {},
        "validation_loader_state": validation_loader.state_dict() if validation_loader is not None else {},
        "model_card": model_card,
    }


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    model = ModelConfig(
        d_model=int(args.d_model),
        event_layers=int(args.event_layers),
        event_heads=int(args.event_heads),
        fusion_layers=int(args.fusion_layers),
        fusion_heads=int(args.fusion_heads),
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
        max_origins_per_epoch=int(args.max_origins_per_epoch),
        sample_fraction=float(args.sample_fraction),
        sample_hash_modulus=int(args.sample_hash_modulus),
        sample_hash_buckets=tuple(int(v) for v in _split_csv(args.sample_hash_buckets)),
        val_sample_hash_buckets=tuple(int(v) for v in _split_csv(args.val_sample_hash_buckets)),
    )
    train = TrainConfig(
        run_name=str(args.run_name),
        output_root=Path(args.output_root),
        max_steps=int(args.max_steps),
        epochs=int(args.epochs),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        grad_clip_norm=float(args.grad_clip_norm),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        seed=int(args.seed),
        logging_steps=int(args.logging_steps),
        fast_summary_steps=int(args.fast_summary_steps),
        train_metric_window_steps=int(args.train_metric_window_steps),
        validation_steps=int(args.validation_steps),
        validation_batches=int(args.validation_batches),
        checkpoint_latest_steps=int(args.checkpoint_latest_steps),
        checkpoint_archive_steps=int(args.checkpoint_archive_steps),
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


def _make_loader(config: LoaderConfig, *, validation: bool, optional: bool = False) -> Any:
    from research.mlops.rolling_loader.ticker_month_dataset import AsyncTickerMonthBatchLoader

    try:
        return AsyncTickerMonthBatchLoader(validation_loader_config_from_v3(config) if validation else loader_config_from_v3(config))
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
    return int(ckpt.get("step", 0))


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
