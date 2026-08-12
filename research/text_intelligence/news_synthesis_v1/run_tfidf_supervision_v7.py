from __future__ import annotations

import argparse
import json
from pathlib import Path

from .embedding_supervision import DEFAULT_DATA_ROOT, write_json
from .run_embedding_supervision import _clickhouse_client, train_model
from .run_tfidf_supervision_v2 import _metrics
from .run_tfidf_supervision_v5 import _train_args
from .tfidf_supervision_v5 import DEFAULT_RAW_DRIVE_ROOT
from .tfidf_supervision_v7 import DEFAULT_TFIDF_V7_ROOT, prepare_tfidf_v7_dataset


def build_comparison(v7_report: dict) -> dict:
    runtime = Path(r"D:\TradingML\runtimes") / "text_intelligence" / "news_synthesis_v1"
    v6 = json.loads(
        (runtime / "tfidf_source_ablation_v6" / "comparison.json").read_text(encoding="utf-8")
    )
    metrics = dict(v6["metrics"])
    metrics["provenance_multiview_v7"] = _metrics(v7_report)
    shared_controls = {
        key: value
        for key, value in v6["controls"].items()
        if key not in {"external_pdf_metadata_excluded", "same_feature_extractor_and_budgets"}
    }
    return {
        "status": "complete",
        "population": v6["population"],
        "experimental_control": {
            **shared_controls,
            "same_exact_population_as_v6": True,
            "same_model_and_training_as_v6": True,
            "feature_only_change": True,
            "validation_driven_iteration": False,
            "v6_baseline_lanes_exclude_enrichment_and_metadata": True,
            "v7_includes_gated_external_pdf_enrichment": True,
            "v7_includes_invariant_original_metadata": True,
            "v7_uses_normalized_provider_semantics": True,
        },
        "representations": {
            **v6["lane_definitions"],
            "provenance_multiview_v7": (
                "original provider lexical and invariant metadata, normalized provider semantic "
                "view, and issuer-local provenance-separated external/PDF views"
            ),
        },
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, train, and evaluate News Synthesis TF-IDF V7 from original text, "
            "invariant metadata, normalized semantics, and gated enrichment."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V7_ROOT)
    parser.add_argument("--raw-drive-root", type=Path, default=DEFAULT_RAW_DRIVE_ROOT)
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    args = parser.parse_args()
    data_root = args.root / "data"
    run_root = args.root / "run"
    client = _clickhouse_client(args)
    prepare = prepare_tfidf_v7_dataset(
        source_data_root=args.source_data_root,
        output_root=data_root,
        client=client,
        raw_drive_root=args.raw_drive_root,
        source_database=args.source_database,
        identity_database=args.identity_database,
        min_document_frequency=args.min_document_frequency,
        source_batch_size=args.source_batch_size,
    )
    train = train_model(_train_args(data_root, run_root, args.torch_threads))
    comparison = build_comparison(train)
    write_json(args.root / "comparison.json", comparison)
    print(json.dumps({"prepare": prepare, "train": train, "comparison": comparison}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
