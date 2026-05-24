from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_provider.config import DEFAULT_PROCESSED_ROOT  # noqa: E402
from src.data_provider.store import existing_dates, partition_path  # noqa: E402


DEFAULT_CHRONOS_SRC = Path("D:/TradingCodes/public-codes/chronos-forecasting-main/src")
DEFAULT_MODEL_ID = "autogluon/chronos-2-small"
COVARIATE_COLUMNS = ("raw_open", "raw_high", "raw_low", "raw_volume", "raw_transactions")
SOURCE_COLUMNS = (
    "ticker",
    "session_date",
    "bar_time_market",
    "minute_of_day",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
)


@dataclass(slots=True)
class PreparedSeries:
    train_inputs: list[dict]
    validation_inputs: list[dict] | None
    metadata: dict


@dataclass(slots=True)
class ProbeWindow:
    input: dict
    last_close: float
    actual_close: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fine-tune Chronos 2 with LoRA on provider-built 1m bars. Inputs are raw close target plus raw open, "
            "high, low, volume, and transactions past covariates. Data is loaded and preprocessed before the GPU "
            "model is loaded."
        )
    )
    parser.add_argument("--start-date", required=True, help="First provider session date to use, for example 2024-01-02.")
    parser.add_argument("--end-date", required=True, help="Last provider session date to use, for example 2026-04-07.")
    parser.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT), help="Processed provider market_data root.")
    parser.add_argument("--chronos-src", default=str(DEFAULT_CHRONOS_SRC), help="Path to chronos-forecasting src folder.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="Base Chronos 2 model id or local model path.")
    parser.add_argument("--device-map", default="cuda", help='Transformers device_map, e.g. "cuda", "cpu", or "auto".')
    parser.add_argument("--context-length", type=int, default=64, help="Chronos context length for fine-tuning.")
    parser.add_argument("--min-past", type=int, default=64, help="Minimum historical bars before each training target.")
    parser.add_argument("--prediction-length", type=int, default=1, help="Forecast horizon in bars.")
    parser.add_argument("--num-steps", type=int, default=2000, help="LoRA fine-tuning optimizer steps.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2048,
        help="Chronos training batch size measured in variates, not ticker series.",
    )
    parser.add_argument("--learning-rate", type=float, default=1e-4, help="LoRA learning rate.")
    parser.add_argument("--logging-steps", type=int, default=50, help="Trainer logging interval.")
    parser.add_argument("--eval-steps", type=int, default=500, help="Validation/probe evaluation interval in training steps.")
    parser.add_argument(
        "--validation-sessions",
        type=int,
        default=20,
        help="Number of latest sessions reserved for validation. Use 0 to disable validation.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=2000,
        help="Top dollar-volume tickers to fine-tune on. Use 0 for all tickers in range.",
    )
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument(
        "--min-bars-per-ticker",
        type=int,
        default=512,
        help="Drop ticker splits shorter than this many bars before preparing Chronos inputs.",
    )
    parser.add_argument(
        "--session-scope",
        choices=["all", "regular"],
        default="all",
        help="Use all provider bars or regular-session bars only.",
    )
    parser.add_argument(
        "--load-chunk-sessions",
        type=int,
        default=20,
        help="Number of provider sessions loaded per Polars chunk while preparing ticker arrays.",
    )
    parser.add_argument(
        "--probe-window-count",
        type=int,
        default=3000,
        help="Fixed validation forecast windows used for trading-metric probes. Use 0 to disable probes.",
    )
    parser.add_argument(
        "--probe-batch-size",
        type=int,
        default=1024,
        help="Chronos prediction batch size for probe evaluation, measured in variates.",
    )
    parser.add_argument(
        "--probe-direction-threshold-bps",
        type=float,
        default=0.0,
        help="Return threshold for probe up/down/flat direction labels, in basis points.",
    )
    parser.add_argument("--probe-seed", type=int, default=17, help="Random seed for fixed validation probe windows.")
    parser.add_argument(
        "--output-name",
        default="",
        help="Optional run folder name under processed_root/models/chronos2. Defaults to a timestamped name.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Prepare data and metadata without loading or training Chronos.")
    return parser.parse_args()


def add_chronos_src(path: Path) -> None:
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


def available_sessions(processed_root: Path, start: date, end: date) -> list[str]:
    sessions = [
        session
        for session in existing_dates(processed_root, "bars", "1m")
        if start.isoformat() <= session <= end.isoformat()
    ]
    if not sessions:
        raise SystemExit(f"No provider 1m bars found between {start} and {end} under {processed_root}.")
    return sessions


def parse_tickers(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def selected_tickers(processed_root: Path, sessions: list[str], args: argparse.Namespace) -> list[str] | None:
    requested = parse_tickers(args.tickers)
    if requested:
        return requested
    if args.max_tickers <= 0:
        return None

    paths = [partition_path(processed_root, "bars", "1m", session) for session in sessions]
    scan = pl.scan_parquet([str(path) for path in paths], missing_columns="insert", extra_columns="ignore")
    ranking = (
        scan.select("ticker", "close", "volume")
        .with_columns(
            (
                pl.col("close").cast(pl.Float64, strict=False).fill_null(0.0)
                * pl.col("volume").cast(pl.Float64, strict=False).fill_null(0.0)
            ).alias("_dollar_volume")
        )
        .group_by("ticker")
        .agg(pl.sum("_dollar_volume").alias("_dollar_volume"))
        .sort("_dollar_volume", descending=True)
        .head(args.max_tickers)
        .collect()
    )
    return [str(value) for value in ranking.get_column("ticker").to_list()]


def session_chunks(sessions: list[str], chunk_size: int) -> Iterable[list[str]]:
    size = max(1, chunk_size)
    for index in range(0, len(sessions), size):
        yield sessions[index : index + size]


def load_raw_chunk(processed_root: Path, sessions: list[str], tickers: list[str] | None, session_scope: str) -> pl.DataFrame:
    paths = [partition_path(processed_root, "bars", "1m", session) for session in sessions]
    scan = pl.scan_parquet([str(path) for path in paths], missing_columns="insert", extra_columns="ignore")
    names = set(scan.collect_schema().names())
    if session_scope == "regular" and "minute_of_day" not in names:
        raise SystemExit("--session-scope regular requires provider column minute_of_day.")
    required = {"ticker", "session_date", "bar_time_market", "open", "high", "low", "close", "volume", "transactions"}
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"Provider bars are missing required columns: {missing}")
    scan = scan.select([column for column in SOURCE_COLUMNS if column in names])
    if tickers:
        scan = scan.filter(pl.col("ticker").is_in(tickers))
    if session_scope == "regular":
        scan = scan.filter((pl.col("minute_of_day") >= 9 * 60 + 30) & (pl.col("minute_of_day") < 16 * 60))
    return (
        scan.with_columns(
            pl.col("close").cast(pl.Float32, strict=False).alias("target_close"),
            pl.col("open").cast(pl.Float32, strict=False).alias("raw_open"),
            pl.col("high").cast(pl.Float32, strict=False).alias("raw_high"),
            pl.col("low").cast(pl.Float32, strict=False).alias("raw_low"),
            pl.when(pl.col("volume").cast(pl.Float32, strict=False) >= 0.0)
            .then(pl.col("volume").cast(pl.Float32, strict=False))
            .otherwise(0.0)
            .alias("raw_volume"),
            pl.when(pl.col("transactions").cast(pl.Float32, strict=False) >= 0.0)
            .then(pl.col("transactions").cast(pl.Float32, strict=False))
            .otherwise(0.0)
            .alias("raw_transactions"),
        )
        .filter(pl.col("target_close") > 0.0)
        .select("ticker", "session_date", "bar_time_market", "target_close", *COVARIATE_COLUMNS)
        .sort(["ticker", "bar_time_market"])
        .collect()
    )


def append_parts(parts_by_ticker: dict[str, dict[str, list]], frame: pl.DataFrame) -> None:
    for ticker_frame in frame.partition_by("ticker", maintain_order=True):
        if ticker_frame.is_empty():
            continue
        ticker = str(ticker_frame.get_column("ticker")[0])
        parts = parts_by_ticker[ticker]
        parts["session_date"].append(ticker_frame.get_column("session_date").cast(pl.Utf8).to_numpy())
        parts["target_close"].append(ticker_frame.get_column("target_close").cast(pl.Float32).to_numpy())
        for column in COVARIATE_COLUMNS:
            parts[column].append(ticker_frame.get_column(column).cast(pl.Float32).to_numpy())


def build_raw_inputs(parts_by_ticker: dict[str, dict[str, list]], train_sessions: set[str], validation_sessions: set[str], args: argparse.Namespace) -> PreparedSeries:
    train_inputs: list[dict] = []
    validation_inputs: list[dict] = []
    train_bars = 0
    validation_bars = 0
    dropped_train = 0
    dropped_validation = 0

    for ticker in sorted(parts_by_ticker):
        parts = parts_by_ticker[ticker]
        session_dates = np.concatenate(parts["session_date"])
        target = np.concatenate(parts["target_close"]).astype(np.float32, copy=False)
        covariates = {column: np.concatenate(parts[column]).astype(np.float32, copy=False) for column in COVARIATE_COLUMNS}

        train_mask = np.isin(session_dates, list(train_sessions))
        validation_mask = np.isin(session_dates, list(validation_sessions))

        train_item = masked_input(target, covariates, train_mask)
        if train_item is not None and len(train_item["target"]) >= args.min_bars_per_ticker:
            train_bars += int(len(train_item["target"]))
            train_inputs.append(train_item)
        else:
            dropped_train += 1

        if validation_sessions:
            validation_item = masked_input(target, covariates, validation_mask)
            if validation_item is not None and len(validation_item["target"]) >= args.min_bars_per_ticker:
                validation_bars += int(len(validation_item["target"]))
                validation_inputs.append(validation_item)
            else:
                dropped_validation += 1

    if not train_inputs:
        raise SystemExit("No train inputs remain after filtering. Lower --min-bars-per-ticker or widen the date range.")

    metadata = {
        "raw_train_series": len(train_inputs),
        "raw_validation_series": len(validation_inputs),
        "train_bars": train_bars,
        "validation_bars": validation_bars,
        "dropped_train_series": dropped_train,
        "dropped_validation_series": dropped_validation,
        "input_channels": {
            "target": "target_close",
            "past_covariates": list(COVARIATE_COLUMNS),
        },
    }
    return PreparedSeries(
        train_inputs=train_inputs,
        validation_inputs=validation_inputs if validation_inputs else None,
        metadata=metadata,
    )


def masked_input(target: np.ndarray, covariates: dict[str, np.ndarray], mask: np.ndarray) -> dict | None:
    if not mask.any():
        return None
    return {
        "target": target[mask],
        "past_covariates": {column: values[mask] for column, values in covariates.items()},
    }


def prepare_chronos_inputs(raw_inputs: list[dict], prediction_length: int, min_past: int, mode: str) -> list[dict]:
    from chronos.chronos2.dataset import prepare_inputs

    return prepare_inputs(raw_inputs, prediction_length=prediction_length, min_past=min_past, mode=mode)


def build_probe_windows(raw_inputs: list[dict] | None, args: argparse.Namespace) -> list[ProbeWindow]:
    if not raw_inputs or args.probe_window_count <= 0:
        return []

    required_context = max(args.context_length, args.min_past)
    horizon = 1
    eligible: list[tuple[int, int, int]] = []
    for item_index, item in enumerate(raw_inputs):
        target = np.asarray(item["target"], dtype=np.float32)
        first_origin = required_context - 1
        last_origin = len(target) - horizon - 1
        if last_origin >= first_origin:
            eligible.append((item_index, first_origin, last_origin))

    if not eligible:
        return []

    rng = np.random.default_rng(args.probe_seed)
    per_series = max(1, math.ceil(args.probe_window_count / len(eligible)))
    selected_refs: list[tuple[int, int]] = []
    for item_index, first_origin, last_origin in eligible:
        available = last_origin - first_origin + 1
        take = min(per_series, available)
        origins = rng.choice(np.arange(first_origin, last_origin + 1), size=take, replace=False)
        selected_refs.extend((item_index, int(origin)) for origin in origins)

    if len(selected_refs) > args.probe_window_count:
        keep = rng.choice(np.arange(len(selected_refs)), size=args.probe_window_count, replace=False)
        selected_refs = [selected_refs[int(index)] for index in keep]

    windows: list[ProbeWindow] = []
    for item_index, origin in selected_refs:
        item = raw_inputs[item_index]
        target = np.asarray(item["target"], dtype=np.float32)
        start = max(0, origin + 1 - args.context_length)
        actual_index = origin + horizon
        last_close = float(target[origin])
        actual_close = float(target[actual_index])
        if not np.isfinite(last_close) or not np.isfinite(actual_close) or last_close <= 0.0 or actual_close <= 0.0:
            continue
        windows.append(
            ProbeWindow(
                input={
                    "target": target[start : origin + 1].astype(np.float32, copy=False),
                    "past_covariates": {
                        column: np.asarray(values, dtype=np.float32)[start : origin + 1]
                        for column, values in item.get("past_covariates", {}).items()
                    },
                },
                last_close=last_close,
                actual_close=actual_close,
            )
        )
    return windows


def log_return(end_value: np.ndarray, start_value: np.ndarray) -> np.ndarray:
    return np.log(end_value / start_value)


def direction_labels(returns: np.ndarray, threshold: float) -> np.ndarray:
    labels = np.zeros(len(returns), dtype=np.int8)
    labels[returns > threshold] = 1
    labels[returns < -threshold] = -1
    return labels


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2 or np.std(left) == 0.0 or np.std(right) == 0.0:
        return float("nan")
    return float(np.corrcoef(left, right)[0, 1])


def make_probe_callback(
    *,
    probe_windows: list[ProbeWindow],
    prediction_length: int,
    context_length: int,
    batch_size: int,
    direction_threshold_bps: float,
    output_path: Path,
):
    from chronos.chronos2.pipeline import Chronos2Pipeline
    from transformers.trainer_callback import TrainerCallback

    class ProbeEvaluationCallback(TrainerCallback):
        def __init__(self) -> None:
            self.probe_inputs = [window.input for window in probe_windows]
            self.last_close = np.array([window.last_close for window in probe_windows], dtype=np.float64)
            self.actual_close = np.array([window.actual_close for window in probe_windows], dtype=np.float64)
            self.batch_size = batch_size
            self.threshold = direction_threshold_bps / 10_000.0
            self.output_path = output_path
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

        def on_evaluate(self, args, state, control, metrics=None, model=None, **kwargs):  # noqa: ANN001
            if model is None or not self.probe_inputs:
                return control

            started = time.perf_counter()
            was_training = bool(getattr(model, "training", False))
            current_batch_size = self.batch_size
            try:
                model.eval()
                while True:
                    try:
                        pipeline = Chronos2Pipeline(model=model)
                        _, means = pipeline.predict_quantiles(
                            self.probe_inputs,
                            prediction_length=prediction_length,
                            quantile_levels=[0.5],
                            batch_size=current_batch_size,
                            context_length=context_length,
                            limit_prediction_length=False,
                        )
                        break
                    except RuntimeError as exc:
                        if "out of memory" not in str(exc).lower() or current_batch_size <= 64:
                            raise
                        current_batch_size = max(64, current_batch_size // 2)
                        try:
                            import torch

                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass

                predicted_close = np.array([float(mean[0, 0].detach().cpu().item()) for mean in means], dtype=np.float64)
                finite = np.isfinite(predicted_close) & np.isfinite(self.actual_close) & np.isfinite(self.last_close)
                finite &= (predicted_close > 0.0) & (self.actual_close > 0.0) & (self.last_close > 0.0)
                if not finite.any():
                    print(f"[probe] step={state.global_step} skipped: no finite predictions", flush=True)
                    return control

                predicted_return = log_return(predicted_close[finite], self.last_close[finite])
                actual_return = log_return(self.actual_close[finite], self.last_close[finite])
                error = predicted_return - actual_return
                pred_direction = direction_labels(predicted_return, self.threshold)
                actual_direction = direction_labels(actual_return, self.threshold)
                abs_error_bps = np.abs(error) * 10_000.0
                naive_abs_error_bps = np.abs(actual_return) * 10_000.0
                row = {
                    "step": int(state.global_step),
                    "n": int(len(actual_return)),
                    "eval_loss": float(metrics["eval_loss"]) if metrics and "eval_loss" in metrics else None,
                    "dir_acc_pct": float(np.mean(pred_direction == actual_direction) * 100.0),
                    "mae_bps": float(np.mean(abs_error_bps)),
                    "rmse_bps": float(np.sqrt(np.mean(np.square(error))) * 10_000.0),
                    "naive_mae_bps": float(np.mean(naive_abs_error_bps)),
                    "edge_vs_naive_bps": float(np.mean(naive_abs_error_bps) - np.mean(abs_error_bps)),
                    "bias_bps": float(np.mean(error) * 10_000.0),
                    "corr": correlation(predicted_return, actual_return),
                    "batch_size": int(current_batch_size),
                    "elapsed_s": float(time.perf_counter() - started),
                }
                with self.output_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
                eval_loss_text = f"{row['eval_loss']:.6f}" if row["eval_loss"] is not None else "na"
                print(
                    "[probe] "
                    f"step={row['step']} n={row['n']:,} "
                    f"dir={row['dir_acc_pct']:.2f}% "
                    f"mae={row['mae_bps']:.2f}bps "
                    f"naive={row['naive_mae_bps']:.2f}bps "
                    f"edge={row['edge_vs_naive_bps']:+.2f}bps "
                    f"bias={row['bias_bps']:+.2f}bps "
                    f"corr={row['corr']:.4f} "
                    f"eval_loss={eval_loss_text} "
                    f"elapsed={row['elapsed_s']:.1f}s",
                    flush=True,
                )
            except Exception as exc:
                print(f"[probe] step={state.global_step} skipped: {type(exc).__name__}: {exc}", flush=True)
            finally:
                if was_training:
                    model.train()
            return control

    return ProbeEvaluationCallback()


def load_chronos_pipeline(model_id: str, device_map: str):
    try:
        from chronos import BaseChronosPipeline
    except ImportError as exc:
        raise SystemExit(
            "Could not import Chronos 2. Install chronos-forecasting dependencies or pass --chronos-src. "
            f"Import error: {exc}"
        ) from exc
    return BaseChronosPipeline.from_pretrained(model_id, device_map=device_map)


def require_lora_dependency() -> None:
    try:
        import peft  # noqa: F401
    except ImportError as exc:
        raise SystemExit("LoRA fine-tuning requires `peft`. Install it in this environment before running training.") from exc


def output_dir_for_args(processed_root: Path, args: argparse.Namespace) -> Path:
    if args.output_name:
        name = args.output_name
    else:
        name = (
            f"chronos2_small_lora_1m_raw_ohlcv_ctx{args.context_length}_h{args.prediction_length}_"
            f"{args.start_date}_{args.end_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    return processed_root / "models" / "chronos2" / name


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise SystemExit("--end-date must be >= --start-date.")
    if args.context_length < 1 or args.min_past < 1:
        raise SystemExit("--context-length and --min-past must be positive.")
    if args.prediction_length < 1:
        raise SystemExit("--prediction-length must be positive.")
    if args.eval_steps < 1 or args.logging_steps < 1:
        raise SystemExit("--eval-steps and --logging-steps must be positive.")
    if args.probe_window_count < 0 or args.probe_batch_size < 1:
        raise SystemExit("--probe-window-count cannot be negative and --probe-batch-size must be positive.")
    if args.min_bars_per_ticker < args.min_past + args.prediction_length:
        raise SystemExit("--min-bars-per-ticker must be at least --min-past + --prediction-length.")

    processed_root = Path(args.processed_root)
    add_chronos_src(Path(args.chronos_src))
    sessions = available_sessions(processed_root, start, end)
    if args.validation_sessions > 0 and len(sessions) <= args.validation_sessions:
        raise SystemExit("Date range must contain more sessions than --validation-sessions.")

    validation_session_list = sessions[-args.validation_sessions :] if args.validation_sessions > 0 else []
    train_session_list = sessions[: len(sessions) - len(validation_session_list)]
    train_sessions = set(train_session_list)
    validation_sessions = set(validation_session_list)

    print(f"Preparing data from {len(sessions)} sessions: train={len(train_sessions)} validation={len(validation_sessions)}", flush=True)
    tickers = selected_tickers(processed_root, train_session_list, args)
    if tickers:
        print(f"Selected {len(tickers)} tickers.", flush=True)
    else:
        print("Using all tickers in range.", flush=True)

    parts_by_ticker: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for chunk_index, chunk_sessions in enumerate(session_chunks(sessions, args.load_chunk_sessions), start=1):
        frame = load_raw_chunk(processed_root, chunk_sessions, tickers, args.session_scope)
        append_parts(parts_by_ticker, frame)
        print(
            f"Loaded chunk {chunk_index}: sessions={chunk_sessions[0]}..{chunk_sessions[-1]} "
            f"rows={frame.height:,} tickers_seen={len(parts_by_ticker):,}",
            flush=True,
        )

    raw_series = build_raw_inputs(parts_by_ticker, train_sessions, validation_sessions, args)
    del parts_by_ticker

    output_dir = output_dir_for_args(processed_root, args)
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "prepared",
        "output_dir": str(output_dir),
        "finetuned_checkpoint": str(output_dir / "finetuned-ckpt"),
        "args": vars(args),
        "data": {
            "sessions": len(sessions),
            "train_sessions": len(train_sessions),
            "validation_sessions": len(validation_sessions),
            "train_start": train_session_list[0],
            "train_end": train_session_list[-1],
            "validation_start": validation_session_list[0] if validation_session_list else None,
            "validation_end": validation_session_list[-1] if validation_session_list else None,
            **raw_series.metadata,
        },
    }
    write_json(output_dir / "metadata.json", metadata)

    if args.dry_run:
        metadata["status"] = "dry_run_complete"
        write_json(output_dir / "metadata.json", metadata)
        print(f"Dry run complete. Metadata written to {output_dir / 'metadata.json'}", flush=True)
        return

    probe_windows = build_probe_windows(raw_series.validation_inputs, args)
    metadata["data"]["probe_windows"] = len(probe_windows)
    metadata["probe_metrics_path"] = str(output_dir / "probe_metrics.jsonl") if probe_windows else None
    write_json(output_dir / "metadata.json", metadata)
    if probe_windows:
        print(
            f"Probe evaluation enabled: windows={len(probe_windows):,} eval_steps={args.eval_steps} "
            f"batch_size={args.probe_batch_size} metrics={output_dir / 'probe_metrics.jsonl'}",
            flush=True,
        )
    elif args.probe_window_count > 0:
        print("Probe evaluation disabled: no eligible validation windows.", flush=True)

    print("Preprocessing Chronos tensors before loading the GPU model...", flush=True)
    train_inputs = prepare_chronos_inputs(raw_series.train_inputs, args.prediction_length, args.min_past, "train")
    validation_inputs = (
        prepare_chronos_inputs(raw_series.validation_inputs, args.prediction_length, args.min_past, "validation")
        if raw_series.validation_inputs
        else None
    )
    metadata["data"]["prepared_train_series"] = len(train_inputs)
    metadata["data"]["prepared_validation_series"] = len(validation_inputs or [])
    write_json(output_dir / "metadata.json", metadata)

    del raw_series

    require_lora_dependency()
    print("Loading Chronos model after data preparation is complete...", flush=True)
    pipeline = load_chronos_pipeline(args.model_id, args.device_map)

    metadata["status"] = "training"
    metadata["training_started_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(output_dir / "metadata.json", metadata)

    callbacks = []
    if probe_windows and validation_inputs:
        callbacks.append(
            make_probe_callback(
                probe_windows=probe_windows,
                prediction_length=args.prediction_length,
                context_length=args.context_length,
                batch_size=args.probe_batch_size,
                direction_threshold_bps=args.probe_direction_threshold_bps,
                output_path=output_dir / "probe_metrics.jsonl",
            )
        )

    finetuned_pipeline = pipeline.fit(
        inputs=train_inputs,
        validation_inputs=validation_inputs,
        finetune_mode="lora",
        prediction_length=args.prediction_length,
        context_length=args.context_length,
        min_past=args.min_past,
        learning_rate=args.learning_rate,
        num_steps=args.num_steps,
        batch_size=args.batch_size,
        output_dir=output_dir,
        finetuned_ckpt_name="finetuned-ckpt",
        convert_inputs=False,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        save_steps=args.eval_steps,
        callbacks=callbacks,
        remove_printer_callback=True,
    )

    metadata["status"] = "complete"
    metadata["completed_at"] = datetime.now().isoformat(timespec="seconds")
    metadata["finetuned_checkpoint"] = str(output_dir / "finetuned-ckpt")
    write_json(output_dir / "metadata.json", metadata)
    print(f"Fine-tuned model saved to {output_dir / 'finetuned-ckpt'}", flush=True)

    del finetuned_pipeline


if __name__ == "__main__":
    main()
