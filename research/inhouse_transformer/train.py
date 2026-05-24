from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.config import (  # noqa: E402
    DataConfig,
    ExperimentConfig,
    ModelConfig,
    TrainConfig,
)
from research.inhouse_transformer.data import (  # noqa: E402
    RollingBarWindowDataset,
    available_sessions,
    count_coverage,
    parse_ticker_list,
    resolve_end_date,
    select_top_tickers,
)
from research.inhouse_transformer.metrics import MetricAccumulator, append_jsonl  # noqa: E402

torch = None
DataLoader = None
FeatureTemporalTransformer = None
forecast_loss = None


def parse_args() -> argparse.Namespace:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Train an in-house feature/time transformer baseline on provider-built 1m bars. "
            "Defaults use train sessions through 2025, validation in Jan-Feb 2026, and test after Mar 2026."
        )
    )
    parser.add_argument("--processed-root", default=str(defaults.data.processed_root))
    parser.add_argument("--train-start-date", default=defaults.data.train_start_date)
    parser.add_argument("--train-end-date", default=defaults.data.train_end_date)
    parser.add_argument("--validation-start-date", default=defaults.data.validation_start_date)
    parser.add_argument("--validation-end-date", default=defaults.data.validation_end_date)
    parser.add_argument("--test-start-date", default=defaults.data.test_start_date)
    parser.add_argument("--test-end-date", default=defaults.data.test_end_date)
    parser.add_argument("--session-scope", choices=["all", "regular"], default=defaults.data.session_scope)
    parser.add_argument("--context-length", type=int, default=defaults.data.context_length)
    parser.add_argument("--horizon", type=int, default=defaults.data.horizon)
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument("--max-tickers", type=int, default=defaults.data.max_tickers)
    parser.add_argument("--allow-target-across-session", action="store_true")
    parser.add_argument("--no-carry-context-across-session", action="store_true")

    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument("--feature-attention-layers", type=int, default=defaults.model.feature_attention_layers)
    parser.add_argument("--temporal-layers", type=int, default=defaults.model.temporal_layers)
    parser.add_argument("--num-heads", type=int, default=defaults.model.num_heads)
    parser.add_argument("--ff-dim", type=int, default=defaults.model.ff_dim)
    parser.add_argument("--dropout", type=float, default=defaults.model.dropout)
    parser.add_argument("--direction-loss-weight", type=float, default=defaults.model.direction_loss_weight)
    parser.add_argument("--direction-threshold-bps", type=float, default=defaults.model.direction_threshold_bps)

    parser.add_argument("--batch-size", type=int, default=defaults.train.batch_size)
    parser.add_argument("--epochs", type=int, default=defaults.train.epochs)
    parser.add_argument("--max-steps", type=int, default=defaults.train.max_steps)
    parser.add_argument("--learning-rate", type=float, default=defaults.train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=defaults.train.weight_decay)
    parser.add_argument("--warmup-steps", type=int, default=defaults.train.warmup_steps)
    parser.add_argument("--grad-clip-norm", type=float, default=defaults.train.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=defaults.train.logging_steps)
    parser.add_argument("--eval-steps", type=int, default=defaults.train.eval_steps)
    parser.add_argument("--validation-window-count", type=int, default=defaults.train.validation_window_count)
    parser.add_argument("--test-window-count", type=int, default=defaults.train.test_window_count)
    parser.add_argument(
        "--max-batches-per-session",
        type=int,
        default=defaults.train.max_batches_per_session,
        help="Optional cap for quick experiments. 0 means use all eligible windows.",
    )
    parser.add_argument("--num-workers", type=int, default=defaults.train.num_workers)
    parser.add_argument("--seed", type=int, default=defaults.train.seed)
    parser.set_defaults(amp=defaults.train.amp)
    parser.add_argument("--amp", dest="amp", action="store_true")
    parser.add_argument("--no-amp", dest="amp", action="store_false")
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--output-name", default=defaults.train.output_name)
    parser.add_argument("--resume-latest", action="store_true")
    parser.add_argument("--device", default="cuda", help='Use "cuda" when available, otherwise "cpu".')
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    processed_root = Path(args.processed_root)
    data = DataConfig(
        processed_root=processed_root,
        train_start_date=args.train_start_date,
        train_end_date=args.train_end_date,
        validation_start_date=args.validation_start_date,
        validation_end_date=args.validation_end_date,
        test_start_date=args.test_start_date,
        test_end_date=resolve_end_date(processed_root, args.test_end_date),
        session_scope=args.session_scope,
        context_length=args.context_length,
        horizon=args.horizon,
        tickers=parse_ticker_list(args.tickers),
        max_tickers=args.max_tickers,
        allow_target_across_session=bool(args.allow_target_across_session),
        carry_context_across_session=not bool(args.no_carry_context_across_session),
    )
    model = ModelConfig(
        d_model=args.d_model,
        feature_attention_layers=args.feature_attention_layers,
        temporal_layers=args.temporal_layers,
        num_heads=args.num_heads,
        ff_dim=args.ff_dim,
        dropout=args.dropout,
        direction_loss_weight=args.direction_loss_weight,
        direction_threshold_bps=args.direction_threshold_bps,
    )
    train = TrainConfig(
        batch_size=args.batch_size,
        epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        warmup_steps=args.warmup_steps,
        grad_clip_norm=args.grad_clip_norm,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        validation_window_count=args.validation_window_count,
        test_window_count=args.test_window_count,
        max_batches_per_session=args.max_batches_per_session,
        num_workers=args.num_workers,
        seed=args.seed,
        amp=args.amp,
        compile_model=args.compile_model,
        output_name=args.output_name,
        resume_latest=args.resume_latest,
    )
    return ExperimentConfig(data=data, model=model, train=train)


def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    set_seed(config.train.seed)
    train_sessions = available_sessions(
        config.data.processed_root, config.data.train_start_date, config.data.train_end_date
    )
    validation_sessions = available_sessions(
        config.data.processed_root, config.data.validation_start_date, config.data.validation_end_date
    )
    test_sessions = available_sessions(
        config.data.processed_root, config.data.test_start_date, config.data.test_end_date
    )
    tickers = config.data.tickers or select_top_tickers(
        config.data.processed_root, train_sessions, config.data.max_tickers
    )

    output_dir = make_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    metadata = metadata_payload(config, train_sessions, validation_sessions, test_sessions, tickers, output_dir)
    write_json(output_dir / "metadata.json", metadata)

    print_split_summary(metadata)
    print(
        f"Features={len(config.data.input_feature_columns)} time_features={len(config.data.time_feature_columns)} "
        f"targets={list(config.data.target_columns)} horizon={config.data.horizon}",
        flush=True,
    )
    print(f"Output directory: {output_dir}", flush=True)

    coverage = count_coverage(
        config=config.data,
        sessions=train_sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        max_batches_per_session=config.train.max_batches_per_session,
    )
    planned_steps = config.train.max_steps or max(1, coverage.batches * config.train.epochs)
    print(
        f"Training plan: windows={coverage.windows:,} batches_per_epoch={coverage.batches:,} "
        f"epochs={config.train.epochs} max_steps={planned_steps:,}",
        flush=True,
    )
    if args.dry_run:
        print("Dry run complete after data split, ticker selection, and coverage count.", flush=True)
        return

    load_torch_stack()
    set_seed(config.train.seed)
    device = resolve_device(args.device)
    model = FeatureTemporalTransformer(
        feature_count=len(config.data.input_feature_columns),
        time_feature_count=len(config.data.time_feature_columns),
        context_length=config.data.context_length,
        horizon=config.data.horizon,
        target_count=len(config.data.target_columns),
        config=config.model,
    ).to(device)
    if config.train.compile_model:
        model = torch.compile(model)

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.train.learning_rate,
        weight_decay=config.train.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lr_lambda=lambda step: lr_multiplier(step, config.train.warmup_steps, planned_steps),
    )
    scaler = (
        torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
        if hasattr(torch, "amp")
        else torch.cuda.amp.GradScaler(enabled=config.train.amp and device.type == "cuda")
    )
    start_step, best_score = maybe_resume(model, optimizer, scheduler, output_dir, config.train.resume_latest, device)

    train_dataset = RollingBarWindowDataset(
        config=config.data,
        sessions=train_sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        seed=config.train.seed,
        mode="train",
        epochs=config.train.epochs,
        max_batches_per_session=config.train.max_batches_per_session,
        shuffle=True,
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=None,
        num_workers=config.train.num_workers,
        pin_memory=device.type == "cuda",
    )

    running_loss = 0.0
    running_regression = 0.0
    running_direction = 0.0
    running_batches = 0
    step = start_step
    last_log_time = time.perf_counter()
    for batch in train_loader:
        if step >= planned_steps:
            break
        step += 1
        model.train()
        batch = move_batch(batch, device)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=config.train.amp and device.type == "cuda"):
            prediction, direction_logits = model(batch["values"], batch["time_features"])
            loss, loss_parts = forecast_loss(
                prediction,
                batch["targets"],
                direction_logits,
                batch["direction"],
                config.model.direction_loss_weight,
            )
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
        scaler.step(optimizer)
        scaler.update()
        scheduler.step()

        running_loss += loss_parts["loss"]
        running_regression += loss_parts["regression_loss"]
        running_direction += loss_parts["direction_loss"]
        running_batches += 1

        if step == 1 or step % config.train.logging_steps == 0:
            elapsed = max(1e-6, time.perf_counter() - last_log_time)
            avg_loss = running_loss / max(1, running_batches)
            avg_regression = running_regression / max(1, running_batches)
            avg_direction = running_direction / max(1, running_batches)
            samples_per_sec = config.train.batch_size * running_batches / elapsed
            lr = optimizer.param_groups[0]["lr"]
            print(
                f"train step={step:,}/{planned_steps:,} loss={avg_loss:.6f} "
                f"reg={avg_regression:.6f} dir={avg_direction:.6f} "
                f"lr={lr:.3e} samples_s={samples_per_sec:,.0f}",
                flush=True,
            )
            append_jsonl(
                metrics_path,
                {
                    "type": "train",
                    "step": step,
                    "loss": avg_loss,
                    "regression_loss": avg_regression,
                    "direction_loss": avg_direction,
                    "lr": lr,
                    "samples_per_sec": samples_per_sec,
                    "time": datetime.now().isoformat(timespec="seconds"),
                },
            )
            running_loss = running_regression = running_direction = 0.0
            running_batches = 0
            last_log_time = time.perf_counter()

        if step % config.train.eval_steps == 0 or step == planned_steps:
            validation_metrics = evaluate(
                model=model,
                config=config,
                sessions=validation_sessions,
                tickers=tickers,
                device=device,
                max_windows=config.train.validation_window_count,
                label="validation",
            )
            validation_metrics.update({"type": "validation", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
            append_jsonl(metrics_path, validation_metrics)
            print_metric_line(validation_metrics)
            score = validation_metrics.get("validation_h1_close_mae_bps", math.inf)
            if score < best_score:
                best_score = float(score)
                save_checkpoint(output_dir / "best.pt", model, optimizer, scheduler, step, best_score, config)
                print(f"Saved best checkpoint at step={step:,} h1_close_mae_bps={best_score:.4f}", flush=True)
            save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)

    test_metrics = evaluate(
        model=model,
        config=config,
        sessions=test_sessions,
        tickers=tickers,
        device=device,
        max_windows=config.train.test_window_count,
        label="test",
    )
    test_metrics.update({"type": "test", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
    append_jsonl(metrics_path, test_metrics)
    print_metric_line(test_metrics)
    save_checkpoint(output_dir / "last.pt", model, optimizer, scheduler, step, best_score, config)
    print(f"Training complete. Checkpoints and metrics are in {output_dir}", flush=True)


def evaluate(
    *,
    model: torch.nn.Module,
    config: ExperimentConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    device: torch.device,
    max_windows: int,
    label: str,
) -> dict[str, Any]:
    assert torch is not None
    model.eval()
    dataset = RollingBarWindowDataset(
        config=config.data,
        sessions=sessions,
        tickers=tickers,
        batch_size=config.train.batch_size,
        seed=config.train.seed + 100,
        mode=label,
        max_windows=max_windows,
        shuffle=False,
    )
    loader = DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=device.type == "cuda")
    accumulator = MetricAccumulator(
        horizon=config.data.horizon,
        target_columns=config.data.target_columns,
        direction_threshold_bps=config.model.direction_threshold_bps,
    )
    loss_sum = 0.0
    batches = 0
    with torch.no_grad():
        for batch in loader:
            batch = move_batch(batch, device)
            prediction, direction_logits = model(batch["values"], batch["time_features"])
            loss, _ = forecast_loss(
                prediction,
                batch["targets"],
                direction_logits,
                batch["direction"],
                config.model.direction_loss_weight,
            )
            loss_sum += float(loss.detach().cpu())
            batches += 1
            accumulator.update(prediction.detach().cpu().numpy(), batch["targets"].detach().cpu().numpy())
    metrics = accumulator.compute(prefix=f"{label}_")
    metrics[f"{label}_loss"] = loss_sum / max(1, batches)
    metrics[f"{label}_batches"] = batches
    return metrics


def print_metric_line(metrics: dict[str, Any]) -> None:
    label = str(metrics["type"])
    step = metrics.get("step", 0)
    parts = [
        f"{label} step={step:,}",
        f"loss={metrics.get(f'{label}_loss', 0.0):.6f}",
        f"windows={metrics.get(f'{label}_windows', 0):,}",
    ]
    for horizon in range(1, 4):
        mae_key = f"{label}_h{horizon}_close_mae_bps"
        dir_key = f"{label}_h{horizon}_close_dir_acc_pct"
        edge_key = f"{label}_h{horizon}_close_edge_vs_naive_bps"
        if mae_key in metrics:
            parts.append(
                f"h{horizon}_mae={metrics[mae_key]:.3f}bps "
                f"h{horizon}_dir={metrics[dir_key]:.2f}% "
                f"h{horizon}_edge={metrics[edge_key]:.3f}bps"
            )
    print(" | ".join(parts), flush=True)


def resolve_device(requested: str) -> torch.device:
    assert torch is not None
    if requested == "cuda" and not torch.cuda.is_available():
        print("CUDA requested but unavailable; using CPU.", flush=True)
        return torch.device("cpu")
    return torch.device(requested)


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


def lr_multiplier(step: int, warmup_steps: int, total_steps: int) -> float:
    if warmup_steps > 0 and step < warmup_steps:
        return max(1e-4, float(step + 1) / float(warmup_steps))
    if total_steps <= warmup_steps:
        return 1.0
    progress = min(1.0, (step - warmup_steps) / float(total_steps - warmup_steps))
    return 0.1 + 0.9 * 0.5 * (1.0 + math.cos(math.pi * progress))


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def make_output_dir(config: ExperimentConfig) -> Path:
    root = config.data.processed_root / "models" / "inhouse_transformer"
    if config.train.output_name:
        return root / config.train.output_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = (
        f"feature_temporal_ctx{config.data.context_length}_h{config.data.horizon}_"
        f"{config.data.train_start_date}_{config.data.test_end_date}_{timestamp}"
    )
    return root / name


def save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    step: int,
    best_score: float,
    config: ExperimentConfig,
) -> None:
    assert torch is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "step": step,
            "best_score": best_score,
            "config": config_to_dict(config),
        },
        path,
    )


def maybe_resume(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    output_dir: Path,
    resume_latest: bool,
    device: torch.device,
) -> tuple[int, float]:
    assert torch is not None
    if not resume_latest:
        return 0, math.inf
    checkpoint_path = output_dir / "last.pt"
    if not checkpoint_path.exists():
        print(f"--resume-latest requested but no checkpoint found at {checkpoint_path}; starting fresh.", flush=True)
        return 0, math.inf
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    scheduler.load_state_dict(checkpoint["scheduler"])
    step = int(checkpoint.get("step") or 0)
    best_score = float(checkpoint.get("best_score") or math.inf)
    print(f"Resumed checkpoint {checkpoint_path} at step={step:,} best_score={best_score:.4f}", flush=True)
    return step, best_score


def metadata_payload(
    config: ExperimentConfig,
    train_sessions: list[str],
    validation_sessions: list[str],
    test_sessions: list[str],
    tickers: tuple[str, ...],
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "data": {
            **config_to_dict(config)["data"],
            "train_sessions": len(train_sessions),
            "train_actual_start": train_sessions[0],
            "train_actual_end": train_sessions[-1],
            "validation_sessions": len(validation_sessions),
            "validation_actual_start": validation_sessions[0],
            "validation_actual_end": validation_sessions[-1],
            "test_sessions": len(test_sessions),
            "test_actual_start": test_sessions[0],
            "test_actual_end": test_sessions[-1],
            "selected_tickers": len(tickers) if tickers else "all",
        },
        "model": config_to_dict(config)["model"],
        "train": config_to_dict(config)["train"],
    }


def print_split_summary(metadata: dict[str, Any]) -> None:
    data = metadata["data"]
    print("Dataset split:", flush=True)
    print(
        f"  train: {data['train_actual_start']} -> {data['train_actual_end']} "
        f"({data['train_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  validation: {data['validation_actual_start']} -> {data['validation_actual_end']} "
        f"({data['validation_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  test: {data['test_actual_start']} -> {data['test_actual_end']} "
        f"({data['test_sessions']} sessions)",
        flush=True,
    )
    print(
        f"  selected_tickers: {data['selected_tickers']} "
        f"carry_context_across_session={data['carry_context_across_session']} "
        f"allow_target_across_session={data['allow_target_across_session']}",
        flush=True,
    )


def config_to_dict(config: ExperimentConfig) -> dict[str, Any]:
    return {
        "data": {
            "processed_root": str(config.data.processed_root),
            "train_start_date": config.data.train_start_date,
            "train_end_date": config.data.train_end_date,
            "validation_start_date": config.data.validation_start_date,
            "validation_end_date": config.data.validation_end_date,
            "test_start_date": config.data.test_start_date,
            "test_end_date": config.data.test_end_date,
            "timeframe": config.data.timeframe,
            "session_scope": config.data.session_scope,
            "context_length": config.data.context_length,
            "horizon": config.data.horizon,
            "target_columns": list(config.data.target_columns),
            "input_feature_columns": list(config.data.input_feature_columns),
            "time_feature_columns": list(config.data.time_feature_columns),
            "tickers": list(config.data.tickers),
            "max_tickers": config.data.max_tickers,
            "allow_target_across_session": config.data.allow_target_across_session,
            "carry_context_across_session": config.data.carry_context_across_session,
        },
        "model": {
            "d_model": config.model.d_model,
            "feature_attention_layers": config.model.feature_attention_layers,
            "temporal_layers": config.model.temporal_layers,
            "num_heads": config.model.num_heads,
            "ff_dim": config.model.ff_dim,
            "dropout": config.model.dropout,
            "direction_loss_weight": config.model.direction_loss_weight,
            "direction_threshold_bps": config.model.direction_threshold_bps,
        },
        "train": {
            "batch_size": config.train.batch_size,
            "epochs": config.train.epochs,
            "max_steps": config.train.max_steps,
            "learning_rate": config.train.learning_rate,
            "weight_decay": config.train.weight_decay,
            "warmup_steps": config.train.warmup_steps,
            "grad_clip_norm": config.train.grad_clip_norm,
            "logging_steps": config.train.logging_steps,
            "eval_steps": config.train.eval_steps,
            "validation_window_count": config.train.validation_window_count,
            "test_window_count": config.train.test_window_count,
            "max_batches_per_session": config.train.max_batches_per_session,
            "num_workers": config.train.num_workers,
            "seed": config.train.seed,
            "amp": config.train.amp,
            "compile_model": config.train.compile_model,
            "output_name": config.train.output_name,
            "resume_latest": config.train.resume_latest,
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_torch_stack() -> None:
    global DataLoader, FeatureTemporalTransformer, forecast_loss, torch
    if torch is not None:
        return
    try:
        import torch as torch_module
        from torch.utils.data import DataLoader as data_loader_class

        from research.inhouse_transformer.model import (
            FeatureTemporalTransformer as transformer_class,
            forecast_loss as loss_function,
        )
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyTorch is required for training. Activate the training environment first, "
            "for example your ml4t environment, then rerun this script."
        ) from exc
    torch = torch_module
    DataLoader = data_loader_class
    FeatureTemporalTransformer = transformer_class
    forecast_loss = loss_function


if __name__ == "__main__":
    main()
