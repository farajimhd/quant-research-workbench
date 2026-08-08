from __future__ import annotations

import argparse
from pathlib import Path

from .sol_teacher_forecast_reviewed_gold import create_reviewed_audit_gold


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze prediction-blind reviewed Sol forecast audit gold"
    )
    parser.add_argument("--split-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = create_reviewed_audit_gold(
        args.split_root, args.review_root, args.output_root
    )
    counts = manifest["resolution_counts"]
    print(
        "REVIEWED_GOLD "
        f"units={manifest['population']['issuer_units']:,} "
        f"corrected={counts.get('reviewed_correction', 0):,} "
        "uncertain="
        f"{counts.get('policy_uncertain_original_retained', 0):,}"
    )


if __name__ == "__main__":
    main()
