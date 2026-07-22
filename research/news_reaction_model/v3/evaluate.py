from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import queue
import threading
import time
from dataclasses import dataclass, field
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
from research.news_reaction_model.v3 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v3.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v3.data import (
    NewsReactionBatch,
    audit_prepared_dataset,
    month_ranges,
    q,
    qi,
    rows_to_batch,
)
from research.news_reaction_model.v3.model import NewsReactionModelV3

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class EvaluationBatch:
    model_batch: NewsReactionBatch
    trade_prices: np.ndarray


@dataclass(slots=True)
class PositionLedger:
    long: int = 0
    short: int = 0
    flat: int = 0
    long_one_share_pnl: float = 0.0
    short_one_share_pnl: float = 0.0
    one_share_pnl: float = 0.0
    correct: int = 0
    labels: int = 0
    confusion: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=np.int64))

    def add(self, positions: np.ndarray, actual_classes: np.ndarray, anchors: np.ndarray, targets: np.ndarray) -> None:
        positions = np.asarray(positions, dtype=np.int8).reshape(-1)
        actual_classes = np.asarray(actual_classes, dtype=np.int8).reshape(-1)
        anchors = np.asarray(anchors, dtype=np.float64).reshape(-1)
        targets = np.asarray(targets, dtype=np.float64).reshape(-1)
        valid = np.isfinite(anchors) & (anchors > 0) & np.isfinite(targets)
        if not valid.any():
            return
        positions, actual_classes = positions[valid], actual_classes[valid]
        delta = targets[valid] - anchors[valid]
        pnl = positions * delta
        long = positions == 1
        short = positions == -1
        self.long += int(long.sum())
        self.short += int(short.sum())
        self.flat += int((positions == 0).sum())
        self.long_one_share_pnl += float(pnl[long].sum())
        self.short_one_share_pnl += float(pnl[short].sum())
        self.one_share_pnl += float(pnl.sum())
        self.correct += int((positions == actual_classes).sum())
        self.labels += int(len(positions))
        index = {-1: 0, 0: 1, 1: 2}
        for actual, predicted in zip(actual_classes, positions):
            self.confusion[index[int(actual)], index[int(predicted)]] += 1

    def summary(self) -> dict[str, Any]:
        active = self.long + self.short
        return {
            "long": self.long,
            "long_one_share_pnl": self.long_one_share_pnl,
            "short": self.short,
            "short_one_share_pnl": self.short_one_share_pnl,
            "flat": self.flat,
            "one_share_pnl": self.one_share_pnl,
            "active": active,
            "one_share_mean_pnl_per_active": self.one_share_pnl / max(active, 1),
            "three_class_accuracy": self.correct / max(self.labels, 1),
            "labels": self.labels,
            "confusion_actual_rows_predicted_columns": self.confusion.tolist(),
            "class_order": ["short", "flat", "long"],
        }


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
    horizon_values = ",".join(q(value) for value in config.horizons)
    return f"""
WITH raw_labels AS
(
 SELECT r.canonical_news_id AS label_news_id, r.ticker AS label_ticker,
  r.published_at_utc AS label_published_at_utc,
  groupArray(tuple(toString(r.horizon_code), r.anchor_price, r.target_price)) AS trade_targets
 FROM {reactions} AS r FINAL
 WHERE r.label_version = {q(config.label_version)} AND r.applicable = 1
  AND r.horizon_code IN ({horizon_values})
  AND r.target_return IS NOT NULL
  AND r.anchor_price IS NOT NULL AND r.anchor_price > 0
  AND r.target_price IS NOT NULL
  AND r.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
  AND r.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 GROUP BY label_news_id, label_ticker, label_published_at_utc
)
SELECT p.canonical_news_id AS source_id, p.ticker, p.published_at_utc, p.chunks,
 p.publication_session, p.horizon_codes, p.class_targets, p.return_targets, labels.trade_targets
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
    trade_prices = np.full((len(rows), len(config.horizons), 2), np.nan, dtype=np.float64)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    for row_index, row in enumerate(rows):
        for target in row.get("trade_targets", ()):
            if len(target) != 3:
                continue
            horizon = horizon_index.get(str(target[0]))
            if horizon is None:
                continue
            trade_prices[row_index, horizon] = [
                float(target[1]) if target[1] is not None else np.nan,
                float(target[2]) if target[2] is not None else np.nan,
            ]
    return EvaluationBatch(model_batch=model_batch, trade_prices=trade_prices)


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
            client = ClickHouseHttpClient(
                default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(),
            )
            try:
                while not self._stop.is_set():
                    item = tasks.get()
                    if item is None:
                        break
                    month_start, month_end = item
                    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
                    while not self._stop.is_set():
                        text = client.execute(evaluation_batch_sql(
                            self.config, month_start, month_end,
                            cursor_timestamp, cursor_ticker, cursor_id,
                            self.config.query_batch_articles,
                        ))
                        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                        if not rows:
                            break
                        for offset in range(0, len(rows), self.config.batch_size):
                            batch_rows = rows[offset:offset + self.config.batch_size]
                            if not safe_put(rows_to_evaluation_batch(batch_rows, self.config)):
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

        threads = [
            threading.Thread(target=worker, name=f"news-v3-evaluation-loader-{index}", daemon=True)
            for index in range(workers)
        ]
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
    parser = argparse.ArgumentParser(description="Evaluate v3 hierarchical positions and one-share P&L by timeframe.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument("--export-predictions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def evaluate_checkpoint(
    checkpoint: Path,
    *,
    output_dir: Path | None = None,
    start: str = "2026-01-01",
    end_exclusive: str = "2027-01-01",
    export_predictions: bool = True,
    amp: bool = True,
) -> dict[str, Any]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV3(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    audit = audit_prepared_dataset(loader_config, start, end_exclusive)
    destination = output_dir or checkpoint.parent.parent / "evaluation"
    destination.mkdir(parents=True, exist_ok=True)
    prediction_path = destination / "evaluation_predictions.jsonl.gz"
    prediction_file = gzip.open(prediction_path, "wt", encoding="utf-8") if export_predictions else None
    ledgers = {horizon: PositionLedger() for horizon in HORIZONS}
    overall = PositionLedger()
    dataset = ClickHouseEvaluationDataset(loader_config, start=start, end_exclusive=end_exclusive)
    articles, labels = 0, 0
    started = time.perf_counter()
    try:
        for batch_index, evaluation_batch in enumerate(dataset.iter_batches(), start=1):
            cpu_batch = evaluation_batch.model_batch
            model_batch = cpu_batch.to(device)
            with torch.inference_mode(), torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda" and amp,
            ):
                output = model(model_batch.x)
            positions = output.positions().cpu().numpy()
            class_probabilities = output.class_probabilities().cpu().numpy()
            actionable_probabilities = torch.softmax(output.actionable_logits.float(), dim=-1).cpu().numpy()
            direction_probabilities = torch.softmax(output.direction_logits.float(), dim=-1).cpu().numpy()
            magnitudes = output.magnitude_forecasts.float().cpu().numpy()
            if not (
                np.isfinite(class_probabilities).all()
                and np.isfinite(actionable_probabilities).all()
                and np.isfinite(direction_probabilities).all()
                and np.isfinite(magnitudes).all()
            ):
                raise RuntimeError(f"Non-finite hierarchical forecast detected in evaluation batch {batch_index}")
            mask = cpu_batch.label_mask.numpy().astype(bool)
            actual_classes = cpu_batch.class_targets.numpy().astype(np.int8) - 1
            for horizon_index, horizon in enumerate(HORIZONS):
                valid = mask[:, horizon_index]
                if not valid.any():
                    continue
                anchors = evaluation_batch.trade_prices[valid, horizon_index, 0]
                targets = evaluation_batch.trade_prices[valid, horizon_index, 1]
                horizon_positions = positions[valid, horizon_index]
                horizon_actual = actual_classes[valid, horizon_index]
                ledgers[horizon].add(horizon_positions, horizon_actual, anchors, targets)
                overall.add(horizon_positions, horizon_actual, anchors, targets)
                labels += int(valid.sum())
                if prediction_file is not None:
                    write_predictions(
                        prediction_file, cpu_batch, valid, horizon, horizon_index,
                        positions, class_probabilities, actionable_probabilities,
                        direction_probabilities, magnitudes,
                        evaluation_batch.trade_prices, actual_classes,
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

    horizon_summaries = {horizon: ledger.summary() for horizon, ledger in ledgers.items()}
    summary: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "checkpoint": str(checkpoint),
        "validation_range": [start, end_exclusive],
        "articles": articles,
        "labels": labels,
        "elapsed_seconds": time.perf_counter() - started,
        "position_contract": (
            "flat when actionable argmax is false; otherwise short/long from conditional-direction argmax"
        ),
        "pnl_contract": "one share per non-flat article/timeframe position; target_price - anchor_price",
        "limitation": (
            "descriptive independent-timeframe ledger before costs; overlapping positions, capital, and execution are not reconciled"
        ),
        "overall_across_independent_horizons": overall.summary(),
        "horizons": horizon_summaries,
    }
    summary_path = destination / "evaluation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    write_summary_csv(destination / "evaluation_positions.csv", horizon_summaries, overall.summary())
    print(f"COMPLETED articles={articles:,} labels={labels:,} summary={summary_path}", flush=True)
    return summary


def write_predictions(
    handle: Any,
    batch: NewsReactionBatch,
    valid: np.ndarray,
    horizon: str,
    horizon_index: int,
    positions: np.ndarray,
    class_probabilities: np.ndarray,
    actionable_probabilities: np.ndarray,
    direction_probabilities: np.ndarray,
    magnitudes: np.ndarray,
    trade_prices: np.ndarray,
    actual_classes: np.ndarray,
) -> None:
    for row_index in np.flatnonzero(valid):
        anchor = float(trade_prices[row_index, horizon_index, 0])
        target = float(trade_prices[row_index, horizon_index, 1])
        side = int(positions[row_index, horizon_index])
        record = {
            "canonical_news_id": batch.identity["canonical_news_id"][row_index],
            "ticker": batch.identity["ticker"][row_index],
            "published_at_utc": batch.identity["published_at_utc"][row_index],
            "horizon": horizon,
            "position": side,
            "actual_class": int(actual_classes[row_index, horizon_index]),
            "probability_actionable": float(actionable_probabilities[row_index, horizon_index, 1]),
            "probability_negative": float(class_probabilities[row_index, horizon_index, 0]),
            "probability_flat": float(class_probabilities[row_index, horizon_index, 1]),
            "probability_positive": float(class_probabilities[row_index, horizon_index, 2]),
            "probability_up_given_actionable": float(direction_probabilities[row_index, horizon_index, 1]),
            "expected_target_magnitude": float(magnitudes[row_index, horizon_index, 0]),
            "anchor_price": anchor,
            "target_price": target,
            "gross_one_share_pnl": side * (target - anchor),
        }
        handle.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")


def write_summary_csv(
    path: Path,
    horizons: dict[str, dict[str, Any]],
    overall: dict[str, Any],
) -> None:
    fields = (
        "horizon", "long", "long_one_share_pnl", "short", "short_one_share_pnl",
        "flat", "one_share_pnl", "active", "one_share_mean_pnl_per_active", "three_class_accuracy", "labels",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for horizon in HORIZONS:
            writer.writerow({"horizon": horizon, **{field: horizons[horizon][field] for field in fields[1:]}})
        writer.writerow({"horizon": "ALL_INDEPENDENT_HORIZONS", **{field: overall[field] for field in fields[1:]}})


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    evaluate_checkpoint(
        Path(args.checkpoint),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        start=args.start,
        end_exclusive=args.end_exclusive,
        export_predictions=args.export_predictions,
        amp=args.amp,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
