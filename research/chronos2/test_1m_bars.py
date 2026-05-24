from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_provider.config import DEFAULT_PROCESSED_ROOT, DataProviderConfig  # noqa: E402
from src.data_provider.provider import MarketDataProvider  # noqa: E402


DEFAULT_CHRONOS_SRC = Path("D:/TradingCodes/public-codes/chronos-forecasting-main/src")
DEFAULT_FEATURE_GROUPS = ["core", "session", "volatility", "volume_liquidity", "shock"]
DEFAULT_QUANTILES = [0.1, 0.5, 0.9]


@dataclass(frozen=True)
class ForecastOrigin:
    ticker: str
    session_date: str
    origin_index: int
    origin_time: pd.Timestamp
    last_close: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a walk-forward Chronos 2 experiment on provider-built 1m bars. "
            "Each forecast uses history through the origin bar and compares predicted future closes "
            "with realized future closes."
        )
    )
    parser.add_argument("--session-date", required=True, help="Provider session date, for example 2024-05-01.")
    parser.add_argument("--processed-root", default=str(DEFAULT_PROCESSED_ROOT), help="Processed provider market_data root.")
    parser.add_argument("--chronos-src", default=str(DEFAULT_CHRONOS_SRC), help="Path to chronos-forecasting src folder.")
    parser.add_argument("--model-id", default="amazon/chronos-2", help="Chronos 2 model id or local model path.")
    parser.add_argument("--device-map", default="cpu", help='Transformers device_map, e.g. "cpu", "cuda", or "auto".')
    parser.add_argument("--tickers", default="", help="Comma-separated ticker list. If omitted, top volume tickers are used.")
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=5,
        help="Top session-dollar-volume tickers to evaluate when --tickers is omitted. Use 0 for all tickers.",
    )
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


def load_session_bars(args: argparse.Namespace) -> pd.DataFrame:
    session = date.fromisoformat(args.session_date)
    provider = MarketDataProvider(DataProviderConfig(processed_root=Path(args.processed_root)))
    frame = provider.load_bars(
        start_date=session,
        end_date=session,
        timeframe="1m",
        feature_groups=DEFAULT_FEATURE_GROUPS,
    )
    if frame.is_empty():
        raise SystemExit(
            f"No provider 1m bars found for {args.session_date} under {args.processed_root}. "
            "Build the Data Provider artifacts first."
        )

    df = frame.to_pandas()
    if "bar_time_market" not in df.columns:
        raise SystemExit("Provider frame is missing bar_time_market; cannot build chronological windows.")

    df["bar_time_market"] = pd.to_datetime(df["bar_time_market"])
    df["session_date"] = df["session_date"].astype(str)
    df = df.sort_values(["ticker", "bar_time_market"]).reset_index(drop=True)

    if args.session_scope == "regular":
        if "minute_of_day" not in df.columns:
            raise SystemExit("--session-scope regular requires provider column minute_of_day.")
        df = df[(df["minute_of_day"] >= 9 * 60 + 30) & (df["minute_of_day"] < 16 * 60)].copy()

    tickers = parse_tickers(args.tickers)
    if tickers:
        df = df[df["ticker"].isin(tickers)].copy()
    else:
        tickers = select_top_tickers(df, args.max_tickers)
        df = df[df["ticker"].isin(tickers)].copy()

    if df.empty:
        raise SystemExit("No bars remain after ticker/session filtering.")
    return df.sort_values(["ticker", "bar_time_market"]).reset_index(drop=True)


def parse_tickers(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def select_top_tickers(df: pd.DataFrame, max_tickers: int) -> list[str]:
    if max_tickers == 0:
        return sorted(df["ticker"].dropna().unique().tolist())
    dollar_volume = df["close"].astype(float).fillna(0.0) * df["volume"].astype(float).fillna(0.0)
    ranked = (
        df.assign(_session_dollar_volume=dollar_volume)
        .groupby("ticker", sort=False)["_session_dollar_volume"]
        .sum()
        .sort_values(ascending=False)
    )
    return ranked.head(max_tickers).index.tolist()


def add_model_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    result = df.copy()
    close = positive(result["close"])
    open_ = positive(result["open"])
    high = positive(result["high"])
    low = positive(result["low"])
    volume = nonnegative(result.get("volume"))
    transactions = nonnegative(result.get("transactions"))

    result["target_log_close"] = np.log(close)
    result["cov_open_to_close_log"] = np.log(open_ / close)
    result["cov_high_to_close_log"] = np.log(high / close)
    result["cov_low_to_close_log"] = np.log(low / close)
    result["cov_bar_body_log"] = np.log(close / open_)
    result["cov_range_log"] = np.log(high / low)
    result["cov_log_volume"] = np.log1p(volume)
    result["cov_log_transactions"] = np.log1p(transactions)
    result["cov_log_dollar_volume"] = np.log1p(volume * close)

    if "spread" in result.columns:
        result["cov_spread_pct"] = nonnegative(result["spread"]) / close
    if "spread_bps" in result.columns:
        result["cov_spread_bps"] = pd.to_numeric(result["spread_bps"], errors="coerce")
    if {"quote_ask_price", "quote_bid_price"}.issubset(result.columns):
        midpoint = (positive(result["quote_ask_price"]) + positive(result["quote_bid_price"])) / 2.0
        result["cov_quote_mid_to_close_log"] = np.log(midpoint / close)

    if "minute_of_day" in result.columns:
        minute = pd.to_numeric(result["minute_of_day"], errors="coerce")
        result["cov_minute_sin"] = np.sin(2.0 * math.pi * minute / 1440.0)
        result["cov_minute_cos"] = np.cos(2.0 * math.pi * minute / 1440.0)
        result["cov_is_premarket"] = (minute < 9 * 60 + 30).astype(float)
        result["cov_is_regular"] = ((minute >= 9 * 60 + 30) & (minute < 16 * 60)).astype(float)
        result["cov_is_afterhours"] = (minute >= 16 * 60).astype(float)

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

    for column in ["target_log_close", *covariates]:
        result[column] = pd.to_numeric(result[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    result = result[np.isfinite(result["target_log_close"])].copy()
    return result, covariates


def positive(series: pd.Series | None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce") if series is not None else pd.Series(dtype=float)
    return values.where(values > 0.0, np.nan)


def nonnegative(series: pd.Series | None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce") if series is not None else pd.Series(dtype=float)
    return values.where(values >= 0.0, 0.0)


def build_forecast_tasks(
    df: pd.DataFrame,
    covariates: list[str],
    *,
    context_length: int,
    min_context: int,
    prediction_length: int,
    rolling_stride: int,
    max_windows_per_ticker: int,
) -> tuple[list[dict], list[ForecastOrigin], dict[str, pd.DataFrame]]:
    inputs: list[dict] = []
    origins: list[ForecastOrigin] = []
    ticker_frames: dict[str, pd.DataFrame] = {}

    for ticker, ticker_df in df.groupby("ticker", sort=False):
        ticker_df = ticker_df.sort_values("bar_time_market").reset_index(drop=True)
        ticker_frames[str(ticker)] = ticker_df
        last_origin = len(ticker_df) - prediction_length - 1
        if last_origin < min_context - 1:
            continue
        origin_indices = list(range(min_context - 1, last_origin + 1, max(1, rolling_stride)))
        if max_windows_per_ticker > 0:
            origin_indices = origin_indices[:max_windows_per_ticker]

        for origin_index in origin_indices:
            start_index = max(0, origin_index + 1 - context_length)
            history = ticker_df.iloc[start_index : origin_index + 1]
            target = history["target_log_close"].to_numpy(dtype=np.float32)
            past_covariates = {}
            for column in covariates:
                values = history[column].to_numpy(dtype=np.float32)
                if np.isfinite(values).any():
                    past_covariates[column] = values
            inputs.append({"target": target, "past_covariates": past_covariates})
            origins.append(
                ForecastOrigin(
                    ticker=str(ticker),
                    session_date=str(ticker_df.loc[origin_index, "session_date"]),
                    origin_index=int(origin_index),
                    origin_time=pd.Timestamp(ticker_df.loc[origin_index, "bar_time_market"]),
                    last_close=float(ticker_df.loc[origin_index, "close"]),
                )
            )

    if not inputs:
        raise SystemExit(
            "No forecast windows were created. Lower --min-context/--prediction-length or choose tickers with more bars."
        )
    return inputs, origins, ticker_frames


def run_predictions(
    pipeline,
    inputs: list[dict],
    origins: list[ForecastOrigin],
    ticker_frames: dict[str, pd.DataFrame],
    *,
    prediction_length: int,
    quantile_levels: list[float],
    batch_size: int,
    window_batch_size: int,
    direction_threshold_bps: float,
) -> pd.DataFrame:
    rows: list[dict] = []
    threshold = direction_threshold_bps / 10_000.0
    q_index = {level: idx for idx, level in enumerate(quantile_levels)}

    for start in range(0, len(inputs), max(1, window_batch_size)):
        end = min(start + max(1, window_batch_size), len(inputs))
        quantiles, means = pipeline.predict_quantiles(
            inputs[start:end],
            prediction_length=prediction_length,
            quantile_levels=quantile_levels,
            batch_size=batch_size,
        )
        for local_idx, origin in enumerate(origins[start:end]):
            ticker_df = ticker_frames[origin.ticker]
            q_values = quantiles[local_idx][0].detach().cpu().numpy()
            mean_values = means[local_idx][0].detach().cpu().numpy()
            for step in range(1, prediction_length + 1):
                target_index = origin.origin_index + step
                actual_close = float(ticker_df.loc[target_index, "close"])
                actual_log_close = float(ticker_df.loc[target_index, "target_log_close"])
                predicted_log_close = float(mean_values[step - 1])
                predicted_close = float(np.exp(predicted_log_close))
                actual_return = actual_log_close - math.log(origin.last_close)
                predicted_return = predicted_log_close - math.log(origin.last_close)
                pred_direction = direction_label(predicted_return, threshold)
                actual_direction = direction_label(actual_return, threshold)
                row = {
                    "ticker": origin.ticker,
                    "session_date": origin.session_date,
                    "forecast_origin_time": origin.origin_time.isoformat(),
                    "target_time": pd.Timestamp(ticker_df.loc[target_index, "bar_time_market"]).isoformat(),
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
                }
                if 0.1 in q_index:
                    row["p10_close"] = float(np.exp(q_values[step - 1, q_index[0.1]]))
                if 0.5 in q_index:
                    row["p50_close"] = float(np.exp(q_values[step - 1, q_index[0.5]]))
                if 0.9 in q_index:
                    row["p90_close"] = float(np.exp(q_values[step - 1, q_index[0.9]]))
                if {"p10_close", "p90_close"}.issubset(row):
                    row["p10_p90_covered"] = row["p10_close"] <= actual_close <= row["p90_close"]
                    row["p10_p90_width_bps"] = ((row["p90_close"] / row["p10_close"]) - 1.0) * 10_000.0
                rows.append(row)
    return pd.DataFrame(rows)


def direction_label(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def summarize_predictions(predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    horizon_rows = []
    for horizon, group in predictions.groupby("horizon_bars", sort=True):
        horizon_rows.append(metric_row(f"h{horizon}", group))
    horizon_metrics = pd.DataFrame(horizon_rows)
    overall = metric_row("overall", predictions)
    return horizon_metrics, overall


def metric_row(label: str, df: pd.DataFrame) -> dict:
    actual = df["actual_return"].to_numpy(dtype=float)
    predicted = df["predicted_return"].to_numpy(dtype=float)
    nonflat = df["actual_direction"] != 0
    pred_up = df["predicted_direction"] > 0
    pred_down = df["predicted_direction"] < 0
    actual_up = df["actual_direction"] > 0
    actual_down = df["actual_direction"] < 0
    return {
        "bucket": label,
        "n": int(len(df)),
        "mae_close": safe_mean(df["abs_close_error"]),
        "naive_mae_close": safe_mean(df["naive_abs_close_error"]),
        "mae_return_bps": safe_mean(df["abs_return_error"]) * 10_000.0,
        "naive_mae_return_bps": safe_mean(df["naive_abs_return_error"]) * 10_000.0,
        "mae_return_vs_naive": safe_divide(
            safe_mean(df["abs_return_error"]),
            safe_mean(df["naive_abs_return_error"]),
        ),
        "rmse_return_bps": math.sqrt(safe_mean(df["squared_return_error"])) * 10_000.0,
        "naive_rmse_return_bps": math.sqrt(safe_mean(df["naive_squared_return_error"])) * 10_000.0,
        "bias_return_bps": safe_mean(df["predicted_return"] - df["actual_return"]) * 10_000.0,
        "mean_actual_return_bps": safe_mean(df["actual_return"]) * 10_000.0,
        "mean_predicted_return_bps": safe_mean(df["predicted_return"]) * 10_000.0,
        "direction_accuracy_pct": safe_mean(df["direction_correct"]) * 100.0,
        "direction_accuracy_nonflat_pct": safe_mean(df.loc[nonflat, "direction_correct"]) * 100.0,
        "up_precision_pct": safe_divide(float((pred_up & actual_up).sum()), float(pred_up.sum())) * 100.0,
        "down_precision_pct": safe_divide(float((pred_down & actual_down).sum()), float(pred_down.sum())) * 100.0,
        "return_corr": safe_corr(actual, predicted),
        "p10_p90_coverage_pct": safe_mean(df.get("p10_p90_covered", pd.Series(dtype=float))) * 100.0,
        "p10_p90_width_bps": safe_mean(df.get("p10_p90_width_bps", pd.Series(dtype=float))),
    }


def safe_mean(values: Iterable) -> float:
    series = pd.Series(values)
    if series.empty:
        return float("nan")
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.dropna().empty:
        return float("nan")
    return float(numeric.mean())


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def safe_corr(left: np.ndarray, right: np.ndarray) -> float:
    mask = np.isfinite(left) & np.isfinite(right)
    if mask.sum() < 2:
        return float("nan")
    if np.nanstd(left[mask]) == 0.0 or np.nanstd(right[mask]) == 0.0:
        return float("nan")
    return float(np.corrcoef(left[mask], right[mask])[0, 1])


def write_report(
    *,
    args: argparse.Namespace,
    predictions_path: Path,
    metrics_path: Path,
    report_path: Path,
    predictions: pd.DataFrame,
    horizon_metrics: pd.DataFrame,
    overall: dict,
    covariates: list[str],
    tickers: list[str],
) -> None:
    report = [
        "# Chronos 2 1m Bar Forecast Test",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Session date: `{args.session_date}`",
        f"- Model: `{args.model_id}`",
        f"- Device map: `{args.device_map}`",
        f"- Tickers: `{', '.join(tickers)}`",
        f"- Target: `log(close)`; reported returns are log-return deltas from the forecast origin close.",
        f"- Prediction length: `{args.prediction_length}` bars",
        f"- Context length: `{args.context_length}` bars, minimum context `{args.min_context}` bars",
        f"- Direction threshold: `{args.direction_threshold_bps}` bps",
        f"- Forecast rows: `{len(predictions)}`",
        f"- Predictions CSV: `{predictions_path}`",
        f"- Metrics CSV: `{metrics_path}`",
        "",
        "## Overall Metrics",
        "",
        markdown_table(pd.DataFrame([overall])),
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
        "- This is an after-bar-close walk-forward test: the origin bar close is included in the context.",
        "- Direction is measured by comparing predicted and actual future log returns from the origin close.",
        "- Supervision labels are not loaded as model inputs; only provider bars and deterministic feature groups are used.",
        "- Evaluate these metrics against a naive baseline before treating the model output as a tradable edge.",
    ]
    report_path.write_text("\n".join(report), encoding="utf-8")


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    formatted = df.copy()
    for column in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[column]):
            formatted[column] = formatted[column].map(lambda value: "" if pd.isna(value) else f"{value:.4f}")
    columns = list(formatted.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in formatted.iterrows():
        lines.append("| " + " | ".join(str(row[column]) for column in columns) + " |")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.min_context < 3:
        raise SystemExit("--min-context must be at least 3.")
    if args.prediction_length < 1:
        raise SystemExit("--prediction-length must be at least 1.")

    raw_df = load_session_bars(args)
    model_df, covariates = add_model_columns(raw_df)
    inputs, origins, ticker_frames = build_forecast_tasks(
        model_df,
        covariates,
        context_length=args.context_length,
        min_context=args.min_context,
        prediction_length=args.prediction_length,
        rolling_stride=args.rolling_stride,
        max_windows_per_ticker=args.max_windows_per_ticker,
    )

    pipeline = load_chronos_pipeline(Path(args.chronos_src), args.model_id, args.device_map)
    predictions = run_predictions(
        pipeline,
        inputs,
        origins,
        ticker_frames,
        prediction_length=args.prediction_length,
        quantile_levels=DEFAULT_QUANTILES,
        batch_size=args.batch_size,
        window_batch_size=args.window_batch_size,
        direction_threshold_bps=args.direction_threshold_bps,
    )
    horizon_metrics, overall = summarize_predictions(predictions)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"chronos2_1m_{args.session_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    predictions_path = report_dir / f"{run_id}_predictions.csv"
    metrics_path = report_dir / f"{run_id}_metrics.csv"
    report_path = report_dir / f"{run_id}_report.md"
    config_path = report_dir / f"{run_id}_config.json"

    predictions.to_csv(predictions_path, index=False)
    horizon_metrics.to_csv(metrics_path, index=False)
    config_path.write_text(json.dumps(vars(args), indent=2, sort_keys=True), encoding="utf-8")
    write_report(
        args=args,
        predictions_path=predictions_path,
        metrics_path=metrics_path,
        report_path=report_path,
        predictions=predictions,
        horizon_metrics=horizon_metrics,
        overall=overall,
        covariates=covariates,
        tickers=sorted(ticker_frames.keys()),
    )

    print(f"Wrote predictions: {predictions_path}")
    print(f"Wrote metrics: {metrics_path}")
    print(f"Wrote report: {report_path}")


if __name__ == "__main__":
    main()
