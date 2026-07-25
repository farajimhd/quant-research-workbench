from __future__ import annotations

import argparse
import csv
import gzip
import json
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
from research.news_reaction_model.price_bucket_pnl import (
    AnchorPricePnlBreakdown,
    write_anchor_price_pnl_csv,
)
from research.news_reaction_model.v16 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v16.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v16.data import (
    NewsReactionBatch,
    PreparedNewsReactionDataset,
    audit_prepared_dataset,
)
from research.news_reaction_model.v16.time_features import parse_published_at_utc
from research.news_reaction_model.v16.inference import opportunity_predictions
from research.news_reaction_model.v16.metrics import balanced_accuracy, macro_f1
from research.news_reaction_model.v16.model import NewsReactionModelV16
from research.news_reaction_model.v16.opportunity import (
    OPPORTUNITY_CLASSES,
    OPPORTUNITY_CLASS_NAMES,
    OpportunityClass,
    opportunity_contract,
    opportunity_targets,
)
from research.news_reaction_model.v16.prepare_data import q, qi

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class EvaluationBatch:
    model_batch: NewsReactionBatch
    anchors: np.ndarray


@dataclass(slots=True)
class OpportunityLedger:
    labels: int = 0
    long: int = 0
    short: int = 0
    abstained: int = 0
    long_one_share_pnl: float = 0.0
    short_one_share_pnl: float = 0.0
    one_share_pnl: float = 0.0
    profitable: int = 0
    losing: int = 0
    breakeven: int = 0
    confusion: np.ndarray = field(
        default_factory=lambda: np.zeros((OPPORTUNITY_CLASSES, OPPORTUNITY_CLASSES), dtype=np.int64)
    )

    def add(
        self,
        predicted_class: np.ndarray,
        actual_class: np.ndarray,
        position: np.ndarray,
        pnl: np.ndarray,
    ) -> None:
        predicted = np.asarray(predicted_class, dtype=np.int64).reshape(-1)
        actual = np.asarray(actual_class, dtype=np.int64).reshape(-1)
        side = np.asarray(position, dtype=np.int8).reshape(-1)
        values = np.asarray(pnl, dtype=np.float64).reshape(-1)
        valid = (
            np.isfinite(values)
            & (actual >= 0)
            & (actual < OPPORTUNITY_CLASSES)
            & (predicted >= 0)
            & (predicted < OPPORTUNITY_CLASSES)
        )
        predicted, actual, side, values = (
            predicted[valid],
            actual[valid],
            side[valid],
            values[valid],
        )
        np.add.at(self.confusion, (actual, predicted), 1)
        long = side == 1
        short = side == -1
        active = long | short
        active_pnl = values[active]
        self.labels += int(values.size)
        self.long += int(long.sum())
        self.short += int(short.sum())
        self.abstained += int((~active).sum())
        self.long_one_share_pnl += float(values[long].sum())
        self.short_one_share_pnl += float(values[short].sum())
        self.one_share_pnl += float(active_pnl.sum())
        self.profitable += int((active_pnl > 0).sum())
        self.losing += int((active_pnl < 0).sum())
        self.breakeven += int((active_pnl == 0).sum())

    def summary(self) -> dict[str, Any]:
        active = self.long + self.short
        actual_counts = self.confusion.sum(axis=1)
        predicted_counts = self.confusion.sum(axis=0)
        per_class = {}
        for class_index, class_name in enumerate(OPPORTUNITY_CLASS_NAMES):
            support = int(actual_counts[class_index])
            per_class[class_name] = {
                "support": support,
                "support_share": support / max(self.labels, 1),
                "predicted": int(predicted_counts[class_index]),
                "predicted_share": int(predicted_counts[class_index]) / max(self.labels, 1),
                "recall": float(self.confusion[class_index, class_index]) / max(support, 1),
            }
        return {
            "labels": self.labels,
            "long": self.long,
            "long_one_share_pnl": self.long_one_share_pnl,
            "short": self.short,
            "short_one_share_pnl": self.short_one_share_pnl,
            "abstained": self.abstained,
            "active": active,
            "coverage": active / max(self.labels, 1),
            "one_share_pnl": self.one_share_pnl,
            "one_share_mean_pnl_per_active": self.one_share_pnl / max(active, 1),
            "win_rate": self.profitable / max(active, 1),
            "profitable": self.profitable,
            "losing": self.losing,
            "breakeven": self.breakeven,
            "accuracy": float(np.trace(self.confusion)) / max(self.labels, 1),
            "majority_class_accuracy": (
                float(actual_counts.max()) / max(self.labels, 1) if actual_counts.size else 0.0
            ),
            "macro_f1": macro_f1(self.confusion),
            "balanced_accuracy": balanced_accuracy(self.confusion),
            "per_class": per_class,
            "confusion_actual_rows_predicted_columns": self.confusion.tolist(),
            "class_order": list(OPPORTUNITY_CLASS_NAMES),
        }


def midpoint_proxy_pnl(
    position: np.ndarray,
    high_return: np.ndarray,
    low_return: np.ndarray,
    anchors: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Requested descriptive proxy: side * anchor * midpoint(high return, low return)."""
    side = np.asarray(position, dtype=np.int8)
    high = np.asarray(high_return, dtype=np.float64)
    low = np.asarray(low_return, dtype=np.float64)
    anchor = np.asarray(anchors, dtype=np.float64)
    midpoint_return = (high + low) / 2.0
    pnl = side * anchor * midpoint_return
    return midpoint_return, pnl


def anchor_price_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    reactions = f"{qi(config.news_database)}.{qi(config.reaction_table)}"
    horizons = ",".join(q(value) for value in config.horizons)
    return f"""
SELECT
 canonical_news_id,
 ticker,
 toString(published_at_utc) AS published_at_utc_text,
 horizon_code,
 anchor_price
FROM {reactions} FINAL
WHERE label_version = {q(config.label_version)}
 AND applicable = 1
 AND horizon_code IN ({horizons})
 AND anchor_price IS NOT NULL
 AND anchor_price > 0
 AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
ORDER BY published_at_utc, ticker, canonical_news_id, horizon_code
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def _identity_key(canonical_news_id: str, ticker: str, published_at_utc: Any) -> tuple[str, str, int]:
    published_us = int(round(parse_published_at_utc(published_at_utc).timestamp() * 1_000_000.0))
    return canonical_news_id, ticker, published_us


def load_anchor_prices(
    config: LoaderConfig,
    *,
    start: str,
    end_exclusive: str,
) -> dict[tuple[str, str, int], np.ndarray]:
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    anchors: dict[tuple[str, str, int], np.ndarray] = {}
    text = client.execute(anchor_price_sql(config, start, end_exclusive))
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        index = horizon_index.get(str(row["horizon_code"]))
        if index is None:
            continue
        key = _identity_key(
            str(row["canonical_news_id"]),
            str(row["ticker"]),
            row["published_at_utc_text"],
        )
        values = anchors.setdefault(
            key,
            np.full(len(config.horizons), np.nan, dtype=np.float64),
        )
        values[index] = float(row["anchor_price"])
    return anchors


class PreparedEvaluationDataset:
    def __init__(self, config: LoaderConfig, *, start: str, end_exclusive: str) -> None:
        self.config = config
        self.start = start
        self.end_exclusive = end_exclusive
        self.dataset = PreparedNewsReactionDataset(
            config,
            start=start,
            end_exclusive=end_exclusive,
        )
        self.anchors = load_anchor_prices(
            config,
            start=start,
            end_exclusive=end_exclusive,
        )

    def iter_batches(self) -> Iterator[EvaluationBatch]:
        for model_batch in self.dataset.iter_batches():
            anchors = np.full(
                (model_batch.sample_count, len(self.config.horizons)),
                np.nan,
                dtype=np.float64,
            )
            for row_index, (canonical_news_id, ticker, published_at_utc) in enumerate(
                zip(
                    model_batch.identity["canonical_news_id"],
                    model_batch.identity["ticker"],
                    model_batch.identity["published_at_utc"],
                )
            ):
                values = self.anchors.get(
                    _identity_key(canonical_news_id, ticker, published_at_utc)
                )
                if values is not None:
                    anchors[row_index] = values
            yield EvaluationBatch(model_batch=model_batch, anchors=anchors)

    def stop(self) -> None:
        self.dataset.stop()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate V16 three-class opportunities and the midpoint-return P&L proxy."
    )
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--prepared-root",
        default="",
        help="Optional prepared-dataset relocation for evaluating a checkpoint on another host.",
    )
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
    prepared_root: Path | None = None,
) -> dict[str, Any]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    if prepared_root is not None:
        loader_config.prepared_dataset_root = Path(prepared_root)
    model = NewsReactionModelV16(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    audit = audit_prepared_dataset(loader_config, start, end_exclusive)
    destination = output_dir or checkpoint.parent.parent / "evaluation"
    destination.mkdir(parents=True, exist_ok=True)
    prediction_path = destination / "evaluation_predictions.jsonl.gz"
    prediction_file = gzip.open(prediction_path, "wt", encoding="utf-8") if export_predictions else None
    ledgers = {horizon: OpportunityLedger() for horizon in HORIZONS}
    price_breakdowns = {horizon: AnchorPricePnlBreakdown() for horizon in HORIZONS}
    overall = OpportunityLedger()
    overall_prices = AnchorPricePnlBreakdown()
    dataset = PreparedEvaluationDataset(loader_config, start=start, end_exclusive=end_exclusive)
    articles = labels = 0
    started = time.perf_counter()
    try:
        for batch_index, evaluation_batch in enumerate(dataset.iter_batches(), start=1):
            cpu_batch = evaluation_batch.model_batch
            device_batch = cpu_batch.to(device)
            with torch.inference_mode(), torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda" and amp,
            ):
                output = model(device_batch.x)
            plans = {
                horizon: {
                    key: value.detach().cpu().numpy()
                    for key, value in plan.items()
                }
                for horizon, plan in opportunity_predictions(output).items()
            }
            actual_by_horizon = opportunity_targets(
                cpu_batch.return_targets,
                cpu_batch.label_mask,
            )
            returns = cpu_batch.return_targets.numpy()
            mask = cpu_batch.label_mask.numpy().astype(bool)
            for horizon_index, horizon in enumerate(HORIZONS):
                actual_full = actual_by_horizon[horizon].numpy()
                valid = (
                    mask[:, horizon_index]
                    & (actual_full >= 0)
                    & np.isfinite(evaluation_batch.anchors[:, horizon_index])
                    & (evaluation_batch.anchors[:, horizon_index] > 0)
                )
                if not valid.any():
                    continue
                predicted = plans[horizon]["class"][valid]
                confidence = plans[horizon]["confidence"][valid]
                probabilities = plans[horizon]["probabilities"][valid]
                position = plans[horizon]["position"][valid]
                actual = actual_full[valid]
                high = returns[valid, horizon_index, 1]
                low = returns[valid, horizon_index, 2]
                anchors = evaluation_batch.anchors[valid, horizon_index]
                midpoint_return, pnl = midpoint_proxy_pnl(position, high, low, anchors)
                ledgers[horizon].add(predicted, actual, position, pnl)
                overall.add(predicted, actual, position, pnl)
                price_breakdowns[horizon].add(position, pnl, anchors)
                overall_prices.add(position, pnl, anchors)
                labels += int(valid.sum())
                if prediction_file is not None:
                    valid_rows = np.flatnonzero(valid)
                    for local_index, row_index in enumerate(valid_rows):
                        record = {
                            "canonical_news_id": cpu_batch.identity["canonical_news_id"][row_index],
                            "ticker": cpu_batch.identity["ticker"][row_index],
                            "published_at_utc": cpu_batch.identity["published_at_utc"][row_index],
                            "horizon": horizon,
                            "predicted_class": int(predicted[local_index]),
                            "predicted_opportunity": OPPORTUNITY_CLASS_NAMES[int(predicted[local_index])],
                            "actual_class": int(actual[local_index]),
                            "actual_opportunity": OPPORTUNITY_CLASS_NAMES[int(actual[local_index])],
                            "confidence": float(confidence[local_index]),
                            "probabilities": {
                                name: float(probabilities[local_index, class_index])
                                for class_index, name in enumerate(OPPORTUNITY_CLASS_NAMES)
                            },
                            "position": int(position[local_index]),
                            "anchor_price": float(anchors[local_index]),
                            "actual_high_return": float(high[local_index]),
                            "actual_low_return": float(low[local_index]),
                            "midpoint_return": float(midpoint_return[local_index]),
                            "gross_one_share_pnl": float(pnl[local_index]),
                        }
                        prediction_file.write(
                            json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
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
        "opportunity_contract": opportunity_contract(),
        "position_contract": (
            "long only for predicted upside_dominant; short only for predicted "
            "downside_dominant; no position for no_meaningful_opportunity"
        ),
        "pnl_contract": (
            "descriptive one-share proxy: position * anchor_price * "
            "((actual_high_return + actual_low_return) / 2)"
        ),
        "limitation": (
            "The three-class model predicts no exit price. Midpoint P&L uses realized label extrema, "
            "is not executable, ignores path ordering and costs, and is only a learning-task diagnostic."
        ),
        "overall_across_independent_horizons": overall.summary(),
        "horizons": horizon_summaries,
        "anchor_price_pnl": {
            "overall_across_independent_horizons": overall_prices.summary(),
            "horizons": {
                horizon: price_breakdowns[horizon].summary() for horizon in HORIZONS
            },
        },
    }
    summary_path = destination / "evaluation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    write_summary_csv(destination / "evaluation_positions.csv", horizon_summaries, overall.summary())
    write_anchor_price_pnl_csv(
        destination / "evaluation_anchor_price_pnl.csv",
        [(horizon, price_breakdowns[horizon]) for horizon in HORIZONS]
        + [("ALL_INDEPENDENT_HORIZONS", overall_prices)],
    )
    print(f"COMPLETED articles={articles:,} labels={labels:,} summary={summary_path}", flush=True)
    return summary


def write_summary_csv(
    path: Path,
    horizons: dict[str, dict[str, Any]],
    overall: dict[str, Any],
) -> None:
    fields = (
        "horizon",
        "labels",
        "accuracy",
        "macro_f1",
        "balanced_accuracy",
        "long",
        "long_one_share_pnl",
        "short",
        "short_one_share_pnl",
        "abstained",
        "active",
        "coverage",
        "one_share_pnl",
        "one_share_mean_pnl_per_active",
        "win_rate",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for horizon in HORIZONS:
            writer.writerow(
                {"horizon": horizon, **{field: horizons[horizon][field] for field in fields[1:]}}
            )
        writer.writerow(
            {
                "horizon": "ALL_INDEPENDENT_HORIZONS",
                **{field: overall[field] for field in fields[1:]},
            }
        )


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
        prepared_root=Path(args.prepared_root) if args.prepared_root else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
