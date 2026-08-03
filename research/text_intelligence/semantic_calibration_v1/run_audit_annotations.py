from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .annotation_audit import audit_annotations
from .schema import ANNOTATION_VERSION, ANNOTATION_VERSIONS


def default_root() -> Path:
    return (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit persisted semantic annotations.")
    parser.add_argument("--runtime-root", type=Path, default=default_root())
    parser.add_argument(
        "--annotation-version",
        choices=sorted(ANNOTATION_VERSIONS),
        default=ANNOTATION_VERSION,
        help="Annotation schema authority to audit.",
    )
    args = parser.parse_args(argv)
    report = audit_annotations(
        args.runtime_root,
        annotation_version=args.annotation_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
