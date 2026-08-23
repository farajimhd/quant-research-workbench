from __future__ import annotations

import csv
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
from scipy import sparse
from sklearn.ensemble import RandomForestClassifier

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path
from .structured_metadata_rf import (
    CONTRACT_VERSION,
    DEVELOPMENT_CANDIDATES,
    RF_PARAMETERS,
    SEED,
    _build_matrix,
    _calibration,
    _select_threshold,
    binary_metrics,
)
from .structured_metadata_rf_reverse import _labels_for, _latest_labels, _verify_manifest


EXPERIMENT_VERSION = "news_structured_metadata_rf_pre_august_holdout_v1"
EXPECTED_TRAIN_ARTICLES = 346_103
EXPECTED_HOLDOUT_ARTICLES = 5_044
TRAIN_END_UTC = "2026-08-13T21:04:05+00:00"
HOLDOUT_START_UTC = "2026-08-13T21:04:39+00:00"
VALIDATION_START_UTC = "2026-07-01T00:00:00+00:00"


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_new(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    return len(rows)


def _training_validation_mask(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray(
        [str(row["published_at_utc"]) >= VALIDATION_START_UTC for row in rows],
        dtype=bool,
    )


def _holdout_matrix(
    *, holdout_root: Path, parent_root: Path, contract: Mapping[str, Any],
) -> tuple[sparse.csr_matrix, list[dict[str, Any]], np.ndarray]:
    final_report = json.loads(
        (holdout_root / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8")
    )
    if final_report.get("status") != "complete":
        raise ValueError("holdout labels are not complete")
    labels = {
        str(row["source_id"]): str(row["final_label"])
        for row in iter_jsonl(holdout_root / "FINAL_LABELS_V2.jsonl")
    }
    rows = [
        row for row in iter_jsonl(holdout_root / "SOURCE_ROWS.jsonl")
        if labels[str(row["source_id"])] in {"eligible", "ineligible"}
    ]
    rows.sort(key=lambda row: (str(row["published_at_text"]), str(row["source_id"])))
    feature_names = list(map(str, contract["feature_names"]))
    feature_index = {name: index for index, name in enumerate(feature_names)}
    active = {
        str(family): set(map(str, values))
        for family, values in contract["active_categories"].items()
    }
    historical: dict[str, set[str]] = defaultdict(set)
    with (parent_root / "CATEGORY_CATALOG_2010_2025.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        for item in csv.DictReader(handle):
            historical[str(item["family"])].add(str(item["category"]))
    matrix = _build_matrix(
        rows, {str(row["source_id"]): row for row in rows},
        feature_index, active, historical,
    )
    truth = np.asarray(
        [labels[str(row["source_id"])] == "eligible" for row in rows],
        dtype=np.int8,
    )
    return matrix, rows, truth


def train_and_evaluate(
    *, parent_root: Path, authority_root: Path, holdout_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    _verify_manifest(parent_root)
    _verify_manifest(authority_root)
    holdout_validation = json.loads(
        (holdout_root / "VALIDATION.json").read_text(encoding="utf-8")
    )
    if holdout_validation.get("status") != "passed":
        raise ValueError("holdout authority is not validated")
    contract = json.loads((parent_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    if contract.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unexpected frozen feature contract")

    index_2025 = list(iter_jsonl(parent_root / "ROWS_2025_TRAIN.jsonl"))
    index_2026 = list(iter_jsonl(parent_root / "ROWS_2026_TEST.jsonl"))
    train_index = index_2025 + index_2026
    x_train = sparse.vstack(
        (
            sparse.load_npz(parent_root / "X_2025_TRAIN.npz"),
            sparse.load_npz(parent_root / "X_2026_TEST.npz"),
        ),
        format="csr",
    )
    feature_names = list(map(str, contract["feature_names"]))
    if x_train.shape != (len(train_index), len(feature_names)):
        raise ValueError("combined training matrix/contract mismatch")
    if len(train_index) != EXPECTED_TRAIN_ARTICLES:
        raise ValueError(f"unexpected training population: {len(train_index)}")
    if max(str(row["published_at_utc"]) for row in train_index) != TRAIN_END_UTC:
        raise ValueError("training boundary changed")
    train_source_ids = {str(row["source_id"]) for row in train_index}
    if len(train_source_ids) != len(train_index):
        raise ValueError("duplicate training source IDs")
    labels = _latest_labels(authority_root, train_source_ids)
    y_train = _labels_for(train_index, labels)
    x_holdout, holdout_rows, y_holdout = _holdout_matrix(
        holdout_root=holdout_root, parent_root=parent_root, contract=contract,
    )
    if len(holdout_rows) + int(
        json.loads((holdout_root / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8"))["unresolved"]
    ) != EXPECTED_HOLDOUT_ARTICLES:
        raise ValueError("holdout population changed")
    holdout_source_ids = {str(row["source_id"]) for row in holdout_rows}
    if train_source_ids & holdout_source_ids:
        raise ValueError("training/holdout source overlap")
    if min(str(row["published_at_text"]) for row in holdout_rows) != HOLDOUT_START_UTC:
        raise ValueError("holdout boundary changed")

    validation_mask = _training_validation_mask(train_index)
    if not validation_mask.any() or validation_mask.all():
        raise ValueError("invalid internal validation split")
    output_root.mkdir(parents=True)
    started = time.time()
    candidates = []
    best: tuple[tuple[float, float], dict[str, Any], float, list[dict[str, float]]] | None = None
    for candidate_index, candidate in enumerate(DEVELOPMENT_CANDIDATES):
        parameters = {
            **RF_PARAMETERS, **candidate, "n_estimators": 120,
            "random_state": SEED + candidate_index,
        }
        model = RandomForestClassifier(**parameters)
        model.fit(x_train[~validation_mask], y_train[~validation_mask])
        probability = model.predict_proba(x_train[validation_mask])[:, 1]
        threshold, curve = _select_threshold(y_train[validation_mask], probability)
        metrics = binary_metrics(y_train[validation_mask], probability, threshold)
        candidates.append({
            "candidate_index": candidate_index, "parameters": parameters,
            "selected_threshold": threshold, "validation_metrics": metrics,
        })
        rank = (metrics["balanced_accuracy"], metrics["eligible_f1"])
        if best is None or rank > best[0]:
            best = (rank, parameters, threshold, curve)
        del model
    assert best is not None
    _rank, selected_parameters, threshold, threshold_curve = best
    selected_index = int(selected_parameters["random_state"]) - SEED
    final_parameters = {
        **selected_parameters,
        "n_estimators": RF_PARAMETERS["n_estimators"],
        "random_state": SEED,
    }
    model = RandomForestClassifier(**final_parameters)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_holdout)[:, 1]
    prediction = (probability >= threshold).astype(np.int8)
    metrics = binary_metrics(y_holdout, probability, threshold)

    prediction_rows = []
    disagreements = []
    for row, truth, score, predicted in zip(
        holdout_rows, y_holdout, probability, prediction, strict=True,
    ):
        item = {
            "source_id": str(row["source_id"]),
            "published_at_utc": str(row["published_at_text"]),
            "label": "eligible" if truth else "ineligible",
            "eligible_probability": float(score),
            "predicted_label": "eligible" if predicted else "ineligible",
            "label_disagreement": bool(predicted != truth),
        }
        prediction_rows.append(item)
        if item["label_disagreement"]:
            disagreements.append(item)
    _write_jsonl_new(output_root / "PREDICTIONS_HOLDOUT.jsonl", prediction_rows)
    _write_jsonl_new(output_root / "LABEL_DISAGREEMENTS_HOLDOUT.jsonl", disagreements)
    joblib.dump(model, output_root / "RANDOM_FOREST.joblib", compress=3)
    mdi = np.asarray(model.feature_importances_, dtype=np.float64)
    top_features = [
        {"feature": feature_names[int(index)], "mdi_importance": float(mdi[int(index)])}
        for index in np.argsort(mdi)[::-1][:100]
    ]
    report = {
        "experiment_version": EXPERIMENT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_role": "untouched time-forward holdout; no holdout use in model or threshold selection",
        "selected_threshold": threshold,
        "rf_parameters": final_parameters,
        "selection": {
            "development_split": f"2025 through before {VALIDATION_START_UTC}",
            "development_articles": int((~validation_mask).sum()),
            "threshold_selection_split": f"{VALIDATION_START_UTC} through {TRAIN_END_UTC}",
            "threshold_selection_articles": int(validation_mask.sum()),
            "selected_candidate_index": selected_index,
            "candidate_results": candidates,
            "selected_curve": threshold_curve,
        },
        "training": {
            "articles": len(y_train), "eligible": int(y_train.sum()),
            "ineligible": int(len(y_train) - y_train.sum()),
            "start_utc": min(str(row["published_at_utc"]) for row in train_index),
            "end_utc": max(str(row["published_at_utc"]) for row in train_index),
        },
        "holdout": {
            "articles_frozen": EXPECTED_HOLDOUT_ARTICLES,
            "articles_scored": len(y_holdout),
            "unresolved_excluded": EXPECTED_HOLDOUT_ARTICLES - len(y_holdout),
            "eligible": int(y_holdout.sum()),
            "ineligible": int(len(y_holdout) - y_holdout.sum()),
            "metrics": metrics,
            "metrics_at_0_5": binary_metrics(y_holdout, probability, 0.5),
            "calibration": _calibration(y_holdout, probability),
        },
        "top_mdi": top_features,
        "disagreements": {
            "articles": len(disagreements),
            "share": len(disagreements) / len(y_holdout),
            "by_truth": dict(sorted(Counter(item["label"] for item in disagreements).items())),
        },
        "inputs": {
            "parent_hash_manifest_sha256": sha256_path(parent_root / "HASH_MANIFEST.json"),
            "authority_hash_manifest_sha256": sha256_path(authority_root / "HASH_MANIFEST.json"),
            "holdout_hash_manifest_sha256": sha256_path(holdout_root / "HASH_MANIFEST.json"),
            "holdout_final_labels_sha256": sha256_path(holdout_root / "FINAL_LABELS_V2.jsonl"),
            "feature_dictionary_sha256": str(contract["feature_dictionary_sha256"]),
        },
        "train_seconds": time.time() - started,
        "limitations": [
            "Accuracy measures agreement with the blinded forecast-eligibility review authority, not price forecasting.",
            "The 131 unresolved holdout articles are excluded rather than force-labeled.",
            "The holdout must remain sealed and must not be used for subsequent feature, parameter, rule, or threshold tuning.",
        ],
    }
    _write_json_new(output_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    report = json.loads((output_root / "REPORT.json").read_text(encoding="utf-8"))
    predictions = list(iter_jsonl(output_root / "PREDICTIONS_HOLDOUT.jsonl"))
    disagreements = list(iter_jsonl(output_root / "LABEL_DISAGREEMENTS_HOLDOUT.jsonl"))
    scored = int(report["holdout"]["articles_scored"])
    checks = {
        "report_complete": report.get("status") == "complete",
        "experiment_version": report.get("experiment_version") == EXPERIMENT_VERSION,
        "untouched_role": report.get("evaluation_role") == "untouched time-forward holdout; no holdout use in model or threshold selection",
        "train_rows": int(report["training"]["articles"]) == EXPECTED_TRAIN_ARTICLES,
        "train_boundary": str(report["training"]["end_utc"]) == TRAIN_END_UTC,
        "holdout_reconciles": scored + int(report["holdout"]["unresolved_excluded"]) == EXPECTED_HOLDOUT_ARTICLES,
        "prediction_rows": len(predictions) == scored,
        "unique_predictions": len({str(row["source_id"]) for row in predictions}) == scored,
        "disagreement_rows": len(disagreements) == int(report["disagreements"]["articles"]),
        "disagreement_flags": all(bool(row["label_disagreement"]) for row in disagreements),
        "model_present": (output_root / "RANDOM_FOREST.joblib").stat().st_size > 0,
    }
    if not all(checks.values()):
        raise ValueError(f"combined-train holdout validation failed: {checks}")
    validation = {"status": "passed", "checks": checks}
    _write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(
        path for path in output_root.iterdir()
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    )
    _write_json_new(output_root / "HASH_MANIFEST.json", {
        "experiment_version": EXPERIMENT_VERSION,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in files
        },
    })
    return validation
