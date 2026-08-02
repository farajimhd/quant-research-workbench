from __future__ import annotations

import argparse
import json
from pathlib import Path

from .teacher_comparison import compare_teacher, headline
from .teacher_paths import DEFAULT_TEACHER_ROOT
from .run_deterministic_news_v7 import _predict as predict_v7
from .run_deterministic_news_v8 import _predict as predict_v8
from .run_deterministic_news_v9 import predict_with_loaded_authority as predict_v9
from .teacher_split_v9 import ensure_grouped_split


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a deterministic News authority with the disjoint Sol teacher corpus."
    )
    parser.add_argument("--teacher-root", type=Path, default=DEFAULT_TEACHER_ROOT)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--authority", choices=("v7", "v8", "v9"), default="v9")
    parser.add_argument("--split", choices=("all", "development", "validation", "locked_test"), default="all")
    args = parser.parse_args()
    predictor, authority_name = {
        "v7": (predict_v7, "deterministic_v7"),
        "v8": (predict_v8, "deterministic_v8"),
        "v9": (predict_v9, "deterministic_v9"),
    }[args.authority]
    included = None
    suffix = ""
    if args.split != "all":
        manifest = ensure_grouped_split(args.teacher_root)
        included = {
            str(row["sample_id"])
            for row in manifest["items"]
            if row["split"] == args.split
        }
        suffix = f"_{args.split}"
    report = compare_teacher(
        args.teacher_root,
        predictor=predictor,
        authority_name=authority_name,
        workers=args.workers,
        included_sample_ids=included,
        output_suffix=suffix,
    )
    print(json.dumps(headline(report), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
