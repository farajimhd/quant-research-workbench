from __future__ import annotations

import argparse
import csv
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

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v8 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v8.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v8.data import NewsReactionBatch, audit_prepared_dataset, month_ranges, q, qi, rows_to_batch
from research.news_reaction_model.v8.inference import trade_plans
from research.news_reaction_model.v8.model import NewsReactionModelV8
from research.news_reaction_model.price_bucket_pnl import AnchorPricePnlBreakdown, write_anchor_price_pnl_csv

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class EvaluationBatch:
    model_batch: NewsReactionBatch
    anchors: np.ndarray


@dataclass(slots=True)
class PositionLedger:
    long: int = 0
    short: int = 0
    flat: int = 0
    target_touches: int = 0
    ending_fallbacks: int = 0
    long_one_share_pnl: float = 0.0
    short_one_share_pnl: float = 0.0
    one_share_pnl: float = 0.0
    labels: int = 0

    def add(self, side: np.ndarray, pnl: np.ndarray, touched: np.ndarray) -> None:
        side = np.asarray(side, dtype=np.int8).reshape(-1)
        pnl = np.asarray(pnl, dtype=np.float64).reshape(-1)
        touched = np.asarray(touched, dtype=bool).reshape(-1)
        valid = np.isfinite(pnl)
        side, pnl, touched = side[valid], pnl[valid], touched[valid]
        long = side == 1
        short = side == -1
        active = long | short
        self.long += int(long.sum())
        self.short += int(short.sum())
        self.flat += int((side == 0).sum())
        self.target_touches += int((active & touched).sum())
        self.ending_fallbacks += int((active & ~touched).sum())
        self.long_one_share_pnl += float(pnl[long].sum())
        self.short_one_share_pnl += float(pnl[short].sum())
        self.one_share_pnl += float(pnl.sum())
        self.labels += int(len(side))

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
            "target_touches": self.target_touches,
            "ending_fallbacks": self.ending_fallbacks,
            "target_touch_rate": self.target_touches / max(active, 1),
            "one_share_mean_pnl_per_active": self.one_share_pnl / max(active, 1),
            "labels": self.labels,
        }


def simulate_exits(
    side: np.ndarray,
    target_pct: np.ndarray,
    actual_returns: np.ndarray,
    anchors: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply the agreed target-touch/ending-fallback contract without ordering."""
    side = np.asarray(side, dtype=np.int8)
    target_pct = np.asarray(target_pct, dtype=np.float64)
    actual_returns = np.asarray(actual_returns, dtype=np.float64)
    anchors = np.asarray(anchors, dtype=np.float64)
    touched = (
        ((side == 1) & (actual_returns[:, 1] * 100.0 >= target_pct))
        | ((side == -1) & (actual_returns[:, 2] * 100.0 <= target_pct))
    )
    exit_return = np.where(touched, target_pct / 100.0, actual_returns[:, 0])
    pnl = side * anchors * exit_return
    return touched, exit_return, pnl


def evaluation_batch_sql(config: LoaderConfig, start: dt.date, end: dt.date, cursor_timestamp: str, cursor_ticker: str, cursor_id: str, limit: int) -> str:
    prepared = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    reactions = f"{qi(config.news_database)}.{qi(config.reaction_table)}"
    horizons = ",".join(q(value) for value in config.horizons)
    return f"""
WITH anchors AS
(
 SELECT canonical_news_id, ticker, published_at_utc,
  groupArray(tuple(toString(horizon_code), anchor_price)) AS anchor_values
 FROM {reactions} FINAL
 WHERE label_version = {q(config.label_version)} AND applicable = 1
  AND horizon_code IN ({horizons}) AND anchor_price IS NOT NULL AND anchor_price > 0
  AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
  AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 GROUP BY canonical_news_id, ticker, published_at_utc
)
SELECT p.canonical_news_id AS source_id, p.ticker, p.published_at_utc,
 base64Encode(arrayStringConcat(arrayMap(x -> reinterpretAsString(x), p.openai_embedding)))
  AS openai_embedding_b64,
 p.stock_state,
 p.publication_session, p.horizon_codes, p.return_targets, a.anchor_values
FROM {prepared} AS p FINAL
INNER JOIN anchors AS a USING (canonical_news_id, ticker, published_at_utc)
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
    anchors = np.full((len(rows), len(config.horizons)), np.nan, dtype=np.float64)
    horizon_index = {value: index for index, value in enumerate(config.horizons)}
    for row_index, row in enumerate(rows):
        for horizon, anchor in row.get("anchor_values", ()):
            index = horizon_index.get(str(horizon))
            if index is not None and anchor is not None:
                anchors[row_index, index] = float(anchor)
    return EvaluationBatch(model_batch=model_batch, anchors=anchors)


class ClickHouseEvaluationDataset:
    def __init__(self, config: LoaderConfig, *, start: str, end_exclusive: str) -> None:
        self.config, self.start, self.end_exclusive = config, start, end_exclusive
        self._stop = threading.Event()

    def iter_batches(self) -> Iterator[EvaluationBatch]:
        months = month_ranges(self.start, self.end_exclusive)
        tasks: queue.Queue[tuple[dt.date, dt.date] | None] = queue.Queue()
        output: queue.Queue[Any] = queue.Queue(maxsize=max(1, self.config.prefetch_batches))
        for month in months: tasks.put(month)
        workers = max(1, min(self.config.workers, len(months)))
        for _ in range(workers): tasks.put(None)

        def safe_put(value: Any) -> bool:
            while not self._stop.is_set():
                try: output.put(value, timeout=0.25); return True
                except queue.Full: continue
            return False

        def worker() -> None:
            client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
            try:
                while not self._stop.is_set():
                    item = tasks.get()
                    if item is None: break
                    start, end = item
                    cursor_timestamp, cursor_ticker, cursor_id = "1970-01-01", "", ""
                    while not self._stop.is_set():
                        text = client.execute(evaluation_batch_sql(self.config, start, end, cursor_timestamp, cursor_ticker, cursor_id, self.config.query_batch_articles))
                        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
                        if not rows: break
                        for offset in range(0, len(rows), self.config.batch_size):
                            if not safe_put(rows_to_evaluation_batch(rows[offset:offset + self.config.batch_size], self.config)): return
                        cursor_timestamp, cursor_ticker, cursor_id = str(rows[-1]["published_at_utc"]), str(rows[-1]["ticker"]), str(rows[-1]["source_id"])
                        if len(rows) < self.config.query_batch_articles: break
            except BaseException as exc:
                safe_put(exc); self._stop.set()
            finally: safe_put(None)

        threads = [threading.Thread(target=worker, name=f"news-v8-eval-{i}", daemon=True) for i in range(workers)]
        for thread in threads: thread.start()
        done = 0
        while done < workers:
            item = output.get()
            if item is None: done += 1
            elif isinstance(item, BaseException): self.stop(); raise item
            else: yield item
        for thread in threads: thread.join()

    def stop(self) -> None: self._stop.set()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the V8 OpenAI-text ablation with unchanged V7 target-touch plans."
    )
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument("--export-predictions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def evaluate_checkpoint(checkpoint: Path, *, output_dir: Path | None = None, start: str = "2026-01-01", end_exclusive: str = "2027-01-01", export_predictions: bool = True, amp: bool = True) -> dict[str, Any]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV8(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"]); model.eval()
    audit = audit_prepared_dataset(loader_config, start, end_exclusive)
    destination = output_dir or checkpoint.parent.parent / "evaluation"
    destination.mkdir(parents=True, exist_ok=True)
    prediction_file = gzip.open(destination / "evaluation_predictions.jsonl.gz", "wt", encoding="utf-8") if export_predictions else None
    ledgers = {horizon: PositionLedger() for horizon in HORIZONS}; overall = PositionLedger()
    price_breakdowns = {horizon: AnchorPricePnlBreakdown() for horizon in HORIZONS}
    overall_price_breakdown = AnchorPricePnlBreakdown()
    dataset = ClickHouseEvaluationDataset(loader_config, start=start, end_exclusive=end_exclusive)
    articles = labels = 0; started = time.perf_counter()
    try:
        for batch_index, evaluation in enumerate(dataset.iter_batches(), start=1):
            cpu_batch = evaluation.model_batch
            with torch.inference_mode(), torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda" and amp):
                plans = trade_plans(model(cpu_batch.to(device).x))
            returns = cpu_batch.return_targets.numpy(); mask = cpu_batch.label_mask.numpy().astype(bool)
            for horizon_index, horizon in enumerate(HORIZONS):
                valid = mask[:, horizon_index] & np.isfinite(evaluation.anchors[:, horizon_index]) & (evaluation.anchors[:, horizon_index] > 0)
                if not valid.any(): continue
                plan = {key: value.detach().cpu().numpy() for key, value in plans[horizon].items()}
                side = plan["side"][valid].astype(np.int8); target_pct = plan["target_pct"][valid].astype(np.float64)
                actual = returns[valid, horizon_index].astype(np.float64)
                anchors = evaluation.anchors[valid, horizon_index]
                touched, _exit_return, pnl = simulate_exits(side, target_pct, actual, anchors)
                ledgers[horizon].add(side, pnl, touched); overall.add(side, pnl, touched)
                price_breakdowns[horizon].add(side, pnl, anchors); overall_price_breakdown.add(side, pnl, anchors)
                labels += int(valid.sum())
                if prediction_file is not None:
                    for local, row_index in enumerate(np.flatnonzero(valid)):
                        prediction_file.write(json.dumps({
                            "canonical_news_id": cpu_batch.identity["canonical_news_id"][row_index],
                            "ticker": cpu_batch.identity["ticker"][row_index],
                            "published_at_utc": cpu_batch.identity["published_at_utc"][row_index],
                            "horizon": horizon, "position": int(side[local]), "target_pct": float(target_pct[local]),
                            "predicted_high_class": int(plan["high_class"][row_index]), "predicted_low_class": int(plan["low_class"][row_index]),
                            "predicted_ending_class": int(plan["ending_class"][row_index]), "target_touched": bool(touched[local]),
                            "exit": "target" if touched[local] else "ending", "anchor_price": float(anchors[local]),
                            "actual_ending_return": float(actual[local, 0]), "actual_high_return": float(actual[local, 1]),
                            "actual_low_return": float(actual[local, 2]), "gross_one_share_pnl": float(pnl[local]),
                        }, separators=(",", ":"), allow_nan=False) + "\n")
            articles += cpu_batch.sample_count
            if batch_index == 1 or batch_index % 10 == 0:
                print(f"EVALUATE batches={batch_index:,} articles={articles:,}/{audit['rows']:,} labels={labels:,} rate={articles / max(time.perf_counter()-started, 1e-9):,.0f} articles/s", flush=True)
    finally:
        dataset.stop()
        if prediction_file is not None: prediction_file.close()
    horizons = {
        horizon: {**ledger.summary(), "anchor_price_pnl": price_breakdowns[horizon].summary()}
        for horizon, ledger in ledgers.items()
    }
    summary = {
        "model_version": MODEL_VERSION, "checkpoint": str(checkpoint), "validation_range": [start, end_exclusive],
        "articles": articles, "labels": labels, "elapsed_seconds": time.perf_counter() - started,
        "position_contract": "dominant conservative predicted high/low excursion; ties and sub-threshold spans abstain",
        "exit_contract": "exit at predicted conservative target when actual horizon high/low touches it; otherwise exit at actual ending price",
        "limitations": "independent one-share horizon ledgers before costs; no ordering, stop, risk management, capital, or overlap reconciliation",
        "overall_across_independent_horizons": {
            **overall.summary(), "anchor_price_pnl": overall_price_breakdown.summary()
        }, "horizons": horizons,
    }
    (destination / "evaluation_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    fields = ["horizon", "long", "long_one_share_pnl", "short", "short_one_share_pnl", "flat", "target_touches", "ending_fallbacks", "target_touch_rate", "one_share_pnl", "active", "one_share_mean_pnl_per_active", "labels"]
    with (destination / "evaluation_positions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for horizon in HORIZONS: writer.writerow({"horizon": horizon, **{key: horizons[horizon][key] for key in fields[1:]}})
        writer.writerow({"horizon": "ALL_INDEPENDENT_HORIZONS", **{key: overall.summary()[key] for key in fields[1:]}})
    write_anchor_price_pnl_csv(
        destination / "evaluation_anchor_price_pnl.csv",
        [(horizon, price_breakdowns[horizon]) for horizon in HORIZONS]
        + [("ALL_INDEPENDENT_HORIZONS", overall_price_breakdown)],
    )
    print(f"COMPLETED articles={articles:,} labels={labels:,} summary={destination / 'evaluation_summary.json'}", flush=True)
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    evaluate_checkpoint(Path(args.checkpoint), output_dir=Path(args.output_dir) if args.output_dir else None, start=args.start, end_exclusive=args.end_exclusive, export_predictions=args.export_predictions, amp=args.amp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

