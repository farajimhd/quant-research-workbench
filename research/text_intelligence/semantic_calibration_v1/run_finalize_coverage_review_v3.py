from __future__ import annotations

import argparse
import json
from pathlib import Path

from .annotation_audit import audit_annotations
from .coverage_review_v3 import finalize_completed_reviews
from .run_deterministic_news_v6 import DEFAULT_ROOT
from .schema import ANNOTATION_VERSION_V3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist all completed exhaustive News V3 coverage reviews."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    result = finalize_completed_reviews(args.root)
    if not result["failures"] and not result["pending_decisions"]:
        result["audit"] = audit_annotations(
            args.root,
            annotation_version=ANNOTATION_VERSION_V3,
        )
    print(json.dumps(result, indent=2), flush=True)
    return int(bool(result["failures"] or result["pending_decisions"]))


if __name__ == "__main__":
    raise SystemExit(main())
