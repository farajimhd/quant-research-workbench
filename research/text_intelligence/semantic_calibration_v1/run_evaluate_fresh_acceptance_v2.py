from __future__ import annotations

import argparse
from collections import Counter
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


CONTRACT = "news_fresh_acceptance_v2_v9_evaluation"
SPLIT = "fresh_acceptance_v2"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate deterministic V9 only after all second fresh-acceptance "
            "annotations have been frozen prediction-blind."
        )
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_100_v2"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_100_v2" / "evaluation",
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
        raise RuntimeError(
            "second fresh acceptance must pass sample and annotation audits"
        )
    if annotation_audit["completed"] != 100 or annotation_audit["remaining_collection"]:
        raise RuntimeError("all 100 manual annotations must be frozen before V9")

    items = load_collection(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    if len(items) != 100 or {item.split for item in items} != {SPLIT}:
        raise RuntimeError("second fresh-acceptance identity or split contract drift")

    v9_dir = args.output_root / "v9_predictions"
    issuer_resolver = load_v9_issuer_authority()
    generate_v9_predictions(items, v9_dir, issuer_resolver=issuer_resolver)
    v9 = evaluate_predictions(items, prediction_dir=v9_dir, canonical_concepts=True)
    result = {
        "contract": CONTRACT,
        "articles": len(items),
        "split": SPLIT,
        "training_overlap": 0,
        "human_annotations_frozen_before_prediction": True,
        "sample_manifest_sha256": sample_audit["sample_manifest_sha256"],
        "session_counts": dict(
            sorted(
                Counter(
                    str(
                        row.get("publication_session_et")
                        or row.get("market_session")
                        or "unknown"
                    )
                    for row in json.loads(
                        (args.acceptance_root / "sample_manifest.json").read_text(
                            encoding="utf-8"
                        )
                    )["items"]
                ).items()
            )
        ),
        "v9": v9,
        "headline": {"v9": _headline(v9)},
    }
    write_json_atomic(args.output_root / "evaluation.json", result)
    print(json.dumps(result["headline"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
