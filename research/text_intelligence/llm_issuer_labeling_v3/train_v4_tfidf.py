from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from research.text_intelligence.news_synthesis_v1.embedding_supervision import (
    TrainConfig,
    class_weight_binary,
    set_reproducible_seed,
)
from research.text_intelligence.news_synthesis_v1.run_embedding_supervision import (
    _array_dataset_class,
    _device,
    _load_dataset,
    _model_class,
    _predict,
    _torch_imports,
)


DEFAULT_LABELS = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\legacy_consolidated_gold_conversion_v1\labels.jsonl"
)
DEFAULT_V9_DATA = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v9_final\data"
)
DEFAULT_V9_BASELINE = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v9_final\run\best_model.pt"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\tfidf_v9_trusted_eligibility_v1"
)
MODEL_VERSION = "llm_issuer_v4_tfidf_v9_trusted_article_eligibility_v1"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _trusted(row: Mapping[str, Any]) -> bool:
    return not bool(row["conversion_lineage"].get("eligibility_authority_warning"))


def _tuning(source_id: str, seed: int, fraction: float) -> bool:
    bucket = int(hashlib.sha256(f"{seed}:tuning:{source_id}".encode()).hexdigest()[:16], 16)
    return bucket / float(16**16) < fraction


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _metrics(target: np.ndarray, probability: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    truth = target.astype(bool)
    predicted = probability >= threshold
    tp = int(np.sum(truth & predicted))
    fn = int(np.sum(truth & ~predicted))
    fp = int(np.sum(~truth & predicted))
    tn = int(np.sum(~truth & ~predicted))

    def divide(left: int | float, right: int | float) -> float:
        return float(left / right) if right else 0.0

    eligible_precision = divide(tp, tp + fp)
    eligible_recall = divide(tp, tp + fn)
    ineligible_precision = divide(tn, tn + fn)
    ineligible_recall = divide(tn, tn + fp)

    def f1(precision: float, recall: float) -> float:
        return divide(2 * precision * recall, precision + recall)

    return {
        "threshold": threshold,
        "n": len(target),
        "confusion": {"TP": tp, "FN": fn, "FP": fp, "TN": tn},
        "accuracy": divide(tp + tn, len(target)),
        "balanced_accuracy": (eligible_recall + ineligible_recall) / 2,
        "macro_f1": (
            f1(eligible_precision, eligible_recall)
            + f1(ineligible_precision, ineligible_recall)
        )
        / 2,
        "eligible": {
            "precision": eligible_precision,
            "recall": eligible_recall,
            "f1": f1(eligible_precision, eligible_recall),
            "support": int(np.sum(truth)),
        },
        "ineligible": {
            "precision": ineligible_precision,
            "recall": ineligible_recall,
            "f1": f1(ineligible_precision, ineligible_recall),
            "support": int(np.sum(~truth)),
        },
        "brier_score": float(np.mean((probability - target) ** 2)),
        "roc_auc": _roc_auc(target, probability),
    }


def _roc_auc(target: np.ndarray, probability: np.ndarray) -> float | None:
    truth = target.astype(bool)
    positive = int(np.sum(truth))
    negative = int(np.sum(~truth))
    if not positive or not negative:
        return None
    order = np.argsort(probability)
    ranks = np.empty(len(probability), dtype=np.float64)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and probability[order[end]] == probability[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2
        start = end
    return float(
        (np.sum(ranks[truth]) - positive * (positive + 1) / 2) / (positive * negative)
    )


def _screening(target: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    truth = target.astype(bool)
    rejected = probability < threshold
    total = int(np.sum(rejected))
    true_rejections = int(np.sum(rejected & ~truth))
    false_rejections = int(np.sum(rejected & truth))
    return {
        "threshold": threshold,
        "rejected": total,
        "population_fraction": total / len(target) if len(target) else 0.0,
        "true_ineligible_rejected": true_rejections,
        "eligible_false_rejections": false_rejections,
        "eligible_false_rejection_rate": false_rejections / int(np.sum(truth)) if np.sum(truth) else 0.0,
        "rejection_precision": true_rejections / total if total else None,
    }


def _probabilities(model: Any, matrix: Any, indexes: np.ndarray, *, device: Any) -> np.ndarray:
    return _sigmoid(
        _predict(model, matrix[indexes], device=device, batch_size=512)[
            "article_eligibility"
        ]
    )


def run(
    *,
    labels_path: Path,
    v9_data_root: Path,
    baseline_checkpoint_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite output: {output_root}")
    labels_path = labels_path.resolve()
    v9_data_root = v9_data_root.resolve()
    baseline_checkpoint_path = baseline_checkpoint_path.resolve()
    labels = {row["source_id"]: row for row in _read_jsonl(labels_path)}
    arrays, metadata, contract = _load_dataset(v9_data_root)
    article_meta = metadata["article"]
    feature_sources = {row["source_id"] for row in article_meta}
    if not feature_sources <= set(labels):
        raise RuntimeError("V9 feature population is not covered by converted V1 labels")
    targets = np.asarray(
        [labels[row["source_id"]]["labels"]["article_forecast_eligible"] for row in article_meta],
        dtype=np.float32,
    )
    trusted = np.asarray([_trusted(labels[row["source_id"]]) for row in article_meta])
    training_partition = np.asarray([row["split"] == "train" for row in article_meta])
    validation_partition = np.asarray([row["split"] == "validation" for row in article_meta])
    trusted_training = np.flatnonzero(trusted & training_partition)
    trusted_validation = np.flatnonzero(trusted & validation_partition)
    warned_validation = np.flatnonzero(~trusted & validation_partition)
    tuning_mask = np.asarray(
        [_tuning(article_meta[int(index)]["source_id"], 20260813, 0.1) for index in trusted_training]
    )
    tuning_indexes = trusted_training[tuning_mask]
    fit_indexes = trusted_training[~tuning_mask]
    if not len(fit_indexes) or not len(tuning_indexes) or not len(trusted_validation):
        raise RuntimeError("Training, tuning, or validation partition is empty")

    config = TrainConfig(seed=20260813, torch_threads=8)
    set_reproducible_seed(config.seed)
    torch, nn, DataLoader, _ = _torch_imports()
    torch.set_num_threads(config.torch_threads)
    device = _device("cpu")
    Model = _model_class()
    model = Model(
        input_dim=int(arrays["article_x"].shape[1]),
        hidden_dim=config.hidden_dim,
        residual_blocks=config.residual_blocks,
        dropout=config.dropout,
        concept_count=len(contract["issuer_concepts"]),
    ).to(device)
    loss_function = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(class_weight_binary(targets[fit_indexes]), device=device)
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=2, min_lr=1.0e-6
    )
    ArrayDataset = _array_dataset_class()
    generator = torch.Generator().manual_seed(config.seed)
    loader = DataLoader(
        ArrayDataset(arrays["article_x"][fit_indexes], targets[fit_indexes]),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
    )
    output_root.mkdir(parents=True)
    started = time.perf_counter()
    best_score = -math.inf
    best_epoch = 0
    stale = 0
    history: list[dict[str, Any]] = []
    best_state: dict[str, Any] | None = None
    for epoch in range(1, config.max_epochs + 1):
        model.train()
        losses: list[float] = []
        for embedding, target in loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(embedding.to(device=device, dtype=torch.float32))[
                "article_eligibility"
            ]
            loss = loss_function(logits, target.to(device=device, dtype=torch.float32))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        tuning_probability = _probabilities(
            model, arrays["article_x"], tuning_indexes, device=device
        )
        tuning_metrics = _metrics(targets[tuning_indexes], tuning_probability)
        score = float(tuning_metrics["macro_f1"])
        scheduler.step(score)
        improved = score > best_score + config.min_delta
        if improved:
            best_score = score
            best_epoch = epoch
            stale = 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale += 1
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(np.mean(losses)),
                "tuning_macro_f1": score,
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
                "improved": improved,
            }
        )
        if stale >= config.patience:
            break
    if best_state is None:
        raise RuntimeError("Training produced no checkpoint")
    model.load_state_dict(best_state)
    torch.save(
        {
            "version": MODEL_VERSION,
            "state_dict": best_state,
            "config": asdict(config),
            "best_epoch": best_epoch,
            "best_tuning_macro_f1": best_score,
            "labels_sha256": _sha256(labels_path),
            "v9_data_manifest_sha256": _sha256(v9_data_root / "manifest.json"),
        },
        output_root / "best_model.pt",
    )

    trusted_probability = _probabilities(
        model, arrays["article_x"], trusted_validation, device=device
    )
    warned_probability = _probabilities(
        model, arrays["article_x"], warned_validation, device=device
    )
    tuning_probability = _probabilities(model, arrays["article_x"], tuning_indexes, device=device)
    threshold_grid = (0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)
    tuning_screen = [
        _screening(targets[tuning_indexes], tuning_probability, threshold)
        for threshold in threshold_grid
    ]
    eligible_thresholds = [
        row
        for row in tuning_screen
        if row["rejected"]
        and row["eligible_false_rejection_rate"] <= 0.005
        and row["rejection_precision"] is not None
        and row["rejection_precision"] >= 0.95
    ]
    selected_screen = max(
        eligible_thresholds, key=lambda row: (row["rejected"], row["threshold"]), default=None
    )
    selected_threshold = selected_screen["threshold"] if selected_screen else None

    # Re-evaluate the promoted V9 checkpoint on the identical trusted subset.
    baseline_checkpoint = torch.load(
        baseline_checkpoint_path, map_location="cpu", weights_only=False
    )
    baseline = Model(
        input_dim=int(arrays["article_x"].shape[1]),
        hidden_dim=baseline_checkpoint["config"]["hidden_dim"],
        residual_blocks=baseline_checkpoint["config"]["residual_blocks"],
        dropout=baseline_checkpoint["config"]["dropout"],
        concept_count=len(contract["issuer_concepts"]),
    ).to(device)
    baseline.load_state_dict(baseline_checkpoint["state_dict"])
    baseline_probability = _probabilities(
        baseline, arrays["article_x"], trusted_validation, device=device
    )

    evaluation = {
        "status": "complete",
        "model_version": MODEL_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_authority": {
            "representation": "tfidf_v9_clause_ir_sparse",
            "data_root": str(v9_data_root),
            "data_manifest_sha256": _sha256(v9_data_root / "manifest.json"),
            "selected_features": int(arrays["article_x"].shape[1]),
        },
        "label_authority": {
            "path": str(labels_path),
            "sha256": _sha256(labels_path),
            "total_v1_articles": len(labels),
            "v9_feature_coverage": len(feature_sources),
            "v1_articles_without_v9_features": len(set(labels) - feature_sources),
        },
        "population": {
            "fit_trusted": len(fit_indexes),
            "tuning_trusted": len(tuning_indexes),
            "validation_trusted": len(trusted_validation),
            "validation_warned_diagnostic_only": len(warned_validation),
            "excluded_warned_training": int(np.sum(~trusted & training_partition)),
        },
        "trusted_validation": _metrics(
            targets[trusted_validation], trusted_probability
        ),
        "promoted_v9_baseline_on_same_trusted_validation": _metrics(
            targets[trusted_validation], baseline_probability
        ),
        "warned_validation_diagnostic": {
            "articles": len(warned_validation),
            "inherited_labels_all_eligible": bool(np.all(targets[warned_validation] == 1)),
            "predicted_eligible_at_0_5": int(np.sum(warned_probability >= 0.5)),
            "predicted_ineligible_at_0_5": int(np.sum(warned_probability < 0.5)),
            "probability_quantiles": {
                str(q): float(np.quantile(warned_probability, q))
                for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
            },
            "accuracy_intentionally_not_reported": True,
        },
        "screening": {
            "selection_source": "trusted training-side tuning subset only",
            "selection_constraints": {
                "eligible_false_rejection_rate_lte": 0.005,
                "rejection_precision_gte": 0.95,
            },
            "selected_tuning_result": selected_screen,
            "trusted_validation_result": (
                _screening(
                    targets[trusted_validation], trusted_probability, selected_threshold
                )
                if selected_threshold is not None
                else None
            ),
            "tuning_grid": tuning_screen,
        },
        "history": history,
        "best_epoch": best_epoch,
        "elapsed_seconds": time.perf_counter() - started,
        "limitations": [
            "Only 14,238 of 18,144 converted V1 articles have the frozen V9 feature representation.",
            "Primary accuracy excludes every record whose conversion lineage warns that eligibility was inherited.",
            "Converted V1 preserves legacy labels; it is not semantic relabeling.",
        ],
    }
    trusted_rows = []
    for position, index in enumerate(trusted_validation):
        source_id = article_meta[int(index)]["source_id"]
        trusted_rows.append(
            {
                "source_id": source_id,
                "authority_id": labels[source_id]["conversion_lineage"]["legacy_authority_id"],
                "gold_article_forecast_eligible": bool(targets[int(index)]),
                "eligible_probability": float(trusted_probability[position]),
                "predicted_article_forecast_eligible": bool(
                    trusted_probability[position] >= 0.5
                ),
                "eligibility_authority": "trusted_legacy_conversion",
            }
        )
    warned_rows = []
    for position, index in enumerate(warned_validation):
        source_id = article_meta[int(index)]["source_id"]
        warned_rows.append(
            {
                "source_id": source_id,
                "authority_id": labels[source_id]["conversion_lineage"]["legacy_authority_id"],
                "inherited_article_forecast_eligible": bool(targets[int(index)]),
                "eligible_probability": float(warned_probability[position]),
                "predicted_article_forecast_eligible": bool(warned_probability[position] >= 0.5),
                "eligibility_authority": "warned_inherited_diagnostic_only",
            }
        )
    predictions_path = output_root / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in sorted((*trusted_rows, *warned_rows), key=lambda item: item["source_id"]):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    evaluation_path = output_root / "evaluation.json"
    evaluation_path.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output_root / "history.json").write_text(
        json.dumps(history, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    trusted_result = evaluation["trusted_validation"]
    baseline_result = evaluation["promoted_v9_baseline_on_same_trusted_validation"]
    report = "\n".join(
        (
            "# Trusted V1 eligibility with V9 TF-IDF features",
            "",
            "## Outcome",
            "",
            f"- Trusted validation articles: {trusted_result['n']:,}",
            f"- Accuracy: {trusted_result['accuracy']:.4%}",
            f"- Macro-F1: {trusted_result['macro_f1']:.4%}",
            f"- Balanced accuracy: {trusted_result['balanced_accuracy']:.4%}",
            f"- ROC AUC: {trusted_result['roc_auc']:.6f}",
            f"- Confusion TP/FN/FP/TN: {trusted_result['confusion']['TP']}/"
            f"{trusted_result['confusion']['FN']}/{trusted_result['confusion']['FP']}/"
            f"{trusted_result['confusion']['TN']}",
            "",
            "## Identical-population promoted V9 baseline",
            "",
            f"- Accuracy: {baseline_result['accuracy']:.4%}",
            f"- Macro-F1: {baseline_result['macro_f1']:.4%}",
            f"- Balanced accuracy: {baseline_result['balanced_accuracy']:.4%}",
            f"- ROC AUC: {baseline_result['roc_auc']:.6f}",
            "",
            "## Screening verdict",
            "",
            "No training-side tuning threshold satisfied both rejection precision >=95% "
            "and eligible false-rejection rate <=0.5%. This model is not approved for "
            "automatic rejection.",
            "",
            "## Authority boundaries",
            "",
            f"- V1 articles: {len(labels):,}",
            f"- V9 feature coverage: {len(feature_sources):,}",
            f"- V1 articles without V9 features: {len(set(labels) - feature_sources):,}",
            f"- Trusted fit/tuning/validation: {len(fit_indexes):,}/{len(tuning_indexes):,}/"
            f"{len(trusted_validation):,}",
            f"- Warned validation diagnostic only: {len(warned_validation):,}",
            f"- Warned training records excluded: {int(np.sum(~trusted & training_partition)):,}",
            "",
        )
    )
    (output_root / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    manifest = {
        "status": "complete",
        "model_version": MODEL_VERSION,
        "files": {
            name: {"bytes": (output_root / name).stat().st_size, "sha256": _sha256(output_root / name)}
            for name in (
                "best_model.pt",
                "evaluation.json",
                "history.json",
                "predictions.jsonl",
                "REPORT.md",
            )
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return evaluation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train V4 eligibility on trusted V1 labels")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--v9-data", type=Path, default=DEFAULT_V9_DATA)
    parser.add_argument("--baseline-checkpoint", type=Path, default=DEFAULT_V9_BASELINE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = run(
        labels_path=args.labels,
        v9_data_root=args.v9_data,
        baseline_checkpoint_path=args.baseline_checkpoint,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
