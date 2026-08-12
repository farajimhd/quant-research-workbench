from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, TFIDF_V8_DATASET_VERSION
from .run_tfidf_supervision_v7_cv import (
    DEFAULT_CV_SEED,
    DEFAULT_FOLDS,
    DEFAULT_NEWS_SYNTHESIS_AUDIT_DOCUMENTS,
    DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT,
    CrossValidationFeatureSpec,
    run_cross_validation,
)
from .tfidf_supervision_v5 import DEFAULT_RAW_DRIVE_ROOT
from .tfidf_supervision_v8 import (
    V8_FIELD_BUDGETS,
    tfidf_v8_feature_counts,
    v8_view_indexes,
)


DEFAULT_TFIDF_V8_CV_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v8_cv5"
)
DEFAULT_TFIDF_V7_CV_REPORT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v7_cv5"
    / "cross_validation.json"
)
V8_CV_FEATURE_SPEC = CrossValidationFeatureSpec(
    dataset_version=TFIDF_V8_DATASET_VERSION,
    experiment="tfidf_v8_entity_clause_invariant_grouped_stratified_cv5",
    comparison_key="tfidf_v8_cross_validated",
    representation_kind="tfidf_v8_entity_clause_invariant",
    feature_counter=tfidf_v8_feature_counts,
    budgets=V8_FIELD_BUDGETS,
    view_indexes=v8_view_indexes,
    feature_metadata={
        "feature_version": "v8",
        "feature_only_change_from_v7": True,
        "target_clause_masking": True,
        "role_currentness_event_direction_interactions": True,
        "structured_numeric_types": True,
        "same_total_feature_budget_as_v7": True,
        "gold_or_prediction_features": False,
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run grouped stratified cross-validation for feature-only News Synthesis "
            "TF-IDF V8 with per-fold vocabulary and IDF fitting."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--v7-data-root",
        type=Path,
        default=(
            Path(r"D:\TradingML\runtimes")
            / "text_intelligence"
            / "news_synthesis_v1"
            / "tfidf_supervision_v7"
            / "data"
        ),
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V8_CV_ROOT)
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
    parser.add_argument("--prior-cv-path", type=Path, default=DEFAULT_TFIDF_V7_CV_REPORT)
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
    args = parser.parse_args()
    print(json.dumps(run_cross_validation(args, feature_spec=V8_CV_FEATURE_SPEC), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
