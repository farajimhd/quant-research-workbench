from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from .run_deterministic_news_v7 import _predict as predict_v7
from .run_deterministic_news_v8 import _predict as predict_v8
from .run_deterministic_news_v9 import _predict as predict_v9
from .storage import assert_runtime_root, write_json_atomic
from .teacher_paths import DEFAULT_TEACHER_ROOT


@dataclass(frozen=True, slots=True)
class TeacherExample:
    sample_id: str
    item: dict[str, Any]
    truth: dict[str, Any]


def compare_teacher(
    teacher_root: Path,
    *,
    predictor: Callable[[Any], dict[str, Any]] = predict_v7,
    authority_name: str = "deterministic_v7",
    workers: int | None = None,
    included_sample_ids: set[str] | None = None,
    output_suffix: str = "",
) -> dict[str, Any]:
    """Compare one deterministic authority with the independent Sol teacher.

    This is an error-discovery report, not an acceptance score. The Sol corpus
    is model-generated and intentionally disjoint from the human gold set.
    """
    assert_runtime_root(teacher_root)
    manifest = json.loads((teacher_root / "sample_manifest.json").read_text(encoding="utf-8"))
    sample_ids = [
        str(row["sample_id"])
        for row in manifest.get("items") or ()
        if included_sample_ids is None or str(row["sample_id"]) in included_sample_ids
    ]
    label_root = teacher_root / "sol_batch" / "labels"
    missing = [sample_id for sample_id in sample_ids if not (label_root / f"{sample_id}.json").exists()]
    valid_ids = [sample_id for sample_id in sample_ids if sample_id not in set(missing)]
    worker_count = workers or min(16, max(1, os.cpu_count() or 1))
    decision_pairs: list[tuple[str, str]] = []
    role_pairs: list[tuple[str, str]] = []
    origin_pairs: list[tuple[str, str]] = []
    direction_pairs: list[tuple[str, str]] = []
    forecast_pairs: list[tuple[bool, bool]] = []
    reaction_pairs: list[tuple[bool, bool]] = []
    history_pairs: list[tuple[bool, bool]] = []
    truth_tickers: set[tuple[str, str]] = set()
    predicted_tickers: set[tuple[str, str]] = set()
    truth_concepts: set[tuple[str, str, str]] = set()
    predicted_concepts: set[tuple[str, str, str]] = set()
    disagreements: list[dict[str, Any]] = []

    parallel_worker = {
        predict_v7: _compare_one_v7,
        predict_v8: _compare_one_v8,
        predict_v9: _compare_one_v9,
    }.get(predictor)
    if parallel_worker is None and worker_count > 1:
        raise ValueError("custom predictors require workers=1")
    if worker_count > 1:
        executor: ProcessPoolExecutor | None = ProcessPoolExecutor(max_workers=worker_count)
        iterator = executor.map(
            parallel_worker,
            ((teacher_root, sample_id) for sample_id in valid_ids),
            chunksize=8,
        )
    else:
        executor = None
        iterator = (
            _compare_one(teacher_root, sample_id, predictor)
            for sample_id in valid_ids
        )
    for index, (example, truth, prediction) in enumerate(iterator, 1):
        decision_pairs.append(
            (str(truth.get("extraction_decision") or ""), str(prediction.get("extraction_decision") or ""))
        )
        role_pairs.append((str(truth.get("content_role") or ""), str(prediction.get("content_role") or "")))
        origin_pairs.append((str(truth.get("source_origin") or ""), str(prediction.get("source_origin") or "")))
        truth_by_ticker = _labels_by_ticker(truth.get("labels") or ())
        predicted_by_ticker = _labels_by_ticker(prediction.get("labels") or ())
        for ticker in truth_by_ticker:
            truth_tickers.add((example.sample_id, ticker))
        for ticker in predicted_by_ticker:
            predicted_tickers.add((example.sample_id, ticker))
        for ticker in sorted(set(truth_by_ticker) & set(predicted_by_ticker)):
            expected = truth_by_ticker[ticker]
            actual = predicted_by_ticker[ticker]
            expected_class = expected.get("classification") or {}
            actual_class = actual.get("classification") or {}
            direction_pairs.append(
                (str(expected_class.get("semantic_direction") or ""), str(actual_class.get("semantic_direction") or ""))
            )
            forecast_pairs.append((bool(expected.get("forecast_trigger_eligible")), bool(actual.get("forecast_trigger_eligible"))))
            reaction_pairs.append((bool(expected.get("reaction_evaluation_eligible")), bool(actual.get("reaction_evaluation_eligible"))))
            history_pairs.append((bool(expected.get("issuer_history_context_eligible")), bool(actual.get("issuer_history_context_eligible"))))
            for concept in expected_class.get("event_concepts") or ():
                truth_concepts.add((example.sample_id, ticker, str(concept)))
            for concept in actual_class.get("event_concepts") or ():
                predicted_concepts.add((example.sample_id, ticker, str(concept)))
        if _article_disagrees(truth, prediction):
            disagreements.append(_disagreement_row(example, truth, prediction))
        if index % 500 == 0 or index == len(valid_ids):
            print(f"TEACHER COMPARE {authority_name} {index:,}/{len(valid_ids):,}", flush=True)
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)

    report = {
        "report_version": "news_sol_teacher_comparison_v1",
        "authority": authority_name,
        "teacher_label_version": "news_sol_teacher_labels_v1",
        "teacher_is_acceptance_ground_truth": False,
        "valid_teacher_articles": len(valid_ids),
        "missing_teacher_articles": missing,
        "decision": _multiclass_report(decision_pairs),
        "content_role": _multiclass_report(role_pairs),
        "source_origin": _multiclass_report(origin_pairs),
        "ticker_scope": _set_report(truth_tickers, predicted_tickers),
        "semantic_direction": _multiclass_report(direction_pairs),
        "event_concepts": _set_report(truth_concepts, predicted_concepts),
        "forecast": _binary_report(forecast_pairs),
        "reaction": _binary_report(reaction_pairs),
        "history": _binary_report(history_pairs),
        "disagreement_count": len(disagreements),
        "disagreement_by_teacher_role": dict(Counter(row["teacher_role"] for row in disagreements)),
        "disagreements": disagreements,
    }
    output = teacher_root / "deterministic_teacher_comparison" / f"{authority_name}{output_suffix}.json"
    write_json_atomic(output, report)
    return report


def load_teacher_examples(teacher_root: Path) -> tuple[tuple[TeacherExample, ...], list[str]]:
    manifest = json.loads((teacher_root / "sample_manifest.json").read_text(encoding="utf-8"))
    label_root = teacher_root / "sol_batch" / "labels"
    examples: list[TeacherExample] = []
    missing: list[str] = []
    for row in manifest.get("items") or ():
        sample_id = str(row["sample_id"])
        label_path = label_root / f"{sample_id}.json"
        if not label_path.exists():
            missing.append(sample_id)
            continue
        examples.append(
            TeacherExample(
                sample_id=sample_id,
                item=json.loads((teacher_root / "items" / f"{sample_id}.json").read_text(encoding="utf-8")),
                truth=json.loads(label_path.read_text(encoding="utf-8")),
            )
        )
    return tuple(examples), missing


def _compare_one_v7(args: tuple[Path, str]) -> tuple[TeacherExample, dict[str, Any], dict[str, Any]]:
    teacher_root, sample_id = args
    return _compare_one(teacher_root, sample_id, predict_v7)


def _compare_one_v8(args: tuple[Path, str]) -> tuple[TeacherExample, dict[str, Any], dict[str, Any]]:
    teacher_root, sample_id = args
    return _compare_one(teacher_root, sample_id, predict_v8)


def _compare_one_v9(args: tuple[Path, str]) -> tuple[TeacherExample, dict[str, Any], dict[str, Any]]:
    teacher_root, sample_id = args
    return _compare_one(teacher_root, sample_id, predict_v9)


def _compare_one(
    teacher_root: Path,
    sample_id: str,
    predictor: Callable[[Any], dict[str, Any]],
) -> tuple[TeacherExample, dict[str, Any], dict[str, Any]]:
    item = json.loads((teacher_root / "items" / f"{sample_id}.json").read_text(encoding="utf-8"))
    truth = json.loads((teacher_root / "sol_batch" / "labels" / f"{sample_id}.json").read_text(encoding="utf-8"))
    prediction = predictor(SimpleNamespace(blinded=item))
    return (
        TeacherExample(sample_id=sample_id, item=_compact_item(item), truth={}),
        _compact_result(truth),
        _compact_result(prediction),
    )


def _compact_item(item: Mapping[str, Any]) -> dict[str, Any]:
    publication = item.get("publication") or {}
    return {
        "source_id": item.get("source_id"),
        "publication": {"title": publication.get("title")},
    }


def _compact_result(value: Mapping[str, Any]) -> dict[str, Any]:
    labels = []
    for raw in value.get("labels") or ():
        classification = raw.get("classification") or {}
        labels.append(
            {
                "ticker": raw.get("ticker"),
                "canonical_instrument_id": raw.get("canonical_instrument_id"),
                "classification": {
                    "semantic_direction": classification.get("semantic_direction"),
                    "event_concepts": classification.get("event_concepts") or [],
                },
                "forecast_trigger_eligible": bool(raw.get("forecast_trigger_eligible")),
                "reaction_evaluation_eligible": bool(raw.get("reaction_evaluation_eligible")),
                "issuer_history_context_eligible": bool(raw.get("issuer_history_context_eligible")),
            }
        )
    return {
        "extraction_decision": value.get("extraction_decision"),
        "content_role": value.get("content_role"),
        "source_origin": value.get("source_origin"),
        "labels": labels,
    }


def headline(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authority": report["authority"],
        "valid_teacher_articles": report["valid_teacher_articles"],
        "missing_teacher_articles": len(report["missing_teacher_articles"]),
        "decision_macro_f1": report["decision"]["macro_f1"],
        "ticker_scope_f1": report["ticker_scope"]["f1"],
        "content_role_macro_f1": report["content_role"]["macro_f1"],
        "source_origin_macro_f1": report["source_origin"]["macro_f1"],
        "direction_macro_f1": report["semantic_direction"]["macro_f1"],
        "concept_f1": report["event_concepts"]["f1"],
        "forecast_f1": report["forecast"]["f1"],
        "history_f1": report["history"]["f1"],
        "disagreement_count": report["disagreement_count"],
    }


def _labels_by_ticker(values: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for value in values:
        ticker = str(value.get("canonical_instrument_id") or value.get("ticker") or "").upper()
        if ticker:
            output.setdefault(ticker, value)
    return output


def _article_disagrees(truth: Mapping[str, Any], prediction: Mapping[str, Any]) -> bool:
    if any(
        str(truth.get(field) or "") != str(prediction.get(field) or "")
        for field in ("extraction_decision", "content_role", "source_origin")
    ):
        return True
    expected = _labels_by_ticker(truth.get("labels") or ())
    actual = _labels_by_ticker(prediction.get("labels") or ())
    if set(expected) != set(actual):
        return True
    for ticker in expected:
        left = expected[ticker]
        right = actual[ticker]
        left_class = left.get("classification") or {}
        right_class = right.get("classification") or {}
        if str(left_class.get("semantic_direction") or "") != str(right_class.get("semantic_direction") or ""):
            return True
        if bool(left.get("forecast_trigger_eligible")) != bool(right.get("forecast_trigger_eligible")):
            return True
    return False


def _disagreement_row(
    example: TeacherExample,
    truth: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> dict[str, Any]:
    publication = example.item.get("publication") or {}
    return {
        "sample_id": example.sample_id,
        "source_id": example.item.get("source_id"),
        "title": publication.get("title"),
        "teacher_decision": truth.get("extraction_decision"),
        "predicted_decision": prediction.get("extraction_decision"),
        "teacher_role": truth.get("content_role"),
        "predicted_role": prediction.get("content_role"),
        "teacher_origin": truth.get("source_origin"),
        "predicted_origin": prediction.get("source_origin"),
        "teacher_labels": truth.get("labels") or [],
        "predicted_labels": prediction.get("labels") or [],
    }


def _multiclass_report(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    values = list(pairs)
    labels = sorted({value for pair in values for value in pair if value})
    by_class: dict[str, dict[str, float | int]] = {}
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for expected, actual in values:
        confusion[expected][actual] += 1
    for label in labels:
        true_positive = sum(1 for expected, actual in values if expected == actual == label)
        false_positive = sum(1 for expected, actual in values if expected != label and actual == label)
        false_negative = sum(1 for expected, actual in values if expected == label and actual != label)
        by_class[label] = _prf(true_positive, false_positive, false_negative)
    return {
        "count": len(values),
        "accuracy": _round(sum(expected == actual for expected, actual in values) / len(values)) if values else 0.0,
        "macro_f1": _round(sum(float(value["f1"]) for value in by_class.values()) / len(by_class)) if by_class else 0.0,
        "by_class": by_class,
        "confusion": {expected: dict(counter) for expected, counter in confusion.items()},
    }


def _binary_report(pairs: Iterable[tuple[bool, bool]]) -> dict[str, Any]:
    values = list(pairs)
    tp = sum(expected and actual for expected, actual in values)
    fp = sum(not expected and actual for expected, actual in values)
    fn = sum(expected and not actual for expected, actual in values)
    tn = sum(not expected and not actual for expected, actual in values)
    return {**_prf(tp, fp, fn), "true_negative": tn, "count": len(values)}


def _set_report(expected: set, actual: set) -> dict[str, Any]:
    return _prf(len(expected & actual), len(actual - expected), len(expected - actual))


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
    }


def _round(value: float) -> float:
    return round(value, 6)
