from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, write_json
from .run_embedding_supervision import _clickhouse_client, train_model
from .run_tfidf_supervision_v2 import _metrics
from .tfidf_supervision_v3 import DEFAULT_TFIDF_V3_ROOT, prepare_tfidf_v3_dataset


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, train, and evaluate feature-only News Synthesis TF-IDF V3 "
            "with the frozen V2 multi-task MLP."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V3_ROOT)
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    args = parser.parse_args()
    data_root = args.root / "data"
    run_root = args.root / "run"
    client = _clickhouse_client(args)
    prepare = prepare_tfidf_v3_dataset(
        source_data_root=args.source_data_root,
        output_root=data_root,
        client=client,
        identity_database=args.identity_database,
        min_document_frequency=args.min_document_frequency,
    )
    train = train_model(_train_args(data_root, run_root, args.torch_threads))
    comparison = build_comparison(train)
    write_json(args.root / "comparison.json", comparison)
    print(json.dumps({"prepare": prepare, "train": train, "comparison": comparison}, indent=2))
    return 0


def _train_args(data_root: Path, run_root: Path, torch_threads: int) -> argparse.Namespace:
    # Deliberately byte-for-byte equivalent hyperparameters to V2. V3 changes
    # only the input feature representation and therefore input dimension.
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


def build_comparison(v3_report: dict) -> dict:
    runtime = Path(r"D:\TradingML\runtimes") / "text_intelligence" / "news_synthesis_v1"
    v2_comparison = json.loads(
        (runtime / "tfidf_supervision_v2" / "comparison.json").read_text(encoding="utf-8")
    )
    models = dict(v2_comparison["models"])
    models["tfidf_v3"] = _metrics(v3_report)
    return {
        "status": "complete",
        "population": "identical 14,253-article frozen Qwen-complete population",
        "experimental_control": {
            "feature_change_only": True,
            "same_split_as_v2": True,
            "same_model_architecture_and_training_as_v2": True,
            "validation_driven_feature_iteration": False,
        },
        "models": models,
    }


if __name__ == "__main__":
    raise SystemExit(main())
