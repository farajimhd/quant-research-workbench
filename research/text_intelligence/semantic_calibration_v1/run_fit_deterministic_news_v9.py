from __future__ import annotations

import argparse
import json
from pathlib import Path

from .deterministic_v9_calibration import fit_v9_calibration
from .teacher_paths import DEFAULT_TEACHER_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit transparent deterministic News V9 calibration constants.")
    parser.add_argument("--teacher-root", type=Path, default=DEFAULT_TEACHER_ROOT)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    result = fit_v9_calibration(args.teacher_root, workers=args.workers)
    summary = {
        "calibration_version": result["calibration_version"],
        "record_count": result["record_count"],
        "role_overrides": len(result["article_role_overrides"]),
        "origin_overrides": len(result["source_origin_overrides"]),
        "concept_additions": len(result["single_ticker_concept_additions"]),
        "scope_denials": len(result["denied_unit_roles"]),
        "eligibility_rules": len(result["eligibility_tables"]),
        "direction": {key: value for key, value in result["direction"].items() if key not in {"rule_weights", "rule_support"}},
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
