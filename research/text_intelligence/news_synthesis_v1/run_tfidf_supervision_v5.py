from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, write_json
from .run_embedding_supervision import _clickhouse_client, train_model
from .run_tfidf_supervision_v2 import _metrics
from .tfidf_supervision_v5 import (
    DEFAULT_RAW_DRIVE_ROOT,
    DEFAULT_TFIDF_V5_ROOT,
    prepare_tfidf_v5_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, train, and evaluate News Synthesis TF-IDF V5 from retained "
            "original provider title/body/metadata with the frozen V4 model."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V5_ROOT)
    parser.add_argument("--raw-drive-root", type=Path, default=DEFAULT_RAW_DRIVE_ROOT)
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument(
        "--allow-revised-original-artifacts",
        action="store_true",
        help=(
            "Permit current provider artifacts whose retained hash drifted only "
            "when provider ID, original publication timestamp, and title still agree."
        ),
    )
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    args = parser.parse_args()
    data_root = args.root / "data"
    run_root = args.root / "run"
    client = _clickhouse_client(args)
    prepare = prepare_tfidf_v5_dataset(
        source_data_root=args.source_data_root,
        output_root=data_root,
        client=client,
        raw_drive_root=args.raw_drive_root,
        source_database=args.source_database,
        identity_database=args.identity_database,
        min_document_frequency=args.min_document_frequency,
        source_batch_size=args.source_batch_size,
        allow_revised_original_artifacts=args.allow_revised_original_artifacts,
    )
    train = train_model(_train_args(data_root, run_root, args.torch_threads))
    comparison = build_comparison(train)
    write_json(args.root / "comparison.json", comparison)
    print(json.dumps({"prepare": prepare, "train": train, "comparison": comparison}, indent=2))
    return 0


def _train_args(data_root: Path, run_root: Path, torch_threads: int) -> argparse.Namespace:
    return argparse.Namespace(
        data_root=data_root,
        run_root=run_root,
        seed=20260812,
        hidden_dim=384,
        residual_blocks=2,
        dropout=0.20,
        batch_size=256,
        learning_rate=8.0e-4,
        weight_decay=1.0e-4,
        max_epochs=50,
        patience=7,
        min_delta=1.0e-4,
        tuning_fraction=0.10,
        sentiment_loss_weight=0.75,
        concept_loss_weight=1.0,
        workers=0,
        torch_threads=torch_threads,
        device="auto",
    )


def build_comparison(v5_report: dict) -> dict:
    runtime = Path(r"D:\TradingML\runtimes") / "text_intelligence" / "news_synthesis_v1"
    v4_comparison = json.loads(
        (runtime / "tfidf_supervision_v4" / "comparison.json").read_text(encoding="utf-8")
    )
    models = dict(v4_comparison["models"])
    models["tfidf_v5"] = _metrics(v5_report)
    return {
        "status": "complete",
        "population": "identical 14,253-article frozen Qwen-complete population",
        "experimental_control": {
            "original_provider_source_v5": True,
            "normalized_text_fields_used": False,
            "qwen_tokenizer_dependency": False,
            "same_split_as_v4": True,
            "same_model_architecture_and_training_as_v4": True,
            "validation_driven_iteration": False,
            "revised_original_artifact_policy": "explicit_identity_verified_hash_drift",
        },
        "models": models,
    }


if __name__ == "__main__":
    raise SystemExit(main())
