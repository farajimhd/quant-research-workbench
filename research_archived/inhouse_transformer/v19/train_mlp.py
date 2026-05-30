from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch
from torch.utils.data import DataLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.v19.config import DataConfig, TrainConfig  # noqa: E402
from research.inhouse_transformer.v19.data import (  # noqa: E402
    RollingBarWindowDataset,
    available_sessions,
    parse_ticker_list,
    resolve_end_date,
    select_top_tickers,
)
from research.inhouse_transformer.v19.metrics import MetricAccumulator, append_jsonl  # noqa: E402
from research.inhouse_transformer.v19.model_mlp import FlatMLPForecaster  # noqa: E402


LOG_RULE = "*" * 96
DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE = 1024


def parse_args() -> argparse.Namespace:
    data_defaults = DataConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Train a simple flattened-window MLP baseline on the same 1m bar windows used by the transformer. "
            "Use --overfit-window-count to repeat a fixed train sample and verify the model can minimize train loss."
        )
    )
    parser.add_argument("--processed-root", default=str(data_defaults.processed_root))
    parser.add_argument("--train-start-date", default=data_defaults.train_start_date)
    parser.add_argument("--train-end-date", default=data_defaults.train_end_date)
    parser.add_argument("--validation-start-date", default=data_defaults.validation_start_date)
    parser.add_argument("--validation-end-date", default=data_defaults.validation_end_date)
    parser.add_argument("--test-start-date", default=data_defaults.test_start_date)
    parser.add_argument("--test-end-date", default=data_defaults.test_end_date)
    parser.add_argument("--session-scope", choices=["all", "regular"], default=data_defaults.session_scope)
    parser.add_argument("--context-length", type=int, default=data_defaults.context_length)
    parser.add_argument("--horizon", type=int, default=data_defaults.horizon)
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument("--max-tickers", type=int, default=data_defaults.max_tickers)
    parser.add_argument("--allow-target-across-session", action="store_true")
    parser.add_argument("--no-carry-context-across-session", action="store_true")

    parser.add_argument("--hidden-dim", type=int, default=512)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.0)

    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--epochs", type=int, default=train_defaults.epochs)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=25)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--validation-window-count", type=int, default=5000)
    parser.add_argument("--test-window-count", type=int, default=10000)
    parser.add_argument("--max-batches-per-session", type=int, default=0)
    parser.add_argument(
        "--overfit-batches",
        type=int,
        default=0,
        help=(
            "Deprecated compatibility option. Converted to a fixed window count using reference batch size "
            f"{DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--overfit-window-count",
        type=int,
        default=0,
        help="Fixed number of train windows to cache for overfit tests. 0 disables overfit cache.",
    )
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.set_defaults(amp=train_defaults.amp)
    parser.add_argument("--amp", dest="amp", action="store_true")
    parser.add_argument("--no-amp", dest="amp", action="store_false")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output-name", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    data_config = build_data_config(args)
    train_sessions = available_sessions(data_config.processed_root, data_config.train_start_date, data_config.train_end_date)
    validation_sessions = available_sessions(
        data_config.processed_root,
        data_config.validation_start_date,
        data_config.validation_end_date,
    )
    test_sessions = available_sessions(data_config.processed_root, data_config.test_start_date, data_config.test_end_date)
    tickers = data_config.tickers or select_top_tickers(data_config.processed_root, train_sessions, data_config.max_tickers)
    output_dir = make_output_dir(data_config, args)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    overfit_window_count = resolve_overfit_window_count(args)
    metadata = metadata_payload(args, data_config, train_sessions, validation_sessions, test_sessions, tickers, output_dir)
    write_json(output_dir / "metadata.json", metadata)
    print_split_summary(metadata)
    print(
        f"MLP input: context={data_config.context_length} features={len(data_config.input_feature_columns)} "
        f"time_features={len(data_config.time_feature_columns)} target={data_config.horizon}x{len(data_config.target_columns)}",
        flush=True,
    )
    print(
        f"MLP model: hidden_dim={args.hidden_dim} layers={args.layers} dropout={args.dropout} "
        f"overfit_window_count={overfit_window_count}",
        flush=True,
    )
    print(f"Output directory: {output_dir}", flush=True)
    if args.dry_run:
        print("*** Dry run complete after split and ticker selection.", flush=True)
        return

    device = resolve_device(args.device)
    model = FlatMLPForecaster(
        context_length=data_config.context_length,
        feature_count=len(data_config.input_feature_columns),
        time_feature_count=len(data_config.time_feature_columns),
        horizon=data_config.horizon,
        target_count=len(data_config.target_columns),
        hidden_dim=args.hidden_dim,
        layers=args.layers,
        dropout=args.dropout,
    ).to(device)
    print(f"MLP parameters: {sum(p.numel() for p in model.parameters()):,}", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=args.amp and device.type == "cuda")

    train_dataset = RollingBarWindowDataset(
        config=data_config,
        sessions=train_sessions,
        tickers=tickers,
        batch_size=args.batch_size,
        seed=args.seed,
        mode="train",
        epochs=args.epochs,
        max_batches_per_session=args.max_batches_per_session,
        shuffle=True,
    )
    train_loader = DataLoader(train_dataset, batch_size=None, num_workers=0, pin_memory=device.type == "cuda")
    cached_batches = collect_overfit_batches(train_loader, overfit_window_count) if overfit_window_count > 0 else []
    if cached_batches:
        planned_steps = args.max_steps or args.epochs * len(cached_batches)
        train_iter: Iterable[dict[str, torch.Tensor]] = cycle(cached_batches)
        cached_windows = sum(batch_window_count(batch) for batch in cached_batches)
        metadata["train"]["overfit_cached_windows"] = cached_windows
        metadata["train"]["overfit_cached_batches"] = len(cached_batches)
        metadata["train"]["overfit_effective_window_count"] = overfit_window_count
        write_json(output_dir / "metadata.json", metadata)
        print_section(
            f"OVERFIT CACHE READY windows={cached_windows:,} batches={len(cached_batches):,} "
            f"planned_steps={planned_steps:,}"
        )
    else:
        planned_steps = args.max_steps
        train_iter = train_loader
        print_section(f"STREAM TRAINING START max_steps={step_text(planned_steps, planned_steps) if planned_steps else 'dataset_exhaustion'}")

    running_loss = 0.0
    running_batches = 0
    step = 0
    last_eval_step = 0
    last_log_time = time.perf_counter()
    for batch in train_iter:
        if planned_steps > 0 and step >= planned_steps:
            break
        step += 1
        model.train()
        batch = move_batch(batch, device)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=args.amp and device.type == "cuda"):
            prediction = model(batch["values"], batch["time_features"])
            loss = torch.nn.functional.smooth_l1_loss(prediction, batch["targets"])
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip_norm)
        scaler.step(optimizer)
        scaler.update()

        loss_value = float(loss.detach().cpu())
        running_loss += loss_value
        running_batches += 1
        if step == 1 or step % args.logging_steps == 0:
            elapsed = max(1e-6, time.perf_counter() - last_log_time)
            avg_loss = running_loss / max(1, running_batches)
            samples_per_sec = args.batch_size * running_batches / elapsed
            train_metrics = train_batch_metrics(
                prediction=prediction.detach().cpu().numpy(),
                target=batch["targets"].detach().cpu().numpy(),
                data_config=data_config,
            )
            print(
                f"mlp train step={step_text(step, planned_steps)} loss={avg_loss:.6f} "
                f"h1_mae={train_metrics['h1_close_mae_bps']:.3f}bps "
                f"h1_dir={train_metrics['h1_close_dir_acc_pct']:.2f}% "
                f"lr={optimizer.param_groups[0]['lr']:.3e} samples_s={samples_per_sec:,.0f}",
                flush=True,
            )
            append_jsonl(
                metrics_path,
                {
                    "type": "train",
                    "step": step,
                    "loss": avg_loss,
                    "lr": optimizer.param_groups[0]["lr"],
                    "samples_per_sec": samples_per_sec,
                    **train_metrics,
                    "time": datetime.now().isoformat(timespec="seconds"),
                },
            )
            running_loss = 0.0
            running_batches = 0
            last_log_time = time.perf_counter()

        should_eval = (args.eval_steps > 0 and step % args.eval_steps == 0) or (
            planned_steps > 0 and step == planned_steps
        )
        if should_eval:
            if cached_batches:
                print_section(f"TRAIN-CACHE EVAL START step={step:,}")
                cache_metrics = evaluate_batches(model, cached_batches, data_config, device, label="train_cache")
                cache_metrics.update({"type": "train_cache", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
                append_jsonl(metrics_path, cache_metrics)
                print_metric_line(cache_metrics)
                print_section(f"TRAIN-CACHE EVAL END step={step:,}")
            print_section(f"VALIDATION START step={step:,}")
            validation_metrics = evaluate_stream(
                model=model,
                data_config=data_config,
                sessions=validation_sessions,
                tickers=tickers,
                device=device,
                batch_size=args.batch_size,
                max_windows=args.validation_window_count,
                label="validation",
                seed=args.seed + 100,
            )
            validation_metrics.update({"type": "validation", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
            append_jsonl(metrics_path, validation_metrics)
            print_metric_line(validation_metrics)
            save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config)
            print_section(f"VALIDATION END step={step:,}")
            last_eval_step = step

    if step > 0 and last_eval_step != step:
        print_section(f"FINAL VALIDATION START step={step:,}")
        validation_metrics = evaluate_stream(
            model=model,
            data_config=data_config,
            sessions=validation_sessions,
            tickers=tickers,
            device=device,
            batch_size=args.batch_size,
            max_windows=args.validation_window_count,
            label="validation",
            seed=args.seed + 100,
        )
        validation_metrics.update({"type": "validation", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
        append_jsonl(metrics_path, validation_metrics)
        print_metric_line(validation_metrics)
        save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config)
        print_section(f"FINAL VALIDATION END step={step:,}")

    print_section(f"TEST START step={step:,}")
    test_metrics = evaluate_stream(
        model=model,
        data_config=data_config,
        sessions=test_sessions,
        tickers=tickers,
        device=device,
        batch_size=args.batch_size,
        max_windows=args.test_window_count,
        label="test",
        seed=args.seed + 200,
    )
    test_metrics.update({"type": "test", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
    append_jsonl(metrics_path, test_metrics)
    print_metric_line(test_metrics)
    save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config)
    print_section("MLP TRAINING COMPLETE")
    print(f"*** Artifacts: {output_dir}", flush=True)


def build_data_config(args: argparse.Namespace) -> DataConfig:
    processed_root = Path(args.processed_root)
    return DataConfig(
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


def resolve_overfit_window_count(args: argparse.Namespace) -> int:
    if args.overfit_window_count < 0:
        raise SystemExit("--overfit-window-count must be >= 0.")
    if args.overfit_batches < 0:
        raise SystemExit("--overfit-batches must be >= 0.")
    if args.overfit_window_count > 0:
        return int(args.overfit_window_count)
    if args.overfit_batches > 0:
        return int(args.overfit_batches) * DEFAULT_OVERFIT_REFERENCE_BATCH_SIZE
    return 0


def collect_overfit_batches(loader: DataLoader, target_windows: int) -> list[dict[str, torch.Tensor]]:
    print_section(f"BUILDING OVERFIT CACHE target_windows={target_windows:,}")
    batches = []
    collected_windows = 0
    for batch in loader:
        remaining = target_windows - collected_windows
        if remaining <= 0:
            break
        cached = cache_batch(slice_batch(batch, remaining))
        batches.append(cached)
        collected_windows += batch_window_count(cached)
        if collected_windows >= target_windows:
            break
    if not batches:
        raise SystemExit("No overfit batches were created. Pick a date/ticker with enough bars.")
    print_section(f"OVERFIT CACHE BUILT windows={collected_windows:,} batches={len(batches):,}")
    return batches


def slice_batch(batch: dict[str, Any], max_rows: int) -> dict[str, Any]:
    row_count = batch_window_count(batch)
    if row_count <= max_rows:
        return batch
    rows = slice(0, max_rows)
    sliced: dict[str, Any] = {}
    for key, value in batch.items():
        if torch.is_tensor(value):
            sliced[key] = value[rows]
        elif isinstance(value, np.ndarray):
            sliced[key] = value[rows].copy()
        elif isinstance(value, list):
            sliced[key] = list(value[:max_rows])
        else:
            sliced[key] = value
    return sliced


def cache_batch(batch: dict[str, Any]) -> dict[str, Any]:
    cached: dict[str, Any] = {}
    for key, value in batch.items():
        if torch.is_tensor(value):
            cached[key] = value.cpu()
        elif isinstance(value, np.ndarray):
            cached[key] = value.copy()
        elif isinstance(value, list):
            cached[key] = list(value)
        else:
            cached[key] = value
    return cached


def batch_window_count(batch: dict[str, Any]) -> int:
    values = batch.get("values")
    if torch.is_tensor(values):
        return int(values.shape[0])
    if isinstance(values, np.ndarray):
        return int(values.shape[0])
    tickers = batch.get("ticker")
    if isinstance(tickers, list):
        return len(tickers)
    raise ValueError("Batch does not contain values or ticker rows, so window count cannot be determined.")


@torch.no_grad()
def evaluate_stream(
    *,
    model: torch.nn.Module,
    data_config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    device: torch.device,
    batch_size: int,
    max_windows: int,
    label: str,
    seed: int,
) -> dict[str, Any]:
    dataset = RollingBarWindowDataset(
        config=data_config,
        sessions=sessions,
        tickers=tickers,
        batch_size=batch_size,
        seed=seed,
        mode=label,
        max_windows=max_windows,
        shuffle=False,
    )
    loader = DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=device.type == "cuda")
    return evaluate_batches(model, loader, data_config, device, label)


@torch.no_grad()
def evaluate_batches(
    model: torch.nn.Module,
    batches: Iterable[dict[str, torch.Tensor]],
    data_config: DataConfig,
    device: torch.device,
    label: str,
) -> dict[str, Any]:
    model.eval()
    accumulator = MetricAccumulator(data_config.horizon, data_config.target_columns)
    loss_sum = 0.0
    batch_count = 0
    for batch in batches:
        batch = move_batch(batch, device)
        prediction = model(batch["values"], batch["time_features"])
        loss = torch.nn.functional.smooth_l1_loss(prediction, batch["targets"])
        loss_sum += float(loss.detach().cpu())
        batch_count += 1
        accumulator.update(prediction.detach().cpu().numpy(), batch["targets"].detach().cpu().numpy())
    metrics = accumulator.compute(prefix=f"{label}_")
    metrics[f"{label}_loss"] = loss_sum / max(1, batch_count)
    metrics[f"{label}_batches"] = batch_count
    return metrics


def train_batch_metrics(prediction: np.ndarray, target: np.ndarray, data_config: DataConfig) -> dict[str, float]:
    accumulator = MetricAccumulator(data_config.horizon, data_config.target_columns)
    accumulator.update(prediction, target)
    computed = accumulator.compute()
    return {
        "h1_close_mae_bps": float(computed.get("h1_close_mae_bps", math.nan)),
        "h1_close_dir_acc_pct": float(computed.get("h1_close_dir_acc_pct", math.nan)),
        "h1_close_edge_vs_naive_bps": float(computed.get("h1_close_edge_vs_naive_bps", math.nan)),
    }


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


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


def resolve_device(requested: str) -> torch.device:
    if requested == "cuda" and not torch.cuda.is_available():
        print("CUDA requested but unavailable; using CPU.", flush=True)
        return torch.device("cpu")
    return torch.device(requested)


def step_text(step: int, planned_steps: int) -> str:
    return f"{step:,}/{planned_steps:,}" if planned_steps > 0 else f"{step:,}"


def print_section(title: str) -> None:
    print(LOG_RULE, flush=True)
    print(f"*** {title}", flush=True)
    print(LOG_RULE, flush=True)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    step: int,
    args: argparse.Namespace,
    data_config: DataConfig,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "step": step,
            "args": vars(args),
            "data": data_config_to_dict(data_config),
        },
        path,
    )


def make_output_dir(data_config: DataConfig, args: argparse.Namespace) -> Path:
    root = data_config.processed_root / "models" / "inhouse_mlp"
    if args.output_name:
        return root / args.output_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"mlp_ctx{data_config.context_length}_h{data_config.horizon}_{data_config.train_start_date}_{data_config.test_end_date}_{timestamp}"
    return root / name


def metadata_payload(
    args: argparse.Namespace,
    data_config: DataConfig,
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
            **data_config_to_dict(data_config),
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
        "model": {
            "type": "flat_mlp",
            "hidden_dim": args.hidden_dim,
            "layers": args.layers,
            "dropout": args.dropout,
        },
        "train": vars(args),
    }


def data_config_to_dict(config: DataConfig) -> dict[str, Any]:
    return {
        "processed_root": str(config.processed_root),
        "train_start_date": config.train_start_date,
        "train_end_date": config.train_end_date,
        "validation_start_date": config.validation_start_date,
        "validation_end_date": config.validation_end_date,
        "test_start_date": config.test_start_date,
        "test_end_date": config.test_end_date,
        "timeframe": config.timeframe,
        "session_scope": config.session_scope,
        "context_length": config.context_length,
        "horizon": config.horizon,
        "target_columns": list(config.target_columns),
        "input_feature_columns": list(config.input_feature_columns),
        "time_feature_columns": list(config.time_feature_columns),
        "tickers": list(config.tickers),
        "max_tickers": config.max_tickers,
        "allow_target_across_session": config.allow_target_across_session,
        "carry_context_across_session": config.carry_context_across_session,
    }


def print_split_summary(metadata: dict[str, Any]) -> None:
    data = metadata["data"]
    print("Dataset split:", flush=True)
    print(f"  train: {data['train_actual_start']} -> {data['train_actual_end']} ({data['train_sessions']} sessions)", flush=True)
    print(
        f"  validation: {data['validation_actual_start']} -> {data['validation_actual_end']} "
        f"({data['validation_sessions']} sessions)",
        flush=True,
    )
    print(f"  test: {data['test_actual_start']} -> {data['test_actual_end']} ({data['test_sessions']} sessions)", flush=True)
    print(
        f"  selected_tickers: {data['selected_tickers']} "
        f"carry_context_across_session={data['carry_context_across_session']} "
        f"allow_target_across_session={data['allow_target_across_session']}",
        flush=True,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
