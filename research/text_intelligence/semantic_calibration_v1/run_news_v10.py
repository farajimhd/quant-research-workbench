from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib

from research.text_intelligence.scoped_labeling_v1.schema import NEWS_EXTRACTOR_VERSION
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    ISSUER_IDENTITY_AUTHORITY_VERSION,
    NewsIssuerResolver,
)

from .comparison import evaluate_predictions, load_collection
from .news_v10 import (
    DEFAULT_HUMAN_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_TEACHER_ROOT,
    V10_VERSION,
    ForestConfig,
    config_dict,
    file_sha256,
    fit_v10,
    generate_human_predictions,
    load_teacher_articles,
)
from .run_deterministic_news_v6 import _headline
from .run_deterministic_news_v9 import (
    _predict as predict_v9,
    load_v9_issuer_authority,
)
from .deterministic_v9_config import DETERMINISTIC_V9_VERSION
from .storage import assert_runtime_root, read_json, write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train TF-IDF bagged random-forest News V10 and compare it with deterministic V9."
    )
    parser.add_argument("--teacher-root", type=Path, default=DEFAULT_TEACHER_ROOT)
    parser.add_argument("--human-root", type=Path, default=DEFAULT_HUMAN_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=-1)
    parser.add_argument("--article-trees", type=int, default=192)
    parser.add_argument("--unit-trees", type=int, default=192)
    parser.add_argument("--concept-trees", type=int, default=96)
    parser.add_argument("--force-train", action="store_true")
    args = parser.parse_args()
    assert_runtime_root(args.output_root)
    config = ForestConfig(
        workers=args.workers,
        article_trees=args.article_trees,
        unit_trees=args.unit_trees,
        concept_trees=args.concept_trees,
    )
    artifact_path = args.output_root / "model.joblib"
    if artifact_path.exists() and not args.force_train:
        print(f"V10 loading existing artifact {artifact_path}", flush=True)
        model = joblib.load(artifact_path)
        if model.version != V10_VERSION or model.config != config:
            raise RuntimeError("Existing V10 artifact configuration differs; use --force-train explicitly.")
    else:
        articles = load_teacher_articles(args.teacher_root)
        model = fit_v10(articles, config=config, artifact_path=artifact_path)
    items = load_collection(args.human_root)
    if len(items) != 1_000:
        raise RuntimeError(f"Expected 1,000 human articles; found {len(items):,}")
    v10_predictions = args.output_root / "human_predictions_v10"
    generate_human_predictions(model, items, output_dir=v10_predictions)
    v9_predictions = args.output_root / "human_predictions_v9"
    issuer_resolver = load_v9_issuer_authority()
    _generate_v9(items, v9_predictions, issuer_resolver=issuer_resolver)
    v10_report = evaluate_predictions(
        items, prediction_dir=v10_predictions, canonical_concepts=True
    )
    v9_report = evaluate_predictions(
        items, prediction_dir=v9_predictions, canonical_concepts=True
    )
    comparison = {
        "version": V10_VERSION,
        "training_authority": {
            "teacher_root": str(args.teacher_root),
            "valid_articles": model.training_articles,
            "candidate_rows": model.training_candidates,
            "issuer_units": model.training_issuer_units,
            "human_labels_used_for_training": 0,
        },
        "evaluation_authority": {
            "human_root": str(args.human_root),
            "articles": len(items),
        },
        "config": config_dict(config),
        "artifact": {
            "path": str(artifact_path),
            "bytes": artifact_path.stat().st_size,
            "sha256": file_sha256(artifact_path),
        },
        "v10": v10_report,
        "v9": v9_report,
        "headline": {
            "v10": _headline(v10_report),
            "v9": _headline(v9_report),
            "delta_v10_minus_v9": _delta(_headline(v10_report), _headline(v9_report)),
        },
    }
    write_json_atomic(args.output_root / "evaluation.json", comparison)
    print(json.dumps(comparison["headline"], indent=2), flush=True)
    return 0


def _generate_v9(
    items,
    output_dir: Path,
    *,
    issuer_resolver: NewsIssuerResolver,
) -> None:
    assert_runtime_root(output_dir)
    for index, item in enumerate(items, 1):
        target = output_dir / f"{item.sample_id}.json"
        if target.exists():
            existing = read_json(target)
            if (
                str(existing.get("version") or "") == DETERMINISTIC_V9_VERSION
                and str(existing.get("scope_extractor_version") or "")
                == NEWS_EXTRACTOR_VERSION
                and str(
                    (existing.get("identity_resolution") or {}).get(
                        "authority_version"
                    )
                )
                == ISSUER_IDENTITY_AUTHORITY_VERSION
            ):
                continue
        result = predict_v9(item, issuer_resolver=issuer_resolver)
        result.update({
            "sample_id": item.sample_id,
            "split": item.split,
            "source_id": item.blinded["source_id"],
        })
        write_json_atomic(target, result)
        if index % 100 == 0:
            print(f"V9 HUMAN {index:,}", flush=True)


def _delta(left: dict, right: dict) -> dict[str, float]:
    return {
        key: round(float(left[key]) - float(right[key]), 6)
        for key in left
        if key != "sample_count" and key in right
    }


if __name__ == "__main__":
    raise SystemExit(main())
