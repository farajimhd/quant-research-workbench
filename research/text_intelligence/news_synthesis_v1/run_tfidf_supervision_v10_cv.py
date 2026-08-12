from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .embedding_supervision import DEFAULT_DATA_ROOT, TFIDF_V9_DATASET_VERSION, TFIDF_V10_DATASET_VERSION
from .run_tfidf_supervision_v7_cv import (
    DEFAULT_CV_SEED,
    DEFAULT_FOLDS,
    DEFAULT_NEWS_SYNTHESIS_AUDIT_DOCUMENTS,
    DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT,
    CrossValidationFeatureSpec,
    run_cross_validation,
)
from .tfidf_supervision_v5 import DEFAULT_RAW_DRIVE_ROOT
from .tfidf_supervision_v9 import V9_FIELD_BUDGETS, tfidf_v9_feature_counts, v9_view_indexes
from .tfidf_supervision_v10 import (
    V10_FIELD_BUDGETS,
    fit_v10_stable_vocabulary,
    tfidf_v10_feature_counts,
    v10_view_indexes,
)


DEFAULT_RUNTIME_ROOT = (
    Path(r"D:\TradingML\runtimes") / "text_intelligence" / "news_synthesis_v1"
)
V9_CV_FEATURE_SPEC = CrossValidationFeatureSpec(
    dataset_version=TFIDF_V9_DATASET_VERSION,
    experiment="tfidf_v9_clause_ir_grouped_stratified_cv5",
    comparison_key="tfidf_v9_cross_validated",
    representation_kind="tfidf_v9_clause_ir_sparse",
    feature_counter=tfidf_v9_feature_counts,
    budgets=V9_FIELD_BUDGETS,
    view_indexes=v9_view_indexes,
    feature_metadata={"feature_version": "v9", "feature_only_change": True},
)
V10_CV_FEATURE_SPEC = CrossValidationFeatureSpec(
    dataset_version=TFIDF_V10_DATASET_VERSION,
    experiment="tfidf_v10_relational_stable_grouped_stratified_cv5",
    comparison_key="tfidf_v10_cross_validated",
    representation_kind="tfidf_v10_relational_stable",
    feature_counter=tfidf_v10_feature_counts,
    budgets=V10_FIELD_BUDGETS,
    view_indexes=v10_view_indexes,
    vocabulary_fitter=fit_v10_stable_vocabulary,
    feature_metadata={
        "feature_version": "v10",
        "feature_only_change_from_v9": True,
        "metric_value_comparison_relations": True,
        "clause_sentiment_composition": True,
        "aligned_cross_view_evidence": True,
        "separate_structured_view_normalization": True,
        "issuer_local_position_and_density": True,
        "training_only_stability_selection": True,
        "same_total_feature_budget_as_v9": True,
        "gold_or_prediction_features": False,
    },
)


def _arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--v7-data-root", type=Path, default=DEFAULT_RUNTIME_ROOT / "tfidf_supervision_v7" / "data"
    )
    parser.add_argument("--raw-drive-root", type=Path, default=DEFAULT_RAW_DRIVE_ROOT)
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--cv-seed", default=DEFAULT_CV_SEED)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    parser.add_argument(
        "--news-synthesis-audit-documents",
        type=Path,
        nargs="+",
        default=[
            DEFAULT_NEWS_SYNTHESIS_AUDIT_DOCUMENTS,
            DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT
            / "evaluation_current_development_test"
            / "audit_documents.jsonl",
            DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT
            / "evaluation_current_final_test"
            / "audit_documents.jsonl",
        ],
    )


def _per_label_folds(root: Path, section: str, folds: int) -> dict[str, dict[str, float]]:
    reports = [
        json.loads((root / "run" / f"fold_{fold}" / "evaluation.json").read_text(encoding="utf-8"))
        for fold in range(folds)
    ]
    labels = reports[0][section]["per_label"]
    return {
        label: {
            metric: float(np.mean([report[section]["per_label"][label][metric] for report in reports]))
            for metric in ("precision", "recall", "f1", "support")
        }
        for label in labels
    }
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate feature-only TF-IDF V9 and V10 on identical grouped training-partition folds."
    )
    _arguments(parser)
    parser.add_argument("--v9-root", type=Path, default=DEFAULT_RUNTIME_ROOT / "tfidf_supervision_v9_train_cv5")
    parser.add_argument("--v10-root", type=Path, default=DEFAULT_RUNTIME_ROOT / "tfidf_supervision_v10_train_cv5")
    parser.add_argument("--reuse-v9", action="store_true")
    parser.add_argument(
        "--prior-v10-evaluations",
        type=int,
        default=0,
        help="Earlier V10 evaluations superseded solely for implementation defects.",
    )
    args = parser.parse_args()
    if args.reuse_v9:
        v9 = json.loads((args.v9_root / "cross_validation.json").read_text(encoding="utf-8"))
    else:
        args.root = args.v9_root
        args.prior_cv_path = None
        v9 = run_cross_validation(args, feature_spec=V9_CV_FEATURE_SPEC)
    args.root = args.v10_root
    args.prior_cv_path = args.v9_root / "cross_validation.json"
    v10 = run_cross_validation(args, feature_spec=V10_CV_FEATURE_SPEC)
    comparison = {
        "status": "complete",
        "evaluation_authority": "identical_grouped_folds_within_original_75_percent_training_partition",
        "official_validation_used": False,
        "v10_family_evaluation_count": 1 + args.prior_v10_evaluations,
        "superseded_implementation_defect_run_preserved": bool(
            args.prior_v10_evaluations
        ),
        "same_model_and_training_configuration": True,
        "same_fold_assignments": v10["leakage_controls"]["same_fold_assignments_as_prior_cv"],
        "tfidf_v9": v9["aggregate"],
        "tfidf_v10": v10["aggregate"],
        "mean_deltas_v10_minus_v9": {
            metric: v10["aggregate"][metric]["mean"] - v9["aggregate"][metric]["mean"]
            for metric in v9["aggregate"]
        },
        "per_label_fold_means": {
            "tfidf_v9": {
                "sentiment": _per_label_folds(args.v9_root, "issuer_sentiment", args.folds),
                "concepts": _per_label_folds(args.v9_root, "issuer_concepts", args.folds),
            },
            "tfidf_v10": {
                "sentiment": _per_label_folds(args.v10_root, "issuer_sentiment", args.folds),
                "concepts": _per_label_folds(args.v10_root, "issuer_concepts", args.folds),
            },
        },
    }
    from .embedding_supervision import write_json

    write_json(args.v10_root / "comparison.json", comparison)
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
