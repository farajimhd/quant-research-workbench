from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .annotation_audit import audit_annotations
from .audit import audit_sample
from .fresh_acceptance_v3 import build_combined_human_authority_v3
from .schema import ANNOTATION_VERSION_V3


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(description="Certify third fresh-100 and materialize 1,300 human authority.")
    parser.add_argument("--original-root", type=Path, default=base / "news_1000")
    parser.add_argument("--acceptance-v1-root", type=Path, default=base / "news_acceptance_100_v1")
    parser.add_argument("--acceptance-v2-root", type=Path, default=base / "news_acceptance_100_v2")
    parser.add_argument("--acceptance-v3-root", type=Path, default=base / "news_acceptance_100_v3")
    parser.add_argument("--combined-root", type=Path, default=base / "news_1300_v1")
    args = parser.parse_args(argv)
    for root in (args.acceptance_v1_root, args.acceptance_v2_root, args.acceptance_v3_root):
        sample = audit_sample(root)
        annotations = audit_annotations(root, annotation_version=ANNOTATION_VERSION_V3)
        if sample["status"] != "pass" or annotations["status"] != "pass" or annotations["completed"] != 100:
            raise RuntimeError(f"acceptance authority not certified: {root}")
    target = build_combined_human_authority_v3(
        original_root=args.original_root,
        acceptance_roots=(args.acceptance_v1_root, args.acceptance_v2_root, args.acceptance_v3_root),
        combined_root=args.combined_root,
    )
    print(f"READY | combined={target} articles=1,300", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
