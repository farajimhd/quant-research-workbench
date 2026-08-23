from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import torch
from scipy import sparse
from torch import nn

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path
from .structured_metadata_rf import _calibration, _select_threshold, binary_metrics
from .structured_metadata_rf_pre_holdout import (
    EXPECTED_HOLDOUT_ARTICLES,
    EXPECTED_TRAIN_ARTICLES,
    TRAIN_END_UTC,
    _holdout_matrix,
    _training_validation_mask,
)
from .structured_metadata_rf_reverse import _labels_for, _latest_labels, _verify_manifest
from .structured_tfidf_mlp_pre_holdout import _max_abs_scale, _scaled, _seed_everything, _torch_csr
from .structured_tfidf_rf_pre_holdout import (
    _aligned_training_texts,
    _combined_matrix,
    _verify_text_authority,
)


EXPERIMENT_VERSION = "news_structured_tfidf_deepfm_pre_august_holdout_v1"
SEED = 20260824
EMBEDDING_DIMENSION = 32
DEEP_DIMENSION = 128
DROPOUT = 0.10
LEARNING_RATE = 2e-3
WEIGHT_DECAY = 2e-6
BATCH_SIZE = 2048
MAX_SELECTION_EPOCHS = 10
EARLY_STOPPING_PATIENCE = 2


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


def _field_scale(
    matrix: sparse.csr_matrix, structured_columns: int,
) -> np.ndarray:
    if not 0 < structured_columns < matrix.shape[1]:
        raise ValueError("invalid structured/TF-IDF boundary")
    scale = np.ones(matrix.shape[1], dtype=np.float32)
    scale[:structured_columns] = _max_abs_scale(matrix[:, :structured_columns])
    return scale


class SparseDeepFM(nn.Module):
    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.wide_weight = nn.Parameter(torch.zeros(input_dim, 1))
        self.wide_bias = nn.Parameter(torch.zeros(1))
        self.embedding = nn.Parameter(torch.empty(input_dim, EMBEDDING_DIMENSION))
        nn.init.normal_(self.embedding, mean=0.0, std=0.01)
        self.fm_scale = nn.Parameter(torch.ones(1))
        self.deep_scale = nn.Parameter(torch.ones(1))
        self.deep_input = nn.Linear(EMBEDDING_DIMENSION, DEEP_DIMENSION)
        self.deep_block = nn.Sequential(
            nn.LayerNorm(DEEP_DIMENSION),
            nn.GELU(),
            nn.Dropout(DROPOUT),
            nn.Linear(DEEP_DIMENSION, DEEP_DIMENSION),
            nn.LayerNorm(DEEP_DIMENSION),
            nn.GELU(),
            nn.Dropout(DROPOUT),
            nn.Linear(DEEP_DIMENSION, DEEP_DIMENSION),
        )
        self.deep_output = nn.Linear(DEEP_DIMENSION, 1)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        first_order = torch.sparse.mm(values, self.wide_weight).squeeze(1) + self.wide_bias
        embedding_sum = torch.sparse.mm(values, self.embedding)
        squared_values = torch.sparse_csr_tensor(
            values.crow_indices(), values.col_indices(), values.values().square(),
            size=values.shape, dtype=values.dtype, device=values.device,
            check_invariants=False,
        )
        embedding_square_sum = torch.sparse.mm(squared_values, self.embedding.square())
        interaction = 0.5 * (embedding_sum.square() - embedding_square_sum).sum(dim=1)
        deep = self.deep_input(embedding_sum)
        deep = deep + self.deep_block(deep)
        deep_logit = self.deep_output(torch.nn.functional.gelu(deep)).squeeze(1)
        return first_order + self.fm_scale * interaction + self.deep_scale * deep_logit


def _fresh_model(input_dim: int, device: torch.device) -> SparseDeepFM:
    _seed_everything(SEED)
    return SparseDeepFM(input_dim).to(device)


def _probability(
    model: SparseDeepFM, matrix: sparse.csr_matrix, device: torch.device,
) -> np.ndarray:
    model.eval()
    result = []
    with torch.inference_mode():
        for start in range(0, matrix.shape[0], BATCH_SIZE):
            batch = _torch_csr(matrix[start:start + BATCH_SIZE], device)
            result.append(torch.sigmoid(model(batch)).cpu().numpy())
    return np.concatenate(result).astype(np.float64, copy=False)


def _train_epoch(
    model: SparseDeepFM, matrix: sparse.csr_matrix, truth: np.ndarray,
    optimizer: torch.optim.Optimizer, *, device: torch.device, seed: int,
) -> float:
    model.train()
    loss_function = nn.BCEWithLogitsLoss()
    order = np.random.default_rng(seed).permutation(matrix.shape[0])
    total_loss = 0.0
    seen = 0
    for start in range(0, len(order), BATCH_SIZE):
        positions = order[start:start + BATCH_SIZE]
        batch = _torch_csr(matrix[positions], device)
        target = torch.from_numpy(truth[positions].astype(np.float32, copy=False)).to(device)
        optimizer.zero_grad(set_to_none=True)
        loss = loss_function(model(batch), target)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
        optimizer.step()
        total_loss += float(loss.detach()) * len(positions)
        seen += len(positions)
    return total_loss / seen


def train_and_evaluate(
    *, parent_root: Path, authority_root: Path, holdout_root: Path,
    rf_root: Path, text_authority_root: Path, output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    _verify_manifest(parent_root)
    _verify_manifest(authority_root)
    _verify_manifest(rf_root)
    text_path = _verify_text_authority(text_authority_root)
    if json.loads((holdout_root / "VALIDATION.json").read_text(encoding="utf-8")).get("status") != "passed":
        raise ValueError("holdout authority is not validated")
    rf_report = json.loads((rf_root / "REPORT.json").read_text(encoding="utf-8"))
    contract = json.loads((parent_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    structured_columns = int(rf_report["dimensions"]["structured"])

    train_rows = (
        list(iter_jsonl(parent_root / "ROWS_2025_TRAIN.jsonl"))
        + list(iter_jsonl(parent_root / "ROWS_2026_TEST.jsonl"))
    )
    x_structured = sparse.vstack(
        (
            sparse.load_npz(parent_root / "X_2025_TRAIN.npz"),
            sparse.load_npz(parent_root / "X_2026_TEST.npz"),
        ), format="csr",
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
    tfidf = joblib.load(rf_root / "TFIDF_VECTORIZER.joblib")
    x_text_train = tfidf.transform(train_texts)
    x_text_holdout = tfidf.transform(str(row["rendered_text"]) for row in holdout_rows)
    del train_texts
    x_train_raw = _combined_matrix(x_structured, x_text_train)
    x_holdout_raw = _combined_matrix(x_holdout_structured, x_text_holdout)
    if x_train_raw.shape[1] != int(rf_report["dimensions"]["total"]):
        raise ValueError("DeepFM/RF feature dimensionality mismatch")
    validation_mask = _training_validation_mask(train_rows)
    development_positions = np.flatnonzero(~validation_mask)
    validation_positions = np.flatnonzero(validation_mask)
    development_scale = _field_scale(
        x_train_raw[development_positions], structured_columns,
    )
    x_development = _scaled(x_train_raw[development_positions], development_scale)
    x_validation = _scaled(x_train_raw[validation_positions], development_scale)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _fresh_model(x_train_raw.shape[1], device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY,
    )
    history = []
    best: tuple[tuple[float, float], int, float] | None = None
    stale_epochs = 0
    for epoch in range(1, MAX_SELECTION_EPOCHS + 1):
        loss = _train_epoch(
            model, x_development, y_train[development_positions], optimizer,
            device=device, seed=SEED + epoch,
        )
        validation_probability = _probability(model, x_validation, device)
        threshold, _curve = _select_threshold(
            y_train[validation_positions], validation_probability,
        )
        metrics = binary_metrics(
            y_train[validation_positions], validation_probability, threshold,
        )
        item = {
            "epoch": epoch, "training_loss": loss,
            "selected_threshold": threshold, "validation_metrics": metrics,
        }
        history.append(item)
        print(canonical_json({"phase": "selection", **item}), flush=True)
        rank = (metrics["balanced_accuracy"], metrics["eligible_f1"])
        if best is None or rank > best[0]:
            best = (rank, epoch, threshold)
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= EARLY_STOPPING_PATIENCE and epoch >= 3:
                break
    assert best is not None
    _rank, selected_epochs, selected_threshold = best
    del model, optimizer, x_development, x_validation, development_scale
    if device.type == "cuda":
        torch.cuda.empty_cache()

    final_scale = _field_scale(x_train_raw, structured_columns)
    x_train = _scaled(x_train_raw, final_scale)
    x_holdout = _scaled(x_holdout_raw, final_scale)
    model = _fresh_model(x_train.shape[1], device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY,
    )
    final_history = []
    for epoch in range(1, selected_epochs + 1):
        loss = _train_epoch(
            model, x_train, y_train, optimizer,
            device=device, seed=SEED + 10_000 + epoch,
        )
        final_history.append({"epoch": epoch, "training_loss": loss})
        print(canonical_json({"phase": "final", "epoch": epoch, "training_loss": loss}), flush=True)
    probability = _probability(model, x_holdout, device)
    prediction = (probability >= selected_threshold).astype(np.int8)
    metrics = binary_metrics(y_holdout, probability, selected_threshold)

    output_root.mkdir(parents=True)
    predictions = []
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
        predictions.append(item)
        if item["label_disagreement"]:
            disagreements.append(item)
    _write_jsonl_new(output_root / "PREDICTIONS_HOLDOUT.jsonl", predictions)
    _write_jsonl_new(output_root / "LABEL_DISAGREEMENTS_HOLDOUT.jsonl", disagreements)
    np.save(output_root / "COLUMN_SCALE.npy", final_scale, allow_pickle=False)
    torch.save({
        "state_dict": {name: value.detach().cpu() for name, value in model.state_dict().items()},
        "input_dim": x_train.shape[1],
        "embedding_dimension": EMBEDDING_DIMENSION,
        "deep_dimension": DEEP_DIMENSION,
    }, output_root / "MODEL.pt")
    report = {
        "experiment_version": EXPERIMENT_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_role": "iterative model-family benchmark on previously observed temporal holdout",
        "feature_policy": rf_report["feature_policy"],
        "dimensions": rf_report["dimensions"],
        "architecture": {
            "kind": "sparse DeepFM with wide, factorization-interaction, and residual deep branches",
            "embedding_dimension": EMBEDDING_DIMENSION,
            "deep_dimension": DEEP_DIMENSION,
            "dropout": DROPOUT,
            "learning_rate": LEARNING_RATE,
            "weight_decay": WEIGHT_DECAY,
            "batch_size": BATCH_SIZE,
            "optimizer": "AdamW",
            "loss": "BCEWithLogitsLoss",
            "device": str(device),
        },
        "selection": {
            "development_articles": int(len(development_positions)),
            "threshold_selection_articles": int(len(validation_positions)),
            "selected_epochs": selected_epochs,
            "selected_threshold": selected_threshold,
            "history": history,
        },
        "final_training": {
            "articles": len(y_train), "eligible": int(y_train.sum()),
            "ineligible": int(len(y_train) - y_train.sum()),
            "end_utc": TRAIN_END_UTC, "epochs": selected_epochs,
            "history": final_history,
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
            "rf_hash_manifest_sha256": sha256_path(rf_root / "HASH_MANIFEST.json"),
            "tfidf_vectorizer_sha256": sha256_path(rf_root / "TFIDF_VECTORIZER.joblib"),
            "rendered_texts_sha256": sha256_path(text_path),
        },
        "train_seconds": time.time() - started,
        "limitations": [
            "The August holdout had already been inspected for prior model families before DeepFM was proposed.",
            "This score is iterative benchmark evidence, not pristine release evidence for architecture selection.",
            "Accuracy measures forecast-eligibility label agreement, not price forecasting.",
            "The 131 unresolved holdout articles are excluded rather than force-labeled.",
            "Any selected architecture requires confirmation on a later sealed holdout.",
        ],
    }
    _write_json_new(output_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    report = json.loads((output_root / "REPORT.json").read_text(encoding="utf-8"))
    predictions = list(iter_jsonl(output_root / "PREDICTIONS_HOLDOUT.jsonl"))
    disagreements = list(iter_jsonl(output_root / "LABEL_DISAGREEMENTS_HOLDOUT.jsonl"))
    scale = np.load(output_root / "COLUMN_SCALE.npy", allow_pickle=False)
    checkpoint = torch.load(output_root / "MODEL.pt", map_location="cpu", weights_only=True)
    scored = int(report["holdout"]["articles_scored"])
    checks = {
        "report_complete": report.get("status") == "complete",
        "experiment_version": report.get("experiment_version") == EXPERIMENT_VERSION,
        "evaluation_role": report.get("evaluation_role") == "iterative model-family benchmark on previously observed temporal holdout",
        "train_rows": int(report["final_training"]["articles"]) == EXPECTED_TRAIN_ARTICLES,
        "train_boundary": str(report["final_training"]["end_utc"]) == TRAIN_END_UTC,
        "holdout_reconciles": scored + int(report["holdout"]["unresolved_excluded"]) == EXPECTED_HOLDOUT_ARTICLES,
        "prediction_rows": len(predictions) == scored,
        "unique_predictions": len({str(row["source_id"]) for row in predictions}) == scored,
        "disagreement_rows": len(disagreements) == int(report["disagreements"]["articles"]),
        "disagreement_flags": all(bool(row["label_disagreement"]) for row in disagreements),
        "scale_dimensions": len(scale) == int(report["dimensions"]["total"]),
        "checkpoint_dimensions": int(checkpoint["input_dim"]) == int(report["dimensions"]["total"]),
        "checkpoint_embedding": int(checkpoint["embedding_dimension"]) == EMBEDDING_DIMENSION,
        "checkpoint_deep": int(checkpoint["deep_dimension"]) == DEEP_DIMENSION,
    }
    if not all(checks.values()):
        raise ValueError(f"DeepFM validation failed: {checks}")
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
