from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .comparison import (
    _human_by_ticker,
    _prediction_by_ticker,
    evaluate_predictions,
    load_collection,
)
from .run_deterministic_news_v6 import DEFAULT_FROZEN, DEFAULT_ROOT, _frozen_ids
from .storage import assert_runtime_root, read_json, write_json_atomic


METRICS = (
    ("Extraction F1", ("extraction", "f1")),
    ("Ticker scope F1", ("ticker_scope", "f1")),
    ("Role macro F1", ("content_role", "macro_f1")),
    ("Origin macro F1", ("source_origin", "macro_f1")),
    ("Direction macro F1", ("semantic_direction", "macro_f1")),
    ("Concept-family F1", ("event_concepts", "f1")),
    ("Forecast eligibility F1", ("eligibility", "forecast_trigger_eligible", "f1")),
    ("History eligibility F1", ("eligibility", "issuer_history_context_eligible", "f1")),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit deterministic News V7 against V5 and V6.")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--frozen-sample", type=Path, default=DEFAULT_FROZEN)
    args = parser.parse_args()
    output = args.runtime_root / "deterministic_v7" / "audit"
    assert_runtime_root(output)
    items = load_collection(args.runtime_root)
    frozen_ids = _frozen_ids(args.frozen_sample)
    development = tuple(item for item in items if item.sample_id not in frozen_ids)
    frozen = tuple(item for item in items if item.sample_id in frozen_ids)
    v5_dir = args.runtime_root / "v5_v6_calibration" / "v5_predictions"
    reports = {
        "development_v5": evaluate_predictions(development, prediction_dir=v5_dir, canonical_concepts=True),
        "development_v6": read_json(args.runtime_root / "deterministic_v6" / "development_metrics.json"),
        "development_v7": read_json(args.runtime_root / "deterministic_v7" / "development_metrics.json"),
        "frozen_v6": read_json(args.runtime_root / "deterministic_v6" / "frozen-acceptance_metrics.json"),
        "frozen_v7": read_json(args.runtime_root / "deterministic_v7" / "frozen-acceptance_metrics.json"),
    }
    conditional = {
        "development_v6": _conditional(development, args.runtime_root / "deterministic_v6" / "development_predictions"),
        "development_v7": _conditional(development, args.runtime_root / "deterministic_v7" / "development_predictions"),
        "frozen_v6": _conditional(frozen, args.runtime_root / "deterministic_v6" / "frozen-acceptance_predictions"),
        "frozen_v7": _conditional(frozen, args.runtime_root / "deterministic_v7" / "frozen-acceptance_predictions"),
    }
    attribution = _attribution(development, args.runtime_root / "deterministic_v7" / "development_predictions")
    payload = {"reports": reports, "conditional": conditional, "development_error_attribution": attribution}
    write_json_atomic(output / "final_evaluation.json", payload)
    (output / "FINAL_EVALUATION.md").write_text(_markdown(payload), encoding="utf-8")
    print(output / "FINAL_EVALUATION.md")
    return 0


def _conditional(items: Iterable, prediction_dir: Path) -> dict[str, Any]:
    confusion = Counter()
    matched = 0
    for item in items:
        truth = _human_by_ticker(item.truth)
        predicted = _prediction_by_ticker(read_json(prediction_dir / f"{item.sample_id}.json"))
        for ticker in truth.keys() & predicted.keys():
            matched += 1
            confusion[(truth[ticker]["semantic_direction"], predicted[ticker]["semantic_direction"])] += 1
    labels = sorted({value for pair in confusion for value in pair})
    per_class = {}
    for label in labels:
        tp = confusion[(label, label)]
        fp = sum(n for (actual, predicted), n in confusion.items() if predicted == label and actual != label)
        fn = sum(n for (actual, predicted), n in confusion.items() if actual == label and predicted != label)
        denom = 2 * tp + fp + fn
        per_class[label] = 2 * tp / denom if denom else 0.0
    usable = [score for label, score in per_class.items() if label != "__missing__"]
    return {
        "matched_issuer_units": matched,
        "direction_accuracy": sum(n for (a, p), n in confusion.items() if a == p) / matched if matched else 0.0,
        "direction_macro_f1": sum(usable) / len(usable) if usable else 0.0,
        "per_class_f1": per_class,
    }


def _attribution(items: Iterable, prediction_dir: Path) -> dict[str, int]:
    counts = Counter()
    for item in items:
        truth = _human_by_ticker(item.truth)
        prediction = read_json(prediction_dir / f"{item.sample_id}.json")
        predicted = _prediction_by_ticker(prediction)
        if item.truth["content_role"] != prediction.get("content_role"):
            counts["article_role"] += 1
        if item.truth["source_origin"] != prediction.get("source_origin"):
            counts["source_origin"] += 1
        counts["missing_issuer_unit"] += len(truth.keys() - predicted.keys())
        counts["extra_issuer_unit"] += len(predicted.keys() - truth.keys())
        for ticker in truth.keys() & predicted.keys():
            if truth[ticker]["semantic_direction"] != predicted[ticker]["semantic_direction"]:
                counts["direction_on_matched_unit"] += 1
            if set(truth[ticker]["event_concepts"]) != set(predicted[ticker]["event_concepts"]):
                counts["concepts_on_matched_unit"] += 1
            for field in ("forecast_trigger_eligible", "reaction_evaluation_eligible", "issuer_history_context_eligible"):
                if truth[ticker][field] != predicted[ticker][field]:
                    counts[field] += 1
    return dict(sorted(counts.items()))


def _value(report: Mapping[str, Any], path: tuple[str, ...]) -> float:
    value: Any = report
    for key in path:
        value = value[key]
    return float(value)


def _markdown(payload: Mapping[str, Any]) -> str:
    reports = payload["reports"]
    lines = [
        "# Deterministic News V7 final evaluation",
        "",
        "V7 is the immutable rule-only successor derived from V6. The 900 development articles informed generalized structural rules; the frozen 100 were evaluated only after the implementation and configuration were locked.",
        "",
        "## Complete comparison",
        "",
        "| Metric | Dev V5 | Dev V6 | Dev V7 | Frozen V6 | Frozen V7 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, path in METRICS:
        lines.append(
            f"| {label} | {_value(reports['development_v5'], path):.3f} | "
            f"{_value(reports['development_v6'], path):.3f} | {_value(reports['development_v7'], path):.3f} | "
            f"{_value(reports['frozen_v6'], path):.3f} | {_value(reports['frozen_v7'], path):.3f} |"
        )
    lines += ["", "## Direction after issuer scope is correct", "", "| Population | Matched units | Accuracy | Macro F1 |", "|---|---:|---:|---:|"]
    for key, label in (("development_v6", "Dev V6"), ("development_v7", "Dev V7"), ("frozen_v6", "Frozen V6"), ("frozen_v7", "Frozen V7")):
        row = payload["conditional"][key]
        lines.append(f"| {label} | {row['matched_issuer_units']:,} | {row['direction_accuracy']:.3f} | {row['direction_macro_f1']:.3f} |")
    lines += ["", "## Development error attribution", ""]
    for name, count in payload["development_error_attribution"].items():
        lines.append(f"- {name.replace('_', ' ').title()}: {count:,}")
    lines += [
        "",
        "## Interpretation",
        "",
        "- Article-role and source-origin errors are independently measurable structural failures; they are not direction failures.",
        "- Missing issuer units create an automatic missing direction. Conditional direction reports semantic quality only after issuer alignment succeeds.",
        "- Some large roundup annotations are demonstrably non-exhaustive: provider-body passages contain legitimate material events absent from the human issuer-unit list. Those apparent extras were retained as ground-truth limitations rather than converted into article-specific exclusion rules.",
        "- No price reaction, Sol output, statistical classifier, sample identifier, or exact headline exception is used by V7.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
