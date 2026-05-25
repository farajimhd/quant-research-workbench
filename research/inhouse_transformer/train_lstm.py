from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.config import DataConfig, TrainConfig  # noqa: E402
from research.inhouse_transformer.data import (  # noqa: E402
    available_sessions,
    column_array,
    combine_carryover,
    iter_ticker_frames,
    load_session_frame,
    nonnegative_array,
    parse_ticker_list,
    resolve_end_date,
    select_top_tickers,
    tail_carryover,
    ticker_arrays,
    valid_origins,
)
from research.inhouse_transformer.metrics import append_jsonl  # noqa: E402
from research.inhouse_transformer.model_lstm import SimpleLSTMForecaster  # noqa: E402


LOG_RULE = "*" * 96
ACTUAL_FEATURE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
    "spread_bps",
    "quote_bid_size",
    "quote_ask_size",
    "quoted_share_depth",
    "quote_imbalance",
    "quote_valid_ratio",
)
TIME_FEATURE_COUNT = 9


@dataclass(slots=True)
class NormalizationStats:
    mode: str
    input_mean: np.ndarray
    input_std: np.ndarray
    target_mean: np.ndarray
    target_std: np.ndarray


class RunningMoments:
    def __init__(self, width: int) -> None:
        self.count = 0
        self.mean = np.zeros(width, dtype=np.float64)
        self.m2 = np.zeros(width, dtype=np.float64)

    def update(self, values: np.ndarray) -> None:
        values = np.asarray(values, dtype=np.float64)
        if values.size == 0:
            return
        values = values.reshape(-1, values.shape[-1])
        batch_count = values.shape[0]
        batch_mean = values.mean(axis=0)
        batch_m2 = np.square(values - batch_mean).sum(axis=0)
        if self.count == 0:
            self.count = batch_count
            self.mean = batch_mean
            self.m2 = batch_m2
            return
        total = self.count + batch_count
        delta = batch_mean - self.mean
        self.mean = self.mean + delta * batch_count / total
        self.m2 = self.m2 + batch_m2 + np.square(delta) * self.count * batch_count / total
        self.count = total

    def finalize(self) -> tuple[np.ndarray, np.ndarray]:
        if self.count < 2:
            raise SystemExit("Not enough rows to compute normalization statistics.")
        variance = self.m2 / max(1, self.count - 1)
        std = np.sqrt(np.maximum(variance, 1e-12))
        std = np.where(std < 1e-6, 1.0, std)
        return self.mean.astype(np.float32), std.astype(np.float32)


class PriceMetricAccumulator:
    def __init__(self, horizon: int, target_name: str, stats: NormalizationStats) -> None:
        self.horizon = horizon
        self.target_name = target_name
        self.stats = stats
        self.count = 0
        self.abs_bps = np.zeros(horizon, dtype=np.float64)
        self.sq_bps = np.zeros(horizon, dtype=np.float64)
        self.naive_abs_bps = np.zeros(horizon, dtype=np.float64)
        self.dir_correct = np.zeros(horizon, dtype=np.float64)
        self.pred_sum = np.zeros(horizon, dtype=np.float64)
        self.actual_sum = np.zeros(horizon, dtype=np.float64)
        self.pred_sq_sum = np.zeros(horizon, dtype=np.float64)
        self.actual_sq_sum = np.zeros(horizon, dtype=np.float64)
        self.cross_sum = np.zeros(horizon, dtype=np.float64)

    def update(self, prediction_norm: np.ndarray, target_norm: np.ndarray, current_close: np.ndarray) -> None:
        current = np.maximum(np.asarray(current_close, dtype=np.float64).reshape(-1, 1), 1e-6)
        prediction = denormalize_target(prediction_norm, self.stats, current)[:, :, 0]
        target = denormalize_target(target_norm, self.stats, current)[:, :, 0]
        error_bps = (prediction - target) / current * 10000.0
        pred_change_bps = (prediction - current) / current * 10000.0
        actual_change_bps = (target - current) / current * 10000.0
        self.count += prediction.shape[0]
        self.abs_bps += np.abs(error_bps).sum(axis=0)
        self.sq_bps += np.square(error_bps).sum(axis=0)
        self.naive_abs_bps += np.abs(actual_change_bps).sum(axis=0)
        self.dir_correct += ((pred_change_bps > 0.0) == (actual_change_bps > 0.0)).sum(axis=0)
        self.pred_sum += pred_change_bps.sum(axis=0)
        self.actual_sum += actual_change_bps.sum(axis=0)
        self.pred_sq_sum += np.square(pred_change_bps).sum(axis=0)
        self.actual_sq_sum += np.square(actual_change_bps).sum(axis=0)
        self.cross_sum += (pred_change_bps * actual_change_bps).sum(axis=0)

    def compute(self, prefix: str = "") -> dict[str, float]:
        metrics: dict[str, float] = {f"{prefix}windows": float(self.count)}
        denominator = max(1, self.count)
        for horizon_idx in range(self.horizon):
            h = horizon_idx + 1
            mae = self.abs_bps[horizon_idx] / denominator
            rmse = math.sqrt(self.sq_bps[horizon_idx] / denominator)
            naive = self.naive_abs_bps[horizon_idx] / denominator
            metrics[f"{prefix}h{h}_{self.target_name}_mae_bps"] = float(mae)
            metrics[f"{prefix}h{h}_{self.target_name}_rmse_bps"] = float(rmse)
            metrics[f"{prefix}h{h}_{self.target_name}_naive_mae_bps"] = float(naive)
            metrics[f"{prefix}h{h}_{self.target_name}_edge_vs_naive_bps"] = float(naive - mae)
            metrics[f"{prefix}h{h}_{self.target_name}_dir_acc_pct"] = float(
                100.0 * self.dir_correct[horizon_idx] / denominator
            )
            metrics[f"{prefix}h{h}_{self.target_name}_change_corr"] = correlation_from_sums(
                count=denominator,
                x_sum=self.pred_sum[horizon_idx],
                y_sum=self.actual_sum[horizon_idx],
                x_sq_sum=self.pred_sq_sum[horizon_idx],
                y_sq_sum=self.actual_sq_sum[horizon_idx],
                cross_sum=self.cross_sum[horizon_idx],
            )
        return metrics


class ActualValueWindowDataset(IterableDataset):
    def __init__(
        self,
        *,
        config: DataConfig,
        sessions: list[str],
        tickers: tuple[str, ...],
        stats: NormalizationStats,
        target_column: str,
        batch_size: int,
        seed: int,
        mode: str,
        epochs: int = 1,
        max_windows: int = 0,
        max_batches_per_session: int = 0,
        shuffle: bool = False,
    ) -> None:
        self.config = config
        self.sessions = list(sessions)
        self.tickers = tickers
        self.stats = stats
        self.target_column = target_column
        self.batch_size = batch_size
        self.seed = seed
        self.mode = mode
        self.epochs = epochs
        self.max_windows = max_windows
        self.max_batches_per_session = max_batches_per_session
        self.shuffle = shuffle

    def __iter__(self) -> Iterator[dict[str, torch.Tensor]]:
        rng = np.random.default_rng(self.seed)
        emitted_windows = 0
        for epoch in range(self.epochs):
            sessions = list(self.sessions)
            if self.shuffle and not self.config.carry_context_across_session:
                rng.shuffle(sessions)
            carryover: dict[str, Any] = {}
            batch = ActualBatchBuilder(
                batch_size=self.batch_size,
                context_length=self.config.context_length,
                input_size=len(ACTUAL_FEATURE_COLUMNS) + TIME_FEATURE_COUNT,
                horizon=self.config.horizon,
                stats=self.stats,
                target_column=self.target_column,
            )
            for session_index, session in enumerate(sessions, start=1):
                print(LOG_RULE, flush=True)
                print(
                    f"*** {self.mode.upper()} SESSION START {session} "
                    f"| epoch {epoch + 1}/{self.epochs} | session {session_index}/{len(sessions)}",
                    flush=True,
                )
                session_batches = 0
                session_windows = 0
                frame = load_session_frame(self.config, session, self.tickers)
                ticker_frames = iter_ticker_frames(frame, rng=rng, shuffle=self.shuffle) if not frame.is_empty() else iter(())
                for ticker, ticker_frame in ticker_frames:
                    combined = combine_carryover(carryover.get(ticker), ticker_frame, self.config)
                    arrays = actual_arrays(combined, self.config)
                    current_session = str(ticker_frame["session_date"][0])
                    origins = valid_origins(arrays, current_session, self.config)
                    if self.shuffle and origins.size:
                        rng.shuffle(origins)
                    for origin in origins:
                        batch.add(arrays, int(origin), self.config)
                        session_windows += 1
                        emitted_windows += 1
                        if batch.full:
                            yield batch.as_torch()
                            session_batches += 1
                            batch = batch.empty_like()
                            if 0 < self.max_batches_per_session <= session_batches:
                                break
                        if 0 < self.max_windows <= emitted_windows:
                            if len(batch) > 0:
                                yield batch.as_torch()
                                session_batches += 1
                            print(
                                f"*** {self.mode.upper()} SESSION END   {session} "
                                f"| windows={session_windows:,} | batches={session_batches:,} | max_windows_reached",
                                flush=True,
                            )
                            print(LOG_RULE, flush=True)
                            return
                    carryover[ticker] = tail_carryover(combined, self.config)
                    if 0 < self.max_batches_per_session <= session_batches:
                        break
                if len(batch) > 0 and self.mode != "train":
                    yield batch.as_torch()
                    session_batches += 1
                    batch = batch.empty_like()
                print(
                    f"*** {self.mode.upper()} SESSION END   {session} "
                    f"| windows={session_windows:,} | batches={session_batches:,}",
                    flush=True,
                )
                print(LOG_RULE, flush=True)
            if len(batch) > 0:
                yield batch.as_torch()


class ActualBatchBuilder:
    def __init__(
        self,
        *,
        batch_size: int,
        context_length: int,
        input_size: int,
        horizon: int,
        stats: NormalizationStats,
        target_column: str,
    ) -> None:
        self.inputs = np.empty((batch_size, context_length, input_size), dtype=np.float32)
        self.targets = np.empty((batch_size, horizon, 1), dtype=np.float32)
        self.current_close = np.empty((batch_size,), dtype=np.float32)
        self.stats = stats
        self.target_column = target_column
        self.count = 0

    @property
    def full(self) -> bool:
        return self.count >= self.inputs.shape[0]

    def __len__(self) -> int:
        return self.count

    def empty_like(self) -> "ActualBatchBuilder":
        return ActualBatchBuilder(
            batch_size=self.inputs.shape[0],
            context_length=self.inputs.shape[1],
            input_size=self.inputs.shape[2],
            horizon=self.targets.shape[1],
            stats=self.stats,
            target_column=self.target_column,
        )

    def add(self, arrays: dict[str, np.ndarray], origin: int, config: DataConfig) -> None:
        start = origin - config.context_length + 1
        end = origin + 1
        target_start = origin + 1
        target_end = origin + 1 + config.horizon
        current_close = arrays["close"][origin]
        values = normalize_inputs(arrays["actual_features"][start:end], self.stats, current_close)
        self.inputs[self.count] = np.concatenate([values, arrays["time_features"][start:end]], axis=1)
        target_prices = arrays[self.target_column][target_start:target_end].reshape(config.horizon, 1)
        self.targets[self.count] = normalize_target(target_prices, self.stats, current_close)
        self.current_close[self.count] = current_close
        self.count += 1

    def as_torch(self) -> dict[str, torch.Tensor]:
        rows = slice(0, self.count)
        return {
            "inputs": torch.from_numpy(self.inputs[rows].copy()),
            "targets": torch.from_numpy(self.targets[rows].copy()),
            "current_close": torch.from_numpy(self.current_close[rows].copy()),
        }


def parse_args() -> argparse.Namespace:
    data_defaults = DataConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Train a Keras-weather-style LSTM baseline using actual-value bar inputs and actual future price targets. "
            "Inputs are normalized from train-split statistics, but the raw source features are not return-bps encoded."
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
    parser.add_argument("--context-length", type=int, default=64)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--target-column", choices=["open", "high", "low", "close"], default="close")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument("--max-tickers", type=int, default=data_defaults.max_tickers)
    parser.add_argument("--allow-target-across-session", action="store_true")
    parser.add_argument("--no-carry-context-across-session", action="store_true")
    parser.add_argument(
        "--normalization-mode",
        choices=["window", "train_split"],
        default="window",
        help=(
            "window normalizes each sample from its own causal context and starts training immediately. "
            "train_split computes Keras-style global train statistics before training."
        ),
    )
    parser.add_argument("--stats-max-sessions", type=int, default=0)

    parser.add_argument("--hidden-size", type=int, default=32)
    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.0)

    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--loss", choices=["mse", "smooth_l1"], default="mse")
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=25)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--validation-window-count", type=int, default=5000)
    parser.add_argument("--test-window-count", type=int, default=10000)
    parser.add_argument("--max-batches-per-session", type=int, default=0)
    parser.add_argument("--overfit-batches", type=int, default=0)
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
    print_split_summary(train_sessions, validation_sessions, test_sessions, tickers, data_config)
    print(f"Actual-value input columns: {list(ACTUAL_FEATURE_COLUMNS)} + time_features", flush=True)
    print(f"Target: future actual {args.target_column} | horizon={data_config.horizon}", flush=True)
    print(f"Normalization mode: {args.normalization_mode}", flush=True)
    print(
        f"LSTM model: input_size={len(ACTUAL_FEATURE_COLUMNS) + TIME_FEATURE_COUNT} "
        f"hidden_size={args.hidden_size} layers={args.layers} dropout={args.dropout}",
        flush=True,
    )
    print(f"Output directory: {output_dir}", flush=True)
    if args.dry_run:
        print("*** Dry run complete before stats/training.", flush=True)
        return

    stats = (
        make_window_normalization_stats()
        if args.normalization_mode == "window"
        else compute_normalization_stats(data_config, train_sessions, tickers, args.target_column, args.stats_max_sessions)
    )
    write_json(output_dir / "normalization.json", normalization_to_dict(stats))
    metadata = metadata_payload(args, data_config, train_sessions, validation_sessions, test_sessions, tickers, output_dir, stats)
    write_json(output_dir / "metadata.json", metadata)

    device = resolve_device(args.device)
    model = SimpleLSTMForecaster(
        input_size=len(ACTUAL_FEATURE_COLUMNS) + TIME_FEATURE_COUNT,
        hidden_size=args.hidden_size,
        layers=args.layers,
        dropout=args.dropout,
        horizon=data_config.horizon,
        target_count=1,
    ).to(device)
    print(f"LSTM parameters: {sum(p.numel() for p in model.parameters()):,}", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=args.amp and device.type == "cuda")

    train_dataset = make_dataset(
        data_config=data_config,
        sessions=train_sessions,
        tickers=tickers,
        stats=stats,
        args=args,
        mode="train",
        epochs=args.epochs,
        max_windows=0,
        shuffle=True,
    )
    train_loader = DataLoader(train_dataset, batch_size=None, num_workers=0, pin_memory=device.type == "cuda")
    cached_batches = collect_overfit_batches(train_loader, args.overfit_batches) if args.overfit_batches > 0 else []
    if cached_batches:
        planned_steps = args.max_steps or args.epochs * len(cached_batches)
        train_iter: Iterable[dict[str, torch.Tensor]] = cycle(cached_batches)
        print_section(f"OVERFIT CACHE READY batches={len(cached_batches)} planned_steps={planned_steps:,}")
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
            prediction = model(batch["inputs"])
            loss = loss_fn(prediction, batch["targets"], args.loss)
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
            train_metrics = batch_price_metrics(prediction.detach(), batch, stats, data_config.horizon, args.target_column)
            print(
                f"lstm train step={step_text(step, planned_steps)} loss={avg_loss:.6f} "
                f"h1_mae={train_metrics['h1_mae_bps']:.3f}bps "
                f"h1_dir={train_metrics['h1_dir_acc_pct']:.2f}% "
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

        if step % args.eval_steps == 0 or (planned_steps > 0 and step == planned_steps):
            if cached_batches:
                print_section(f"TRAIN-CACHE EVAL START step={step:,}")
                cache_metrics = evaluate_batches(model, cached_batches, stats, data_config.horizon, args.target_column, device, args.loss, "train_cache")
                cache_metrics.update({"type": "train_cache", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
                append_jsonl(metrics_path, cache_metrics)
                print_metric_line(cache_metrics, args.target_column)
                print_section(f"TRAIN-CACHE EVAL END step={step:,}")
            print_section(f"VALIDATION START step={step:,}")
            validation_metrics = evaluate_stream(
                model=model,
                data_config=data_config,
                sessions=validation_sessions,
                tickers=tickers,
                stats=stats,
                args=args,
                device=device,
                max_windows=args.validation_window_count,
                label="validation",
                seed=args.seed + 100,
            )
            validation_metrics.update({"type": "validation", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
            append_jsonl(metrics_path, validation_metrics)
            print_metric_line(validation_metrics, args.target_column)
            save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config, stats)
            print_section(f"VALIDATION END step={step:,}")
            last_eval_step = step

    if step > 0 and last_eval_step != step:
        print_section(f"FINAL VALIDATION START step={step:,}")
        validation_metrics = evaluate_stream(
            model=model,
            data_config=data_config,
            sessions=validation_sessions,
            tickers=tickers,
            stats=stats,
            args=args,
            device=device,
            max_windows=args.validation_window_count,
            label="validation",
            seed=args.seed + 100,
        )
        validation_metrics.update({"type": "validation", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
        append_jsonl(metrics_path, validation_metrics)
        print_metric_line(validation_metrics, args.target_column)
        save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config, stats)
        print_section(f"FINAL VALIDATION END step={step:,}")

    print_section(f"TEST START step={step:,}")
    test_metrics = evaluate_stream(
        model=model,
        data_config=data_config,
        sessions=test_sessions,
        tickers=tickers,
        stats=stats,
        args=args,
        device=device,
        max_windows=args.test_window_count,
        label="test",
        seed=args.seed + 200,
    )
    test_metrics.update({"type": "test", "step": step, "time": datetime.now().isoformat(timespec="seconds")})
    append_jsonl(metrics_path, test_metrics)
    print_metric_line(test_metrics, args.target_column)
    save_checkpoint(output_dir / "last.pt", model, optimizer, step, args, data_config, stats)
    print_section("LSTM TRAINING COMPLETE")
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
        target_columns=(args.target_column,),
        tickers=parse_ticker_list(args.tickers),
        max_tickers=args.max_tickers,
        allow_target_across_session=bool(args.allow_target_across_session),
        carry_context_across_session=not bool(args.no_carry_context_across_session),
    )


def compute_normalization_stats(
    config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    target_column: str,
    max_sessions: int,
) -> NormalizationStats:
    print_section("NORMALIZATION STATS START")
    input_stats = RunningMoments(len(ACTUAL_FEATURE_COLUMNS))
    target_stats = RunningMoments(1)
    selected_sessions = sessions[:max_sessions] if max_sessions > 0 else sessions
    carryover: dict[str, Any] = {}
    for index, session in enumerate(selected_sessions, start=1):
        frame = load_session_frame(config, session, tickers)
        session_targets = 0
        if not frame.is_empty():
            for ticker, ticker_frame in iter_ticker_frames(frame):
                combined = combine_carryover(carryover.get(ticker), ticker_frame, config)
                arrays = actual_arrays(combined, config)
                input_stats.update(arrays["actual_features"])
                origins = valid_origins(arrays, str(ticker_frame["session_date"][0]), config)
                if origins.size:
                    future_values = np.concatenate(
                        [
                            arrays[target_column][origins + offset].reshape(-1, 1)
                            for offset in range(1, config.horizon + 1)
                        ],
                        axis=0,
                    )
                    target_stats.update(future_values)
                    session_targets += future_values.shape[0]
                carryover[ticker] = tail_carryover(combined, config)
        print(
            f"Stats {session} ({index}/{len(selected_sessions)}): "
            f"input_rows={input_stats.count:,} target_rows+={session_targets:,}",
            flush=True,
        )
    input_mean, input_std = input_stats.finalize()
    target_mean, target_std = target_stats.finalize()
    print_section(
        f"NORMALIZATION STATS END input_rows={input_stats.count:,} target_rows={target_stats.count:,}"
    )
    return NormalizationStats(
        mode="train_split",
        input_mean=input_mean,
        input_std=input_std,
        target_mean=target_mean,
        target_std=target_std,
    )


def make_window_normalization_stats() -> NormalizationStats:
    print_section("WINDOW NORMALIZATION ENABLED")
    print(
        "Per-window normalization uses only the current context: price columns are centered against current close, "
        "size columns are log-z-scored inside the context window, spread is clipped/scaled, and no train-wide stats pass is run.",
        flush=True,
    )
    return NormalizationStats(
        mode="window",
        input_mean=np.zeros(len(ACTUAL_FEATURE_COLUMNS), dtype=np.float32),
        input_std=np.ones(len(ACTUAL_FEATURE_COLUMNS), dtype=np.float32),
        target_mean=np.zeros(1, dtype=np.float32),
        target_std=np.ones(1, dtype=np.float32),
    )


def actual_arrays(frame: Any, config: DataConfig) -> dict[str, np.ndarray]:
    base = ticker_arrays(frame, config)
    quote_bid_size = nonnegative_array(frame, "quote_bid_size")
    quote_ask_size = nonnegative_array(frame, "quote_ask_size")
    quoted_share_depth = nonnegative_array(frame, "quoted_share_depth")
    quoted_share_depth = np.where(quoted_share_depth > 0.0, quoted_share_depth, quote_bid_size + quote_ask_size)
    quote_size_sum = quote_bid_size + quote_ask_size
    quote_imbalance = np.divide(
        quote_bid_size - quote_ask_size,
        quote_size_sum,
        out=np.zeros_like(quote_size_sum, dtype=np.float32),
        where=quote_size_sum > 0.0,
    )
    actual_features = np.column_stack(
        [
            base["open"],
            base["high"],
            base["low"],
            base["close"],
            nonnegative_array(frame, "volume"),
            nonnegative_array(frame, "transactions"),
            np.nan_to_num(column_array(frame, "spread_bps"), nan=0.0, posinf=1000.0, neginf=-1000.0),
            quote_bid_size,
            quote_ask_size,
            quoted_share_depth,
            np.clip(quote_imbalance, -1.0, 1.0),
            np.clip(nonnegative_array(frame, "quote_valid_ratio"), 0.0, 1.0),
        ]
    ).astype(np.float32)
    actual_features = np.nan_to_num(actual_features, nan=0.0, posinf=1e9, neginf=-1e9)
    return {
        **base,
        "actual_features": actual_features,
    }


def make_dataset(
    *,
    data_config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    stats: NormalizationStats,
    args: argparse.Namespace,
    mode: str,
    epochs: int,
    max_windows: int,
    shuffle: bool,
) -> ActualValueWindowDataset:
    return ActualValueWindowDataset(
        config=data_config,
        sessions=sessions,
        tickers=tickers,
        stats=stats,
        target_column=args.target_column,
        batch_size=args.batch_size,
        seed=args.seed,
        mode=mode,
        epochs=epochs,
        max_windows=max_windows,
        max_batches_per_session=args.max_batches_per_session,
        shuffle=shuffle,
    )


def collect_overfit_batches(loader: DataLoader, count: int) -> list[dict[str, torch.Tensor]]:
    print_section(f"BUILDING OVERFIT CACHE target_batches={count}")
    batches = []
    for batch in loader:
        batches.append({key: value.cpu() for key, value in batch.items()})
        if len(batches) >= count:
            break
    if not batches:
        raise SystemExit("No overfit batches were created. Pick a date/ticker with enough bars.")
    print_section(f"OVERFIT CACHE BUILT batches={len(batches)}")
    return batches


@torch.no_grad()
def evaluate_stream(
    *,
    model: torch.nn.Module,
    data_config: DataConfig,
    sessions: list[str],
    tickers: tuple[str, ...],
    stats: NormalizationStats,
    args: argparse.Namespace,
    device: torch.device,
    max_windows: int,
    label: str,
    seed: int,
) -> dict[str, Any]:
    dataset = ActualValueWindowDataset(
        config=data_config,
        sessions=sessions,
        tickers=tickers,
        stats=stats,
        target_column=args.target_column,
        batch_size=args.batch_size,
        seed=seed,
        mode=label,
        max_windows=max_windows,
        shuffle=False,
    )
    loader = DataLoader(dataset, batch_size=None, num_workers=0, pin_memory=device.type == "cuda")
    return evaluate_batches(model, loader, stats, data_config.horizon, args.target_column, device, args.loss, label)


@torch.no_grad()
def evaluate_batches(
    model: torch.nn.Module,
    batches: Iterable[dict[str, torch.Tensor]],
    stats: NormalizationStats,
    horizon: int,
    target_column: str,
    device: torch.device,
    loss_name: str,
    label: str,
) -> dict[str, Any]:
    model.eval()
    accumulator = PriceMetricAccumulator(horizon, target_column, stats)
    loss_sum = 0.0
    batch_count = 0
    for batch in batches:
        batch = move_batch(batch, device)
        prediction = model(batch["inputs"])
        loss = loss_fn(prediction, batch["targets"], loss_name)
        loss_sum += float(loss.detach().cpu())
        batch_count += 1
        accumulator.update(
            prediction.detach().cpu().numpy(),
            batch["targets"].detach().cpu().numpy(),
            batch["current_close"].detach().cpu().numpy(),
        )
    metrics = accumulator.compute(prefix=f"{label}_")
    metrics[f"{label}_loss"] = loss_sum / max(1, batch_count)
    metrics[f"{label}_batches"] = batch_count
    return metrics


def batch_price_metrics(
    prediction: torch.Tensor,
    batch: dict[str, torch.Tensor],
    stats: NormalizationStats,
    horizon: int,
    target_column: str,
) -> dict[str, float]:
    accumulator = PriceMetricAccumulator(horizon, target_column, stats)
    accumulator.update(
        prediction.detach().cpu().numpy(),
        batch["targets"].detach().cpu().numpy(),
        batch["current_close"].detach().cpu().numpy(),
    )
    computed = accumulator.compute()
    return {
        "h1_mae_bps": float(computed.get(f"h1_{target_column}_mae_bps", math.nan)),
        "h1_dir_acc_pct": float(computed.get(f"h1_{target_column}_dir_acc_pct", math.nan)),
        "h1_edge_vs_naive_bps": float(computed.get(f"h1_{target_column}_edge_vs_naive_bps", math.nan)),
    }


def print_metric_line(metrics: dict[str, Any], target_column: str) -> None:
    label = str(metrics["type"])
    step = metrics.get("step", 0)
    parts = [
        f"{label} step={step:,}",
        f"loss={metrics.get(f'{label}_loss', 0.0):.6f}",
        f"windows={metrics.get(f'{label}_windows', 0):,.0f}",
    ]
    for horizon in range(1, 1 + 3):
        mae_key = f"{label}_h{horizon}_{target_column}_mae_bps"
        dir_key = f"{label}_h{horizon}_{target_column}_dir_acc_pct"
        edge_key = f"{label}_h{horizon}_{target_column}_edge_vs_naive_bps"
        corr_key = f"{label}_h{horizon}_{target_column}_change_corr"
        if mae_key in metrics:
            parts.append(
                f"h{horizon}_mae={metrics[mae_key]:.3f}bps "
                f"h{horizon}_dir={metrics[dir_key]:.2f}% "
                f"h{horizon}_edge={metrics[edge_key]:.3f}bps "
                f"h{horizon}_corr={metrics[corr_key]:.3f}"
            )
    print(" | ".join(parts), flush=True)


def normalize_inputs(values: np.ndarray, stats: NormalizationStats, current_close: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if stats.mode == "train_split":
        return ((values - stats.input_mean) / stats.input_std).astype(np.float32)

    normalized = np.empty_like(values, dtype=np.float32)
    close_anchor = max(float(current_close), 1e-6)
    normalized[:, 0:4] = values[:, 0:4] / close_anchor - 1.0

    size_columns = (4, 5, 7, 8, 9)
    for column in size_columns:
        logged = np.log1p(np.maximum(values[:, column], 0.0))
        mean = float(logged.mean())
        std = float(logged.std())
        normalized[:, column] = (logged - mean) / max(std, 1e-6)

    normalized[:, 6] = np.clip(values[:, 6] / 1000.0, -1.0, 1.0)
    normalized[:, 10] = np.clip(values[:, 10], -1.0, 1.0)
    normalized[:, 11] = np.clip(values[:, 11], 0.0, 1.0)
    return np.nan_to_num(normalized, nan=0.0, posinf=10.0, neginf=-10.0).astype(np.float32)


def normalize_target(values: np.ndarray, stats: NormalizationStats, current_close: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if stats.mode == "train_split":
        return ((values - stats.target_mean) / stats.target_std).astype(np.float32)
    return (values / max(float(current_close), 1e-6) - 1.0).astype(np.float32)


def denormalize_target(values: np.ndarray, stats: NormalizationStats, current_close: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if stats.mode == "train_split":
        return values * stats.target_std.astype(np.float64) + stats.target_mean.astype(np.float64)
    return (values + 1.0) * np.asarray(current_close, dtype=np.float64).reshape(-1, 1, 1)


def loss_fn(prediction: torch.Tensor, target: torch.Tensor, loss_name: str) -> torch.Tensor:
    if loss_name == "smooth_l1":
        return torch.nn.functional.smooth_l1_loss(prediction, target)
    return torch.nn.functional.mse_loss(prediction, target)


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


def correlation_from_sums(
    *,
    count: int,
    x_sum: float,
    y_sum: float,
    x_sq_sum: float,
    y_sq_sum: float,
    cross_sum: float,
) -> float:
    numerator = count * cross_sum - x_sum * y_sum
    x_denominator = count * x_sq_sum - x_sum * x_sum
    y_denominator = count * y_sq_sum - y_sum * y_sum
    denominator = math.sqrt(max(x_denominator, 0.0) * max(y_denominator, 0.0))
    if denominator <= 0.0:
        return math.nan
    return float(numerator / denominator)


def save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    step: int,
    args: argparse.Namespace,
    data_config: DataConfig,
    stats: NormalizationStats,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "step": step,
            "args": vars(args),
            "data": data_config_to_dict(data_config),
            "normalization": normalization_to_dict(stats),
        },
        path,
    )


def make_output_dir(data_config: DataConfig, args: argparse.Namespace) -> Path:
    root = data_config.processed_root / "models" / "inhouse_lstm"
    if args.output_name:
        return root / args.output_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"lstm_actual_ctx{data_config.context_length}_h{data_config.horizon}_{data_config.train_start_date}_{data_config.test_end_date}_{timestamp}"
    return root / name


def metadata_payload(
    args: argparse.Namespace,
    data_config: DataConfig,
    train_sessions: list[str],
    validation_sessions: list[str],
    test_sessions: list[str],
    tickers: tuple[str, ...],
    output_dir: Path,
    stats: NormalizationStats,
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
            "actual_feature_columns": list(ACTUAL_FEATURE_COLUMNS),
        },
        "model": {
            "type": "actual_value_lstm",
            "hidden_size": args.hidden_size,
            "layers": args.layers,
            "dropout": args.dropout,
        },
        "normalization": normalization_to_dict(stats),
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
        "tickers": list(config.tickers),
        "max_tickers": config.max_tickers,
        "allow_target_across_session": config.allow_target_across_session,
        "carry_context_across_session": config.carry_context_across_session,
    }


def normalization_to_dict(stats: NormalizationStats) -> dict[str, Any]:
    return {
        "mode": stats.mode,
        "input_columns": list(ACTUAL_FEATURE_COLUMNS),
        "input_mean": [float(value) for value in stats.input_mean],
        "input_std": [float(value) for value in stats.input_std],
        "target_mean": [float(value) for value in stats.target_mean],
        "target_std": [float(value) for value in stats.target_std],
    }


def print_split_summary(
    train_sessions: list[str],
    validation_sessions: list[str],
    test_sessions: list[str],
    tickers: tuple[str, ...],
    config: DataConfig,
) -> None:
    print("Dataset split:", flush=True)
    print(f"  train: {train_sessions[0]} -> {train_sessions[-1]} ({len(train_sessions)} sessions)", flush=True)
    print(f"  validation: {validation_sessions[0]} -> {validation_sessions[-1]} ({len(validation_sessions)} sessions)", flush=True)
    print(f"  test: {test_sessions[0]} -> {test_sessions[-1]} ({len(test_sessions)} sessions)", flush=True)
    print(
        f"  selected_tickers: {len(tickers) if tickers else 'all'} "
        f"carry_context_across_session={config.carry_context_across_session} "
        f"allow_target_across_session={config.allow_target_across_session}",
        flush=True,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
