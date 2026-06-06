from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from news_intelligence.config import IntelligenceConfig
from news_intelligence.historical import article_key, load_articles_jsonl, write_jsonl
from news_intelligence.recommended_pipeline import RecommendedNewsPipeline


DEFAULT_SENTIMENT_MODELS = [
    "distilroberta-financial-news",
    "finsentiment-distilbert",
    "prosusai-finbert",
    "finbert-tone",
    "finscience-distilroberta",
]

CATEGORICAL_FIELDS = [
    "sentiment_label",
    "event_type",
    "event_subtype",
    "time_horizon",
    "content_completeness",
    "evidence_basis",
]
NUMERIC_FIELDS = [
    "sentiment_score",
    "sentiment_confidence",
    "materiality_score",
    "urgency_score",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the recommended news-intelligence pipeline against Codex supervision.")
    parser.add_argument("--supervision-run", default="", help="Path to a codex_suppervision run directory. Defaults to latest.")
    parser.add_argument("--supervision-root", default=PACKAGE_ROOT / "codex_suppervision")
    parser.add_argument("--output-root", default=PACKAGE_ROOT / "pipeline_evaluation_runs")
    parser.add_argument("--models", nargs="*", default=DEFAULT_SENTIMENT_MODELS)
    parser.add_argument("--article-limit", type=int, default=0, help="Optional limit from the supervision set. 0 means all.")
    parser.add_argument("--enable-llm", action="store_true", help="Call the configured OpenAI-compatible local LLM endpoint.")
    parser.add_argument("--disable-models", action="store_true", help="Disable transformer sentiment models and use deterministic fallback.")
    parser.add_argument("--llm-max-tokens", type=int, default=0, help="Override NEWS_INTELLIGENCE_LLM_MAX_TOKENS for this run.")
    parser.add_argument("--llm-reasoning-effort", default="", help="Override NEWS_INTELLIGENCE_LLM_REASONING_EFFORT for this run.")
    parser.add_argument("--llm-response-format", default="", help="Override NEWS_INTELLIGENCE_LLM_RESPONSE_FORMAT for this run.")
    parser.add_argument("--llm-merge-mode", default="", help="Override NEWS_INTELLIGENCE_LLM_MERGE_MODE: summary_only or override.")
    parser.add_argument("--llm-min-materiality", type=float, default=None, help="Override NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY.")
    parser.add_argument("--llm-min-text-chars", type=int, default=None, help="Override NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_config_overrides(args)
    supervision_run = Path(args.supervision_run) if args.supervision_run else latest_run(Path(args.supervision_root))
    run_dir = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    articles = load_articles_jsonl(supervision_run / "selected_articles.jsonl")
    labels = load_supervision(supervision_run / "codex_supervision.jsonl")
    if args.article_limit > 0:
        articles = articles[: args.article_limit]
    article_count = len(articles)

    config = IntelligenceConfig.from_env()
    all_results: list[dict[str, Any]] = []
    model_summaries = []
    for model_key in args.models:
        started = time.perf_counter()
        pipeline = RecommendedNewsPipeline(
            config=config,
            sentiment_model_key=model_key,
            enable_models=not args.disable_models,
            enable_llm=args.enable_llm,
        )
        model_rows = []
        for index, article in enumerate(articles, start=1):
            key = article_key(article)
            truth = labels.get(key)
            if truth is None:
                continue
            run = pipeline.classify(article.to_classification_article())
            prediction = run.to_dict()
            row = {
                "model": model_key,
                "row_index": index,
                "article_key": key,
                "article_id": article.article_id,
                "published_at": article.published_at,
                "title": article.title,
                "tickers": article.tickers,
                "truth": truth,
                "prediction": prediction,
            }
            model_rows.append(row)
            all_results.append(row)
        elapsed = time.perf_counter() - started
        summary = summarize_model(model_key, model_rows, elapsed)
        model_summaries.append(summary)
        write_jsonl(run_dir / "pipeline_results.jsonl", all_results)
        (run_dir / "stage_metrics.json").write_text(json.dumps(model_summaries, indent=2, ensure_ascii=False), encoding="utf-8")
        cleanup_runtime()

    final_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "supervision_run": str(supervision_run),
        "output_run": str(run_dir),
        "article_count": article_count,
        "enable_llm": bool(args.enable_llm),
        "models": model_summaries,
        "recommendations": build_recommendations(model_summaries),
    }
    (run_dir / "summary.json").write_text(json.dumps(final_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "experiment_analysis.md").write_text(render_analysis(final_summary), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "summary": final_summary}, indent=2, ensure_ascii=False))
    return 0


def apply_config_overrides(args: argparse.Namespace) -> None:
    if args.llm_max_tokens:
        os.environ["NEWS_INTELLIGENCE_LLM_MAX_TOKENS"] = str(args.llm_max_tokens)
    if args.llm_reasoning_effort:
        os.environ["NEWS_INTELLIGENCE_LLM_REASONING_EFFORT"] = args.llm_reasoning_effort
    if args.llm_response_format:
        os.environ["NEWS_INTELLIGENCE_LLM_RESPONSE_FORMAT"] = args.llm_response_format
    if args.llm_merge_mode:
        os.environ["NEWS_INTELLIGENCE_LLM_MERGE_MODE"] = args.llm_merge_mode
    if args.llm_min_materiality is not None:
        os.environ["NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY"] = str(args.llm_min_materiality)
    if args.llm_min_text_chars is not None:
        os.environ["NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS"] = str(args.llm_min_text_chars)


def latest_run(root: Path) -> Path:
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No supervision runs found in {root}")
    return sorted(candidates)[-1]


def load_supervision(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows[str(item["article_key"])] = item["output_contract"]
    return rows


def summarize_model(model_key: str, rows: list[dict[str, Any]], elapsed_seconds: float) -> dict[str, Any]:
    predictions = [row["prediction"]["response"] for row in rows]
    truths = [row["truth"] for row in rows]
    stage_timings = collect_stage_timings(rows)
    categorical = {
        field: categorical_accuracy(predictions, truths, field)
        for field in CATEGORICAL_FIELDS
    }
    numeric = {
        field: numeric_error(predictions, truths, field)
        for field in NUMERIC_FIELDS
    }
    ticker_scores = ticker_metrics(predictions, truths)
    label_score = labels_jaccard(predictions, truths)
    status_counter = Counter()
    sentiment_source_counter = Counter()
    for row in rows:
        response = row["prediction"]["response"]
        status_counter[response.get("status", "unknown")] += 1
        stack = response.get("model_stack", [])
        sentiment_source_counter[stack[0] if stack else "unknown"] += 1
    return {
        "model": model_key,
        "uses_requested_model": bool(sentiment_source_counter.get(model_key)),
        "article_count": len(rows),
        "elapsed_seconds": elapsed_seconds,
        "throughput_articles_per_second": len(rows) / elapsed_seconds if elapsed_seconds else 0.0,
        "categorical_accuracy": categorical,
        "numeric_error": numeric,
        "affected_ticker_metrics": ticker_scores,
        "labels_mean_jaccard": label_score,
        "stage_timings": stage_timings,
        "status_distribution": dict(status_counter),
        "sentiment_source_distribution": dict(sentiment_source_counter),
    }


def collect_stage_timings(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    timings: dict[str, list[float]] = defaultdict(list)
    status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for stage in row["prediction"].get("stages", []):
            name = stage["name"]
            timings[name].append(float(stage.get("seconds") or 0.0))
            status[name][str(stage.get("status") or "unknown")] += 1
    return {
        name: {
            "count": len(values),
            "mean_seconds": statistics.mean(values) if values else 0.0,
            "median_seconds": statistics.median(values) if values else 0.0,
            "p95_seconds": percentile(values, 0.95),
            "status_distribution": dict(status[name]),
        }
        for name, values in sorted(timings.items())
    }


def categorical_accuracy(predictions: list[dict[str, Any]], truths: list[dict[str, Any]], field: str) -> dict[str, Any]:
    total = len(predictions)
    matches = 0
    confusion: Counter[str] = Counter()
    for prediction, truth in zip(predictions, truths):
        predicted_value = str(prediction.get(field, ""))
        truth_value = str(truth.get(field, ""))
        if predicted_value == truth_value:
            matches += 1
        confusion[f"{truth_value} -> {predicted_value}"] += 1
    return {
        "accuracy": matches / total if total else 0.0,
        "matches": matches,
        "total": total,
        "top_confusions": dict(confusion.most_common(12)),
    }


def numeric_error(predictions: list[dict[str, Any]], truths: list[dict[str, Any]], field: str) -> dict[str, Any]:
    deltas = []
    for prediction, truth in zip(predictions, truths):
        deltas.append(abs(float(prediction.get(field) or 0.0) - float(truth.get(field) or 0.0)))
    return {
        "mae": statistics.mean(deltas) if deltas else 0.0,
        "median_abs_error": statistics.median(deltas) if deltas else 0.0,
        "p95_abs_error": percentile(deltas, 0.95),
    }


def ticker_metrics(predictions: list[dict[str, Any]], truths: list[dict[str, Any]]) -> dict[str, float]:
    tp = fp = fn = 0
    for prediction, truth in zip(predictions, truths):
        predicted = {str(item.get("ticker", "")).upper() for item in prediction.get("affected_tickers", []) if item.get("ticker")}
        expected = {str(item.get("ticker", "")).upper() for item in truth.get("affected_tickers", []) if item.get("ticker")}
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
    }


def labels_jaccard(predictions: list[dict[str, Any]], truths: list[dict[str, Any]]) -> float:
    scores = []
    for prediction, truth in zip(predictions, truths):
        predicted = set(str(item) for item in prediction.get("labels", []))
        expected = set(str(item) for item in truth.get("labels", []))
        if not predicted and not expected:
            scores.append(1.0)
        else:
            scores.append(len(predicted & expected) / len(predicted | expected))
    return statistics.mean(scores) if scores else 0.0


def build_recommendations(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [summary for summary in summaries if summary["article_count"]]
    model_completed = [summary for summary in completed if summary.get("uses_requested_model")]
    if not completed:
        return {}
    comparable = model_completed or completed
    best_sentiment = max(comparable, key=lambda item: item["categorical_accuracy"]["sentiment_label"]["accuracy"])
    fastest = max(comparable, key=lambda item: item["throughput_articles_per_second"])
    best_overall = max(
        comparable,
        key=lambda item: (
            item["categorical_accuracy"]["sentiment_label"]["accuracy"]
            + item["categorical_accuracy"]["event_type"]["accuracy"]
            + item["affected_ticker_metrics"]["f1"]
            + item["labels_mean_jaccard"]
        )
        / 4,
    )
    return {
        "best_sentiment_model": best_sentiment["model"],
        "fastest_model": fastest["model"],
        "best_balanced_model": best_overall["model"],
        "excluded_fallback_only_models": [summary["model"] for summary in completed if not summary.get("uses_requested_model")],
    }


def render_analysis(summary: dict[str, Any]) -> str:
    best = summary.get("recommendations", {})
    lines = [
        "# Recommended News Pipeline Evaluation",
        "",
        "This experiment compares the staged recommended pipeline against the local Codex supervision set. The supervision set is deterministic silver labeling, not human ground truth.",
        "",
        f"Supervision run: `{summary['supervision_run']}`",
        f"Articles evaluated: `{summary['article_count']}`",
        f"LLM enabled: `{summary['enable_llm']}`",
        "",
        "## Executive Read",
        "",
        f"- Best measured sentiment agreement: `{best.get('best_sentiment_model', '')}`.",
        f"- Fastest measured sentiment stage: `{best.get('fastest_model', '')}`.",
        f"- Best balanced hot-path choice in this run: `{best.get('best_balanced_model', '')}`.",
        "- Event, ticker, evidence, and content-completeness scores are high because those stages are deterministic and intentionally close to the silver-label supervisor rules. They verify consistency and timing, not human-level semantic accuracy.",
    ]
    if summary.get("enable_llm"):
        lines.append("- The LLM tier was benchmarked through the configured local OpenAI-compatible endpoint. Inspect the `llm` stage status distribution and p95 latency before enabling it in a synchronous path.")
    else:
        lines.append("- The LLM tier was not benchmarked here. In production it should stay optional and threshold-gated until it is measured under a running local endpoint.")
    lines.extend(
        [
            "",
            "## Model Summary",
            "",
            "| Model | Articles | Sentiment Acc | Event Acc | Ticker F1 | Label Jaccard | Throughput / s | Sentiment Median s | Total Elapsed s |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model in summary["models"]:
        sentiment_timing = model["stage_timings"].get("fast_sentiment", {})
        lines.append(
            "| {model} | {count} | {sent:.3f} | {event:.3f} | {ticker:.3f} | {labels:.3f} | {tps:.3f} | {median:.4f} | {elapsed:.2f} |".format(
                model=model["model"] if model.get("uses_requested_model") else f"{model['model']} (fallback)",
                count=model["article_count"],
                sent=model["categorical_accuracy"]["sentiment_label"]["accuracy"],
                event=model["categorical_accuracy"]["event_type"]["accuracy"],
                ticker=model["affected_ticker_metrics"]["f1"],
                labels=model["labels_mean_jaccard"],
                tps=model["throughput_articles_per_second"],
                median=float(sentiment_timing.get("median_seconds") or 0.0),
                elapsed=model["elapsed_seconds"],
            )
        )
    lines.extend(["", "## Stage Findings", ""])
    for model in summary["models"]:
        lines.append(f"### {model['model']}")
        for stage_name, timing in model["stage_timings"].items():
            lines.append(
                "- `{stage}` median `{median:.4f}s`, p95 `{p95:.4f}s`, statuses `{statuses}`.".format(
                    stage=stage_name,
                    median=float(timing.get("median_seconds") or 0.0),
                    p95=float(timing.get("p95_seconds") or 0.0),
                    statuses=json.dumps(timing.get("status_distribution", {}), ensure_ascii=False),
                )
            )
        lines.append(
            "- Main confusions: sentiment `{sent}`, event `{event}`.".format(
                sent=json.dumps(model["categorical_accuracy"]["sentiment_label"]["top_confusions"], ensure_ascii=False),
                event=json.dumps(model["categorical_accuracy"]["event_type"]["top_confusions"], ensure_ascii=False),
            )
        )
        lines.append("")
    lines.extend(["## Field-Level Interpretation", ""])
    for model in summary["models"]:
        numeric = model["numeric_error"]
        lines.append(
            "- `{model}`: sentiment accuracy `{sent:.3f}`, sentiment score MAE `{score_mae:.3f}`, confidence MAE `{conf_mae:.3f}`, event accuracy `{event:.3f}`.".format(
                model=model["model"],
                sent=model["categorical_accuracy"]["sentiment_label"]["accuracy"],
                score_mae=numeric["sentiment_score"]["mae"],
                conf_mae=numeric["sentiment_confidence"]["mae"],
                event=model["categorical_accuracy"]["event_type"]["accuracy"],
            )
        )
    lines.extend(
        [
            "",
            "## Operational Risk Checks",
            "",
            "- The evaluator records `sentiment_source_distribution` and excludes fallback-only runs from recommendations. This catches model load failures that would otherwise look like fast successful inference.",
            "- The pipeline records per-stage timing for every article, so news-gateway can later alert on slow sentiment inference, LLM endpoint failures, or unexpected postprocess failures.",
            "- Title-only and PDF-enriched rows are kept in the supervision set. That makes the benchmark cover thin-content behavior instead of only clean full-body articles.",
            "- The supervision labels preserve macro, crypto, politics, and other no-direct-ticker stories; they are not treated as junk because they may matter for cross-asset context or future model training.",
            "",
            "## Production Direction",
            "",
        ]
    )
    lines.extend(["## Recommendation", ""])
    recs = summary.get("recommendations", {})
    if recs:
        lines.append(f"- Best sentiment agreement: `{recs.get('best_sentiment_model')}`.")
        lines.append(f"- Fastest candidate: `{recs.get('fastest_model')}`.")
        lines.append(f"- Best balanced candidate: `{recs.get('best_balanced_model')}`.")
        if recs.get("excluded_fallback_only_models"):
            lines.append(f"- Fallback-only runs excluded from model ranking: `{', '.join(recs['excluded_fallback_only_models'])}`.")
    lines.append("- For the hot path, use deterministic preprocessing/event/ticker/evidence stages plus the selected fast sentiment model. Persist the full contract and model/taxonomy versions with every row.")
    lines.append("- For uncommon behavior, route high-materiality or low-confidence articles to the optional LLM tier asynchronously; do not block news persistence on that result.")
    if summary.get("enable_llm"):
        lines.append("- Keep LLM-enriched summaries asynchronous or run them in `summary_only` merge mode unless a future benchmark proves that overriding structured labels improves accuracy.")
    else:
        lines.append("- Keep the LLM stage out of the hot path unless a local endpoint is already warm and measured; this run records it as skipped unless `--enable-llm` is used.")
    lines.append("- Before using this as a production benchmark, review a small random sample manually because silver labels can encode rule bias.")
    return "\n".join(lines) + "\n"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


def cleanup_runtime() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
