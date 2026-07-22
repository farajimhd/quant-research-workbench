from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import torch

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v2 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v2.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v2.data import NewsReactionBatch, audit_prepared_dataset, month_ranges, q, qi, rows_to_batch
from research.news_reaction_model.v2.metrics import PositionPnlAccumulator, RegressionAccumulator
from research.news_reaction_model.v2.model import NewsReactionModelV2

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class EvaluationBatch:
    model_batch: NewsReactionBatch
    raw_returns: np.ndarray
    robust_scales: np.ndarray
    trade_prices: np.ndarray


def evaluation_batch_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor_timestamp: str,
    cursor_ticker: str,
    cursor_id: str,
    limit: int,
) -> str:
    prepared = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    reactions = f"{qi(config.news_database)}.{qi(config.reaction_table)}"
    scales = f"{qi(config.news_database)}.{qi(config.scale_table)}"
    horizon_values = ",".join(q(value) for value in config.horizons)
    return f"""
WITH
scale_rows AS
(
 SELECT ticker, horizon_code, publication_session, argMax(robust_scale, built_at) AS robust_scale
 FROM {scales} FINAL
 WHERE scale_version = {q(config.scale_version)}
 GROUP BY ticker, horizon_code, publication_session
),
raw_labels AS
(
 SELECT r.canonical_news_id AS label_news_id, r.ticker AS label_ticker,
  r.published_at_utc AS label_published_at_utc,
  groupArray(tuple(
   toString(r.horizon_code), r.target_return, r.high_return, r.low_return,
   if(ts.robust_scale > 0, ts.robust_scale, gs.robust_scale),
   r.anchor_price, r.target_price
  )) AS pnl_targets
 FROM {reactions} AS r FINAL
 LEFT JOIN scale_rows AS ts
  ON ts.ticker = r.ticker AND ts.horizon_code = r.horizon_code AND ts.publication_session = r.publication_session
 LEFT JOIN scale_rows AS gs
  ON gs.ticker = '*' AND gs.horizon_code = r.horizon_code AND gs.publication_session = r.publication_session
 WHERE r.label_version = {q(config.label_version)} AND r.applicable = 1
  AND r.horizon_code IN ({horizon_values})
  AND r.target_return IS NOT NULL
  AND r.anchor_price IS NOT NULL AND r.anchor_price > 0
  AND r.target_price IS NOT NULL
  AND (ts.robust_scale > 0 OR gs.robust_scale > 0)
  AND r.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
  AND r.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 GROUP BY label_news_id, label_ticker, label_published_at_utc
)
SELECT p.canonical_news_id AS source_id, p.ticker, p.published_at_utc, p.chunks,
 p.publication_session, p.horizon_codes, p.return_targets, labels.pnl_targets
FROM {prepared} AS p FINAL
INNER JOIN raw_labels AS labels
 ON labels.label_news_id = p.canonical_news_id AND labels.label_ticker = p.ticker
 AND labels.label_published_at_utc = p.published_at_utc
WHERE p.dataset_version = {q(config.dataset_version)}
 AND p.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND p.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 AND (p.published_at_utc, p.ticker, p.canonical_news_id) >
     (toDateTime64({q(cursor_timestamp)}, 9, 'UTC'), {q(cursor_ticker)}, {q(cursor_id)})
ORDER BY p.published_at_utc, p.ticker, p.canonical_news_id
LIMIT {int(limit)}
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def rows_to_evaluation_batch(rows: list[dict[str, Any]], config: LoaderConfig) -> EvaluationBatch:
    model_batch = rows_to_batch(rows, config)
    b, h = len(rows), len(config.horizons)
    raw_returns = np.full((b, h, 3), np.nan, dtype=np.float64)
    robust_scales = np.full((b, h), np.nan, dtype=np.float64)
    trade_prices = np.full((b, h, 2), np.nan, dtype=np.float64)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    for row_index, row in enumerate(rows):
        for target in row.get("pnl_targets", ()):
            if len(target) != 7:
                continue
            horizon = horizon_index.get(str(target[0]))
            if horizon is None:
                continue
            raw_returns[row_index, horizon] = [
                float(target[1]) if target[1] is not None else np.nan,
                float(target[2]) if target[2] is not None else np.nan,
                float(target[3]) if target[3] is not None else np.nan,
            ]
            robust_scales[row_index, horizon] = float(target[4]) if target[4] is not None else np.nan
            trade_prices[row_index, horizon] = [
                float(target[5]) if target[5] is not None else np.nan,
                float(target[6]) if target[6] is not None else np.nan,
            ]
    return EvaluationBatch(
        model_batch=model_batch,
        raw_returns=raw_returns,
        robust_scales=robust_scales,
        trade_prices=trade_prices,
    )


class ClickHouseEvaluationDataset:
    def __init__(self, config: LoaderConfig, *, start: str, end_exclusive: str) -> None:
        self.config = config
        self.start = start
        self.end_exclusive = end_exclusive
        self._stop = threading.Event()

    def iter_batches(self) -> Iterator[EvaluationBatch]:
        tasks: queue.Queue[tuple[dt.date, dt.date] | None] = queue.Queue()
        output: queue.Queue[Any] = queue.Queue(maxsize=max(1, self.config.prefetch_batches))
        months = month_ranges(self.start, self.end_exclusive)
        for month in months:
            tasks.put(month)
        workers = max(1, min(self.config.workers, len(months)))
        for _ in range(workers):
            tasks.put(None)

        def safe_put(value: Any) -> bool:
            while not self._stop.is_set():
                try:
                    output.put(value, timeout=0.25)
                    return True
                except queue.Full:
                    continue
            return False

        def worker() -> None:
            client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
            try:
                while not self._stop.is_set():
                    item = tasks.get()
                    if item is None:
                        break
                    month_start, month_end = item
                    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
                    while not self._stop.is_set():
                        text = client.execute(evaluation_batch_sql(
                            self.config, month_start, month_end, cursor_timestamp, cursor_ticker, cursor_id,
                            self.config.query_batch_articles,
                        ))
                        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                        if not rows:
                            break
                        for offset in range(0, len(rows), self.config.batch_size):
                            if not safe_put(rows_to_evaluation_batch(rows[offset:offset + self.config.batch_size], self.config)):
                                return
                        cursor_timestamp = str(rows[-1]["published_at_utc"])
                        cursor_ticker = str(rows[-1]["ticker"])
                        cursor_id = str(rows[-1]["source_id"])
                        if len(rows) < self.config.query_batch_articles:
                            break
            except BaseException as exc:
                safe_put(exc)
                self._stop.set()
            finally:
                safe_put(None)

        threads = [threading.Thread(target=worker, name=f"news-evaluation-loader-{index}", daemon=True) for index in range(workers)]
        for thread in threads:
            thread.start()
        completed = 0
        while completed < workers:
            item = output.get()
            if item is None:
                completed += 1
            elif isinstance(item, BaseException):
                self.stop()
                raise item
            else:
                yield item
        for thread in threads:
            thread.join()

    def stop(self) -> None:
        self._stop.set()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate news-reaction-model v2 direction and event-level P&L.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument("--flat-z", default="0.5")
    parser.add_argument("--cost-bps", default="0,2,5,10")
    parser.add_argument("--notional", type=float, default=10_000.0)
    parser.add_argument("--export-predictions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    checkpoint = Path(args.checkpoint)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model_config = ModelConfig(**state["config"]["model"])
    model = NewsReactionModelV2(model_config).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    audit = audit_prepared_dataset(loader_config, args.start, args.end_exclusive)
    flat_values = tuple(float(value) for value in args.flat_z.split(",") if value.strip())
    if not flat_values or any(value < 0 for value in flat_values):
        raise SystemExit("--flat-z must contain one or more non-negative values")
    costs = tuple(float(value) for value in args.cost_bps.split(",") if value.strip())
    output_dir = Path(args.output_dir) if args.output_dir else checkpoint.parent.parent / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "evaluation_predictions.jsonl.gz"
    prediction_file = gzip.open(prediction_path, "wt", encoding="utf-8") if args.export_predictions else None
    regression = RegressionAccumulator()
    accumulators = {horizon: PositionPnlAccumulator() for horizon in HORIZONS}
    overall = PositionPnlAccumulator()
    dataset = ClickHouseEvaluationDataset(loader_config, start=args.start, end_exclusive=args.end_exclusive)
    articles, labels = 0, 0
    started = time.perf_counter()
    try:
        for batch_index, evaluation_batch in enumerate(dataset.iter_batches(), start=1):
            cpu_batch = evaluation_batch.model_batch
            model_batch = cpu_batch.to(device)
            with torch.inference_mode(), torch.autocast(
                device_type=device.type, dtype=torch.bfloat16,
                enabled=device.type == "cuda" and args.amp,
            ):
                output = model(model_batch.x)
            forecasts = output.return_forecasts.float().cpu()
            if not bool(torch.isfinite(forecasts).all()):
                raise RuntimeError(f"Non-finite forecast detected in evaluation batch {batch_index}")
            regression.add(forecasts, cpu_batch.return_targets, cpu_batch.label_mask)
            predicted = forecasts.numpy()
            actual = cpu_batch.return_targets.numpy()
            mask = cpu_batch.label_mask.numpy().astype(bool)
            for horizon_index, horizon in enumerate(HORIZONS):
                valid = mask[:, horizon_index]
                if not valid.any():
                    continue
                values = (
                    predicted[valid, horizon_index, 0], actual[valid, horizon_index, 0],
                    evaluation_batch.raw_returns[valid, horizon_index, 0],
                    evaluation_batch.raw_returns[valid, horizon_index, 1],
                    evaluation_batch.raw_returns[valid, horizon_index, 2],
                    evaluation_batch.robust_scales[valid, horizon_index],
                    evaluation_batch.trade_prices[valid, horizon_index, 0],
                    evaluation_batch.trade_prices[valid, horizon_index, 1],
                )
                accumulators[horizon].add(*values)
                overall.add(*values)
                labels += int(valid.sum())
                if prediction_file is not None:
                    _write_predictions(
                        prediction_file, cpu_batch, valid, horizon, horizon_index,
                        predicted, actual, evaluation_batch.raw_returns, evaluation_batch.robust_scales,
                        evaluation_batch.trade_prices,
                        flat_values[0],
                    )
            articles += cpu_batch.sample_count
            if batch_index == 1 or batch_index % 10 == 0:
                elapsed = time.perf_counter() - started
                print(
                    f"EVALUATE batches={batch_index:,} articles={articles:,}/{audit['rows']:,} "
                    f"labels={labels:,} rate={articles / max(elapsed, 1e-9):,.0f} articles/s",
                    flush=True,
                )
    finally:
        dataset.stop()
        if prediction_file is not None:
            prediction_file.close()

    summary: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "checkpoint": str(checkpoint),
        "validation_range": [args.start, args.end_exclusive],
        "articles": articles,
        "labels": labels,
        "elapsed_seconds": time.perf_counter() - started,
        "cost_bps": list(costs),
        "notional_per_event": args.notional,
        "pnl_contract": {
            "position_source": "predicted_abnormal_target_return",
            "flat_band": "flat_z * training_only_robust_scale(ticker,horizon,publication_session)",
            "gross_raw_pnl": "position * raw_target_return",
            "gross_abnormal_pnl": "position * abnormal_target_return",
            "gross_one_share_pnl": "position * (target_price - anchor_price)",
            "limitation": "event-level fixed-notional proxy; overlapping positions and execution sequencing are not reconciled",
        },
        "regression": regression.compute("validation"),
        "flat_band_scenarios": {},
    }
    for flat_z in flat_values:
        scenario = {
            "overall": overall.compute(flat_z=flat_z, cost_bps=costs, notional=args.notional),
            "horizons": {
                horizon: accumulator.compute(flat_z=flat_z, cost_bps=costs, notional=args.notional)
                for horizon, accumulator in accumulators.items()
            },
        }
        summary["flat_band_scenarios"][f"{flat_z:g}"] = scenario
    summary_path = output_dir / "evaluation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    print(f"COMPLETED articles={articles:,} labels={labels:,} summary={summary_path}", flush=True)
    if prediction_file is not None:
        print(f"PREDICTIONS {prediction_path}", flush=True)
    return 0


def _write_predictions(
    handle: Any,
    batch: NewsReactionBatch,
    valid: np.ndarray,
    horizon: str,
    horizon_index: int,
    predicted: np.ndarray,
    actual: np.ndarray,
    raw_returns: np.ndarray,
    robust_scales: np.ndarray,
    trade_prices: np.ndarray,
    flat_z: float,
) -> None:
    for row_index in np.flatnonzero(valid):
        scale = float(robust_scales[row_index, horizon_index])
        prediction = float(predicted[row_index, horizon_index, 0])
        actual_abnormal = float(actual[row_index, horizon_index, 0])
        raw_target = float(raw_returns[row_index, horizon_index, 0])
        anchor_price = float(trade_prices[row_index, horizon_index, 0])
        target_price = float(trade_prices[row_index, horizon_index, 1])
        threshold = flat_z * scale
        predicted_side = 1 if prediction > threshold else -1 if prediction < -threshold else 0
        actual_side = 1 if actual_abnormal > threshold else -1 if actual_abnormal < -threshold else 0
        record = {
            "canonical_news_id": batch.identity["canonical_news_id"][row_index],
            "ticker": batch.identity["ticker"][row_index],
            "published_at_utc": batch.identity["published_at_utc"][row_index],
            "horizon": horizon,
            "predicted_abnormal_target_return": prediction,
            "actual_abnormal_target_return": actual_abnormal,
            "actual_raw_target_return": raw_target,
            "anchor_price": anchor_price,
            "target_price": target_price,
            "robust_scale": scale,
            "flat_band": threshold,
            "predicted_side": predicted_side,
            "actual_side": actual_side,
            "gross_raw_pnl_return": predicted_side * raw_target,
            "gross_abnormal_pnl_return": predicted_side * actual_abnormal,
            "gross_one_share_pnl": predicted_side * (target_price - anchor_price),
        }
        handle.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
