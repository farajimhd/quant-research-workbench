from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_provider.config import DEFAULT_PROCESSED_ROOT  # noqa: E402
from src.data_provider.store import existing_dates, partition_path  # noqa: E402


DEFAULT_CHRONOS_SRC = Path("D:/TradingCodes/public-codes/chronos-forecasting-main/src")
DEFAULT_MODEL_ID = "autogluon/chronos-2-small"
TARGET_COLUMNS = ("target_close", "target_high", "target_low")
COVARIATE_COLUMNS = ("raw_open", "raw_volume", "raw_transactions")
VARIATE_COUNT = len(TARGET_COLUMNS) + len(COVARIATE_COLUMNS)
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
class ProbeWindow:
    input: dict
    last_close: float
    actual_close: float


@dataclass(slots=True)
class CoveragePlan:
    steps: int
    epochs: int
    full_pass_batches: int
    full_pass_windows: int
    sessions_with_windows: int
    max_windows_per_batch: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream fine-tune Chronos 2 with LoRA on provider-built 1m bars. Targets are raw close, high, and low; "
            "past-only covariates are raw open, volume, and transactions. The script loads one provider session at a "
            "time and yields Chronos-ready batches instead of holding the full corpus in RAM."
        )
    )
    parser.add_argument("--start-date", required=True, help="First provider session date to use, for example 2024-01-02.")
    parser.add_argument("--end-date", required=True, help="Last provider session date to use, for example 2026-04-07.")
    parser.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT), help="Processed provider market_data root.")
    parser.add_argument("--chronos-src", default=str(DEFAULT_CHRONOS_SRC), help="Path to chronos-forecasting src folder.")
    parser.add_argument(
        "--model-id",
        default="",
        help=(
            "Explicit base or fine-tuned Chronos model id/path. If omitted, the script resumes the latest managed "
            "finetuned checkpoint when available, otherwise uses the small base model."
        ),
    )
    parser.set_defaults(resume_latest=True)
    parser.add_argument(
        "--resume-latest",
        dest="resume_latest",
        action="store_true",
        help="Resume from the latest compatible managed finetuned checkpoint when --model-id is omitted.",
    )
    parser.add_argument(
        "--no-resume-latest",
        dest="resume_latest",
        action="store_false",
        help="Ignore managed finetuned checkpoints and start from the small base model when --model-id is omitted.",
    )
    parser.add_argument("--device-map", default="cuda", help='Transformers device_map, e.g. "cuda", "cpu", or "auto".')
    parser.add_argument("--context-length", type=int, default=64, help="Historical bars per training window.")
    parser.add_argument("--min-past", type=int, default=64, help="Minimum historical bars before each training target.")
    parser.add_argument("--prediction-length", type=int, default=1, help="Forecast horizon in bars.")
    parser.add_argument(
        "--num-steps",
        type=int,
        default=0,
        help="LoRA fine-tuning optimizer steps. Use 0 to auto-train for --epochs full streamed passes.",
    )
    parser.add_argument("--epochs", type=int, default=1, help="Full streamed passes used when --num-steps is 0.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2048,
        help="Chronos training batch size measured in variates. Each ticker window uses six variates.",
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
        "--eval-window-count",
        type=int,
        default=3000,
        help="Maximum validation windows used for Chronos eval_loss at each eval point. Use 0 for all validation windows.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=2000,
        help="Top dollar-volume tickers to fine-tune on. Use 0 for all tickers in range.",
    )
    parser.add_argument("--tickers", default="", help="Comma-separated ticker override. If set, --max-tickers is ignored.")
    parser.add_argument(
        "--session-scope",
        choices=["all", "regular"],
        default="all",
        help="Use all provider bars or regular-session bars only.",
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
    parser.add_argument("--seed", type=int, default=17, help="Random seed for session order, validation sampling, and probes.")
    parser.add_argument(
        "--output-name",
        default="",
        help="Optional run folder name under processed_root/models/chronos2. Defaults to a timestamped name.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Load one sample session and build one batch without loading Chronos.")
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


def positive_expr(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Float32, strict=False)
    return pl.when(value > 0.0).then(value).otherwise(None)


def nonnegative_expr(column: str, columns: set[str]) -> pl.Expr:
    if column not in columns:
        return pl.lit(0.0, dtype=pl.Float32)
    value = pl.col(column).cast(pl.Float32, strict=False)
    return pl.when(value >= 0.0).then(value).otherwise(0.0)


def load_session_frame(processed_root: Path, session: str, tickers: list[str] | None, session_scope: str) -> pl.DataFrame:
    path = partition_path(processed_root, "bars", "1m", session)
    scan = pl.scan_parquet(str(path), missing_columns="insert", extra_columns="ignore")
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
            positive_expr("close").alias("target_close"),
            positive_expr("high").alias("target_high"),
            positive_expr("low").alias("target_low"),
            positive_expr("open").alias("raw_open"),
            nonnegative_expr("volume", names).alias("raw_volume"),
            nonnegative_expr("transactions", names).alias("raw_transactions"),
        )
        .filter(
            pl.col("target_close").is_not_null()
            & pl.col("target_high").is_not_null()
            & pl.col("target_low").is_not_null()
        )
        .select("ticker", "session_date", "bar_time_market", *TARGET_COLUMNS, *COVARIATE_COLUMNS)
        .sort(["ticker", "bar_time_market"])
        .collect()
    )


def session_arrays(frame: pl.DataFrame) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    for ticker_frame in frame.partition_by("ticker", maintain_order=True):
        if ticker_frame.is_empty():
            continue
        ticker = str(ticker_frame.get_column("ticker")[0])
        rows = [
            ticker_frame.get_column(column).cast(pl.Float32, strict=False).to_numpy()
            for column in (*TARGET_COLUMNS, *COVARIATE_COLUMNS)
        ]
        values = np.stack(rows).astype(np.float32, copy=False)
        if values.shape[1] >= 2 and np.isfinite(values[: len(TARGET_COLUMNS)]).all(axis=0).any():
            arrays[ticker] = values
    return arrays


def session_origins(frame: pl.DataFrame, min_past: int, prediction_length: int) -> dict[object, list[tuple[str, int]]]:
    indexed = frame.with_columns(
        pl.int_range(0, pl.len()).over("ticker").alias("__ticker_index"),
        pl.len().over("ticker").alias("__ticker_len"),
    )
    eligible = indexed.filter(
        (pl.col("__ticker_index") >= min_past - 1)
        & (pl.col("__ticker_index") <= pl.col("__ticker_len") - prediction_length - 1)
    )
    origins: dict[object, list[tuple[str, int]]] = {}
    for row in eligible.select(["bar_time_market", "ticker", "__ticker_index"]).iter_rows(named=True):
        origins.setdefault(row["bar_time_market"], []).append((str(row["ticker"]), int(row["__ticker_index"])))
    return origins


def build_batch(
    *,
    arrays_by_ticker: dict[str, np.ndarray],
    origin_refs: list[tuple[str, int]],
    context_length: int,
    prediction_length: int,
    output_patch_size: int,
):
    import torch

    contexts: list[np.ndarray] = []
    future_targets: list[np.ndarray] = []
    future_covariates: list[np.ndarray] = []
    group_ids: list[np.ndarray] = []
    for group_id, (ticker, origin_index) in enumerate(origin_refs):
        values = arrays_by_ticker[ticker]
        start_index = max(0, origin_index + 1 - context_length)
        history = values[:, start_index : origin_index + 1]
        context = np.full((VARIATE_COUNT, context_length), np.nan, dtype=np.float32)
        context[:, -history.shape[1] :] = history

        future = np.full((VARIATE_COUNT, prediction_length), np.nan, dtype=np.float32)
        future[: len(TARGET_COLUMNS), :] = values[
            : len(TARGET_COLUMNS), origin_index + 1 : origin_index + 1 + prediction_length
        ]
        future_cov = np.full((VARIATE_COUNT, prediction_length), np.nan, dtype=np.float32)

        contexts.append(context)
        future_targets.append(future)
        future_covariates.append(future_cov)
        group_ids.append(np.full(VARIATE_COUNT, group_id, dtype=np.int64))

    return {
        "context": torch.from_numpy(np.concatenate(contexts, axis=0)),
        "future_target": torch.from_numpy(np.concatenate(future_targets, axis=0)),
        "future_covariates": torch.from_numpy(np.concatenate(future_covariates, axis=0)),
        "group_ids": torch.from_numpy(np.concatenate(group_ids, axis=0)),
        "num_output_patches": math.ceil(prediction_length / output_patch_size),
    }


def batched_refs(refs: list[tuple[str, int]], max_windows: int) -> Iterable[list[tuple[str, int]]]:
    for index in range(0, len(refs), max_windows):
        yield refs[index : index + max_windows]


def count_stream_coverage(
    *,
    processed_root: Path,
    sessions: list[str],
    tickers: list[str] | None,
    session_scope: str,
    min_past: int,
    prediction_length: int,
    batch_size: int,
    epochs: int,
) -> CoveragePlan:
    max_windows_per_batch = max(1, batch_size // VARIATE_COUNT)
    full_pass_batches = 0
    full_pass_windows = 0
    sessions_with_windows = 0
    for session_index, session in enumerate(sessions, start=1):
        frame = load_session_frame(processed_root, session, tickers, session_scope)
        if frame.is_empty():
            print(f"Coverage count {session_index}/{len(sessions)} {session}: rows=0 windows=0 batches=0", flush=True)
            continue
        arrays = session_arrays(frame)
        origins_by_time = session_origins(frame, min_past, prediction_length)
        session_windows = 0
        session_batches = 0
        for refs in origins_by_time.values():
            valid_windows = sum(1 for ticker, _origin_index in refs if ticker in arrays)
            if valid_windows:
                session_windows += valid_windows
                session_batches += math.ceil(valid_windows / max_windows_per_batch)
        if session_windows:
            sessions_with_windows += 1
        full_pass_windows += session_windows
        full_pass_batches += session_batches
        print(
            f"Coverage count {session_index}/{len(sessions)} {session}: "
            f"rows={frame.height:,} windows={session_windows:,} batches={session_batches:,} "
            f"total_batches={full_pass_batches:,}",
            flush=True,
        )

    if full_pass_batches <= 0:
        raise SystemExit("No train batches were found. Lower --min-past or choose a more active date range.")
    return CoveragePlan(
        steps=full_pass_batches * epochs,
        epochs=epochs,
        full_pass_batches=full_pass_batches,
        full_pass_windows=full_pass_windows,
        sessions_with_windows=sessions_with_windows,
        max_windows_per_batch=max_windows_per_batch,
    )


class SessionStreamingChronosDataset:
    def __init__(
        self,
        *,
        processed_root: Path,
        sessions: list[str],
        tickers: list[str] | None,
        session_scope: str,
        context_length: int,
        min_past: int,
        prediction_length: int,
        batch_size: int,
        output_patch_size: int,
        seed: int,
        mode: str,
        max_windows: int = 0,
    ) -> None:
        self.processed_root = processed_root
        self.sessions = sessions
        self.tickers = tickers
        self.session_scope = session_scope
        self.context_length = context_length
        self.min_past = min_past
        self.prediction_length = prediction_length
        self.batch_size = batch_size
        self.output_patch_size = output_patch_size
        self.seed = seed
        self.mode = mode
        self.max_windows = max_windows

    def __iter__(self) -> Iterator[dict]:
        rng = np.random.default_rng(self.seed)
        max_windows_per_batch = max(1, self.batch_size // VARIATE_COUNT)
        emitted_windows = 0
        while True:
            sessions = list(self.sessions)
            if self.mode == "train":
                rng.shuffle(sessions)
            for session in sessions:
                frame = load_session_frame(self.processed_root, session, self.tickers, self.session_scope)
                if frame.is_empty():
                    continue
                arrays = session_arrays(frame)
                origins_by_time = session_origins(frame, self.min_past, self.prediction_length)
                times = list(origins_by_time)
                if self.mode == "train":
                    rng.shuffle(times)
                for timestamp in times:
                    refs = [ref for ref in origins_by_time[timestamp] if ref[0] in arrays]
                    if not refs:
                        continue
                    if self.mode == "train":
                        rng.shuffle(refs)
                    for chunk_refs in batched_refs(refs, max_windows_per_batch):
                        if self.max_windows > 0 and emitted_windows >= self.max_windows:
                            return
                        if self.max_windows > 0:
                            remaining = self.max_windows - emitted_windows
                            chunk_refs = chunk_refs[:remaining]
                        emitted_windows += len(chunk_refs)
                        yield build_batch(
                            arrays_by_ticker=arrays,
                            origin_refs=chunk_refs,
                            context_length=self.context_length,
                            prediction_length=self.prediction_length,
                            output_patch_size=self.output_patch_size,
                        )
            if self.mode != "train":
                return


def iter_probe_candidates(
    *,
    processed_root: Path,
    sessions: list[str],
    tickers: list[str] | None,
    session_scope: str,
    context_length: int,
    min_past: int,
    prediction_length: int,
) -> Iterator[ProbeWindow]:
    horizon = 1
    for session in sessions:
        frame = load_session_frame(processed_root, session, tickers, session_scope)
        if frame.is_empty():
            continue
        arrays = session_arrays(frame)
        origins_by_time = session_origins(frame, min_past, prediction_length)
        for refs in origins_by_time.values():
            for ticker, origin_index in refs:
                values = arrays.get(ticker)
                if values is None:
                    continue
                start_index = max(0, origin_index + 1 - context_length)
                last_close = float(values[0, origin_index])
                actual_close = float(values[0, origin_index + horizon])
                if not np.isfinite(last_close) or not np.isfinite(actual_close) or last_close <= 0.0 or actual_close <= 0.0:
                    continue
                yield ProbeWindow(
                    input={
                        "target": values[: len(TARGET_COLUMNS), start_index : origin_index + 1],
                        "past_covariates": {
                            column: values[len(TARGET_COLUMNS) + idx, start_index : origin_index + 1]
                            for idx, column in enumerate(COVARIATE_COLUMNS)
                        },
                    },
                    last_close=last_close,
                    actual_close=actual_close,
                )


def build_probe_windows(
    *,
    processed_root: Path,
    sessions: list[str],
    tickers: list[str] | None,
    session_scope: str,
    context_length: int,
    min_past: int,
    prediction_length: int,
    count: int,
    seed: int,
) -> list[ProbeWindow]:
    if count <= 0 or not sessions:
        return []
    rng = np.random.default_rng(seed)
    reservoir: list[ProbeWindow] = []
    seen = 0
    for window in iter_probe_candidates(
        processed_root=processed_root,
        sessions=sessions,
        tickers=tickers,
        session_scope=session_scope,
        context_length=context_length,
        min_past=min_past,
        prediction_length=prediction_length,
    ):
        seen += 1
        if len(reservoir) < count:
            reservoir.append(window)
        else:
            replace_index = int(rng.integers(0, seen))
            if replace_index < count:
                reservoir[replace_index] = window
    return reservoir


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


def apply_or_continue_lora(model):
    if hasattr(model, "peft_config"):
        if hasattr(model, "get_nb_trainable_parameters"):
            trainable, total = model.get_nb_trainable_parameters()
            print(
                f"Continuing existing LoRA: trainable_parameters={trainable:,} total_parameters={total:,}",
                flush=True,
            )
        else:
            print("Continuing existing LoRA checkpoint.", flush=True)
        return model

    from peft import LoraConfig, get_peft_model

    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=[
            "self_attention.q",
            "self_attention.v",
            "self_attention.k",
            "self_attention.o",
            "output_patch_embedding.output_layer",
        ],
    )
    model = get_peft_model(model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"Using LoRA: trainable_parameters={trainable:,} total_parameters={total:,}", flush=True)
    return model


def compatible_metadata(run_dir: Path) -> bool:
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        return True
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    data = metadata.get("data", {})
    targets = data.get("targets")
    covariates = data.get("past_covariates")
    if targets is None and covariates is None:
        return True
    return targets == list(TARGET_COLUMNS) and covariates == list(COVARIATE_COLUMNS)


def latest_managed_checkpoint(processed_root: Path, output_dir: Path) -> Path | None:
    models_root = processed_root / "models" / "chronos2"
    if not models_root.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    output_dir_resolved = output_dir.resolve()
    for run_dir in models_root.iterdir():
        if not run_dir.is_dir():
            continue
        try:
            if run_dir.resolve() == output_dir_resolved:
                continue
        except OSError:
            continue
        checkpoint = run_dir / "finetuned-ckpt"
        if not checkpoint.is_dir() or not compatible_metadata(run_dir):
            continue
        try:
            modified = checkpoint.stat().st_mtime
        except OSError:
            continue
        candidates.append((modified, checkpoint))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def resolve_model_id(processed_root: Path, output_dir: Path, args: argparse.Namespace) -> tuple[str, str]:
    if args.model_id:
        return args.model_id, "explicit"
    if args.resume_latest:
        latest = latest_managed_checkpoint(processed_root, output_dir)
        if latest is not None:
            return str(latest), "latest_managed_finetuned"
    return DEFAULT_MODEL_ID, "default_small_base"


def make_training_args(args: argparse.Namespace, output_dir: Path, use_cpu: bool, has_sm80: bool, has_validation: bool):
    from transformers.training_args import TrainingArguments

    training_kwargs: dict = dict(
        output_dir=str(output_dir),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        lr_scheduler_type="linear",
        warmup_ratio=0.0,
        optim="adamw_torch_fused",
        logging_strategy="steps",
        logging_steps=args.logging_steps,
        disable_tqdm=False,
        report_to="none",
        max_steps=args.num_steps,
        gradient_accumulation_steps=1,
        dataloader_num_workers=0,
        tf32=has_sm80 and not use_cpu,
        bf16=has_sm80 and not use_cpu,
        save_only_model=True,
        prediction_loss_only=True,
        save_total_limit=1,
        save_strategy="no",
        save_steps=None,
        eval_strategy="no",
        eval_steps=None,
        load_best_model_at_end=False,
        metric_for_best_model=None,
        use_cpu=use_cpu,
    )
    if has_validation:
        training_kwargs.update(
            save_strategy="steps",
            save_steps=args.eval_steps,
            eval_strategy="steps",
            eval_steps=args.eval_steps,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            label_names=["future_target"],
        )
    return TrainingArguments(**training_kwargs)


def output_dir_for_args(processed_root: Path, args: argparse.Namespace) -> Path:
    if args.output_name:
        name = args.output_name
    else:
        name = (
            f"chronos2_small_lora_1m_stream_targets_chl_ctx{args.context_length}_h{args.prediction_length}_"
            f"{args.start_date}_{args.end_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    return processed_root / "models" / "chronos2" / name


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def validate_args(args: argparse.Namespace) -> None:
    if date.fromisoformat(args.end_date) < date.fromisoformat(args.start_date):
        raise SystemExit("--end-date must be >= --start-date.")
    if args.context_length < 1 or args.min_past < 1:
        raise SystemExit("--context-length and --min-past must be positive.")
    if args.prediction_length < 1:
        raise SystemExit("--prediction-length must be positive.")
    if args.num_steps < 0 or args.epochs < 1:
        raise SystemExit("--num-steps cannot be negative and --epochs must be positive.")
    if args.eval_steps < 1 or args.logging_steps < 1:
        raise SystemExit("--eval-steps and --logging-steps must be positive.")
    if args.batch_size < VARIATE_COUNT or args.probe_batch_size < VARIATE_COUNT:
        raise SystemExit(f"--batch-size and --probe-batch-size must be at least {VARIATE_COUNT}.")
    if args.eval_window_count < 0 or args.probe_window_count < 0:
        raise SystemExit("--eval-window-count and --probe-window-count cannot be negative.")


def dry_run(processed_root: Path, sessions: list[str], train_sessions: list[str], tickers: list[str] | None, args: argparse.Namespace) -> dict:
    sample_session = train_sessions[0]
    frame = load_session_frame(processed_root, sample_session, tickers, args.session_scope)
    arrays = session_arrays(frame)
    origins = session_origins(frame, args.min_past, args.prediction_length)
    first_refs: list[tuple[str, int]] = []
    for refs in origins.values():
        first_refs = [ref for ref in refs if ref[0] in arrays][: max(1, args.batch_size // VARIATE_COUNT)]
        if first_refs:
            break
    if not first_refs:
        raise SystemExit("Dry run could not create a sample batch. Lower --min-past or choose a more active date range.")
    batch_rows = len(first_refs) * VARIATE_COUNT
    return {
        "sample_session": sample_session,
        "sessions": len(sessions),
        "train_sessions": len(train_sessions),
        "sample_rows": frame.height,
        "sample_tickers": len(arrays),
        "sample_windows": len(first_refs),
        "context_shape": [batch_rows, args.context_length],
        "future_target_shape": [batch_rows, args.prediction_length],
        "future_covariates_shape": [batch_rows, args.prediction_length],
        "targets": list(TARGET_COLUMNS),
        "past_covariates": list(COVARIATE_COLUMNS),
    }


def main() -> None:
    args = parse_args()
    validate_args(args)

    processed_root = Path(args.processed_root)
    add_chronos_src(Path(args.chronos_src))
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    sessions = available_sessions(processed_root, start, end)
    if args.validation_sessions > 0 and len(sessions) <= args.validation_sessions:
        raise SystemExit("Date range must contain more sessions than --validation-sessions.")

    validation_session_list = sessions[-args.validation_sessions :] if args.validation_sessions > 0 else []
    train_session_list = sessions[: len(sessions) - len(validation_session_list)]
    output_dir = output_dir_for_args(processed_root, args)
    resolved_model_id, model_source = resolve_model_id(processed_root, output_dir, args)

    print(
        f"Preparing streaming training from {len(sessions)} sessions: "
        f"train={len(train_session_list)} validation={len(validation_session_list)}",
        flush=True,
    )
    print(f"Model source: {model_source} ({resolved_model_id})", flush=True)
    tickers = selected_tickers(processed_root, train_session_list, args)
    if tickers:
        print(f"Selected {len(tickers)} tickers.", flush=True)
    else:
        print("Using all tickers in range.", flush=True)

    coverage_plan = (
        count_stream_coverage(
            processed_root=processed_root,
            sessions=train_session_list,
            tickers=tickers,
            session_scope=args.session_scope,
            min_past=args.min_past,
            prediction_length=args.prediction_length,
            batch_size=args.batch_size,
            epochs=args.epochs,
        )
        if args.num_steps == 0
        else None
    )
    if coverage_plan is not None:
        args.num_steps = coverage_plan.steps
        print(
            f"Auto training steps: full_pass_batches={coverage_plan.full_pass_batches:,} "
            f"epochs={coverage_plan.epochs} max_steps={args.num_steps:,} "
            f"full_pass_windows={coverage_plan.full_pass_windows:,}",
            flush=True,
        )
    else:
        max_windows_per_batch = max(1, args.batch_size // VARIATE_COUNT)
        planned_windows = args.num_steps * max_windows_per_batch
        print(
            f"Manual training steps: max_steps={args.num_steps:,} "
            f"approx_max_windows={planned_windows:,} max_windows_per_batch={max_windows_per_batch:,}",
            flush=True,
        )

    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "prepared",
        "output_dir": str(output_dir),
        "finetuned_checkpoint": str(output_dir / "finetuned-ckpt"),
        "model_source": model_source,
        "loaded_model_id": resolved_model_id,
        "args": vars(args),
        "data": {
            "sessions": len(sessions),
            "train_sessions": len(train_session_list),
            "validation_sessions": len(validation_session_list),
            "train_start": train_session_list[0],
            "train_end": train_session_list[-1],
            "validation_start": validation_session_list[0] if validation_session_list else None,
            "validation_end": validation_session_list[-1] if validation_session_list else None,
            "streaming": True,
            "targets": list(TARGET_COLUMNS),
            "past_covariates": list(COVARIATE_COLUMNS),
            "variates_per_window": VARIATE_COUNT,
            "coverage_plan": {
                "mode": "auto_full_pass" if coverage_plan is not None else "manual_steps",
                "steps": args.num_steps,
                "epochs": args.epochs,
                "full_pass_batches": coverage_plan.full_pass_batches if coverage_plan is not None else None,
                "full_pass_windows": coverage_plan.full_pass_windows if coverage_plan is not None else None,
                "sessions_with_windows": coverage_plan.sessions_with_windows if coverage_plan is not None else None,
                "max_windows_per_batch": (
                    coverage_plan.max_windows_per_batch
                    if coverage_plan is not None
                    else max(1, args.batch_size // VARIATE_COUNT)
                ),
            },
        },
    }

    if args.dry_run:
        metadata["status"] = "dry_run_complete"
        metadata["dry_run"] = dry_run(processed_root, sessions, train_session_list, tickers, args)
        write_json(output_dir / "metadata.json", metadata)
        print(json.dumps(metadata["dry_run"], indent=2, sort_keys=True), flush=True)
        print(f"Dry run complete. Metadata written to {output_dir / 'metadata.json'}", flush=True)
        return

    require_lora_dependency()
    print("Loading Chronos model after streaming data plan is ready...", flush=True)
    pipeline = load_chronos_pipeline(resolved_model_id, args.device_map)

    import torch
    from chronos.chronos2.pipeline import Chronos2Pipeline
    from chronos.chronos2.trainer import Chronos2Trainer, EvaluateAndSaveFinalStepCallback
    from torch.utils.data import IterableDataset
    from transformers.trainer_callback import PrinterCallback

    model = apply_or_continue_lora(pipeline.model)
    use_cpu = str(model.device) == "cpu"
    has_sm80 = torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8
    output_patch_size = pipeline.model_output_patch_size
    StreamingDataset = type("StreamingSessionChronosDataset", (SessionStreamingChronosDataset, IterableDataset), {})

    train_dataset = StreamingDataset(
        processed_root=processed_root,
        sessions=train_session_list,
        tickers=tickers,
        session_scope=args.session_scope,
        context_length=args.context_length,
        min_past=args.min_past,
        prediction_length=args.prediction_length,
        batch_size=args.batch_size,
        output_patch_size=output_patch_size,
        seed=args.seed,
        mode="train",
    )
    validation_dataset = (
        StreamingDataset(
            processed_root=processed_root,
            sessions=validation_session_list,
            tickers=tickers,
            session_scope=args.session_scope,
            context_length=args.context_length,
            min_past=args.min_past,
            prediction_length=args.prediction_length,
            batch_size=args.batch_size,
            output_patch_size=output_patch_size,
            seed=args.seed + 1,
            mode="validation",
            max_windows=args.eval_window_count,
        )
        if validation_session_list
        else None
    )

    probe_windows = build_probe_windows(
        processed_root=processed_root,
        sessions=validation_session_list,
        tickers=tickers,
        session_scope=args.session_scope,
        context_length=args.context_length,
        min_past=args.min_past,
        prediction_length=args.prediction_length,
        count=args.probe_window_count,
        seed=args.seed + 2,
    )
    metadata["data"]["probe_windows"] = len(probe_windows)
    metadata["probe_metrics_path"] = str(output_dir / "probe_metrics.jsonl") if probe_windows else None
    metadata["status"] = "training"
    metadata["training_started_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(output_dir / "metadata.json", metadata)

    callbacks = [EvaluateAndSaveFinalStepCallback()] if validation_dataset is not None else []
    if probe_windows and validation_dataset is not None:
        print(
            f"Probe evaluation enabled: windows={len(probe_windows):,} eval_steps={args.eval_steps} "
            f"batch_size={args.probe_batch_size} metrics={output_dir / 'probe_metrics.jsonl'}",
            flush=True,
        )
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
    elif args.probe_window_count > 0:
        print("Probe evaluation disabled: no eligible validation windows.", flush=True)

    training_args = make_training_args(
        args=args,
        output_dir=output_dir,
        use_cpu=use_cpu,
        has_sm80=has_sm80,
        has_validation=validation_dataset is not None,
    )
    if not use_cpu:
        training_args._n_gpu = 1

    trainer = Chronos2Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        callbacks=callbacks,
    )
    trainer.pop_callback(PrinterCallback)
    trainer.train()

    model.chronos_config.context_length = max(model.chronos_config.context_length, args.context_length)
    model.chronos_config.max_output_patches = max(
        model.chronos_config.max_output_patches, math.ceil(args.prediction_length / output_patch_size)
    )
    model.config.chronos_config = model.chronos_config.__dict__
    finetuned_pipeline = Chronos2Pipeline(model=model)
    finetuned_path = output_dir / "finetuned-ckpt"
    finetuned_pipeline.save_pretrained(finetuned_path)

    metadata["status"] = "complete"
    metadata["completed_at"] = datetime.now().isoformat(timespec="seconds")
    metadata["finetuned_checkpoint"] = str(finetuned_path)
    write_json(output_dir / "metadata.json", metadata)
    print(f"Fine-tuned model saved to {finetuned_path}", flush=True)


if __name__ == "__main__":
    main()
