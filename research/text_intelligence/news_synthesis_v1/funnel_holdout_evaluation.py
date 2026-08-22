from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from research.mlops.clickhouse import ClickHouseHttpClient

from .engine import ENGINE_VERSION, NewsSynthesisEngine
from .funnel import FUNNEL_VERSION, NewsSynthesisFunnel
from .funnel_holdout import HOLDOUT_VERSION
from .funnel_holdout_review import REVIEW_VERSION
from .provider_context import ROUTER_VERSION
from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path
from .storage import load_identity_index


EVALUATION_VERSION = "news_synthesis_funnel_fresh_holdout_evaluation_v2"


def _source(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"], "source_timestamp": row["published_at_utc"],
        "provider": row["provider"], "title": row["title"], "text": row["rendered_text"],
        "tickers": row["tickers"], "channels": row["channels"],
        "provider_tags": row["provider_tags"], "content_quality_flags": row["content_quality_flags"],
        "quality_flags": row["renderer_quality_flags"],
        "render_status": "title_only" if int(row.get("source_count") or 0) == 0 else "rendered",
        "rendered_text_hash": row["rendered_text_hash"], "source_revision_key": row["source_revision_key"],
    }


def _prediction_row(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": result["source_id"], "source_text_sha256": result["source_text_sha256"],
        "article_label": result["final"]["forecast_eligibility"],
        "final_lane": result["final"]["lane"], "analysis_depth": result["final"]["analysis_depth"],
        "context_class": result["final"]["context_class"],
        "provider_route": (result.get("prefilter") or {}).get("route", "insufficient_information"),
        "ticker_labels": result["ticker_labels"],
    }


def _safe_div(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def run_final_evaluation(
    *,
    root: Path,
    client: ClickHouseHttpClient,
    database: str = "q_live",
    output_name: str = "final_evaluation_v2",
) -> dict[str, Any]:
    holdout_manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    gold_manifest = json.loads((root / "gold_review_v1" / "GOLD_MANIFEST.json").read_text(encoding="utf-8"))
    if holdout_manifest.get("holdout_version") != HOLDOUT_VERSION or holdout_manifest.get("status") != "sealed_unlabeled":
        raise RuntimeError("invalid sealed holdout authority")
    if gold_manifest.get("review_version") != REVIEW_VERSION or gold_manifest.get("status") != "gold_complete":
        raise RuntimeError("gold must be complete before any prediction")
    sample_path = root / "SEALED_SAMPLE.jsonl"
    gold_path = Path(gold_manifest["gold_path"])
    if sha256_path(sample_path) != holdout_manifest["files"][sample_path.name]["sha256"] or sha256_path(gold_path) != gold_manifest["gold_sha256"]:
        raise RuntimeError("holdout or gold hash mismatch")
    sample = list(iter_jsonl(sample_path))
    gold = list(iter_jsonl(gold_path))
    if [str(row["source_id"]) for row in sample] != [str(row["source_id"]) for row in gold]:
        raise RuntimeError("sample/gold identity or order mismatch")

    if not output_name or Path(output_name).name != output_name or output_name in {".", ".."}:
        raise ValueError("output_name must be one safe directory name")
    output_root = root / output_name
    funnel = NewsSynthesisFunnel(NewsSynthesisEngine(load_identity_index(client, database)))
    output_root.mkdir()
    prediction_path = output_root / "PREDICTIONS.jsonl"
    predictions = []
    with prediction_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in sample:
            prediction = _prediction_row(funnel.process(_source(row)))
            predictions.append(prediction)
            handle.write(canonical_json(prediction) + "\n")

    confusion: Counter[tuple[str, str]] = Counter()
    ticker_confusion: Counter[tuple[str, str]] = Counter()
    sentiment_correct = 0
    sentiment_total = 0
    end_to_end_sentiment_correct = 0
    prefilter_gold_eligible = 0
    semantic_rows = 0
    semantic_correct = 0
    error_rows = []
    for truth, prediction in zip(gold, predictions):
        actual = str(truth["article_label"])
        predicted = str(prediction["article_label"])
        confusion[(actual, predicted)] += 1
        if prediction["analysis_depth"] == "fast_context" and actual == "eligible":
            prefilter_gold_eligible += 1
        if prediction["analysis_depth"] == "full_semantic":
            semantic_rows += 1
            semantic_correct += int(actual == predicted)
        if actual != predicted:
            error_rows.append({
                "source_id": truth["source_id"], "actual": actual, "predicted": predicted,
                "analysis_depth": prediction["analysis_depth"], "provider_route": prediction["provider_route"],
            })
        predicted_tickers = {str(row["ticker"]): row for row in prediction["ticker_labels"]}
        for unit in truth["ticker_labels"]:
            ticker = str(unit["ticker"])
            actual_unit = str(unit["forecast_eligibility"])
            predicted_unit_row = predicted_tickers.get(ticker, {})
            predicted_unit = str(predicted_unit_row.get("forecast_eligibility") or "ineligible")
            ticker_confusion[(actual_unit, predicted_unit)] += 1
            if actual_unit == "eligible":
                sentiment_total += 1
                sentiment_match = predicted_unit == "eligible" and str(predicted_unit_row.get("sentiment") or "neutral") == str(unit["sentiment"])
                end_to_end_sentiment_correct += int(sentiment_match)
                if predicted_unit == "eligible":
                    sentiment_correct += int(str(predicted_unit_row.get("sentiment") or "neutral") == str(unit["sentiment"]))

    tp = confusion[("eligible", "eligible")]
    fn = sum(count for (actual, predicted), count in confusion.items() if actual == "eligible" and predicted != "eligible")
    tn = confusion[("ineligible", "ineligible")]
    fp = sum(count for (actual, predicted), count in confusion.items() if actual != "eligible" and predicted == "eligible")
    eligible_recall = _safe_div(tp, tp + fn)
    ineligible_recall = _safe_div(tn, tn + fp)
    accuracy = _safe_div(sum(count for (actual, predicted), count in confusion.items() if actual == predicted), len(gold))
    report = {
        "evaluation_version": EVALUATION_VERSION, "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "versions": {"holdout": HOLDOUT_VERSION, "review": REVIEW_VERSION, "funnel": FUNNEL_VERSION, "router": ROUTER_VERSION, "engine": ENGINE_VERSION},
        "articles": len(gold), "gold_article_labels": dict(sorted(Counter(str(row["article_label"]) for row in gold).items())),
        "article_accuracy": accuracy,
        "article_balanced_accuracy": ((eligible_recall or 0.0) + (ineligible_recall or 0.0)) / 2,
        "eligible_precision": _safe_div(tp, tp + fp), "eligible_recall": eligible_recall,
        "ineligible_recall": ineligible_recall,
        "confusion": {f"actual_{actual}__predicted_{predicted}": count for (actual, predicted), count in sorted(confusion.items())},
        "prefilter": {
            "fast_context_articles": sum(row["analysis_depth"] == "fast_context" for row in predictions),
            "compute_reduction_share": _safe_div(sum(row["analysis_depth"] == "fast_context" for row in predictions), len(predictions)),
            "gold_eligible_false_rejections": prefilter_gold_eligible,
        },
        "semantic_lane": {"articles": semantic_rows, "accuracy": _safe_div(semantic_correct, semantic_rows)},
        "ticker_units": sum(ticker_confusion.values()),
        "ticker_accuracy": _safe_div(sum(count for (actual, predicted), count in ticker_confusion.items() if actual == predicted), sum(ticker_confusion.values())),
        "eligible_ticker_sentiment_accuracy_given_predicted_eligible": _safe_div(sentiment_correct, ticker_confusion[("eligible", "eligible")]),
        "eligible_ticker_end_to_end_sentiment_accuracy": _safe_div(end_to_end_sentiment_correct, sentiment_total),
        "errors": len(error_rows),
        "lineage": {"sample_sha256": sha256_path(sample_path), "gold_sha256": sha256_path(gold_path), "predictions_sha256": sha256_path(prediction_path)},
    }
    report_path = output_root / "REPORT.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors_path = output_root / "ERRORS.jsonl"
    with errors_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in error_rows:
            handle.write(canonical_json(row) + "\n")
    return report
