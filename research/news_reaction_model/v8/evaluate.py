from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import torch

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v8 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v8.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v8.data import (
    NewsReactionBatch,
    audit_prepared_dataset,
    float32_array_base64_sql,
    month_ranges,
    q,
    qi,
    rows_to_batch,
)
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
    profitable: int = 0
    losing: int = 0
    breakeven: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    active_returns: list[float] = field(default_factory=list)

    def add(
        self,
        side: np.ndarray,
        pnl: np.ndarray,
        touched: np.ndarray,
        anchors: np.ndarray | None = None,
    ) -> None:
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
        active_pnl = pnl[active]
        self.profitable += int((active_pnl > 0).sum())
        self.losing += int((active_pnl < 0).sum())
        self.breakeven += int((active_pnl == 0).sum())
        self.gross_profit += float(active_pnl[active_pnl > 0].sum())
        self.gross_loss += float(active_pnl[active_pnl < 0].sum())
        if anchors is not None:
            anchors = np.asarray(anchors, dtype=np.float64).reshape(-1)[valid]
            active_anchors = anchors[active]
            valid_returns = np.isfinite(active_anchors) & (active_anchors > 0)
            self.active_returns.extend(
                (active_pnl[valid_returns] / active_anchors[valid_returns]).tolist()
            )

    def summary(self) -> dict[str, Any]:
        active = self.long + self.short
        returns = np.asarray(self.active_returns, dtype=np.float64)
        if returns.size > 1:
            mean_return = float(returns.mean())
            return_se = float(returns.std(ddof=1) / math.sqrt(returns.size))
            return_ci = (mean_return - 1.96 * return_se, mean_return + 1.96 * return_se)
        elif returns.size == 1:
            mean_return = float(returns[0])
            return_ci = (mean_return, mean_return)
        else:
            mean_return = 0.0
            return_ci = (0.0, 0.0)
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
            "coverage": active / max(self.labels, 1),
            "profitable": self.profitable,
            "losing": self.losing,
            "breakeven": self.breakeven,
            "win_rate": self.profitable / max(active, 1),
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": self.gross_profit / -self.gross_loss if self.gross_loss < 0 else None,
            "mean_active_return": mean_return,
            "median_active_return": float(np.median(returns)) if returns.size else 0.0,
            "mean_active_return_95ci_naive": list(return_ci),
        }


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    confidence: float
    edge_pct: float

    @property
    def key(self) -> tuple[float, float]:
        return self.confidence, self.edge_pct


def parse_threshold_values(raw: str, *, name: str, maximum: float | None = None) -> tuple[float, ...]:
    try:
        values = tuple(sorted({float(value.strip()) for value in raw.split(",") if value.strip()}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a comma-separated list of numbers") from exc
    if not values:
        raise argparse.ArgumentTypeError(f"{name} must contain at least one value")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise argparse.ArgumentTypeError(f"{name} values must be finite and non-negative")
    if maximum is not None and any(value > maximum for value in values):
        raise argparse.ArgumentTypeError(f"{name} values must not exceed {maximum}")
    return values


def threshold_side(
    base_side: np.ndarray,
    plan_confidence: np.ndarray,
    edge_pct: np.ndarray,
    rule: ThresholdRule,
) -> np.ndarray:
    base_side = np.asarray(base_side, dtype=np.int8)
    selected = (
        (base_side != 0)
        & (np.asarray(plan_confidence, dtype=np.float64) >= rule.confidence)
        & (np.asarray(edge_pct, dtype=np.float64) >= rule.edge_pct)
    )
    return np.where(selected, base_side, 0).astype(np.int8, copy=False)


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
    embedding_transport = float32_array_base64_sql("p.openai_embedding")
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
 {embedding_transport} AS openai_embedding_b64,
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
    parser.add_argument(
        "--confidence-thresholds",
        default="0,0.3,0.4,0.5,0.6,0.7",
        help="Comma-separated minimum plan confidences. Plan confidence is min(high, low).",
    )
    parser.add_argument(
        "--edge-thresholds-pct",
        default="0,0.25,0.5,1,2",
        help="Comma-separated minimum directional edges in percentage points.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def evaluate_checkpoint(
    checkpoint: Path,
    *,
    output_dir: Path | None = None,
    start: str = "2026-01-01",
    end_exclusive: str = "2027-01-01",
    export_predictions: bool = True,
    amp: bool = True,
    confidence_thresholds: tuple[float, ...] = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7),
    edge_thresholds_pct: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 2.0),
) -> dict[str, Any]:
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
    confidence_thresholds = tuple(sorted(set(float(value) for value in confidence_thresholds)))
    edge_thresholds_pct = tuple(sorted(set(float(value) for value in edge_thresholds_pct)))
    if 0.0 not in confidence_thresholds or 0.0 not in edge_thresholds_pct:
        raise ValueError("Threshold sweep must include confidence=0 and edge_pct=0 to preserve the baseline audit")
    rules = tuple(
        ThresholdRule(confidence, edge_pct)
        for confidence in confidence_thresholds
        for edge_pct in edge_thresholds_pct
    )
    sweep_ledgers = {
        horizon: {rule.key: PositionLedger() for rule in rules}
        for horizon in HORIZONS
    }
    overall_sweep_ledgers = {rule.key: PositionLedger() for rule in rules}
    sweep_price_breakdowns = {
        horizon: {rule.key: AnchorPricePnlBreakdown() for rule in rules}
        for horizon in HORIZONS
    }
    overall_sweep_price_breakdowns = {
        rule.key: AnchorPricePnlBreakdown() for rule in rules
    }
    article_pnl_parts: list[np.ndarray] = []
    dataset = ClickHouseEvaluationDataset(loader_config, start=start, end_exclusive=end_exclusive)
    articles = labels = 0; started = time.perf_counter()
    try:
        for batch_index, evaluation in enumerate(dataset.iter_batches(), start=1):
            cpu_batch = evaluation.model_batch
            batch_article_pnl = np.zeros((cpu_batch.sample_count, len(rules)), dtype=np.float64)
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
                ledgers[horizon].add(side, pnl, touched, anchors)
                overall.add(side, pnl, touched, anchors)
                price_breakdowns[horizon].add(side, pnl, anchors); overall_price_breakdown.add(side, pnl, anchors)
                labels += int(valid.sum())
                plan_confidence = plan["plan_confidence"][valid].astype(np.float64)
                edge_pct = plan["edge_pct"][valid].astype(np.float64)
                valid_indices = np.flatnonzero(valid)
                for rule_index, rule in enumerate(rules):
                    filtered_side = threshold_side(side, plan_confidence, edge_pct, rule)
                    selected = filtered_side != 0
                    filtered_pnl = np.where(selected, pnl, 0.0)
                    filtered_touched = selected & touched
                    sweep_ledgers[horizon][rule.key].add(
                        filtered_side, filtered_pnl, filtered_touched, anchors
                    )
                    overall_sweep_ledgers[rule.key].add(
                        filtered_side, filtered_pnl, filtered_touched, anchors
                    )
                    sweep_price_breakdowns[horizon][rule.key].add(
                        filtered_side, filtered_pnl, anchors
                    )
                    overall_sweep_price_breakdowns[rule.key].add(
                        filtered_side, filtered_pnl, anchors
                    )
                    batch_article_pnl[valid_indices, rule_index] += filtered_pnl
                if prediction_file is not None:
                    for local, row_index in enumerate(np.flatnonzero(valid)):
                        prediction_file.write(json.dumps({
                            "canonical_news_id": cpu_batch.identity["canonical_news_id"][row_index],
                            "ticker": cpu_batch.identity["ticker"][row_index],
                            "published_at_utc": cpu_batch.identity["published_at_utc"][row_index],
                            "horizon": horizon, "position": int(side[local]), "target_pct": float(target_pct[local]),
                            "predicted_high_class": int(plan["high_class"][row_index]), "predicted_low_class": int(plan["low_class"][row_index]),
                            "predicted_ending_class": int(plan["ending_class"][row_index]), "target_touched": bool(touched[local]),
                            "upside_pct": float(plan["upside_pct"][row_index]),
                            "downside_pct": float(plan["downside_pct"][row_index]),
                            "span_pct": float(plan["span_pct"][row_index]),
                            "edge_pct": float(plan["edge_pct"][row_index]),
                            "plan_confidence": float(plan["plan_confidence"][row_index]),
                            "high_confidence": float(plan["high_confidence"][row_index]),
                            "low_confidence": float(plan["low_confidence"][row_index]),
                            "ending_confidence": float(plan["ending_confidence"][row_index]),
                            "exit": "target" if touched[local] else "ending", "anchor_price": float(anchors[local]),
                            "actual_ending_return": float(actual[local, 0]), "actual_high_return": float(actual[local, 1]),
                            "actual_low_return": float(actual[local, 2]), "gross_one_share_pnl": float(pnl[local]),
                        }, separators=(",", ":"), allow_nan=False) + "\n")
            articles += cpu_batch.sample_count
            article_pnl_parts.append(batch_article_pnl)
            if batch_index == 1 or batch_index % 10 == 0:
                print(f"EVALUATE batches={batch_index:,} articles={articles:,}/{audit['rows']:,} labels={labels:,} rate={articles / max(time.perf_counter()-started, 1e-9):,.0f} articles/s", flush=True)
    finally:
        dataset.stop()
        if prediction_file is not None: prediction_file.close()
    horizons = {
        horizon: {**ledger.summary(), "anchor_price_pnl": price_breakdowns[horizon].summary()}
        for horizon, ledger in ledgers.items()
    }
    article_pnl = (
        np.concatenate(article_pnl_parts, axis=0)
        if article_pnl_parts
        else np.empty((0, len(rules)), dtype=np.float64)
    )
    sweep_rows: list[dict[str, Any]] = []
    for horizon in (*HORIZONS, "ALL_INDEPENDENT_HORIZONS"):
        horizon_ledgers = (
            overall_sweep_ledgers
            if horizon == "ALL_INDEPENDENT_HORIZONS"
            else sweep_ledgers[horizon]
        )
        for rule_index, rule in enumerate(rules):
            row = {
                "horizon": horizon,
                "confidence_threshold": rule.confidence,
                "edge_threshold_pct": rule.edge_pct,
                **horizon_ledgers[rule.key].summary(),
            }
            if horizon == "ALL_INDEPENDENT_HORIZONS" and article_pnl.shape[0]:
                values = article_pnl[:, rule_index]
                mean = float(values.mean())
                se = float(values.std(ddof=1) / math.sqrt(values.size)) if values.size > 1 else 0.0
                row.update({
                    "article_mean_one_share_pnl": mean,
                    "article_mean_one_share_pnl_95ci": [
                        mean - 1.96 * se,
                        mean + 1.96 * se,
                    ],
                })
            sweep_rows.append(row)
    baseline_key = (0.0, 0.0)
    baseline_summary = overall_sweep_ledgers[baseline_key].summary()
    direct_summary = overall.summary()
    for key in ("labels", "active", "long", "short", "flat", "target_touches", "ending_fallbacks"):
        if baseline_summary[key] != direct_summary[key]:
            raise RuntimeError(f"Threshold baseline drift for {key}: {baseline_summary[key]} != {direct_summary[key]}")
    if not math.isclose(
        baseline_summary["one_share_pnl"],
        direct_summary["one_share_pnl"],
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise RuntimeError("Threshold baseline P&L does not reproduce the unchanged evaluation")
    summary = {
        "model_version": MODEL_VERSION, "checkpoint": str(checkpoint), "validation_range": [start, end_exclusive],
        "articles": articles, "labels": labels, "elapsed_seconds": time.perf_counter() - started,
        "position_contract": "dominant conservative predicted high/low excursion; ties and sub-threshold spans abstain",
        "exit_contract": "exit at predicted conservative target when actual horizon high/low touches it; otherwise exit at actual ending price",
        "limitations": "independent one-share horizon ledgers before costs; no ordering, stop, risk management, capital, or overlap reconciliation",
        "overall_across_independent_horizons": {
            **overall.summary(), "anchor_price_pnl": overall_price_breakdown.summary()
        }, "horizons": horizons,
        "threshold_sweep": {
            "selection_contract": (
                "retain an existing non-flat plan only when min(high_confidence, low_confidence) "
                "meets the confidence threshold and abs(conservative_upside_pct - "
                "conservative_downside_pct) meets the directional-edge threshold"
            ),
            "confidence_thresholds": list(confidence_thresholds),
            "edge_thresholds_pct": list(edge_thresholds_pct),
            "baseline_reproduced": True,
            "statistical_note": (
                "active-return intervals treat positions as independent; overall article P&L "
                "intervals cluster all horizons from one article-ticker row"
            ),
            "selection_warning": (
                "This is exploratory tuning on the held-out 2026 comparison set. A chosen rule "
                "requires confirmation on a later untouched period before production use."
            ),
            "rows": sweep_rows,
        },
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
    sweep_fields = [
        "horizon", "confidence_threshold", "edge_threshold_pct", "labels", "active",
        "coverage", "long", "long_one_share_pnl", "short", "short_one_share_pnl",
        "flat", "one_share_pnl", "one_share_mean_pnl_per_active", "profitable",
        "losing", "breakeven", "win_rate", "gross_profit", "gross_loss",
        "profit_factor", "target_touches", "ending_fallbacks", "target_touch_rate",
        "mean_active_return", "median_active_return", "mean_active_return_95ci_naive",
        "article_mean_one_share_pnl", "article_mean_one_share_pnl_95ci",
    ]
    with (destination / "evaluation_threshold_sweep.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sweep_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sweep_rows)
    threshold_price_fields = [
        "horizon", "confidence_threshold", "edge_threshold_pct", "price_bucket",
        "price_label", "minimum_inclusive", "maximum_exclusive", "labels",
        "active_positions", "abstained", "one_share_pnl",
        "mean_one_share_pnl_per_active", "long_positions", "long_one_share_pnl",
        "long_mean_one_share_pnl", "long_win_rate", "short_positions",
        "short_one_share_pnl", "short_mean_one_share_pnl", "short_win_rate",
    ]
    with (destination / "evaluation_threshold_sweep_anchor_price.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=threshold_price_fields)
        writer.writeheader()
        for horizon in (*HORIZONS, "ALL_INDEPENDENT_HORIZONS"):
            breakdowns = (
                overall_sweep_price_breakdowns
                if horizon == "ALL_INDEPENDENT_HORIZONS"
                else sweep_price_breakdowns[horizon]
            )
            for rule in rules:
                bucket_rows = breakdowns[rule.key].summary()["buckets"]
                for bucket_key, bucket in bucket_rows.items():
                    writer.writerow({
                        "horizon": horizon,
                        "confidence_threshold": rule.confidence,
                        "edge_threshold_pct": rule.edge_pct,
                        "price_bucket": bucket_key,
                        "price_label": bucket["label"],
                        "minimum_inclusive": bucket["minimum_inclusive"],
                        "maximum_exclusive": bucket["maximum_exclusive"],
                        "labels": bucket["labels"],
                        "active_positions": bucket["active_positions"],
                        "abstained": bucket["abstained"],
                        "one_share_pnl": bucket["one_share_pnl"],
                        "mean_one_share_pnl_per_active": bucket["mean_one_share_pnl_per_active"],
                        "long_positions": bucket["long"]["positions"],
                        "long_one_share_pnl": bucket["long"]["one_share_pnl"],
                        "long_mean_one_share_pnl": bucket["long"]["mean_one_share_pnl"],
                        "long_win_rate": bucket["long"]["win_rate"],
                        "short_positions": bucket["short"]["positions"],
                        "short_one_share_pnl": bucket["short"]["one_share_pnl"],
                        "short_mean_one_share_pnl": bucket["short"]["mean_one_share_pnl"],
                        "short_win_rate": bucket["short"]["win_rate"],
                    })
    print(f"COMPLETED articles={articles:,} labels={labels:,} summary={destination / 'evaluation_summary.json'}", flush=True)
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    confidence_thresholds = parse_threshold_values(
        args.confidence_thresholds, name="confidence thresholds", maximum=1.0
    )
    edge_thresholds_pct = parse_threshold_values(
        args.edge_thresholds_pct, name="edge thresholds"
    )
    evaluate_checkpoint(
        Path(args.checkpoint),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        start=args.start,
        end_exclusive=args.end_exclusive,
        export_predictions=args.export_predictions,
        amp=args.amp,
        confidence_thresholds=confidence_thresholds,
        edge_thresholds_pct=edge_thresholds_pct,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
