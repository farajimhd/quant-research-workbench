from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import numpy as np

from .deterministic_v6_config import DIRECTION_RULES
from .deterministic_v8_config import DIRECTION_RULES_V8
from .run_deterministic_news_v8 import _predict as predict_v8
from .schema import stable_json_hash
from .storage import assert_runtime_root, write_json_atomic
from .teacher_paths import DEFAULT_TEACHER_ROOT
from .teacher_split_v9 import ensure_grouped_split
from .deterministic_v9_signals import article_signals_from_parts


CALIBRATION_VERSION = "news_deterministic_v9_calibration_1"
DIRECTION_CLASSES = ("positive", "negative", "neutral", "mixed")


def fit_v9_calibration(
    teacher_root: Path = DEFAULT_TEACHER_ROOT,
    *,
    workers: int | None = None,
) -> dict[str, Any]:
    output_root = teacher_root / "deterministic_v9"
    assert_runtime_root(output_root)
    split = ensure_grouped_split(teacher_root, output_root=output_root)
    records = _load_or_build_records(
        teacher_root,
        split,
        output_root=output_root,
        workers=workers,
    )
    development = [row for row in records if row["split"] == "development"]
    validation = [row for row in records if row["split"] == "validation"]
    role_overrides = _fit_article_overrides(development, "content_role")
    origin_overrides = _fit_article_overrides(development, "source_origin")
    concept_additions = _fit_concept_additions(development)
    scope_denials = _fit_scope_denials(development)
    eligibility_tables = _fit_eligibility_tables(development)
    direction = _fit_direction(development, validation)
    config = {
        "calibration_version": CALIBRATION_VERSION,
        "split_version": split["split_version"],
        "split_items_sha256": split["items_sha256"],
        "record_count": len(records),
        "records_sha256": stable_json_hash(records),
        "article_role_overrides": role_overrides,
        "source_origin_overrides": origin_overrides,
        "single_ticker_concept_additions": concept_additions,
        "denied_unit_roles": scope_denials,
        "eligibility_tables": eligibility_tables,
        "direction": direction,
    }
    write_json_atomic(output_root / "calibration_config.json", config)
    return config


def _load_or_build_records(
    teacher_root: Path,
    split: Mapping[str, Any],
    *,
    output_root: Path,
    workers: int | None,
) -> list[dict[str, Any]]:
    cache = output_root / "v8_teacher_records.json"
    if cache.exists():
        value = json.loads(cache.read_text(encoding="utf-8"))
        if value.get("split_items_sha256") != split["items_sha256"]:
            raise RuntimeError("V9 cached records do not match the immutable split.")
        return list(value["records"])
    worker_count = workers or min(16, max(1, os.cpu_count() or 1))
    inputs = [
        (teacher_root, row["sample_id"], row["split"])
        for row in split["items"]
    ]
    records: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        for index, row in enumerate(executor.map(_build_record, inputs, chunksize=8), 1):
            records.append(row)
            if index % 500 == 0 or index == len(inputs):
                print(f"V9 FEATURES {index:,}/{len(inputs):,}", flush=True)
    records.sort(key=lambda row: row["sample_id"])
    write_json_atomic(cache, {
        "record_version": "news_deterministic_v9_teacher_records_1",
        "split_items_sha256": split["items_sha256"],
        "records": records,
    })
    return records


def _build_record(args: tuple[Path, str, str]) -> dict[str, Any]:
    teacher_root, sample_id, split = args
    item = json.loads((teacher_root / "items" / f"{sample_id}.json").read_text(encoding="utf-8"))
    truth = json.loads((teacher_root / "sol_batch" / "labels" / f"{sample_id}.json").read_text(encoding="utf-8"))
    prediction = predict_v8(SimpleNamespace(blinded=item))
    publication = item.get("publication") or {}
    signals = article_signals(item, prediction)
    return {
        "sample_id": sample_id,
        "split": split,
        "calendar_year": int(str(item.get("source_timestamp"))[:4]),
        "provider_ticker_count": len(publication.get("provider_tickers") or ()),
        "signals": signals,
        "truth": _compact_result(truth),
        "prediction": _compact_result(prediction, retain_runtime_fields=True),
    }


def article_signals(item: Mapping[str, Any], prediction: Mapping[str, Any]) -> list[str]:
    publication = item.get("publication") or {}
    return list(article_signals_from_parts(
        title=str(publication.get("title") or ""),
        provider_tickers=publication.get("provider_tickers") or (),
        provider_tags=publication.get("provider_tags") or (),
        channels=publication.get("channels") or (),
        evidence=prediction.get("evidence") or (),
    ))


def _compact_result(value: Mapping[str, Any], *, retain_runtime_fields: bool = False) -> dict[str, Any]:
    labels = []
    for raw in value.get("labels") or ():
        classification = raw.get("classification") or {}
        row = {
            "ticker": str(raw.get("canonical_instrument_id") or raw.get("ticker") or "").upper(),
            "unit_role": str(raw.get("unit_role") or ""),
            "classification": {
                "semantic_direction": classification.get("semantic_direction"),
                "event_concepts": sorted(set(classification.get("event_concepts") or ())),
            },
            "forecast_trigger_eligible": bool(raw.get("forecast_trigger_eligible")),
            "reaction_evaluation_eligible": bool(raw.get("reaction_evaluation_eligible")),
            "issuer_history_context_eligible": bool(raw.get("issuer_history_context_eligible")),
        }
        if retain_runtime_fields:
            row["classification"].update({
                "semantic_score_raw": float(classification.get("semantic_score_raw") or 0.0),
                "deterministic_direction_evidence": list(classification.get("deterministic_direction_evidence") or ()),
            })
        labels.append(row)
    return {
        "extraction_decision": value.get("extraction_decision"),
        "content_role": value.get("content_role"),
        "source_origin": value.get("source_origin"),
        "labels": labels,
    }


def _fit_article_overrides(records: Iterable[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    current_correct: Counter[str] = Counter()
    for row in records:
        truth = str(row["truth"].get(field) or "")
        current = str(row["prediction"].get(field) or "")
        for signal in row["signals"]:
            counts[signal][truth] += 1
            current_correct[signal] += int(current == truth)
    output = []
    for signal, labels in counts.items():
        support = sum(labels.values())
        target, target_count = sorted(labels.items(), key=lambda item: (-item[1], item[0]))[0]
        precision = target_count / support
        improvement = target_count - current_correct[signal]
        if support < 12 or precision < 0.82 or improvement < 2:
            continue
        output.append({
            "signal": signal,
            "value": target,
            "support": support,
            "precision": round(precision, 6),
            "development_improvement": improvement,
        })
    return sorted(output, key=lambda row: (-row["precision"], -row["support"], row["signal"]))


def _fit_concept_additions(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    totals: Counter[str] = Counter()
    concept_counts: dict[str, Counter[str]] = defaultdict(Counter)
    current_concepts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in records:
        truth_labels = row["truth"]["labels"]
        predicted_labels = row["prediction"]["labels"]
        if len(truth_labels) != 1 or len(predicted_labels) != 1 or row["provider_ticker_count"] != 1:
            continue
        truth = set(truth_labels[0]["classification"]["event_concepts"])
        current = set(predicted_labels[0]["classification"]["event_concepts"])
        for signal in row["signals"]:
            totals[signal] += 1
            concept_counts[signal].update(truth)
            current_concepts[signal].update(current)
    output = []
    for signal, counts in concept_counts.items():
        total = totals[signal]
        for concept, support in counts.items():
            precision = support / total
            existing = current_concepts[signal][concept]
            if support < 10 or precision < 0.90 or support - existing < 2:
                continue
            output.append({
                "signal": signal,
                "concept": concept,
                "support": support,
                "precision": round(precision, 6),
                "development_recovery": support - existing,
            })
    return sorted(output, key=lambda row: (-row["precision"], -row["support"], row["signal"], row["concept"]))


def _fit_scope_denials(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in records:
        truth_tickers = {label["ticker"] for label in row["truth"]["labels"]}
        for label in row["prediction"]["labels"]:
            role = label["unit_role"] or "__missing__"
            counts[role]["total"] += 1
            counts[role]["matched"] += int(label["ticker"] in truth_tickers)
    output = []
    for role, values in counts.items():
        precision = values["matched"] / values["total"]
        if values["total"] >= 30 and precision < 0.25:
            output.append({"unit_role": role, "support": values["total"], "precision": round(precision, 6)})
    return sorted(output, key=lambda row: (row["precision"], -row["support"], row["unit_role"]))


def _fit_eligibility_tables(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, Counter[bool]] = defaultdict(Counter)
    for row in records:
        truth_by_ticker = {label["ticker"]: label for label in row["truth"]["labels"]}
        role = str(row["prediction"]["content_role"] or "")
        origin = str(row["prediction"]["source_origin"] or "")
        for label in row["prediction"]["labels"]:
            truth = truth_by_ticker.get(label["ticker"])
            if truth is None:
                continue
            has_event = bool(label["classification"]["event_concepts"])
            key = "|".join((role, origin, label["unit_role"] or "__missing__", str(int(has_event))))
            counts[key][bool(truth["forecast_trigger_eligible"])] += 1
    output = []
    for key, values in counts.items():
        support = sum(values.values())
        positive_rate = values[True] / support
        if support < 20 or (0.15 < positive_rate < 0.85):
            continue
        output.append({
            "key": key,
            "eligible": positive_rate >= 0.85,
            "support": support,
            "precision": round(max(positive_rate, 1.0 - positive_rate), 6),
        })
    return sorted(output, key=lambda row: (-row["precision"], -row["support"], row["key"]))


def _fit_direction(
    development: Iterable[Mapping[str, Any]],
    validation: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rule_defaults = {rule.rule_id: float(rule.weight) for rule in (*DIRECTION_RULES, *DIRECTION_RULES_V8)}
    rule_ids = sorted(rule_defaults)
    train = _direction_arrays(development, rule_ids, rule_defaults)
    valid = _direction_arrays(validation, rule_ids, rule_defaults)
    weights = np.array([rule_defaults[rule_id] for rule_id in rule_ids], dtype=np.float64)
    thresholds = {"base_scale": 1.0, "positive": 0.35, "negative": -0.35, "component": 0.35, "mixed_margin": 1.05}
    baseline_train = _direction_macro_f1(_direction_predict(train, weights, thresholds), train["truth"])
    baseline_validation = _direction_macro_f1(_direction_predict(valid, weights, thresholds), valid["truth"])

    support = train["x"].sum(axis=0)
    for _ in range(2):
        for index, default in enumerate(weights.copy()):
            if support[index] < 20:
                continue
            sign = 1.0 if rule_defaults[rule_ids[index]] >= 0 else -1.0
            hit_truth = train["truth"][train["x"][:, index] > 0]
            desired = "positive" if sign > 0 else "negative"
            opposite = "negative" if sign > 0 else "positive"
            desired_count = int(np.sum(hit_truth == desired))
            opposite_count = int(np.sum(hit_truth == opposite))
            empirical = min(2.0, max(0.1, abs(math.log((desired_count + 2) / (opposite_count + 2)))))
            magnitudes = sorted({
                0.0, 0.15, 0.30, 0.45, 0.60, 0.80, 1.00, 1.25, 1.50, 1.80, 2.00,
                round(abs(rule_defaults[rule_ids[index]]), 2), round(empirical, 2),
            })
            best_value = weights[index]
            best_score = _direction_objective(train, weights, thresholds, rule_defaults, rule_ids)
            for magnitude in magnitudes:
                candidate = sign * magnitude
                weights[index] = candidate
                score = _direction_objective(train, weights, thresholds, rule_defaults, rule_ids)
                if score > best_score + 1e-9:
                    best_score, best_value = score, candidate
            weights[index] = best_value

    best_thresholds = dict(thresholds)
    best_validation = -1.0
    for base_scale in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25):
        for positive in (0.20, 0.35, 0.50, 0.65, 0.80):
            for negative_abs in (0.20, 0.35, 0.50, 0.65, 0.80):
                for component in (0.25, 0.40, 0.55, 0.70):
                    for margin in (0.50, 0.75, 1.00, 1.25, 1.50):
                        candidate = {
                            "base_scale": base_scale,
                            "positive": positive,
                            "negative": -negative_abs,
                            "component": component,
                            "mixed_margin": margin,
                        }
                        score = _direction_macro_f1(_direction_predict(valid, weights, candidate), valid["truth"])
                        if score > best_validation + 1e-9:
                            best_validation, best_thresholds = score, candidate
    return {
        "rule_weights": {rule_id: round(float(weights[index]), 4) for index, rule_id in enumerate(rule_ids)},
        "rule_support": {rule_id: int(support[index]) for index, rule_id in enumerate(rule_ids)},
        "thresholds": best_thresholds,
        "development_units": len(train["truth"]),
        "validation_units": len(valid["truth"]),
        "baseline_development_macro_f1": round(baseline_train, 6),
        "baseline_validation_macro_f1": round(baseline_validation, 6),
        "calibrated_development_macro_f1": round(
            _direction_macro_f1(_direction_predict(train, weights, best_thresholds), train["truth"]), 6
        ),
        "calibrated_validation_macro_f1": round(best_validation, 6),
    }


def _direction_arrays(
    records: Iterable[Mapping[str, Any]],
    rule_ids: list[str],
    defaults: Mapping[str, float],
) -> dict[str, np.ndarray]:
    rows: list[tuple[float, set[str], str]] = []
    for row in records:
        truth_by_ticker = {label["ticker"]: label for label in row["truth"]["labels"]}
        for label in row["prediction"]["labels"]:
            truth = truth_by_ticker.get(label["ticker"])
            if truth is None:
                continue
            classification = label["classification"]
            matched = {
                str(value).split(":", 1)[0]
                for value in classification.get("deterministic_direction_evidence") or ()
            }
            added = sum(defaults.get(rule_id, 0.0) for rule_id in matched)
            base = float(classification.get("semantic_score_raw") or 0.0) - added
            direction = str(truth["classification"].get("semantic_direction") or "neutral")
            if direction in DIRECTION_CLASSES:
                rows.append((base, matched, direction))
    x = np.zeros((len(rows), len(rule_ids)), dtype=np.float64)
    for row_index, (_, matched, _) in enumerate(rows):
        for column, rule_id in enumerate(rule_ids):
            x[row_index, column] = float(rule_id in matched)
    return {
        "base": np.array([row[0] for row in rows], dtype=np.float64),
        "x": x,
        "truth": np.array([row[2] for row in rows], dtype=object),
    }


def _direction_predict(data: Mapping[str, np.ndarray], weights: np.ndarray, config: Mapping[str, float]) -> np.ndarray:
    base = data["base"] * float(config["base_scale"])
    raw = base + data["x"] @ weights
    positive = np.maximum(base, 0.0) + data["x"] @ np.maximum(weights, 0.0)
    negative = np.maximum(-base, 0.0) + data["x"] @ np.maximum(-weights, 0.0)
    output = np.full(len(raw), "neutral", dtype=object)
    output[raw >= float(config["positive"])] = "positive"
    output[raw <= float(config["negative"])] = "negative"
    mixed = (
        (positive >= float(config["component"]))
        & (negative >= float(config["component"]))
        & (np.abs(positive - negative) < float(config["mixed_margin"]))
    )
    output[mixed] = "mixed"
    return output


def _direction_macro_f1(predicted: np.ndarray, truth: np.ndarray) -> float:
    scores = []
    for label in DIRECTION_CLASSES:
        tp = int(np.sum((truth == label) & (predicted == label)))
        fp = int(np.sum((truth != label) & (predicted == label)))
        fn = int(np.sum((truth == label) & (predicted != label)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores)


def _direction_objective(
    data: Mapping[str, np.ndarray],
    weights: np.ndarray,
    thresholds: Mapping[str, float],
    defaults: Mapping[str, float],
    rule_ids: list[str],
) -> float:
    score = _direction_macro_f1(_direction_predict(data, weights, thresholds), data["truth"])
    deviation = sum(abs(float(weights[index]) - defaults[rule_id]) for index, rule_id in enumerate(rule_ids))
    return score - 0.00025 * deviation
