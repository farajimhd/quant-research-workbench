from __future__ import annotations

import argparse
import json
from pathlib import Path

from .comparison import evaluate_predictions, load_collection
from .run_deterministic_news_v6 import DEFAULT_FROZEN, DEFAULT_ROOT, _frozen_ids, _headline
from .schema import ANNOTATION_VERSION_V3
from .storage import assert_runtime_root, write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate unchanged deterministic V6/V7 predictions against corrected V3 gold."
    )
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--frozen-sample", type=Path, default=DEFAULT_FROZEN)
    args = parser.parse_args()
    assert_runtime_root(args.runtime_root)
    collection = load_collection(
        args.runtime_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    frozen_ids = _frozen_ids(args.frozen_sample)
    results: dict[str, dict] = {}
    for version in ("v6", "v7"):
        version_root = args.runtime_root / f"deterministic_{version}"
        for phase in ("development", "frozen-acceptance"):
            selected = tuple(
                item
                for item in collection
                if (item.sample_id in frozen_ids) == (phase == "frozen-acceptance")
            )
            prediction_dir = version_root / f"{phase}_predictions"
            missing = [
                item.sample_id
                for item in selected
                if not (prediction_dir / f"{item.sample_id}.json").exists()
            ]
            if missing:
                raise RuntimeError(
                    f"{version} {phase} missing {len(missing)} unchanged predictions"
                )
            report = evaluate_predictions(
                selected,
                prediction_dir=prediction_dir,
                canonical_concepts=True,
            )
            write_json_atomic(
                version_root / f"coverage_v3_{phase}_metrics.json",
                report,
            )
            results[f"{version}_{phase}"] = _headline(report)
    write_json_atomic(
        args.runtime_root / "coverage_review_v3" / "deterministic_evaluation.json",
        results,
    )
    print(json.dumps(results, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
