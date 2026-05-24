from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import numpy as np
import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_provider.config import DEFAULT_PROCESSED_ROOT, DataProviderConfig  # noqa: E402
from src.data_provider.provider import MarketDataProvider  # noqa: E402


DEFAULT_CHRONOS_SRC = Path("D:/TradingCodes/public-codes/chronos-forecasting-main/src")
DEFAULT_QUANTILES = [0.1, 0.5, 0.9]
PREDICTION_FIELDS = [
    "ticker",
    "session_date",
    "forecast_origin_time",
    "target_time",
    "horizon_bars",
    "last_close",
    "actual_close",
    "predicted_close",
    "naive_close",
    "actual_return",
    "predicted_return",
    "naive_return",
    "abs_close_error",
    "naive_abs_close_error",
    "abs_return_error",
    "naive_abs_return_error",
    "squared_return_error",
    "naive_squared_return_error",
    "actual_direction",
    "predicted_direction",
    "direction_correct",
    "direction_correct_nonflat",
    "p10_close",
    "p50_close",
    "p90_close",
    "p10_p90_covered",
    "p10_p90_width_bps",
]
TIMESTAMP_METRIC_FIELDS = [
    "forecast_origin_time",
    "n",
    "direction_accuracy_pct",
    "mae_return_bps",
    "naive_mae_return_bps",
    "mae_return_vs_naive",
    "mean_actual_return_bps",
    "mean_predicted_return_bps",
    "spearman_pred_actual",
    "top1_actual_return_bps",
    "top5_mean_actual_return_bps",
    "top10_mean_actual_return_bps",
    "top_decile_mean_actual_return_bps",
    "universe_mean_actual_return_bps",
    "top_decile_excess_actual_return_bps",
    "top_decile_direction_accuracy_pct",
]


@dataclass(frozen=True)
class ForecastOrigin:
    ticker: str
    session_date: str
    origin_index: int
    origin_time: object
    last_close: float


@dataclass
class MetricState:
    n: int = 0
    sum_abs_close_error: float = 0.0
    sum_naive_abs_close_error: float = 0.0
    sum_abs_return_error: float = 0.0
    sum_naive_abs_return_error: float = 0.0
    sum_squared_return_error: float = 0.0
    sum_naive_squared_return_error: float = 0.0
    sum_bias_return: float = 0.0
    sum_actual_return: float = 0.0
    sum_predicted_return: float = 0.0
    direction_correct: int = 0
    nonflat_count: int = 0
    nonflat_direction_correct: int = 0
    pred_up_count: int = 0
    pred_up_actual_up_count: int = 0
    pred_down_count: int = 0
    pred_down_actual_down_count: int = 0
    corr_count: int = 0
    corr_sum_actual: float = 0.0
    corr_sum_predicted: float = 0.0
    corr_sum_actual_sq: float = 0.0
    corr_sum_predicted_sq: float = 0.0
    corr_sum_cross: float = 0.0
    coverage_count: int = 0
    coverage_hit_count: int = 0
    width_count: int = 0
    sum_width_bps: float = 0.0

    def update(self, row: dict) -> None:
        actual = float(row["actual_return"])
        predicted = float(row["predicted_return"])
        self.n += 1
        self.sum_abs_close_error += float(row["abs_close_error"])
        self.sum_naive_abs_close_error += float(row["naive_abs_close_error"])
        self.sum_abs_return_error += float(row["abs_return_error"])
        self.sum_naive_abs_return_error += float(row["naive_abs_return_error"])
        self.sum_squared_return_error += float(row["squared_return_error"])
        self.sum_naive_squared_return_error += float(row["naive_squared_return_error"])
        self.sum_bias_return += predicted - actual
        self.sum_actual_return += actual
        self.sum_predicted_return += predicted

        actual_direction = int(row["actual_direction"])
        predicted_direction = int(row["predicted_direction"])
        if bool(row["direction_correct"]):
            self.direction_correct += 1
        if actual_direction != 0:
            self.nonflat_count += 1
            if bool(row["direction_correct"]):
                self.nonflat_direction_correct += 1
        if predicted_direction > 0:
            self.pred_up_count += 1
            if actual_direction > 0:
                self.pred_up_actual_up_count += 1
        if predicted_direction < 0:
            self.pred_down_count += 1
            if actual_direction < 0:
                self.pred_down_actual_down_count += 1

        if math.isfinite(actual) and math.isfinite(predicted):
            self.corr_count += 1
            self.corr_sum_actual += actual
            self.corr_sum_predicted += predicted
            self.corr_sum_actual_sq += actual * actual
            self.corr_sum_predicted_sq += predicted * predicted
            self.corr_sum_cross += actual * predicted

        if row.get("p10_p90_covered") != "":
            self.coverage_count += 1
            if bool(row["p10_p90_covered"]):
                self.coverage_hit_count += 1
        width = row.get("p10_p90_width_bps")
        if width != "" and width is not None and math.isfinite(float(width)):
            self.width_count += 1
            self.sum_width_bps += float(width)

    def to_row(self, bucket: str) -> dict:
        return {
            "bucket": bucket,
            "n": self.n,
            "mae_close": divide(self.sum_abs_close_error, self.n),
            "naive_mae_close": divide(self.sum_naive_abs_close_error, self.n),
            "mae_return_bps": divide(self.sum_abs_return_error, self.n) * 10_000.0,
            "naive_mae_return_bps": divide(self.sum_naive_abs_return_error, self.n) * 10_000.0,
            "mae_return_vs_naive": divide(
                divide(self.sum_abs_return_error, self.n),
                divide(self.sum_naive_abs_return_error, self.n),
            ),
            "rmse_return_bps": math.sqrt(divide(self.sum_squared_return_error, self.n)) * 10_000.0,
            "naive_rmse_return_bps": math.sqrt(divide(self.sum_naive_squared_return_error, self.n)) * 10_000.0,
            "bias_return_bps": divide(self.sum_bias_return, self.n) * 10_000.0,
            "mean_actual_return_bps": divide(self.sum_actual_return, self.n) * 10_000.0,
            "mean_predicted_return_bps": divide(self.sum_predicted_return, self.n) * 10_000.0,
            "direction_accuracy_pct": divide(self.direction_correct, self.n) * 100.0,
            "direction_accuracy_nonflat_pct": divide(self.nonflat_direction_correct, self.nonflat_count) * 100.0,
            "up_precision_pct": divide(self.pred_up_actual_up_count, self.pred_up_count) * 100.0,
            "down_precision_pct": divide(self.pred_down_actual_down_count, self.pred_down_count) * 100.0,
            "return_corr": self.return_corr(),
            "p10_p90_coverage_pct": divide(self.coverage_hit_count, self.coverage_count) * 100.0,
            "p10_p90_width_bps": divide(self.sum_width_bps, self.width_count),
        }

    def return_corr(self) -> float:
        n = self.corr_count
        if n < 2:
            return float("nan")
        numerator = n * self.corr_sum_cross - self.corr_sum_actual * self.corr_sum_predicted
        actual_var = n * self.corr_sum_actual_sq - self.corr_sum_actual * self.corr_sum_actual
        predicted_var = n * self.corr_sum_predicted_sq - self.corr_sum_predicted * self.corr_sum_predicted
        denominator = math.sqrt(max(actual_var, 0.0) * max(predicted_var, 0.0))
        return numerator / denominator if denominator else float("nan")


@dataclass
class MetricAccumulator:
    overall: MetricState = field(default_factory=MetricState)
    by_horizon: dict[int, MetricState] = field(default_factory=dict)

    def update(self, row: dict) -> None:
        horizon = int(row["horizon_bars"])
        self.overall.update(row)
        self.by_horizon.setdefault(horizon, MetricState()).update(row)

    def rows(self) -> list[dict]:
        return [self.overall.to_row("overall")] + [
            self.by_horizon[horizon].to_row(f"h{horizon}") for horizon in sorted(self.by_horizon)
        ]


@dataclass
class TimestampAccumulator:
    n: int = 0
    sum_direction_accuracy_pct: float = 0.0
    sum_top1_actual_return_bps: float = 0.0
    sum_top5_mean_actual_return_bps: float = 0.0
    sum_top10_mean_actual_return_bps: float = 0.0
    sum_top_decile_mean_actual_return_bps: float = 0.0
    sum_universe_mean_actual_return_bps: float = 0.0
    sum_top_decile_excess_actual_return_bps: float = 0.0
    spearman_count: int = 0
    sum_spearman: float = 0.0

    def update(self, row: dict) -> None:
        self.n += 1
        self.sum_direction_accuracy_pct += float(row["direction_accuracy_pct"])
        self.sum_top1_actual_return_bps += float(row["top1_actual_return_bps"])
        self.sum_top5_mean_actual_return_bps += float(row["top5_mean_actual_return_bps"])
        self.sum_top10_mean_actual_return_bps += float(row["top10_mean_actual_return_bps"])
        self.sum_top_decile_mean_actual_return_bps += float(row["top_decile_mean_actual_return_bps"])
        self.sum_universe_mean_actual_return_bps += float(row["universe_mean_actual_return_bps"])
        self.sum_top_decile_excess_actual_return_bps += float(row["top_decile_excess_actual_return_bps"])
        spearman = row.get("spearman_pred_actual")
        if spearman != "" and spearman is not None and math.isfinite(float(spearman)):
            self.spearman_count += 1
            self.sum_spearman += float(spearman)

    def to_row(self) -> dict:
        return {
            "timestamps": self.n,
            "avg_direction_accuracy_pct": divide(self.sum_direction_accuracy_pct, self.n),
            "avg_spearman_pred_actual": divide(self.sum_spearman, self.spearman_count),
            "avg_top1_actual_return_bps": divide(self.sum_top1_actual_return_bps, self.n),
            "avg_top5_actual_return_bps": divide(self.sum_top5_mean_actual_return_bps, self.n),
            "avg_top10_actual_return_bps": divide(self.sum_top10_mean_actual_return_bps, self.n),
            "avg_top_decile_actual_return_bps": divide(self.sum_top_decile_mean_actual_return_bps, self.n),
            "avg_universe_actual_return_bps": divide(self.sum_universe_mean_actual_return_bps, self.n),
            "avg_top_decile_excess_actual_return_bps": divide(self.sum_top_decile_excess_actual_return_bps, self.n),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Timestamp-first Chronos 2 next-bar evaluator. The script loads one provider-built 1m session into "
            "Polars, then for each timestamp batches all eligible tickers through Chronos and streams live metrics."
        )
    )
    parser.add_argument("--session-date", required=True, help="Provider session date, for example 2024-05-01.")
    parser.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT), help="Processed provider market_data root.")
    parser.add_argument("--chronos-src", default=str(DEFAULT_CHRONOS_SRC), help="Path to chronos-forecasting src folder.")
    parser.add_argument("--model-id", default="amazon/chronos-2", help="Chronos 2 model id or local model path.")
    parser.add_argument("--device-map", default="cpu", help='Transformers device_map, e.g. "cpu", "cuda", or "auto".')
    parser.add_argument("--tickers", default="", help="Comma-separated ticker list. If omitted, all session tickers are used.")
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Top session-dollar-volume tickers to evaluate when --tickers is omitted. Default 0 means all tickers.",
    )
    parser.add_argument("--context-length", type=int, default=128, help="Historical bars per forecast origin.")
    parser.add_argument("--min-context", type=int, default=128, help="Minimum historical bars required before forecasting.")
    parser.add_argument("--prediction-length", type=int, default=1, help="Future bars to forecast. Default is next bar only.")
    parser.add_argument("--rolling-stride", type=int, default=1, help="Evaluate every Nth eligible timestamp.")
    parser.add_argument(
        "--max-total-windows",
        type=int,
        default=0,
        help="Optional global forecast-origin cap across all timestamps. Use 0 for no cap.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8192,
        help="Chronos internal batch size measured in variates. Default targets the raw four-variate input on a 24GB GPU.",
    )
    parser.add_argument(
        "--window-batch-size",
        type=int,
        default=2048,
        help="Maximum forecast windows sent to one predict call before recursive OOM splitting.",
    )
    parser.add_argument(
        "--session-scope",
        choices=["all", "regular"],
        default="all",
        help="Use all provider bars or regular-session bars only.",
    )
    parser.add_argument(
        "--direction-threshold-bps",
        type=float,
        default=0.0,
        help="Return threshold for up/down/flat direction labels, in basis points.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(REPO_ROOT / "research" / "chronos2" / "reports"),
        help="Directory where predictions CSV, live metrics, and markdown report are written.",
    )
    parser.add_argument("--metrics-every-timestamps", type=int, default=1, help="Rewrite live metrics after every N timestamps.")
    parser.add_argument("--progress-every-timestamps", type=int, default=25, help="Print progress every N timestamps.")
    return parser.parse_args()


def load_chronos_pipeline(chronos_src: Path, model_id: str, device_map: str):
    if chronos_src.exists() and str(chronos_src) not in sys.path:
        sys.path.insert(0, str(chronos_src))
    try:
        from chronos import BaseChronosPipeline
    except ImportError as exc:
        raise SystemExit(
            "Could not import Chronos 2. Install chronos-forecasting dependencies or pass "
            f"--chronos-src pointing at the local repo src folder. Import error: {exc}"
        ) from exc
    return BaseChronosPipeline.from_pretrained(model_id, device_map=device_map)


def provider_for_args(args: argparse.Namespace) -> MarketDataProvider:
    return MarketDataProvider(DataProviderConfig(processed_root=Path(args.processed_root)))


def load_session_frame(provider: MarketDataProvider, args: argparse.Namespace) -> pl.DataFrame:
    session = date.fromisoformat(args.session_date)
    requested_columns = [
        "ticker",
        "session_date",
        "bar_time_market",
        "minute_of_day",
        "open",
        "close",
        "volume",
        "transactions",
    ]
    frame = provider.load_bars(
        start_date=session,
        end_date=session,
        timeframe="1m",
        feature_groups=[],
        columns=requested_columns,
    )
    if frame.is_empty():
        raise SystemExit(
            f"No provider 1m bars found for {args.session_date} under {args.processed_root}. "
            "Build the Data Provider artifacts first."
        )
    if args.session_scope == "regular":
        if "minute_of_day" not in frame.columns:
            raise SystemExit("--session-scope regular requires provider column minute_of_day.")
        frame = frame.filter((pl.col("minute_of_day") >= 9 * 60 + 30) & (pl.col("minute_of_day") < 16 * 60))
    requested_tickers = parse_tickers(args.tickers)
    if requested_tickers:
        frame = frame.filter(pl.col("ticker").is_in(requested_tickers))
    elif args.max_tickers > 0:
        top_tickers = (
            frame.with_columns(
                (
                    pl.col("close").cast(pl.Float64, strict=False).fill_null(0.0)
                    * pl.col("volume").cast(pl.Float64, strict=False).fill_null(0.0)
                ).alias("_session_dollar_volume")
            )
            .group_by("ticker")
            .agg(pl.sum("_session_dollar_volume").alias("_session_dollar_volume"))
            .sort("_session_dollar_volume", descending=True)
            .head(args.max_tickers)
            .get_column("ticker")
            .to_list()
        )
        frame = frame.filter(pl.col("ticker").is_in(top_tickers))
    if frame.is_empty():
        raise SystemExit("No bars remain after ticker/session filtering.")
    return frame.sort(["ticker", "bar_time_market"])


def parse_tickers(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def add_model_columns(frame: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    close = positive_expr("close")
    open_ = positive_expr("open")
    volume = nonnegative_expr("volume", frame.columns)
    transactions = nonnegative_expr("transactions", frame.columns)

    exprs: list[pl.Expr] = [
        close.alias("target_close"),
        open_.alias("raw_open"),
        volume.alias("raw_volume"),
        transactions.alias("raw_transactions"),
    ]
    result = frame.with_columns(exprs)
    covariates = ["raw_open", "raw_volume", "raw_transactions"]
    cast_exprs = [
        pl.col(column).cast(pl.Float64, strict=False).replace([float("inf"), float("-inf")], None).alias(column)
        for column in ["target_close", *covariates]
    ]
    return (
        result.with_columns(cast_exprs)
        .filter(pl.col("target_close").is_not_null())
        .sort(["ticker", "bar_time_market"]),
        covariates,
    )


def positive_expr(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Float64, strict=False)
    return pl.when(value > 0.0).then(value).otherwise(None)


def nonnegative_expr(column: str, columns: list[str]) -> pl.Expr:
    if column not in columns:
        return pl.lit(0.0)
    value = pl.col(column).cast(pl.Float64, strict=False)
    return pl.when(value >= 0.0).then(value).otherwise(0.0)


def ticker_arrays(frame: pl.DataFrame, covariates: list[str]) -> dict[str, dict]:
    arrays: dict[str, dict] = {}
    for ticker_frame in frame.partition_by("ticker", maintain_order=True):
        if ticker_frame.is_empty():
            continue
        ticker = str(ticker_frame.get_column("ticker")[0])
        ticker_data: dict[str, np.ndarray | list] = {
            "close": ticker_frame.get_column("close").cast(pl.Float64, strict=False).to_numpy(),
            "target_value": ticker_frame.get_column("target_close").cast(pl.Float64, strict=False).to_numpy(),
            "bar_time_market": ticker_frame.get_column("bar_time_market").to_list(),
            "session_date": ticker_frame.get_column("session_date").cast(pl.Utf8).to_list(),
        }
        for column in covariates:
            values = ticker_frame.get_column(column).cast(pl.Float32, strict=False).to_numpy()
            if np.isfinite(values).any():
                ticker_data[column] = values
        arrays[ticker] = ticker_data
    return arrays


def origin_map(frame: pl.DataFrame, args: argparse.Namespace) -> dict[object, list[tuple[str, int]]]:
    indexed = frame.with_columns(
        pl.int_range(0, pl.len()).over("ticker").alias("__ticker_index"),
        pl.len().over("ticker").alias("__ticker_len"),
    )
    eligible = indexed.filter(
        (pl.col("__ticker_index") >= args.min_context - 1)
        & (pl.col("__ticker_index") <= pl.col("__ticker_len") - args.prediction_length - 1)
    )
    if args.rolling_stride > 1:
        eligible = eligible.filter(((pl.col("__ticker_index") - (args.min_context - 1)) % args.rolling_stride) == 0)
    origins: dict[object, list[tuple[str, int]]] = {}
    for row in eligible.select(["bar_time_market", "ticker", "__ticker_index"]).iter_rows(named=True):
        origins.setdefault(row["bar_time_market"], []).append((str(row["ticker"]), int(row["__ticker_index"])))
    return origins


def build_input_chunk(
    *,
    arrays_by_ticker: dict[str, dict],
    covariates: list[str],
    origin_refs: list[tuple[str, int]],
    args: argparse.Namespace,
) -> tuple[list[dict], list[ForecastOrigin]]:
    inputs: list[dict] = []
    origins: list[ForecastOrigin] = []
    for ticker, origin_index in origin_refs:
        arrays = arrays_by_ticker[ticker]
        target_value = arrays["target_value"]
        close = arrays["close"]
        session_dates = arrays["session_date"]
        times = arrays["bar_time_market"]
        assert isinstance(target_value, np.ndarray)
        assert isinstance(close, np.ndarray)
        assert isinstance(session_dates, list)
        assert isinstance(times, list)
        start_index = max(0, origin_index + 1 - args.context_length)
        past_covariates = {
            column: arrays[column][start_index : origin_index + 1]
            for column in covariates
            if column in arrays
        }
        inputs.append(
            {
                "target": target_value[start_index : origin_index + 1].astype(np.float32, copy=False),
                "past_covariates": past_covariates,
            }
        )
        origins.append(
            ForecastOrigin(
                ticker=ticker,
                session_date=str(session_dates[origin_index]),
                origin_index=int(origin_index),
                origin_time=times[origin_index],
                last_close=float(close[origin_index]),
            )
        )
    return inputs, origins


def predict_rows_with_retry(
    *,
    pipeline,
    inputs: list[dict],
    origins: list[ForecastOrigin],
    arrays_by_ticker: dict[str, dict],
    prediction_length: int,
    quantile_levels: list[float],
    batch_size: int,
    direction_threshold_bps: float,
    device_map: str,
) -> list[dict]:
    try:
        return predict_rows(
            pipeline=pipeline,
            inputs=inputs,
            origins=origins,
            arrays_by_ticker=arrays_by_ticker,
            prediction_length=prediction_length,
            quantile_levels=quantile_levels,
            batch_size=batch_size,
            direction_threshold_bps=direction_threshold_bps,
        )
    except RuntimeError as exc:
        if "out of memory" not in str(exc).lower() or len(inputs) <= 1:
            raise
        clear_cuda_cache(device_map)
        midpoint = len(inputs) // 2
        return predict_rows_with_retry(
            pipeline=pipeline,
            inputs=inputs[:midpoint],
            origins=origins[:midpoint],
            arrays_by_ticker=arrays_by_ticker,
            prediction_length=prediction_length,
            quantile_levels=quantile_levels,
            batch_size=max(64, batch_size // 2),
            direction_threshold_bps=direction_threshold_bps,
            device_map=device_map,
        ) + predict_rows_with_retry(
            pipeline=pipeline,
            inputs=inputs[midpoint:],
            origins=origins[midpoint:],
            arrays_by_ticker=arrays_by_ticker,
            prediction_length=prediction_length,
            quantile_levels=quantile_levels,
            batch_size=max(64, batch_size // 2),
            direction_threshold_bps=direction_threshold_bps,
            device_map=device_map,
        )


def predict_rows(
    *,
    pipeline,
    inputs: list[dict],
    origins: list[ForecastOrigin],
    arrays_by_ticker: dict[str, dict],
    prediction_length: int,
    quantile_levels: list[float],
    batch_size: int,
    direction_threshold_bps: float,
) -> list[dict]:
    quantiles, means = pipeline.predict_quantiles(
        inputs,
        prediction_length=prediction_length,
        quantile_levels=quantile_levels,
        batch_size=batch_size,
    )
    threshold = direction_threshold_bps / 10_000.0
    q_index = {level: idx for idx, level in enumerate(quantile_levels)}
    rows: list[dict] = []
    for local_idx, origin in enumerate(origins):
        arrays = arrays_by_ticker[origin.ticker]
        close = arrays["close"]
        times = arrays["bar_time_market"]
        assert isinstance(close, np.ndarray)
        assert isinstance(times, list)

        q_values = quantiles[local_idx][0].detach().cpu().numpy()
        mean_values = means[local_idx][0].detach().cpu().numpy()
        for step in range(1, prediction_length + 1):
            target_index = origin.origin_index + step
            actual_close = float(close[target_index])
            predicted_close = float(mean_values[step - 1])
            actual_return = log_return(actual_close, origin.last_close)
            predicted_return = log_return(predicted_close, origin.last_close)
            pred_direction = direction_label(predicted_return, threshold)
            actual_direction = direction_label(actual_return, threshold)
            row = {
                "ticker": origin.ticker,
                "session_date": origin.session_date,
                "forecast_origin_time": isoformat(origin.origin_time),
                "target_time": isoformat(times[target_index]),
                "horizon_bars": step,
                "last_close": origin.last_close,
                "actual_close": actual_close,
                "predicted_close": predicted_close,
                "naive_close": origin.last_close,
                "actual_return": actual_return,
                "predicted_return": predicted_return,
                "naive_return": 0.0,
                "abs_close_error": abs(predicted_close - actual_close),
                "naive_abs_close_error": abs(origin.last_close - actual_close),
                "abs_return_error": abs(predicted_return - actual_return),
                "naive_abs_return_error": abs(actual_return),
                "squared_return_error": (predicted_return - actual_return) ** 2,
                "naive_squared_return_error": actual_return**2,
                "actual_direction": actual_direction,
                "predicted_direction": pred_direction,
                "direction_correct": pred_direction == actual_direction,
                "direction_correct_nonflat": pred_direction == actual_direction and actual_direction != 0,
                "p10_close": "",
                "p50_close": "",
                "p90_close": "",
                "p10_p90_covered": "",
                "p10_p90_width_bps": "",
            }
            if 0.1 in q_index:
                row["p10_close"] = float(q_values[step - 1, q_index[0.1]])
            if 0.5 in q_index:
                row["p50_close"] = float(q_values[step - 1, q_index[0.5]])
            if 0.9 in q_index:
                row["p90_close"] = float(q_values[step - 1, q_index[0.9]])
            if row["p10_close"] != "" and row["p90_close"] != "":
                p10 = float(row["p10_close"])
                p90 = float(row["p90_close"])
                row["p10_p90_covered"] = p10 <= actual_close <= p90
                row["p10_p90_width_bps"] = ((p90 / p10) - 1.0) * 10_000.0 if p10 > 0.0 else ""
            rows.append(row)
    return rows


def timestamp_metric(rows: list[dict], forecast_origin_time: object) -> dict | None:
    horizon_1 = [row for row in rows if int(row["horizon_bars"]) == 1]
    if not horizon_1:
        return None
    predicted = np.array([float(row["predicted_return"]) for row in horizon_1], dtype=np.float64)
    actual = np.array([float(row["actual_return"]) for row in horizon_1], dtype=np.float64)
    direction_ok = np.array([bool(row["direction_correct"]) for row in horizon_1], dtype=bool)
    order = np.argsort(-predicted)
    top_decile_n = max(1, math.ceil(len(horizon_1) * 0.10))
    top5 = order[: min(5, len(order))]
    top10 = order[: min(10, len(order))]
    top_decile = order[:top_decile_n]
    universe_mean = float(np.mean(actual) * 10_000.0)
    top_decile_mean = float(np.mean(actual[top_decile]) * 10_000.0)
    return {
        "forecast_origin_time": isoformat(forecast_origin_time),
        "n": len(horizon_1),
        "direction_accuracy_pct": float(np.mean(direction_ok) * 100.0),
        "mae_return_bps": float(np.mean(np.abs(predicted - actual)) * 10_000.0),
        "naive_mae_return_bps": float(np.mean(np.abs(actual)) * 10_000.0),
        "mae_return_vs_naive": divide(float(np.mean(np.abs(predicted - actual))), float(np.mean(np.abs(actual)))),
        "mean_actual_return_bps": universe_mean,
        "mean_predicted_return_bps": float(np.mean(predicted) * 10_000.0),
        "spearman_pred_actual": spearman_corr(predicted, actual),
        "top1_actual_return_bps": float(actual[order[0]] * 10_000.0),
        "top5_mean_actual_return_bps": float(np.mean(actual[top5]) * 10_000.0),
        "top10_mean_actual_return_bps": float(np.mean(actual[top10]) * 10_000.0),
        "top_decile_mean_actual_return_bps": top_decile_mean,
        "universe_mean_actual_return_bps": universe_mean,
        "top_decile_excess_actual_return_bps": top_decile_mean - universe_mean,
        "top_decile_direction_accuracy_pct": float(np.mean(direction_ok[top_decile]) * 100.0),
    }


def spearman_corr(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2:
        return float("nan")
    left_rank = rankdata(left)
    right_rank = rankdata(right)
    if np.std(left_rank) == 0.0 or np.std(right_rank) == 0.0:
        return float("nan")
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    sorted_values = values[order]
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        avg_rank = (start + end - 1) / 2.0 + 1.0
        ranks[order[start:end]] = avg_rank
        start = end
    return ranks


def direction_label(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def log_return(price: float, base_price: float) -> float:
    if not math.isfinite(price) or not math.isfinite(base_price) or base_price <= 0.0:
        return float("nan")
    return math.log(max(price, 1.0e-12) / base_price)


def chunks(values: list, size: int) -> Iterator[list]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def isoformat(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def clear_cuda_cache(device_map: str) -> None:
    if "cuda" not in str(device_map).lower():
        return
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


def write_metrics_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_live_report(
    *,
    args: argparse.Namespace,
    predictions_path: Path,
    metrics_path: Path,
    timestamp_metrics_path: Path,
    report_path: Path,
    overall_rows: list[dict],
    timestamp_summary: dict,
    covariates: list[str],
    processed_timestamps: int,
    processed_windows: int,
    selected_ticker_count: int,
    is_final: bool,
) -> None:
    status = "Final" if is_final else "Live"
    report = [
        f"# Chronos 2 1m Bar Forecast Test ({status})",
        "",
        f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Session date: `{args.session_date}`",
        f"- Model: `{args.model_id}`",
        f"- Device map: `{args.device_map}`",
        f"- Selected tickers: `{selected_ticker_count}`",
        f"- Processed timestamps: `{processed_timestamps}`",
        f"- Forecast origins processed: `{processed_windows}`",
        "- Target: raw `close`; default forecast is the next bar.",
        f"- Prediction length: `{args.prediction_length}`",
        f"- Context length/min context: `{args.context_length}` / `{args.min_context}`",
        f"- Chronos batch size/window chunk: `{args.batch_size}` / `{args.window_batch_size}`",
        f"- Predictions CSV: `{predictions_path}`",
        f"- Live metrics CSV: `{metrics_path}`",
        f"- Timestamp metrics CSV: `{timestamp_metrics_path}`",
        "",
        "## Overall Metrics",
        "",
        markdown_table(overall_rows),
        "",
        "## Timestamp Ranking Summary",
        "",
        markdown_table([timestamp_summary] if timestamp_summary else []),
        "",
        "## Input Channels",
        "",
        (
            "Chronos receives raw `close` values as the target and raw `open`, `volume`, and `transactions` "
            "as `past_covariates`. The script does not log-transform, normalize, z-score, or scale these inputs "
            "before passing them to Chronos. No future covariates are supplied."
        ),
        "",
        "\n".join(f"- `{column}`" for column in covariates),
    ]
    report_path.write_text("\n".join(report), encoding="utf-8")


def markdown_table(rows: list[dict]) -> str:
    if not rows:
        return "_No rows yet._"
    columns = list(rows[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, float):
                values.append("" if math.isnan(value) else f"{value:.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.min_context < 3:
        raise SystemExit("--min-context must be at least 3.")
    if args.context_length < args.min_context:
        raise SystemExit("--context-length must be >= --min-context.")
    if args.prediction_length < 1:
        raise SystemExit("--prediction-length must be at least 1.")

    provider = provider_for_args(args)
    session_frame = load_session_frame(provider, args)
    model_frame, covariates = add_model_columns(session_frame)
    arrays_by_ticker = ticker_arrays(model_frame, covariates)
    origins_by_time = origin_map(model_frame, args)
    selected_ticker_count = len(arrays_by_ticker)
    if not origins_by_time:
        raise SystemExit(
            "No eligible forecast origins. "
            f"Need at least min_context + prediction_length bars per ticker ({args.min_context + args.prediction_length})."
        )

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"chronos2_1m_{args.session_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    predictions_path = report_dir / f"{run_id}_predictions.csv"
    metrics_path = report_dir / f"{run_id}_live_metrics.csv"
    timestamp_metrics_path = report_dir / f"{run_id}_timestamp_metrics.csv"
    report_path = report_dir / f"{run_id}_report.md"
    config_path = report_dir / f"{run_id}_config.json"
    config_path.write_text(json.dumps(vars(args), indent=2, sort_keys=True), encoding="utf-8")

    pipeline = load_chronos_pipeline(Path(args.chronos_src), args.model_id, args.device_map)
    metrics = MetricAccumulator()
    timestamp_accumulator = TimestampAccumulator()
    processed_timestamps = 0
    processed_windows = 0
    windows_remaining = args.max_total_windows if args.max_total_windows > 0 else None

    with predictions_path.open("w", newline="", encoding="utf-8") as pred_handle, timestamp_metrics_path.open(
        "w", newline="", encoding="utf-8"
    ) as ts_handle:
        prediction_writer = csv.DictWriter(pred_handle, fieldnames=PREDICTION_FIELDS)
        timestamp_writer = csv.DictWriter(ts_handle, fieldnames=TIMESTAMP_METRIC_FIELDS)
        prediction_writer.writeheader()
        timestamp_writer.writeheader()

        for forecast_time in sorted(origins_by_time):
            origin_refs = origins_by_time[forecast_time]
            if windows_remaining is not None:
                if windows_remaining <= 0:
                    break
                origin_refs = origin_refs[:windows_remaining]
                windows_remaining -= len(origin_refs)
            if not origin_refs:
                continue

            timestamp_rows: list[dict] = []
            for origin_chunk in chunks(origin_refs, max(1, args.window_batch_size)):
                inputs, origins = build_input_chunk(
                    arrays_by_ticker=arrays_by_ticker,
                    covariates=covariates,
                    origin_refs=origin_chunk,
                    args=args,
                )
                rows = predict_rows_with_retry(
                    pipeline=pipeline,
                    inputs=inputs,
                    origins=origins,
                    arrays_by_ticker=arrays_by_ticker,
                    prediction_length=args.prediction_length,
                    quantile_levels=DEFAULT_QUANTILES,
                    batch_size=args.batch_size,
                    direction_threshold_bps=args.direction_threshold_bps,
                    device_map=args.device_map,
                )
                for row in rows:
                    prediction_writer.writerow(row)
                    metrics.update(row)
                timestamp_rows.extend(rows)
                processed_windows += len(origin_chunk)
                pred_handle.flush()
                clear_cuda_cache(args.device_map)

            timestamp_row = timestamp_metric(timestamp_rows, forecast_time)
            if timestamp_row:
                timestamp_writer.writerow(timestamp_row)
                timestamp_accumulator.update(timestamp_row)
                ts_handle.flush()

            processed_timestamps += 1
            if processed_timestamps % max(1, args.metrics_every_timestamps) == 0:
                metric_rows = metrics.rows()
                write_metrics_csv(metrics_path, metric_rows)
                write_live_report(
                    args=args,
                    predictions_path=predictions_path,
                    metrics_path=metrics_path,
                    timestamp_metrics_path=timestamp_metrics_path,
                    report_path=report_path,
                    overall_rows=metric_rows,
                    timestamp_summary=timestamp_accumulator.to_row(),
                    covariates=covariates,
                    processed_timestamps=processed_timestamps,
                    processed_windows=processed_windows,
                    selected_ticker_count=selected_ticker_count,
                    is_final=False,
                )
            if args.progress_every_timestamps > 0 and processed_timestamps % args.progress_every_timestamps == 0:
                print(
                    f"Processed timestamps={processed_timestamps}/{len(origins_by_time)} "
                    f"forecast_origins={processed_windows} rows={metrics.overall.n}",
                    flush=True,
                )
            if windows_remaining is not None and windows_remaining <= 0:
                break

    if metrics.overall.n == 0:
        raise SystemExit("No prediction rows were written.")

    metric_rows = metrics.rows()
    write_metrics_csv(metrics_path, metric_rows)
    write_live_report(
        args=args,
        predictions_path=predictions_path,
        metrics_path=metrics_path,
        timestamp_metrics_path=timestamp_metrics_path,
        report_path=report_path,
        overall_rows=metric_rows,
        timestamp_summary=timestamp_accumulator.to_row(),
        covariates=covariates,
        processed_timestamps=processed_timestamps,
        processed_windows=processed_windows,
        selected_ticker_count=selected_ticker_count,
        is_final=True,
    )

    print(f"Wrote predictions: {predictions_path}")
    print(f"Wrote live metrics: {metrics_path}")
    print(f"Wrote timestamp metrics: {timestamp_metrics_path}")
    print(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
