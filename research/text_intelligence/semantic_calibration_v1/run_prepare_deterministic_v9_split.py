from __future__ import annotations

import argparse
import json
from pathlib import Path

from .teacher_paths import DEFAULT_TEACHER_ROOT
from .teacher_split_v9 import ensure_grouped_split


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the immutable grouped Sol split for deterministic News V9.")
    parser.add_argument("--teacher-root", type=Path, default=DEFAULT_TEACHER_ROOT)
    args = parser.parse_args()
    result = ensure_grouped_split(args.teacher_root)
    print(json.dumps({key: result[key] for key in (
        "split_version", "valid_count", "counts", "group_count", "largest_group", "group_leakage_count",
    )}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
