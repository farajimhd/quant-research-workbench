from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, DEFAULT_RUN_ROOT, write_json
from .representation_comparison import (
    prepare_openai_dataset,
    subset_to_common_authority,
)
from .run_embedding_supervision import _clickhouse_client, train_model
from .tfidf_supervision import prepare_tfidf_dataset


ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "representation_comparison_v1"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a controlled Qwen, TF-IDF, and OpenAI News Synthesis comparison.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--qwen-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--qwen-full-run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--max-features", type=int, default=4096)
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    args = parser.parse_args()
    root = args.root.resolve()
    tfidf_full = root / "full" / "tfidf" / "data"
    openai_common = root / "common" / "openai" / "data"
    qwen_common = root / "common" / "qwen" / "data"
    tfidf_common = root / "common" / "tfidf" / "data"
    client = _clickhouse_client(args)
    if not (openai_common / "VALIDATION.json").is_file():
        prepare_openai_dataset(source_root=args.qwen_data_root, output_root=openai_common, client=client)
    if not (qwen_common / "VALIDATION.json").is_file():
        subset_to_common_authority(
            source_root=args.qwen_data_root, authority_root=openai_common,
            output_root=qwen_common, representation="qwen",
        )
    if not (tfidf_full / "VALIDATION.json").is_file():
        prepare_tfidf_dataset(
            source_data_root=args.qwen_data_root, output_root=tfidf_full, client=client,
            max_features=args.max_features,
            min_document_frequency=args.min_document_frequency,
        )
    if not (tfidf_common / "VALIDATION.json").is_file():
        prepare_tfidf_dataset(
            source_data_root=qwen_common, output_root=tfidf_common, client=client,
            max_features=args.max_features,
            min_document_frequency=args.min_document_frequency,
        )
    full_tfidf_report = _train_or_load(
        tfidf_full, root / "full" / "tfidf" / "run", args.torch_threads
    )
    reports = {}
    for name, data_root in (
        ("qwen", qwen_common),
        ("tfidf", tfidf_common),
        ("openai", openai_common),
    ):
        reports[name] = _train_or_load(
            data_root, root / "common" / name / "run", args.torch_threads
        )
    full_qwen = json.loads((args.qwen_full_run_root / "evaluation.json").read_text(encoding="utf-8"))
    comparison = {
        "status": "complete",
        "comparison_boundary": (
            "All three common-cohort models use identical article and issuer units, frozen "
            "train/validation assignments, grouped tuning, architecture, losses, and metrics."
        ),
        "full_population": {
            "qwen": _metrics(full_qwen),
            "tfidf": _metrics(full_tfidf_report),
        },
        "common_cohort": {name: _metrics(report) for name, report in reports.items()},
        "coverage": {
            name: report["dataset_validation"] for name, report in reports.items()
        },
    }
    write_json(root / "comparison.json", comparison)
    print(json.dumps(comparison, indent=2), flush=True)
    return 0


def _train_args(data_root: Path, run_root: Path, torch_threads: int) -> argparse.Namespace:
    return argparse.Namespace(
        data_root=data_root, run_root=run_root, seed=20260812, hidden_dim=384,
        residual_blocks=2, dropout=0.20, batch_size=256, learning_rate=8.0e-4,
        weight_decay=1.0e-4, max_epochs=50, patience=7, min_delta=1.0e-4,
        tuning_fraction=0.10, sentiment_loss_weight=0.75, concept_loss_weight=1.0,
        workers=0, torch_threads=torch_threads, device="auto",
    )


def _train_or_load(data_root: Path, run_root: Path, torch_threads: int) -> dict:
    evaluation = run_root / "evaluation.json"
    if evaluation.is_file():
        return json.loads(evaluation.read_text(encoding="utf-8"))
    return train_model(_train_args(data_root, run_root, torch_threads))


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
