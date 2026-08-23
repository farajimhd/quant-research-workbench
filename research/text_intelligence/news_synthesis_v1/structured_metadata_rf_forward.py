from __future__ import annotations

import json
import time
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
    _calibration,
    _select_threshold,
    _slice_metrics,
    binary_metrics,
)
from .structured_metadata_rf_reverse import _labels_for, _latest_labels, _verify_manifest


EXPERIMENT_VERSION = "news_structured_metadata_rf_forward_2025_to_2026_final_labels_v1"
DEFAULT_PARENT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\structured_metadata_rf_v1"
)
DEFAULT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\structured_metadata_rf_2025_to_2026_final_labels_v1"
)


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


def train_and_evaluate(
    *, parent_root: Path = DEFAULT_PARENT, authority_root: Path = DEFAULT_AUTHORITY,
    output_root: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    _verify_manifest(parent_root)
    _verify_manifest(authority_root)
    contract = json.loads((parent_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    if contract.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unexpected frozen feature contract")

    train_index = list(iter_jsonl(parent_root / "ROWS_2025_TRAIN.jsonl"))
    test_index = list(iter_jsonl(parent_root / "ROWS_2026_TEST.jsonl"))
    x_train = sparse.load_npz(parent_root / "X_2025_TRAIN.npz").tocsr()
    x_test = sparse.load_npz(parent_root / "X_2026_TEST.npz").tocsr()
    feature_names = list(map(str, contract["feature_names"]))
    if x_train.shape != (len(train_index), len(feature_names)):
        raise ValueError("2025 matrix/contract mismatch")
    if x_test.shape != (len(test_index), len(feature_names)):
        raise ValueError("2026 matrix/contract mismatch")
    if len(train_index) != 203_847 or len(test_index) != 142_256:
        raise ValueError("unexpected forward chronological population")

    source_ids = {str(row["source_id"]) for row in train_index + test_index}
    if len(source_ids) != len(train_index) + len(test_index):
        raise ValueError("train/test source overlap")
    labels = _latest_labels(authority_root, source_ids)
    y_train = _labels_for(train_index, labels)
    y_test = _labels_for(test_index, labels)
    validation_mask = np.asarray([
        str(row["published_at_utc"]) >= "2025-11-01" for row in train_index
    ])

    output_root.mkdir(parents=True)
    started = time.time()
    development_results = []
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
        development_results.append({
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
        **selected_parameters, "n_estimators": RF_PARAMETERS["n_estimators"],
        "random_state": SEED,
    }
    model = RandomForestClassifier(**final_parameters)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_test)[:, 1]
    prediction = (probability >= threshold).astype(np.int8)
    metrics = binary_metrics(y_test, probability, threshold)

    prediction_rows = []
    disagreement_rows = []
    for row, truth, score, predicted in zip(
        test_index, y_test, probability, prediction, strict=True,
    ):
        item = {
            **row, "label": "eligible" if truth else "ineligible",
            "eligible_probability": float(score),
            "predicted_label": "eligible" if predicted else "ineligible",
            "label_disagreement": bool(predicted != truth),
        }
        prediction_rows.append(item)
        if item["label_disagreement"]:
            disagreement_rows.append(item)
    predictions_path = output_root / "PREDICTIONS_2026.jsonl"
    disagreements_path = output_root / "LABEL_DISAGREEMENTS_2026.jsonl"
    _write_jsonl_new(predictions_path, prediction_rows)
    _write_jsonl_new(disagreements_path, disagreement_rows)
    joblib.dump(model, output_root / "RANDOM_FOREST.joblib", compress=3)

    mdi = np.asarray(model.feature_importances_, dtype=np.float64)
    top_features = [
        {"feature": feature_names[int(index)], "mdi_importance": float(mdi[int(index)])}
        for index in np.argsort(mdi)[::-1][:100]
    ]
    report = {
        "experiment_version": EXPERIMENT_VERSION,
        "contract_version": CONTRACT_VERSION, "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_role": "forward-temporal diagnostic: revised 2025 train, revised 2026 evaluation",
        "selected_threshold": threshold, "rf_parameters": final_parameters,
        "selection": {
            "development_split": "2025 before November",
            "development_articles": int((~validation_mask).sum()),
            "threshold_selection_split": "2025 November-December",
            "threshold_selection_articles": int(validation_mask.sum()),
            "selected_candidate_index": selected_index,
            "candidate_results": development_results,
            "selected_curve": threshold_curve,
        },
        "training": {
            "split": "all decisive 2025", "articles": len(y_train),
            "eligible": int(y_train.sum()), "ineligible": int(len(y_train) - y_train.sum()),
        },
        "test": {
            "split": "all decisive January-August 2026", "articles": len(y_test),
            "eligible": int(y_test.sum()), "ineligible": int(len(y_test) - y_test.sum()),
            "metrics": metrics, "metrics_at_0_5": binary_metrics(y_test, probability, 0.5),
            "calibration": _calibration(y_test, probability),
            "by_source_split": _slice_metrics(test_index, y_test, probability, threshold, "split"),
            "by_month": _slice_metrics(test_index, y_test, probability, threshold, "month"),
            "by_session_segment": _slice_metrics(
                test_index, y_test, probability, threshold, "session_segment"
            ),
            "by_market_cap_max_bucket": _slice_metrics(
                test_index, y_test, probability, threshold, "market_cap_max_bucket"
            ),
        },
        "top_mdi": top_features,
        "disagreements": {
            "articles": len(disagreement_rows),
            "share": len(disagreement_rows) / len(test_index),
        },
        "inputs": {
            "parent_feature_runtime": str(parent_root),
            "parent_hash_manifest_sha256": sha256_path(parent_root / "HASH_MANIFEST.json"),
            "latest_label_authority": str(authority_root),
            "authority_hash_manifest_sha256": sha256_path(authority_root / "HASH_MANIFEST.json"),
            "feature_dictionary_sha256": str(contract["feature_dictionary_sha256"]),
        },
        "limitations": [
            "The 2025 feature dictionary and 2025 labels informed model development.",
            "The 2026 supervision labels were corrected after RF-conditioned candidate selection.",
            "The 2026 evaluation population is therefore not an untouched release holdout.",
        ],
        "train_seconds": time.time() - started,
    }
    _write_json_new(output_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    report = json.loads((output_root / "REPORT.json").read_text(encoding="utf-8"))
    predictions = list(iter_jsonl(output_root / "PREDICTIONS_2026.jsonl"))
    disagreements = list(iter_jsonl(output_root / "LABEL_DISAGREEMENTS_2026.jsonl"))
    checks = {
        "report_complete": report.get("status") == "complete",
        "experiment_version": report.get("experiment_version") == EXPERIMENT_VERSION,
        "train_rows": report.get("training", {}).get("articles") == 203_847,
        "test_rows": report.get("test", {}).get("articles") == 142_256,
        "prediction_rows": len(predictions) == 142_256,
        "unique_predictions": len({str(row["source_id"]) for row in predictions}) == 142_256,
        "disagreement_rows": len(disagreements) == int(report["disagreements"]["articles"]),
        "disagreement_flags": all(bool(row["label_disagreement"]) for row in disagreements),
        "model_present": (output_root / "RANDOM_FOREST.joblib").stat().st_size > 0,
    }
    if not all(checks.values()):
        raise ValueError(f"forward experiment validation failed: {checks}")
    validation = {"status": "passed", "checks": checks}
    _write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(
        path for path in output_root.iterdir()
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    )
    _write_json_new(output_root / "HASH_MANIFEST.json", {
        "experiment_version": EXPERIMENT_VERSION,
        "files": {path.name: {
            "bytes": path.stat().st_size, "sha256": sha256_path(path),
        } for path in files},
    })
    return validation
