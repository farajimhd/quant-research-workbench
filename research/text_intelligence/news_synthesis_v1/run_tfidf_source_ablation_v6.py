from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .embedding_supervision import DEFAULT_DATA_ROOT, write_json
from .run_embedding_supervision import train_model
from .run_tfidf_supervision_v2 import _metrics
from .run_tfidf_supervision_v5 import _train_args
from .tfidf_source_ablation_v6 import (
    DEFAULT_RAW_DRIVE_ROOT,
    DEFAULT_TFIDF_V6_ROOT,
    LANES,
    prepare_source_ablation_v6,
)
from .run_embedding_supervision import _clickhouse_client


def build_result(prepare: dict[str, Any], trained: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metrics = {lane: _metrics(trained[lane]) for lane in LANES}
    original = metrics["original_provider"]
    normalized = metrics["normalized_provider"]
    rendered = metrics["rendered_provider"]

    def delta(left: MappingLike, right: MappingLike, key: str) -> float:
        return float(left[key]) - float(right[key])

    comparisons = {}
    for name, left, right in (
        ("original_minus_normalized", original, normalized),
        ("original_minus_rendered", original, rendered),
        ("rendered_minus_normalized", rendered, normalized),
    ):
        comparisons[name] = {
            "article_accuracy": delta(left, right, "article_eligibility_accuracy"),
            "article_macro_f1": delta(left, right, "article_eligibility_macro_f1"),
            "issuer_accuracy": delta(left, right, "issuer_eligibility_accuracy"),
            "issuer_macro_f1": delta(left, right, "issuer_eligibility_macro_f1"),
            "sentiment_accuracy": delta(left, right, "issuer_sentiment_accuracy"),
            "sentiment_macro_f1": delta(left, right, "issuer_sentiment_macro_f1"),
            "concept_subset_accuracy": delta(left, right, "concept_subset_accuracy"),
            "concept_micro_f1": delta(left, right, "concept_micro_f1"),
            "concept_macro_f1": delta(left, right, "concept_macro_f1"),
        }
    return {
        "status": "complete",
        "experiment": prepare["experiment"],
        "population": prepare["population"],
        "controls": prepare["controls"],
        "lane_definitions": prepare["lane_definitions"],
        "metrics": metrics,
        "deltas": comparisons,
        "selected_dimensions": {
            lane: int(prepare["lanes"][lane]["selected_features"]) for lane in LANES
        },
    }


MappingLike = dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a controlled News Synthesis TF-IDF source-representation ablation "
            "over exact frozen provider artifacts."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V6_ROOT)
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
    client = _clickhouse_client(args)
    prepare = prepare_source_ablation_v6(
        source_data_root=args.source_data_root,
        output_root=args.root,
        client=client,
        raw_drive_root=args.raw_drive_root,
        source_database=args.source_database,
        identity_database=args.identity_database,
        min_document_frequency=args.min_document_frequency,
        source_batch_size=args.source_batch_size,
    )
    trained: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        data_root = args.root / "data" / lane
        run_root = args.root / "run" / lane
        trained[lane] = train_model(_train_args(data_root, run_root, args.torch_threads))
    result = build_result(prepare, trained)
    write_json(args.root / "comparison.json", result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
