from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, write_json
from .run_embedding_supervision import _clickhouse_client, train_model
from .tfidf_supervision_v2 import DEFAULT_TFIDF_V2_ROOT, prepare_tfidf_v2_dataset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, train, and evaluate field-aware News Synthesis TF-IDF V2."
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V2_ROOT)
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    args = parser.parse_args()
    data_root = args.root / "data"
    run_root = args.root / "run"
    client = _clickhouse_client(args)
    prepare = prepare_tfidf_v2_dataset(
        source_data_root=args.source_data_root,
        output_root=data_root,
        client=client,
        min_document_frequency=args.min_document_frequency,
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


def build_comparison(v2_report: dict) -> dict:
    comparison_root = (
        Path(r"D:\TradingML\runtimes")
        / "text_intelligence"
        / "news_synthesis_v1"
        / "representation_comparison_v1"
    )
    qwen = json.loads(
        (
            Path(r"D:\TradingML\runtimes")
            / "text_intelligence"
            / "news_synthesis_v1"
            / "qwen_embedding_supervision_v1"
            / "run"
            / "evaluation.json"
        ).read_text(encoding="utf-8")
    )
    v1 = json.loads(
        (comparison_root / "full" / "tfidf" / "run" / "evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "status": "complete",
        "population": "identical 14,253-article frozen Qwen-complete population",
        "models": {
            "qwen": _metrics(qwen),
            "tfidf_v1": _metrics(v1),
            "tfidf_v2": _metrics(v2_report),
        },
    }


def _metrics(report: dict) -> dict:
    return {
        "article_eligibility_accuracy": report["article_forecast_eligibility"]["accuracy"],
        "article_eligibility_macro_f1": report["article_forecast_eligibility"]["macro_f1"],
        "issuer_eligibility_accuracy": report["issuer_forecast_eligibility"]["accuracy"],
        "issuer_eligibility_macro_f1": report["issuer_forecast_eligibility"]["macro_f1"],
        "issuer_sentiment_accuracy": report["issuer_sentiment"]["accuracy"],
        "issuer_sentiment_macro_f1": report["issuer_sentiment"]["macro_f1"],
        "concept_subset_accuracy": report["issuer_concepts"]["subset_accuracy"],
        "concept_micro_f1": report["issuer_concepts"]["micro_f1"],
        "concept_macro_f1": report["issuer_concepts"]["macro_f1_supported_labels"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
