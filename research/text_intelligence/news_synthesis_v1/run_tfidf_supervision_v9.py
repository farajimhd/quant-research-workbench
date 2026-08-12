from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import write_json
from .run_embedding_supervision import _clickhouse_client, train_model
from .run_tfidf_supervision_v2 import _metrics
from .run_tfidf_supervision_v5 import _train_args
from .tfidf_supervision_v5 import DEFAULT_RAW_DRIVE_ROOT
from .tfidf_supervision_v9 import (
    DEFAULT_TFIDF_V9_ROOT,
    prepare_v8_sparse_dataset,
    prepare_v9_sparse_dataset,
)


DEFAULT_TFIDF_V7_DATA_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v7"
    / "data"
)
DEFAULT_TFIDF_V8_OFFICIAL_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v8_official_validation"
)
DEFAULT_TFIDF_V7_EVALUATION = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v7"
    / "run"
    / "evaluation.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare sparse V8/V9 features, train the unchanged multitask MLP, and "
            "evaluate once on the untouched official validation partition."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_TFIDF_V7_DATA_ROOT)
    parser.add_argument("--v8-root", type=Path, default=DEFAULT_TFIDF_V8_OFFICIAL_ROOT)
    parser.add_argument("--v9-root", type=Path, default=DEFAULT_TFIDF_V9_ROOT)
    parser.add_argument("--v7-evaluation", type=Path, default=DEFAULT_TFIDF_V7_EVALUATION)
    parser.add_argument("--raw-drive-root", type=Path, default=DEFAULT_RAW_DRIVE_ROOT)
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    parser.add_argument("--reuse-existing-v8", action="store_true")
    parser.add_argument(
        "--prior-v9-evaluations",
        type=int,
        default=0,
        help="Earlier V9-family validation executions superseded for implementation defects.",
    )
    args = parser.parse_args()
    if args.v9_root.exists() or (args.v8_root.exists() and not args.reuse_existing_v8):
        raise RuntimeError("Refusing to overwrite an existing V8 or V9 official evaluation root")
    client = _clickhouse_client(args)
    if args.reuse_existing_v8:
        v8_prepare = {
            "manifest": json.loads(
                (args.v8_root / "data" / "manifest.json").read_text(encoding="utf-8")
            )
        }
        v8_train = json.loads(
            (args.v8_root / "run" / "evaluation.json").read_text(encoding="utf-8")
        )
    else:
        v8_prepare = prepare_v8_sparse_dataset(
            source_data_root=args.source_data_root,
            output_root=args.v8_root / "data",
            client=client,
            raw_drive_root=args.raw_drive_root,
            source_database=args.source_database,
            min_document_frequency=args.min_document_frequency,
            source_batch_size=args.source_batch_size,
        )
        v8_train = train_model(
            _train_args(args.v8_root / "data", args.v8_root / "run", args.torch_threads)
        )
    v9_prepare = prepare_v9_sparse_dataset(
        source_data_root=args.source_data_root,
        output_root=args.v9_root / "data",
        client=client,
        raw_drive_root=args.raw_drive_root,
        source_database=args.source_database,
        min_document_frequency=args.min_document_frequency,
        source_batch_size=args.source_batch_size,
    )
    v9_train = train_model(
        _train_args(args.v9_root / "data", args.v9_root / "run", args.torch_threads)
    )
    v7 = json.loads(args.v7_evaluation.read_text(encoding="utf-8"))
    comparison = {
        "status": "complete",
        "evaluation_authority": "untouched_official_25_percent_validation",
        "selection_policy": (
            "The V9 feature design was frozen before evaluation; no validation metrics "
            "were used for feature selection. Any declared prior executions were "
            "superseded only for implementation defects."
        ),
        "population": {
            "train_articles": v9_train["dataset_validation"]["train_articles"],
            "validation_articles": v9_train["dataset_validation"]["validation_articles"],
            "issuer_units": v9_train["dataset_validation"]["issuer_units"],
        },
        "controls": {
            "same_model_and_training_configuration": v8_train["config"] == v9_train["config"],
            "same_official_split": True,
            "same_source_and_label_authority": True,
            "feature_only_change_v8_to_v9": True,
            "sparse_storage_only_changes_materialization": True,
            "official_validation_used_for_feature_tuning": False,
            "official_validation_evaluation_count_v9_family": 1
            + args.prior_v9_evaluations,
            "superseded_implementation_defect_run_preserved": bool(
                args.prior_v9_evaluations
            ),
            "v8_runtime_reused_without_retraining": bool(args.reuse_existing_v8),
        },
        "metrics": {
            "tfidf_v7": _metrics(v7),
            "tfidf_v8": _metrics(v8_train),
            "tfidf_v9": _metrics(v9_train),
        },
        "preparation": {
            "v8": v8_prepare["manifest"]["performance"],
            "v9": v9_prepare["manifest"]["performance"],
        },
    }
    write_json(args.v9_root / "comparison.json", comparison)
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
