from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_provider.config import DEFAULT_PROCESSED_ROOT, DataProviderConfig  # noqa: E402
from src.data_provider.provider import MarketDataProvider  # noqa: E402


DEFAULT_CHRONOS_SRC = Path("D:/TradingCodes/public-codes/chronos-forecasting-main/src")
DEFAULT_FEATURE_GROUPS = ["core", "session", "volatility", "volume_liquidity", "shock"]
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

    def horizon_rows(self) -> list[dict]:
        return [self.by_horizon[horizon].to_row(f"h{horizon}") for horizon in sorted(self.by_horizon)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a walk-forward Chronos 2 experiment on provider-built 1m bars. "
            "Provider data is loaded in ticker batches with Polars, forecast rows are streamed to CSV, "
            "and metrics are aggregated incrementally."
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
    parser.add_argument("--ticker-batch-size", type=int, default=100, help="Provider ticker batch size.")
    parser.add_argument("--context-length", type=int, default=512, help="Maximum historical bars per forecast origin.")
    parser.add_argument("--min-context", type=int, default=128, help="Minimum historical bars required before forecasting.")
    parser.add_argument("--prediction-length", type=int, default=15, help="Number of future bars to forecast.")
    parser.add_argument("--rolling-stride", type=int, default=1, help="Evaluate every Nth eligible origin bar.")
    parser.add_argument(
        "--max-windows-per-ticker",
        type=int,
        default=0,
        help="Limit forecast origins per ticker for quick tests. Use 0 for every eligible bar.",
    )
    parser.add_argument(
        "--max-total-windows",
        type=int,
        default=0,
        help="Optional global forecast-origin cap across all tickers. Use 0 for no cap.",
    )
    parser.add_argument("--batch-size", type=int, default=64, help="Chronos batch size in variates, not forecast windows.")
    parser.add_argument("--window-batch-size", type=int, default=32, help="Forecast origin windows per predict call.")
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
        help="Directory where predictions CSV and markdown report are written.",
    )
    parser.add_argument("--progress-every-tickers", type=int, default=100, help="Print progress every N processed tickers.")
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


def select_session_tickers(provider: MarketDataProvider, args: argparse.Namespace) -> list[str]:
    requested = parse_tickers(args.tickers)
    if requested:
        return requested

    session = date.fromisoformat(args.session_date)
    frame = provider.load_bars(
        start_date=session,
        end_date=session,
        timeframe="1m",
        feature_groups=[],
        columns=["ticker", "close", "volume"],
    )
    if frame.is_empty():
        raise SystemExit(
            f"No provider 1m bars found for {args.session_date} under {args.processed_root}. "
            "Build the Data Provider artifacts first."
        )

    frame = frame.with_columns(
        (pl.col("close").cast(pl.Float64, strict=False).fill_null(0.0) * pl.col("volume").cast(pl.Float64, strict=False).fill_null(0.0))
        .alias("_session_dollar_volume")
    )
    ranked = (
        frame.group_by("ticker")
        .agg(pl.sum("_session_dollar_volume").alias("_session_dollar_volume"))
        .sort("_session_dollar_volume", descending=True)
    )
    if args.max_tickers > 0:
        ranked = ranked.head(args.max_tickers)
    return ranked.get_column("ticker").cast(pl.Utf8).to_list()


def load_ticker_batch(provider: MarketDataProvider, args: argparse.Namespace, tickers: list[str]) -> pl.DataFrame:
    session = date.fromisoformat(args.session_date)
    frame = provider.load_bars(
        start_date=session,
        end_date=session,
        timeframe="1m",
        tickers=tickers,
        feature_groups=DEFAULT_FEATURE_GROUPS,
    )
    if frame.is_empty():
        return frame
    if args.session_scope == "regular":
        if "minute_of_day" not in frame.columns:
            raise SystemExit("--session-scope regular requires provider column minute_of_day.")
        frame = frame.filter((pl.col("minute_of_day") >= 9 * 60 + 30) & (pl.col("minute_of_day") < 16 * 60))
    return frame.sort(["ticker", "bar_time_market"])


def parse_tickers(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def add_model_columns(frame: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    close = positive_expr("close")
    open_ = positive_expr("open")
    high = positive_expr("high")
    low = positive_expr("low")
    volume = nonnegative_expr("volume", frame.columns)
    transactions = nonnegative_expr("transactions", frame.columns)

    exprs: list[pl.Expr] = [
        close.log().alias("target_log_close"),
        (open_ / close).log().alias("cov_open_to_close_log"),
        (high / close).log().alias("cov_high_to_close_log"),
        (low / close).log().alias("cov_low_to_close_log"),
        (close / open_).log().alias("cov_bar_body_log"),
        (high / low).log().alias("cov_range_log"),
        (volume + 1.0).log().alias("cov_log_volume"),
        (transactions + 1.0).log().alias("cov_log_transactions"),
        ((volume * close) + 1.0).log().alias("cov_log_dollar_volume"),
    ]

    if "spread" in frame.columns:
        exprs.append((nonnegative_expr("spread", frame.columns) / close).alias("cov_spread_pct"))
    if "spread_bps" in frame.columns:
        exprs.append(pl.col("spread_bps").cast(pl.Float64, strict=False).alias("cov_spread_bps"))
    if {"quote_ask_price", "quote_bid_price"}.issubset(frame.columns):
        midpoint = (positive_expr("quote_ask_price") + positive_expr("quote_bid_price")) / 2.0
        exprs.append((midpoint / close).log().alias("cov_quote_mid_to_close_log"))
    if "minute_of_day" in frame.columns:
        minute = pl.col("minute_of_day").cast(pl.Float64, strict=False)
        exprs.extend(
            [
                (2.0 * math.pi * minute / 1440.0).sin().alias("cov_minute_sin"),
                (2.0 * math.pi * minute / 1440.0).cos().alias("cov_minute_cos"),
                (minute < 9 * 60 + 30).cast(pl.Float64).alias("cov_is_premarket"),
                ((minute >= 9 * 60 + 30) & (minute < 16 * 60)).cast(pl.Float64).alias("cov_is_regular"),
                (minute >= 16 * 60).cast(pl.Float64).alias("cov_is_afterhours"),
            ]
        )

    result = frame.with_columns(exprs)
    provider_covariates = [
        "return_1",
        "return_5",
        "log_return_1",
        "bar_range",
        "body",
        "body_abs",
        "upper_wick",
        "lower_wick",
        "close_location",
        "distance_to_day_open_pct",
        "distance_to_day_high_pct",
        "distance_to_day_low_pct",
        "gap_pct",
        "true_range",
        "true_range_ema5_pct",
        "true_range_ema20_pct",
        "atr14",
        "return_z20",
        "range_z20",
        "relative_volume20",
        "relative_dollar_volume20",
        "volume_z20",
        "transactions_z20",
        "transactions_vs_prior_3",
        "quote_valid_ratio",
        "locked_or_crossed_count",
        "price_shock_score",
        "volume_shock_score",
        "price_volume_shock_score",
        "bars_since_price_shock",
        "bars_since_volume_shock",
        "shock_confirmation_delay_minutes",
    ]
    engineered_covariates = [column for column in result.columns if column.startswith("cov_")]
    covariates = engineered_covariates + [column for column in provider_covariates if column in result.columns]
    cast_exprs = [
        pl.col(column).cast(pl.Float64, strict=False).replace([float("inf"), float("-inf")], None).alias(column)
        for column in ["target_log_close", *covariates]
    ]
    result = result.with_columns(cast_exprs).filter(pl.col("target_log_close").is_not_null()).sort(["ticker", "bar_time_market"])
    return result, covariates


def positive_expr(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Float64, strict=False)
    return pl.when(value > 0.0).then(value).otherwise(None)


def nonnegative_expr(column: str, columns: list[str]) -> pl.Expr:
    if column not in columns:
        return pl.lit(0.0)
    value = pl.col(column).cast(pl.Float64, strict=False)
    return pl.when(value >= 0.0).then(value).otherwise(0.0)


def ticker_groups(frame: pl.DataFrame) -> Iterator[tuple[str, pl.DataFrame]]:
    for partition in frame.partition_by("ticker", maintain_order=True):
        if partition.is_empty():
            continue
        ticker = str(partition.get_column("ticker")[0])
        yield ticker, partition


def process_ticker(
    *,
    pipeline,
    ticker: str,
    ticker_frame: pl.DataFrame,
    covariates: list[str],
    args: argparse.Namespace,
    writer: csv.DictWriter,
    metrics: MetricAccumulator,
    windows_remaining: int | None,
) -> tuple[int, int | None, bool]:
    length = ticker_frame.height
    required_bars = args.min_context + args.prediction_length
    if length < required_bars:
        return 0, windows_remaining, False

    last_origin = length - args.prediction_length - 1
    origin_indices = list(range(args.min_context - 1, last_origin + 1, max(1, args.rolling_stride)))
    if args.max_windows_per_ticker > 0:
        origin_indices = origin_indices[: args.max_windows_per_ticker]
    if windows_remaining is not None:
        if windows_remaining <= 0:
            return 0, windows_remaining, True
        origin_indices = origin_indices[:windows_remaining]
        windows_remaining -= len(origin_indices)
    if not origin_indices:
        return 0, windows_remaining, windows_remaining == 0

    arrays = ticker_arrays(ticker_frame, covariates)
    processed = 0
    for chunk in chunks(origin_indices, max(1, args.window_batch_size)):
        inputs, origins = build_input_chunk(ticker=ticker, ticker_frame=ticker_frame, arrays=arrays, covariates=covariates, origin_indices=chunk, args=args)
        rows = predict_rows(
            pipeline=pipeline,
            inputs=inputs,
            origins=origins,
            arrays=arrays,
            prediction_length=args.prediction_length,
            quantile_levels=DEFAULT_QUANTILES,
            batch_size=args.batch_size,
            direction_threshold_bps=args.direction_threshold_bps,
        )
        for row in rows:
            writer.writerow(row)
            metrics.update(row)
        processed += len(chunk)
        clear_cuda_cache(args.device_map)
    return processed, windows_remaining, windows_remaining == 0 if windows_remaining is not None else False


def ticker_arrays(ticker_frame: pl.DataFrame, covariates: list[str]) -> dict[str, np.ndarray | list]:
    arrays: dict[str, np.ndarray | list] = {
        "close": ticker_frame.get_column("close").cast(pl.Float64, strict=False).to_numpy(),
        "target_log_close": ticker_frame.get_column("target_log_close").cast(pl.Float64, strict=False).to_numpy(),
        "bar_time_market": ticker_frame.get_column("bar_time_market").to_list(),
        "session_date": ticker_frame.get_column("session_date").cast(pl.Utf8).to_list(),
    }
    for column in covariates:
        values = ticker_frame.get_column(column).cast(pl.Float32, strict=False).to_numpy()
        if np.isfinite(values).any():
            arrays[column] = values
    return arrays


def build_input_chunk(
    *,
    ticker: str,
    ticker_frame: pl.DataFrame,
    arrays: dict[str, np.ndarray | list],
    covariates: list[str],
    origin_indices: list[int],
    args: argparse.Namespace,
) -> tuple[list[dict], list[ForecastOrigin]]:
    target_log_close = arrays["target_log_close"]
    close = arrays["close"]
    session_dates = arrays["session_date"]
    times = arrays["bar_time_market"]
    assert isinstance(target_log_close, np.ndarray)
    assert isinstance(close, np.ndarray)
    assert isinstance(session_dates, list)
    assert isinstance(times, list)

    inputs: list[dict] = []
    origins: list[ForecastOrigin] = []
    for origin_index in origin_indices:
        start_index = max(0, origin_index + 1 - args.context_length)
        past_covariates = {
            column: arrays[column][start_index : origin_index + 1]
            for column in covariates
            if column in arrays
        }
        inputs.append(
            {
                "target": target_log_close[start_index : origin_index + 1].astype(np.float32, copy=False),
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


def predict_rows(
    *,
    pipeline,
    inputs: list[dict],
    origins: list[ForecastOrigin],
    arrays: dict[str, np.ndarray | list],
    prediction_length: int,
    quantile_levels: list[float],
    batch_size: int,
    direction_threshold_bps: float,
) -> list[dict]:
    close = arrays["close"]
    target_log_close = arrays["target_log_close"]
    times = arrays["bar_time_market"]
    assert isinstance(close, np.ndarray)
    assert isinstance(target_log_close, np.ndarray)
    assert isinstance(times, list)

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
        q_values = quantiles[local_idx][0].detach().cpu().numpy()
        mean_values = means[local_idx][0].detach().cpu().numpy()
        origin_log_close = math.log(origin.last_close)
        for step in range(1, prediction_length + 1):
            target_index = origin.origin_index + step
            actual_close = float(close[target_index])
            actual_log_close = float(target_log_close[target_index])
            predicted_log_close = float(mean_values[step - 1])
            predicted_close = float(np.exp(predicted_log_close))
            actual_return = actual_log_close - origin_log_close
            predicted_return = predicted_log_close - origin_log_close
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
                row["p10_close"] = float(np.exp(q_values[step - 1, q_index[0.1]]))
            if 0.5 in q_index:
                row["p50_close"] = float(np.exp(q_values[step - 1, q_index[0.5]]))
            if 0.9 in q_index:
                row["p90_close"] = float(np.exp(q_values[step - 1, q_index[0.9]]))
            if row["p10_close"] != "" and row["p90_close"] != "":
                p10 = float(row["p10_close"])
                p90 = float(row["p90_close"])
                row["p10_p90_covered"] = p10 <= actual_close <= p90
                row["p10_p90_width_bps"] = ((p90 / p10) - 1.0) * 10_000.0 if p10 > 0.0 else ""
            rows.append(row)
    return rows


def direction_label(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


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


def write_report(
    *,
    args: argparse.Namespace,
    predictions_path: Path,
    metrics_path: Path,
    report_path: Path,
    overall: dict,
    horizon_metrics: list[dict],
    covariates: list[str],
    tickers_seen: list[str],
    selected_ticker_count: int,
    processed_windows: int,
) -> None:
    report = [
        "# Chronos 2 1m Bar Forecast Test",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Session date: `{args.session_date}`",
        f"- Model: `{args.model_id}`",
        f"- Device map: `{args.device_map}`",
        f"- Selected tickers: `{selected_ticker_count}`",
        f"- Tickers with forecast windows: `{len(tickers_seen)}`",
        f"- Forecast origins processed: `{processed_windows}`",
        f"- Target: `log(close)`; reported returns are log-return deltas from the forecast origin close.",
        f"- Prediction length: `{args.prediction_length}` bars",
        f"- Context length: `{args.context_length}` bars, minimum context `{args.min_context}` bars",
        f"- Ticker batch size: `{args.ticker_batch_size}`, window batch size: `{args.window_batch_size}`",
        f"- Direction threshold: `{args.direction_threshold_bps}` bps",
        f"- Predictions CSV: `{predictions_path}`",
        f"- Metrics CSV: `{metrics_path}`",
        "",
        "## Overall Metrics",
        "",
        markdown_table([overall]),
        "",
        "## Metrics By Horizon",
        "",
        markdown_table(horizon_metrics),
        "",
        "## Input Channels",
        "",
        "Chronos receives `target` as the historical `log(close)` sequence and receives these columns as "
        "`past_covariates`. No `future_covariates` are supplied.",
        "",
        "\n".join(f"- `{column}`" for column in covariates),
        "",
        "## Notes",
        "",
        "- Provider data is loaded in ticker batches with Polars rather than materialized as one pandas frame.",
        "- Prediction rows are streamed to CSV; metrics are aggregated incrementally.",
        "- This is an after-bar-close walk-forward test: the origin bar close is included in the context.",
        "- Supervision labels are not loaded as model inputs; only provider bars and deterministic feature groups are used.",
        "- Evaluate these metrics against the naive baseline columns before treating the model output as a tradable edge.",
    ]
    report_path.write_text("\n".join(report), encoding="utf-8")


def markdown_table(rows: list[dict]) -> str:
    if not rows:
        return "_No rows._"
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
    if args.prediction_length < 1:
        raise SystemExit("--prediction-length must be at least 1.")
    if args.ticker_batch_size < 1:
        raise SystemExit("--ticker-batch-size must be at least 1.")

    provider = provider_for_args(args)
    selected_tickers = select_session_tickers(provider, args)
    if not selected_tickers:
        raise SystemExit("No tickers selected for the requested session.")

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"chronos2_1m_{args.session_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    predictions_path = report_dir / f"{run_id}_predictions.csv"
    metrics_path = report_dir / f"{run_id}_metrics.csv"
    report_path = report_dir / f"{run_id}_report.md"
    config_path = report_dir / f"{run_id}_config.json"
    config_path.write_text(json.dumps(vars(args), indent=2, sort_keys=True), encoding="utf-8")

    pipeline = load_chronos_pipeline(Path(args.chronos_src), args.model_id, args.device_map)
    metrics = MetricAccumulator()
    covariates_seen: list[str] = []
    tickers_seen: list[str] = []
    processed_tickers = 0
    processed_windows = 0
    windows_remaining = args.max_total_windows if args.max_total_windows > 0 else None

    with predictions_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_FIELDS)
        writer.writeheader()

        for ticker_batch in chunks(selected_tickers, args.ticker_batch_size):
            batch_frame = load_ticker_batch(provider, args, ticker_batch)
            if batch_frame.is_empty():
                processed_tickers += len(ticker_batch)
                continue
            model_frame, covariates = add_model_columns(batch_frame)
            if not covariates_seen:
                covariates_seen = covariates
            for ticker, ticker_frame in ticker_groups(model_frame):
                count, windows_remaining, stop = process_ticker(
                    pipeline=pipeline,
                    ticker=ticker,
                    ticker_frame=ticker_frame,
                    covariates=covariates,
                    args=args,
                    writer=writer,
                    metrics=metrics,
                    windows_remaining=windows_remaining,
                )
                processed_tickers += 1
                if count > 0:
                    tickers_seen.append(ticker)
                    processed_windows += count
                if args.progress_every_tickers > 0 and processed_tickers % args.progress_every_tickers == 0:
                    print(
                        f"Processed {processed_tickers}/{len(selected_tickers)} tickers, "
                        f"forecast_origins={processed_windows}, rows={metrics.overall.n}",
                        flush=True,
                    )
                if stop:
                    break
            del batch_frame
            del model_frame
            if windows_remaining is not None and windows_remaining <= 0:
                break

    if metrics.overall.n == 0:
        required_bars = args.min_context + args.prediction_length
        raise SystemExit(
            "No forecast windows were created. "
            f"Need at least min_context + prediction_length bars per ticker ({required_bars} with current args). "
            "Lower --min-context/--prediction-length, choose a more active session, or use --max-tickers N to inspect "
            "a smaller active universe first."
        )

    horizon_metrics = metrics.horizon_rows()
    overall = metrics.overall.to_row("overall")
    write_metrics_csv(metrics_path, horizon_metrics)
    write_report(
        args=args,
        predictions_path=predictions_path,
        metrics_path=metrics_path,
        report_path=report_path,
        overall=overall,
        horizon_metrics=horizon_metrics,
        covariates=covariates_seen,
        tickers_seen=tickers_seen,
        selected_ticker_count=len(selected_tickers),
        processed_windows=processed_windows,
    )

    print(f"Wrote predictions: {predictions_path}")
    print(f"Wrote metrics: {metrics_path}")
    print(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
