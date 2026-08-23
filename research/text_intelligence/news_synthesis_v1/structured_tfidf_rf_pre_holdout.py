from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
from scipy import sparse
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path
from .rf_challenger import TFIDF_PARAMETERS
from .structured_metadata_rf import _calibration, _select_threshold, binary_metrics
from .structured_metadata_rf_pre_holdout import (
    EXPECTED_HOLDOUT_ARTICLES,
    EXPECTED_TRAIN_ARTICLES,
    TRAIN_END_UTC,
    VALIDATION_START_UTC,
    _holdout_matrix,
    _training_validation_mask,
)
from .structured_metadata_rf_reverse import _labels_for, _latest_labels, _verify_manifest


EXPERIMENT_VERSION = "news_structured_tfidf_rf_pre_august_holdout_v1"
EXPECTED_TEXT_AUTHORITY_ROWS = 360_287


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_new(path: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
    return len(rows)


def _verify_text_authority(root: Path) -> Path:
    validation = json.loads((root / "VALIDATION.json").read_text(encoding="utf-8"))
    if (
        validation.get("status") != "validated"
        or not validation.get("exact_text_alignment")
        or int(validation.get("article_count", 0)) != EXPECTED_TEXT_AUTHORITY_ROWS
    ):
        raise ValueError("rendered-text authority is not validated")
    manifest = json.loads((root / "HASH_MANIFEST.json").read_text(encoding="utf-8"))
    metadata = manifest.get("rendered_texts.jsonl")
    path = root / "rendered_texts.jsonl"
    if not metadata or sha256_path(path) != str(metadata["sha256"]):
        raise ValueError("rendered-text authority hash mismatch")
    return path


def _aligned_training_texts(
    text_path: Path, train_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    positions = {str(row["source_id"]): index for index, row in enumerate(train_rows)}
    if len(positions) != len(train_rows):
        raise ValueError("duplicate training source IDs")
    texts: list[str | None] = [None] * len(train_rows)
    found = 0
    seen: set[str] = set()
    for row in iter_jsonl(text_path):
        source_id = str(row["source_id"])
        position = positions.get(source_id)
        if position is None:
            continue
        if source_id in seen:
            raise ValueError(f"duplicate rendered training source: {source_id}")
        text = str(row.get("rendered_text") or "")
        if not text:
            raise ValueError(f"empty rendered training text: {source_id}")
        texts[position] = text
        seen.add(source_id)
        found += 1
    if found != len(train_rows) or any(text is None for text in texts):
        raise ValueError(f"rendered-text coverage mismatch: {found}/{len(train_rows)}")
    return [str(text) for text in texts]


def _combined_matrix(
    structured: sparse.spmatrix, text: sparse.spmatrix,
) -> sparse.csr_matrix:
    if structured.shape[0] != text.shape[0]:
        raise ValueError("structured/TF-IDF row mismatch")
    return sparse.hstack((structured, text), format="csr", dtype=np.float32)


def train_and_evaluate(
    *, parent_root: Path, authority_root: Path, holdout_root: Path,
    base_model_root: Path, text_authority_root: Path, output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    _verify_manifest(parent_root)
    _verify_manifest(authority_root)
    _verify_manifest(base_model_root)
    text_path = _verify_text_authority(text_authority_root)
    holdout_validation = json.loads(
        (holdout_root / "VALIDATION.json").read_text(encoding="utf-8")
    )
    if holdout_validation.get("status") != "passed":
        raise ValueError("holdout authority is not validated")
    contract = json.loads((parent_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    base_report = json.loads((base_model_root / "REPORT.json").read_text(encoding="utf-8"))
    base_parameters = dict(base_report["rf_parameters"])
    if int(base_parameters["n_estimators"]) != 400:
        raise ValueError("unexpected base forest configuration")

    train_rows = (
        list(iter_jsonl(parent_root / "ROWS_2025_TRAIN.jsonl"))
        + list(iter_jsonl(parent_root / "ROWS_2026_TEST.jsonl"))
    )
    x_structured = sparse.vstack(
        (
            sparse.load_npz(parent_root / "X_2025_TRAIN.npz"),
            sparse.load_npz(parent_root / "X_2026_TEST.npz"),
        ),
        format="csr",
    )
    if len(train_rows) != EXPECTED_TRAIN_ARTICLES or x_structured.shape[0] != len(train_rows):
        raise ValueError("training population changed")
    if max(str(row["published_at_utc"]) for row in train_rows) != TRAIN_END_UTC:
        raise ValueError("training boundary changed")
    source_ids = {str(row["source_id"]) for row in train_rows}
    y_train = _labels_for(train_rows, _latest_labels(authority_root, source_ids))
    x_holdout_structured, holdout_rows, y_holdout = _holdout_matrix(
        holdout_root=holdout_root, parent_root=parent_root, contract=contract,
    )
    if len(holdout_rows) + int(
        json.loads((holdout_root / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8"))["unresolved"]
    ) != EXPECTED_HOLDOUT_ARTICLES:
        raise ValueError("holdout population changed")
    if source_ids & {str(row["source_id"]) for row in holdout_rows}:
        raise ValueError("training/holdout source overlap")

    started = time.time()
    train_texts = _aligned_training_texts(text_path, train_rows)
    holdout_texts = [str(row["rendered_text"]) for row in holdout_rows]
    validation_mask = _training_validation_mask(train_rows)
    development_positions = np.flatnonzero(~validation_mask)
    validation_positions = np.flatnonzero(validation_mask)

    development_vectorizer = TfidfVectorizer(**TFIDF_PARAMETERS)
    x_text_development = development_vectorizer.fit_transform(
        train_texts[position] for position in development_positions
    )
    x_text_validation = development_vectorizer.transform(
        train_texts[position] for position in validation_positions
    )
    selection_parameters = {
        **base_parameters, "n_estimators": 120,
    }
    selection_model = RandomForestClassifier(**selection_parameters)
    selection_model.fit(
        _combined_matrix(x_structured[development_positions], x_text_development),
        y_train[development_positions],
    )
    validation_probability = selection_model.predict_proba(
        _combined_matrix(x_structured[validation_positions], x_text_validation)
    )[:, 1]
    threshold, threshold_curve = _select_threshold(
        y_train[validation_positions], validation_probability
    )
    validation_metrics = binary_metrics(
        y_train[validation_positions], validation_probability, threshold
    )
    del (
        selection_model, development_vectorizer,
        x_text_development, x_text_validation,
    )

    tfidf = TfidfVectorizer(**TFIDF_PARAMETERS)
    x_text_train = tfidf.fit_transform(train_texts)
    x_text_holdout = tfidf.transform(holdout_texts)
    del train_texts, holdout_texts
    x_train = _combined_matrix(x_structured, x_text_train)
    x_holdout = _combined_matrix(x_holdout_structured, x_text_holdout)
    model = RandomForestClassifier(**base_parameters)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_holdout)[:, 1]
    prediction = (probability >= threshold).astype(np.int8)
    metrics = binary_metrics(y_holdout, probability, threshold)

    output_root.mkdir(parents=True)
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
    joblib.dump(tfidf, output_root / "TFIDF_VECTORIZER.joblib", compress=3)
    joblib.dump(model, output_root / "RANDOM_FOREST.joblib", compress=3)

    structured_columns = int(x_structured.shape[1])
    tfidf_columns = int(x_text_train.shape[1])
    report = {
        "experiment_version": EXPERIMENT_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_role": "untouched time-forward holdout; TF-IDF vocabulary, model, and threshold use pre-boundary data only",
        "feature_policy": "frozen structured metadata contract plus word unigram/bigram TF-IDF from rendered text",
        "rf_parameters": base_parameters,
        "tfidf_parameters": {**TFIDF_PARAMETERS, "dtype": "float32"},
        "dimensions": {
            "structured": structured_columns,
            "tfidf": tfidf_columns,
            "total": structured_columns + tfidf_columns,
        },
        "selection": {
            "configuration_source": str(base_model_root),
            "configuration_policy": "fixed current structured-model forest configuration; no TF-IDF candidate search",
            "development_articles": int(len(development_positions)),
            "threshold_selection_articles": int(len(validation_positions)),
            "threshold_selection_start_utc": VALIDATION_START_UTC,
            "selected_threshold": threshold,
            "validation_metrics": validation_metrics,
            "threshold_curve": threshold_curve,
        },
        "training": {
            "articles": len(y_train), "eligible": int(y_train.sum()),
            "ineligible": int(len(y_train) - y_train.sum()),
            "end_utc": TRAIN_END_UTC,
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
        "disagreements": {
            "articles": len(disagreements),
            "share": len(disagreements) / len(y_holdout),
        },
        "inputs": {
            "parent_hash_manifest_sha256": sha256_path(parent_root / "HASH_MANIFEST.json"),
            "authority_hash_manifest_sha256": sha256_path(authority_root / "HASH_MANIFEST.json"),
            "holdout_hash_manifest_sha256": sha256_path(holdout_root / "HASH_MANIFEST.json"),
            "base_model_hash_manifest_sha256": sha256_path(base_model_root / "HASH_MANIFEST.json"),
            "rendered_texts_sha256": sha256_path(text_path),
        },
        "train_seconds": time.time() - started,
        "limitations": [
            "Accuracy measures agreement with blinded forecast-eligibility review, not price forecasting.",
            "The 131 unresolved holdout articles are excluded rather than force-labeled.",
            "The holdout must remain sealed and must not be used for feature, parameter, or threshold tuning.",
        ],
    }
    _write_json_new(output_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    report = json.loads((output_root / "REPORT.json").read_text(encoding="utf-8"))
    predictions = list(iter_jsonl(output_root / "PREDICTIONS_HOLDOUT.jsonl"))
    disagreements = list(iter_jsonl(output_root / "LABEL_DISAGREEMENTS_HOLDOUT.jsonl"))
    tfidf: TfidfVectorizer = joblib.load(output_root / "TFIDF_VECTORIZER.joblib")
    model: RandomForestClassifier = joblib.load(output_root / "RANDOM_FOREST.joblib")
    dimensions = report["dimensions"]
    scored = int(report["holdout"]["articles_scored"])
    checks = {
        "report_complete": report.get("status") == "complete",
        "experiment_version": report.get("experiment_version") == EXPERIMENT_VERSION,
        "train_rows": int(report["training"]["articles"]) == EXPECTED_TRAIN_ARTICLES,
        "train_boundary": str(report["training"]["end_utc"]) == TRAIN_END_UTC,
        "holdout_reconciles": scored + int(report["holdout"]["unresolved_excluded"]) == EXPECTED_HOLDOUT_ARTICLES,
        "prediction_rows": len(predictions) == scored,
        "unique_predictions": len({str(row["source_id"]) for row in predictions}) == scored,
        "disagreement_rows": len(disagreements) == int(report["disagreements"]["articles"]),
        "disagreement_flags": all(bool(row["label_disagreement"]) for row in disagreements),
        "tfidf_dimensions": len(tfidf.get_feature_names_out()) == int(dimensions["tfidf"]),
        "model_dimensions": int(model.n_features_in_) == int(dimensions["total"]),
        "tree_count": len(model.estimators_) == int(report["rf_parameters"]["n_estimators"]),
    }
    if not all(checks.values()):
        raise ValueError(f"structured plus TF-IDF validation failed: {checks}")
    validation = {"status": "passed", "checks": checks}
    _write_json_new(output_root / "VALIDATION.json", validation)
    _write_json_new(output_root / "HASH_MANIFEST.json", {
        "experiment_version": EXPERIMENT_VERSION,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in sorted(output_root.iterdir())
            if path.is_file() and path.name != "HASH_MANIFEST.json"
        },
    })
    return validation
