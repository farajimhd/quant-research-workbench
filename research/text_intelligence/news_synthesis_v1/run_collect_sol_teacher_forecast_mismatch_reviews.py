from __future__ import annotations

import argparse
from pathlib import Path

from .sol_teacher_forecast_mismatch_review_collection import collect_mismatch_reviews


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect forecast mismatch reviews")
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    args = parser.parse_args()
    progress = collect_mismatch_reviews(args.audit_root, args.input_root)
    print(
        f"COLLECTED batches={progress['reviewed_batches']}/{progress['total_batches']} "
        f"mismatches={progress['reviewed_mismatches']}/{progress['total_mismatches']}"
    )


if __name__ == "__main__":
    main()
