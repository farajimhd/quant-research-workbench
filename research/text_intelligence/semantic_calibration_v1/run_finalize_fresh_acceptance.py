from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .annotation_audit import audit_annotations
from .audit import audit_sample
from .fresh_acceptance import build_combined_human_authority
from .schema import ANNOTATION_VERSION_V3


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Certify the fresh 100 and materialize the combined 1,100 human authority."
    )
    parser.add_argument("--original-root", type=Path, default=base / "news_1000")
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_100_v1"
    )
    parser.add_argument("--combined-root", type=Path, default=base / "news_1100_v1")
    args = parser.parse_args(argv)
    sample_audit = audit_sample(args.acceptance_root)
    annotation_audit = audit_annotations(
        args.acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    errors = [*sample_audit["errors"], *annotation_audit["errors"]]
    if annotation_audit["completed"] != 100:
        errors.append(f"annotation_count:{annotation_audit['completed']}")
    if errors:
        raise RuntimeError("fresh acceptance audit failed: " + ", ".join(errors[:20]))
    target = build_combined_human_authority(
        original_root=args.original_root,
        acceptance_root=args.acceptance_root,
        combined_root=args.combined_root,
    )
    print(
        f"READY | fresh_audit=pass completed={annotation_audit['completed']:,} "
        f"combined={target} articles=1,100",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
