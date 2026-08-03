from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .annotation_audit import audit_annotations
from .comparison import evaluate_predictions, load_collection
from .run_deterministic_news_v6 import _headline
from .run_deterministic_news_v9 import generate_v9_predictions, load_v9_issuer_authority
from .schema import ANNOTATION_VERSION_V3
from .storage import assert_runtime_root, write_json_atomic


CONTRACT = "news_human_1300_candidate21_regression_v1"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Regress deterministic V9 candidate 21 over the certified 1,300-item authority."
    )
    parser.add_argument("--human-root", type=Path, default=base / "news_1300_v1")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_1300_v1" / "candidate21_regression",
    )
    args = parser.parse_args(argv)
    assert_runtime_root(args.output_root)
    annotation_audit = audit_annotations(
        args.human_root,
        annotation_version=ANNOTATION_VERSION_V3,
        write_report=False,
    )
    if annotation_audit["status"] != "pass" or annotation_audit["completed"] != 1_300:
        raise RuntimeError("certified 1,300-item human authority is incomplete")
    items = load_collection(args.human_root, annotation_version=ANNOTATION_VERSION_V3)
    if len(items) != 1_300:
        raise RuntimeError(f"expected 1,300 items, found {len(items):,}")
    prediction_dir = args.output_root / "v9_predictions"
    generate_v9_predictions(
        items,
        prediction_dir,
        issuer_resolver=load_v9_issuer_authority(),
    )
    report = evaluate_predictions(items, prediction_dir=prediction_dir, canonical_concepts=True)
    result = {
        "contract": CONTRACT,
        "articles": len(items),
        "candidate": "21",
        "v9": report,
        "headline": {"v9": _headline(report)},
    }
    write_json_atomic(args.output_root / "evaluation.json", result)
    print(json.dumps(result["headline"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
