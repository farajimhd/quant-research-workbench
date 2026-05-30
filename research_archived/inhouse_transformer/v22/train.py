from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.v22.model_artifacts import save_model_architecture_artifacts  # noqa: E402
from research.inhouse_transformer.v22.metrics import MetricAccumulator, append_jsonl  # noqa: E402
from research.inhouse_transformer.v22.targets import (  # noqa: E402
    binary_magnitude_logits_to_distribution_stats,
    target_values_to_bps,
)
from research.inhouse_transformer.v22.config import DataConfig, ExperimentConfig, ModelConfig, TrainConfig  # noqa: E402
from research.inhouse_transformer.v22.data import (  # noqa: E402
    CHUNK_SUMMARY_COLUMNS,
    QUOTE_FEATURE_COLUMNS,
    TRADE_FEATURE_COLUMNS,
    EventChunkDataset,
    available_sessions,
    count_coverage,
    parse_ticker_list,
    target_bit_count,
    uses_all_tickers,
)

torch = None
DataLoader = None
HierarchicalEventTransformer = None
forecast_loss = None

LOG_RULE = "*" * 96
EXPERIMENT_VERSION = "v22"
DEFAULT_WANDB_PROJECT = "May2026-microstructure-event-language-v22"


def parse_args() -> argparse.Namespace:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description="Train v22 hierarchical quote/trade event-language transformer.")
    parser.add_argument("--flatfiles-root", default=str(defaults.data.flatfiles_root))
    parser.add_argument("--canonical-root", default=str(defaults.data.canonical_root))
    parser.add_argument("--cache-root", default=str(defaults.data.cache_root))
    parser.add_argument("--train-start-date", default=defaults.data.train_start_date)
    parser.add_argument("--train-end-date", default=defaults.data.train_end_date)
    parser.add_argument("--validation-start-date", default=defaults.data.validation_start_date)
    parser.add_argument("--validation-end-date", default=defaults.data.validation_end_date)
    parser.add_argument("--test-start-date", default=defaults.data.test_start_date)
    parser.add_argument("--test-end-date", default=defaults.data.test_end_date)
    parser.add_argument("--chunk-ms", type=int, default=defaults.data.chunk_ms)
    parser.add_argument("--context-seconds", type=int, default=defaults.data.context_seconds)
    parser.add_argument("--horizon-steps", type=int, default=defaults.data.horizon_steps)
    parser.add_argument("--horizon-seconds", type=int, default=defaults.data.horizon_seconds)
    parser.add_argument("--uniform-horizons", action="store_true", help="Use evenly spaced horizon_seconds targets instead of target-cache horizons.")
    parser.add_argument("--origin-stride-chunks", type=int, default=defaults.data.origin_stride_chunks)
    parser.add_argument("--max-quote-events", type=int, default=defaults.data.max_quote_events)
    parser.add_argument("--max-trade-events", type=int, default=defaults.data.max_trade_events)
    parser.add_argument("--max-total-events", type=int, default=defaults.data.max_total_events)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--session-filter-mode", choices=["market_time", "utc_hour"], default=defaults.data.session_filter_mode)
    parser.add_argument("--session-timezone", default=defaults.data.session_timezone)
    parser.add_argument("--session-start-time-market", default=defaults.data.session_start_time_market)
    parser.add_argument("--session-end-time-market", default=defaults.data.session_end_time_market)
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.data.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.data.session_end_hour_utc)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--max-windows-per-ticker-session", type=int, default=defaults.data.max_windows_per_ticker_session)

    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument("--quote-hidden-dim", type=int, default=defaults.model.quote_hidden_dim)
    parser.add_argument("--trade-hidden-dim", type=int, default=defaults.model.trade_hidden_dim)
    parser.add_argument("--local-layers", type=int, default=defaults.model.local_layers)
    parser.add_argument("--global-layers", type=int, default=defaults.model.global_layers)
    parser.add_argument("--num-heads", type=int, default=defaults.model.num_heads)
    parser.add_argument("--ff-dim", type=int, default=defaults.model.ff_dim)
    parser.add_argument("--dropout", type=float, default=defaults.model.dropout)
    parser.add_argument("--direction-threshold-bps", type=float, default=defaults.model.direction_threshold_bps)

    parser.add_argument("--output-root", default=str(defaults.train.output_root))
    parser.add_argument("--output-name", default=defaults.train.output_name)
    parser.add_argument("--batch-size", type=int, default=defaults.train.batch_size)
    parser.add_argument("--epochs", type=int, default=defaults.train.epochs)
    parser.add_argument("--max-steps", type=int, default=defaults.train.max_steps)
    parser.add_argument("--learning-rate", type=float, default=defaults.train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=defaults.train.weight_decay)
    parser.add_argument("--warmup-steps", type=int, default=defaults.train.warmup_steps)
    parser.add_argument("--lr-scheduler", choices=["cosine_warm_restarts", "constant"], default=defaults.train.lr_scheduler)
    parser.add_argument("--cosine-restart-t0-steps", type=int, default=defaults.train.cosine_restart_t0_steps)
    parser.add_argument("--cosine-restart-t-mult", type=int, default=defaults.train.cosine_restart_t_mult)
    parser.add_argument("--min-learning-rate", type=float, default=defaults.train.min_learning_rate)
    parser.add_argument("--grad-clip-norm", type=float, default=defaults.train.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=defaults.train.logging_steps)
    parser.add_argument("--eval-steps", type=int, default=defaults.train.eval_steps)
    parser.add_argument("--validation-window-count", type=int, default=defaults.train.validation_window_count)
    parser.add_argument("--test-window-count", type=int, default=defaults.train.test_window_count)
    parser.add_argument("--num-workers", type=int, default=defaults.train.num_workers)
    parser.add_argument("--prefetch-factor", type=int, default=defaults.train.prefetch_factor)
    parser.add_argument("--seed", type=int, default=defaults.train.seed)
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--checkpoint-policy", choices=["all", "last_only"], default=defaults.train.checkpoint_policy)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--no-resume-latest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--count-coverage", action="store_true")

    parser.add_argument("--wandb-entity", default="")
    parser.add_argument("--wandb-project", default=DEFAULT_WANDB_PROJECT)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-run-id", default="")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    data = DataConfig(
        flatfiles_root=Path(args.flatfiles_root),
        canonical_root=Path(args.canonical_root),
        cache_root=Path(args.cache_root),
        train_start_date=args.train_start_date,
        train_end_date=args.train_end_date,
        validation_start_date=args.validation_start_date,
        validation_end_date=args.validation_end_date,
        test_start_date=args.test_start_date,
        test_end_date=args.test_end_date,
        tickers=parse_ticker_list(args.tickers),
        chunk_ms=args.chunk_ms,
        context_seconds=args.context_seconds,
        horizon_steps=args.horizon_steps,
        horizon_seconds=args.horizon_seconds,
        use_target_cache_horizons=not args.uniform_horizons,
        origin_stride_chunks=args.origin_stride_chunks,
        max_quote_events=args.max_quote_events,
        max_trade_events=args.max_trade_events,
        max_total_events=args.max_total_events,
        session_filter_mode=args.session_filter_mode,
        session_timezone=args.session_timezone,
        session_start_time_market=args.session_start_time_market,
        session_end_time_market=args.session_end_time_market,
        session_start_hour_utc=args.session_start_hour_utc,
        session_end_hour_utc=args.session_end_hour_utc,
        rebuild_cache=args.rebuild_cache,
        max_windows_per_ticker_session=args.max_windows_per_ticker_session,
    )
    model = ModelConfig(
        d_model=args.d_model,
        quote_hidden_dim=args.quote_hidden_dim,
        trade_hidden_dim=args.trade_hidden_dim,
        local_layers=args.local_layers,
        global_layers=args.global_layers,
        num_heads=args.num_heads,
        ff_dim=args.ff_dim,
        dropout=args.dropout,
        target_bit_count=target_bit_count(data),
        direction_threshold_bps=args.direction_threshold_bps,
    )
    train = TrainConfig(
        output_root=Path(args.output_root),
        batch_size=args.batch_size,
        epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        warmup_steps=args.warmup_steps,
        lr_scheduler=args.lr_scheduler,
        cosine_restart_t0_steps=args.cosine_restart_t0_steps,
        cosine_restart_t_mult=args.cosine_restart_t_mult,
        min_learning_rate=args.min_learning_rate,
        grad_clip_norm=args.grad_clip_norm,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        validation_window_count=args.validation_window_count,
        test_window_count=args.test_window_count,
        num_workers=args.num_workers,
        prefetch_factor=args.prefetch_factor,
        seed=args.seed,
        amp=not args.no_amp,
        compile_model=args.compile_model,
        output_name=args.output_name,
        resume_latest=not args.no_resume_latest,
        fresh_start=args.fresh_start,
        checkpoint_policy=args.checkpoint_policy,
    )
    return ExperimentConfig(data=data, model=model, train=train)


def import_torch_modules() -> None:
    global torch, DataLoader, HierarchicalEventTransformer, forecast_loss
    import torch as torch_module
    from torch.utils.data import DataLoader as TorchDataLoader

    from research.inhouse_transformer.v22.model import (
        HierarchicalEventTransformer as TorchHierarchicalEventTransformer,
        forecast_loss as torch_forecast_loss,
    )

    torch = torch_module
    DataLoader = TorchDataLoader
    HierarchicalEventTransformer = TorchHierarchicalEventTransformer
    forecast_loss = torch_forecast_loss


def main() -> None:
    args = parse_args()
    load_dotenv(REPO_ROOT / ".env")
    import_torch_modules()
    config = build_config(args)
    set_seed(config.train.seed)

    train_sessions = available_sessions(config.data.flatfiles_root, config.data.train_start_date, config.data.train_end_date)
    validation_sessions = available_sessions(config.data.flatfiles_root, config.data.validation_start_date, config.data.validation_end_date)
    test_sessions = available_sessions(config.data.flatfiles_root, config.data.test_start_date, config.data.test_end_date)
    tickers = config.data.tickers

    print("Dataset split:")
    print(f"  train: {train_sessions[0]} -> {train_sessions[-1]} ({len(train_sessions)} sessions)")
    print(f"  validation: {validation_sessions[0]} -> {validation_sessions[-1]} ({len(validation_sessions)} sessions)")
    print(f"  test: {test_sessions[0]} -> {test_sessions[-1]} ({len(test_sessions)} sessions)")
    print(f"  tickers={'ALL' if uses_all_tickers(tickers) else ','.join(tickers)}")
    print(
        f"Inputs: context_chunks={config.data.context_chunks} quote={config.data.max_quote_events}x{len(QUOTE_FEATURE_COLUMNS)} "
        f"trade={config.data.max_trade_events}x{len(TRADE_FEATURE_COLUMNS)} total_events={config.data.max_total_events} "
        f"summary={len(CHUNK_SUMMARY_COLUMNS)} target=[{config.data.target_horizon_count}, 1, {target_bit_count(config.data)}]",
        flush=True,
    )
    print(f"Target horizons seconds: {', '.join(f'{value:g}' for value in config.data.target_horizon_seconds)}", flush=True)
    print(f"Flatfiles root: {config.data.flatfiles_root}")
    print(f"Event chunk cache root: {config.data.cache_root}")

    output_dir = resolve_output_dir(config, args)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}", flush=True)
    write_config(output_dir, config, args)

    if args.count_coverage:
        coverage = count_coverage(config=config.data, sessions=train_sessions, tickers=tickers, batch_size=config.train.batch_size)
        print(
            f"Coverage: sessions={coverage.sessions} sessions_with_windows={coverage.sessions_with_windows} "
            f"windows={coverage.windows:,} batches={coverage.batches:,}",
            flush=True,
        )
    if args.dry_run:
        print("Dry run complete.")
        return

    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    model = HierarchicalEventTransformer(
        quote_feature_count=len(QUOTE_FEATURE_COLUMNS),
        trade_feature_count=len(TRADE_FEATURE_COLUMNS),
        chunk_summary_count=len(CHUNK_SUMMARY_COLUMNS),
        context_chunks=config.data.context_chunks,
        max_quote_events=config.data.max_quote_events,
        max_trade_events=config.data.max_trade_events,
        max_total_events=config.data.max_total_events,
        horizon_steps=config.data.target_horizon_count,
        target_count=len(config.data.target_columns),
        config=config.model,
    ).to(device)
    if config.train.compile_model and hasattr(torch, "compile"):
        model = torch.compile(model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
    wandb_run = init_wandb(args, config, output_dir)
    try:
        save_model_architecture_artifacts(
            model=model,
            data_config=config.data,
            output_dir=output_dir,
            version=EXPERIMENT_VERSION,
            torch_module=torch,
            wandb_run=wandb_run,
            summary_batch_size=1,
            graph_depth=3,
        )
    except Exception as exc:
        print(f"*** Model architecture artifact generation skipped: {exc}", flush=True)

    global_step = 0
    if config.train.resume_latest and not config.train.fresh_start:
        global_step = load_latest_checkpoint(output_dir, model, optimizer, scheduler, scaler)

    train_loader = build_loader(
        config=config,
        sessions=train_sessions,
        tickers=tickers,
        mode="train",
        epochs=config.train.epochs,
        max_windows=0,
        shuffle=True,
    )
    metrics_path = output_dir / "metrics.jsonl"
    running_loss = 0.0
    running_bit_acc = 0.0
    running_samples = 0
    running_batches = 0
    last_log_time = time.time()
    try:
        for batch in train_loader:
            global_step += 1
            batch = move_batch_to_device(batch, device)
            model.train()
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, enabled=config.train.amp and device.type == "cuda"):
                prediction = model(
                    batch["quote_values"],
                    batch["trade_values"],
                    batch["event_kinds"],
                    batch["event_indices"],
                    batch["chunk_summary"],
                )
                loss, loss_parts = forecast_loss(prediction, batch["targets"])
            if not torch.isfinite(loss):
                save_checkpoint(output_dir, "nonfinite.pt", model, optimizer, scheduler, scaler, global_step, config)
                raise FloatingPointError(f"Non-finite loss at step {global_step}: {loss_parts}")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
            scaler.step(optimizer)
            scaler.update()
            step_scheduler(scheduler, global_step)

            rows = int(batch["targets"].shape[0])
            running_loss += float(loss.detach().cpu()) * rows
            running_bit_acc += float(loss_parts["bit_accuracy_pct"]) * rows
            running_samples += rows
            running_batches += 1
            if global_step % config.train.logging_steps == 0:
                elapsed = max(1e-6, time.time() - last_log_time)
                row = {
                    "split": "train",
                    "train_step": global_step,
                    "loss": running_loss / max(1, running_samples),
                    "bit_accuracy_pct": running_bit_acc / max(1, running_samples),
                    "lr": current_lr(optimizer),
                    "samples_per_sec": running_samples / elapsed,
                    "batches": running_batches,
                    "windows": running_samples,
                }
                print(format_status("TRAIN", row), flush=True)
                append_jsonl(metrics_path, row)
                log_wandb(wandb_run, "train", row)
                running_loss = 0.0
                running_bit_acc = 0.0
                running_samples = 0
                running_batches = 0
                last_log_time = time.time()
            if global_step % config.train.eval_steps == 0:
                print(LOG_RULE, flush=True)
                print(f"*** VALIDATION START | step={global_step:,}", flush=True)
                validation_metrics = evaluate(model=model, config=config, sessions=validation_sessions, tickers=tickers, device=device, max_windows=config.train.validation_window_count)
                validation_metrics.update({"split": "validation", "train_step": global_step, "lr": current_lr(optimizer)})
                print(format_status("VALIDATION", validation_metrics), flush=True)
                append_jsonl(metrics_path, validation_metrics)
                log_wandb(wandb_run, "validation", validation_metrics)
                print(f"*** VALIDATION END   | step={global_step:,}", flush=True)
                print(LOG_RULE, flush=True)
                save_checkpoint(output_dir, "last.pt", model, optimizer, scheduler, scaler, global_step, config)
            if 0 < config.train.max_steps <= global_step:
                break
    finally:
        save_checkpoint(output_dir, "last.pt", model, optimizer, scheduler, scaler, global_step, config)

    print(LOG_RULE, flush=True)
    print(f"*** TEST START | step={global_step:,}", flush=True)
    test_metrics = evaluate(model=model, config=config, sessions=test_sessions, tickers=tickers, device=device, max_windows=config.train.test_window_count)
    test_metrics.update({"split": "test", "train_step": global_step, "lr": current_lr(optimizer)})
    print(format_status("TEST", test_metrics), flush=True)
    append_jsonl(metrics_path, test_metrics)
    log_wandb(wandb_run, "test", test_metrics)
    print(f"*** TEST END   | step={global_step:,}", flush=True)
    print(LOG_RULE, flush=True)
    save_checkpoint(output_dir, "last.pt", model, optimizer, scheduler, scaler, global_step, config)
    if wandb_run is not None:
        wandb_run.finish()


def evaluate(*, model: Any, config: ExperimentConfig, sessions: list[str], tickers: tuple[str, ...], device: Any, max_windows: int) -> dict[str, Any]:
    loader = build_loader(config=config, sessions=sessions, tickers=tickers, mode="eval", epochs=1, max_windows=max_windows, shuffle=False)
    accumulator = MetricAccumulator(
        horizon=config.data.target_horizon_count,
        target_columns=config.data.target_columns,
        direction_threshold_bps=config.model.direction_threshold_bps,
    )
    total_loss = 0.0
    total_bit_acc = 0.0
    total_windows = 0
    total_batches = 0
    started = time.time()
    was_training = model.training
    model.eval()
    with torch.inference_mode():
        for batch in loader:
            batch = move_batch_to_device(batch, device)
            with torch.autocast(device_type=device.type, enabled=config.train.amp and device.type == "cuda"):
                prediction = model(
                    batch["quote_values"],
                    batch["trade_values"],
                    batch["event_kinds"],
                    batch["event_indices"],
                    batch["chunk_summary"],
                )
                loss, loss_parts = forecast_loss(prediction, batch["targets"])
            rows = int(batch["targets"].shape[0])
            prediction_np = prediction.detach().float().cpu().numpy()
            target_bps = batch["target_bps"].detach().float().cpu().numpy()
            current_mid = batch["current_mid"].detach().float().cpu().numpy()
            last_move = batch["last_close_return_bps"].detach().float().cpu().numpy()
            prediction_bps = target_values_to_bps(
                prediction_np,
                current_mid,
                np.zeros((rows,), dtype=np.float32),
                np.ones((rows,), dtype=np.float32),
                "binary_magnitude_bps",
            )
            stats = binary_magnitude_logits_to_distribution_stats(prediction_np)
            accumulator.update(prediction_bps, target_bps, last_close_return_bps=last_move)
            accumulator.update_confidence(
                expected_signed_bps=stats["expected_signed_bps"],
                target=target_bps,
                confidence=stats["confidence"],
                magnitude_std_bps=stats["magnitude_std_bps"],
                p_up=stats["p_up"],
                sign_confidence=stats["sign_confidence"],
            )
            total_loss += float(loss.detach().cpu()) * rows
            total_bit_acc += float(loss_parts["bit_accuracy_pct"]) * rows
            total_windows += rows
            total_batches += 1
    model.train(was_training)
    metrics = accumulator.compute()
    metrics["loss"] = total_loss / max(1, total_windows)
    metrics["bit_accuracy_pct"] = total_bit_acc / max(1, total_windows)
    metrics["batches"] = total_batches
    metrics["windows_per_sec"] = total_windows / max(1e-6, time.time() - started)
    add_final_aliases(metrics, horizon=config.data.target_horizon_count)
    return metrics


def add_final_aliases(metrics: dict[str, Any], *, horizon: int) -> None:
    for idx in range(1, horizon + 1):
        prefix = f"h{idx}_close"
        if f"{prefix}_expected_signed_mae_bps" in metrics:
            metrics[f"h{idx}_final_mae_bps"] = metrics[f"{prefix}_expected_signed_mae_bps"]
        if f"{prefix}_expected_dir_acc_pct" in metrics:
            metrics[f"h{idx}_final_dir_acc_pct"] = metrics[f"{prefix}_expected_dir_acc_pct"]
        if f"{prefix}_expected_signed_corr" in metrics:
            metrics[f"h{idx}_final_corr"] = metrics[f"{prefix}_expected_signed_corr"]


def build_loader(*, config: ExperimentConfig, sessions: list[str], tickers: tuple[str, ...], mode: str, epochs: int, max_windows: int, shuffle: bool) -> Any:
    dataset = EventChunkDataset(
        config=config.data,
        sessions=sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        seed=config.train.seed + (0 if mode == "train" else 100),
        mode=mode,
        epochs=epochs,
        max_windows=max_windows,
        shuffle=shuffle,
    )
    kwargs: dict[str, Any] = {"batch_size": None, "num_workers": config.train.num_workers if mode == "train" else 0, "pin_memory": True}
    if kwargs["num_workers"] > 0:
        kwargs["prefetch_factor"] = max(1, config.train.prefetch_factor)
        kwargs["persistent_workers"] = True
    return DataLoader(dataset, **kwargs)


def build_scheduler(optimizer: Any, config: TrainConfig) -> Any | None:
    if config.lr_scheduler == "constant":
        return None
    return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer,
        T_0=max(1, config.cosine_restart_t0_steps),
        T_mult=max(1, config.cosine_restart_t_mult),
        eta_min=config.min_learning_rate,
    )


def step_scheduler(scheduler: Any | None, global_step: int) -> None:
    if scheduler is not None:
        scheduler.step(global_step)


def move_batch_to_device(batch: dict[str, Any], device: Any) -> dict[str, Any]:
    moved = dict(batch)
    for key in ("quote_values", "trade_values", "event_kinds", "event_indices", "chunk_summary", "targets", "target_bps", "target_bid", "target_ask", "target_mid", "current_mid", "last_close_return_bps"):
        moved[key] = batch[key].to(device, non_blocking=True)
    return moved


def current_lr(optimizer: Any) -> float:
    return float(optimizer.param_groups[0]["lr"])


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace) -> Path:
    if args.output_name:
        return config.train.output_root / args.output_name
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return config.train.output_root / (
        f"event_language_chunk{config.data.chunk_ms}_ctx{config.data.context_chunks}_"
        f"h{config.data.target_horizon_count}_{horizon_slug(config.data)}_{config.data.train_start_date}_"
        f"{config.data.test_end_date}_{stamp}"
    )


def horizon_slug(config: DataConfig) -> str:
    values = []
    for seconds in config.target_horizon_seconds:
        if seconds >= 60.0 and seconds % 60.0 == 0:
            values.append(f"{int(seconds // 60)}m")
        else:
            values.append(f"{seconds:g}s")
    return "-".join(values)


def write_config(output_dir: Path, config: ExperimentConfig, args: argparse.Namespace) -> None:
    payload = {
        "version": EXPERIMENT_VERSION,
        "args": vars(args),
        "data": dataclass_to_dict(config.data),
        "model": dataclass_to_dict(config.model),
        "train": dataclass_to_dict(config.train),
        "quote_feature_columns": list(QUOTE_FEATURE_COLUMNS),
        "trade_feature_columns": list(TRADE_FEATURE_COLUMNS),
        "chunk_summary_columns": list(CHUNK_SUMMARY_COLUMNS),
    }
    (output_dir / "config.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path) -> Any | None:
    if not args.wandb_project or str(args.wandb_project).lower() in {"none", "disabled", "off"}:
        return None
    try:
        import wandb
    except ModuleNotFoundError:
        print("*** wandb is not installed; metrics will only be written to metrics.jsonl.", flush=True)
        return None
    run_name = args.wandb_run_name or f"{EXPERIMENT_VERSION}-event-language-{config.data.train_start_date}-{config.data.train_end_date}"
    run_id = args.wandb_run_id or stable_run_id(output_dir, run_name)
    print(f"*** WANDB INIT | entity={args.wandb_entity or '(default)'} | project={args.wandb_project} | run={run_name}", flush=True)
    return wandb.init(
        entity=args.wandb_entity or None,
        project=args.wandb_project,
        name=run_name,
        id=run_id,
        resume="allow",
        config={
            "version": EXPERIMENT_VERSION,
            "data": dataclass_to_dict(config.data),
            "model": dataclass_to_dict(config.model),
            "train": dataclass_to_dict(config.train),
        },
    )


def stable_run_id(output_dir: Path, run_name: str) -> str:
    state_path = output_dir / "run_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("wandb_run_id"):
                return str(state["wandb_run_id"])
        except Exception:
            pass
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, str(output_dir.resolve()) + "::" + run_name).hex[:16]
    state_path.write_text(json.dumps({"wandb_run_id": run_id, "wandb_run_name": run_name}, indent=2), encoding="utf-8")
    return run_id


def log_wandb(wandb_run: Any | None, split: str, row: dict[str, Any]) -> None:
    if wandb_run is None:
        return
    payload = {"train_step": row.get("train_step", 0)}
    for key, value in row.items():
        if key in {"split", "train_step"}:
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            payload[f"{split}/{key}"] = value
    wandb_run.log(payload, step=int(row.get("train_step", 0)))


def format_status(label: str, row: dict[str, Any]) -> str:
    keys = ["train_step", "loss", "bit_accuracy_pct", "h1_final_mae_bps", "h1_final_dir_acc_pct", "h6_final_mae_bps", "h6_final_dir_acc_pct", "lr", "samples_per_sec", "windows_per_sec", "windows"]
    parts = [f"{key}={format_value(row[key])}" for key in keys if key in row]
    return f"*** {label} | " + " | ".join(parts)


def format_value(value: Any) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if abs(value) >= 1000:
            return f"{value:,.1f}"
        return f"{value:.6g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def save_checkpoint(output_dir: Path, name: str, model: Any, optimizer: Any, scheduler: Any | None, scaler: Any, global_step: int, config: ExperimentConfig) -> None:
    if config.train.checkpoint_policy == "last_only" and name not in {"last.pt", "nonfinite.pt"}:
        return
    checkpoint = {
        "version": EXPERIMENT_VERSION,
        "global_step": global_step,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
        "scaler_state_dict": scaler.state_dict(),
        "config": {"data": dataclass_to_dict(config.data), "model": dataclass_to_dict(config.model), "train": dataclass_to_dict(config.train)},
    }
    torch.save(checkpoint, output_dir / name)
    state_path = output_dir / "run_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update({"last_checkpoint": name, "global_step": global_step, "updated_at": datetime.now().isoformat()})
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_latest_checkpoint(output_dir: Path, model: Any, optimizer: Any, scheduler: Any | None, scaler: Any) -> int:
    path = output_dir / "last.pt"
    if not path.exists():
        return 0
    checkpoint = torch.load(path, map_location="cpu")
    model.load_state_dict(checkpoint["model_state_dict"])
    optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    if scheduler is not None and checkpoint.get("scheduler_state_dict") is not None:
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
    if checkpoint.get("scaler_state_dict"):
        scaler.load_state_dict(checkpoint["scaler_state_dict"])
    global_step = int(checkpoint.get("global_step", 0))
    print(f"*** RESUME | loaded {path} at step {global_step:,}", flush=True)
    return global_step


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


if __name__ == "__main__":
    main()
