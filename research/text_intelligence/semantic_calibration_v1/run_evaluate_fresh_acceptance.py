from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .annotation_audit import audit_annotations
from .audit import audit_sample
from .comparison import evaluate_predictions, load_collection
from .run_deterministic_news_v6 import _headline
from .run_deterministic_news_v9 import generate_v9_predictions, load_v9_issuer_authority
from .schema import ANNOTATION_VERSION_V3
from .storage import assert_runtime_root, write_json_atomic


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Evaluate V9 and V10 only on the frozen fresh 100-item acceptance set."
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_100_v1"
    )
    parser.add_argument(
        "--v10-artifact",
        type=Path,
        default=base / "news_v10_tfidf_random_forest" / "model.joblib",
    )
    parser.add_argument(
        "--output-root", type=Path, default=base / "news_acceptance_100_v1" / "evaluation"
    )
    parser.add_argument(
        "--v9-only",
        action="store_true",
        help="Refresh V9 while reusing the already persisted V10 predictions.",
    )
    args = parser.parse_args(argv)
    assert_runtime_root(args.output_root)
    sample_audit = audit_sample(args.acceptance_root, write_report=False)
    annotation_audit = audit_annotations(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
        write_report=False,
    )
    if sample_audit["status"] != "pass" or annotation_audit["status"] != "pass":
        raise RuntimeError("fresh acceptance must pass sample and annotation audits")
    if annotation_audit["completed"] != 100 or annotation_audit["remaining_collection"] != 0:
        raise RuntimeError("all 100 manual annotations must be frozen before prediction")

    items = load_collection(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    if len(items) != 100 or {item.split for item in items} != {"fresh_acceptance"}:
        raise RuntimeError("fresh acceptance identity or split contract drift")
    v10_dir = args.output_root / "v10_predictions"
    v9_dir = args.output_root / "v9_predictions"
    if args.v9_only:
        missing = [item.sample_id for item in items if not (v10_dir / f"{item.sample_id}.json").is_file()]
        if missing:
            raise RuntimeError(
                f"--v9-only requires complete persisted V10 predictions; missing={missing[:5]}"
            )
        print(f"V10 reusing {len(items):,} persisted predictions", flush=True)
    else:
        from .news_v10 import (
            V10_VERSION,
            generate_human_predictions,
            human_prediction_cache_complete,
        )
        if human_prediction_cache_complete(items, output_dir=v10_dir):
            print(f"V10 reusing {len(items):,} identity-verified predictions", flush=True)
        else:
            import joblib

            model = joblib.load(args.v10_artifact)
            if model.version != V10_VERSION:
                raise RuntimeError(f"unexpected V10 artifact version: {model.version}")
            generate_human_predictions(model, items, output_dir=v10_dir)
    issuer_resolver = load_v9_issuer_authority()
    generate_v9_predictions(items, v9_dir, issuer_resolver=issuer_resolver)
    v10 = evaluate_predictions(items, prediction_dir=v10_dir, canonical_concepts=True)
    v9 = evaluate_predictions(items, prediction_dir=v9_dir, canonical_concepts=True)
    result = {
        "contract": "news_fresh_acceptance_v1_v9_v10_evaluation",
        "articles": len(items),
        "training_overlap": 0,
        "human_annotations_frozen_before_prediction": True,
        "v10": v10,
        "v9": v9,
        "headline": {
            "v10": _headline(v10),
            "v9": _headline(v9),
            "delta_v10_minus_v9": _delta(_headline(v10), _headline(v9)),
        },
    }
    write_json_atomic(args.output_root / "evaluation.json", result)
    print(json.dumps(result["headline"], indent=2), flush=True)
    return 0


def _delta(left: dict, right: dict) -> dict[str, float]:
    return {
        key: round(float(left[key]) - float(right[key]), 6)
        for key in left
        if key != "sample_count" and key in right
    }


if __name__ == "__main__":
    raise SystemExit(main())
