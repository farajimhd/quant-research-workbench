from __future__ import annotations

import argparse
import json
from pathlib import Path

from .teacher_comparison import DEFAULT_TEACHER_ROOT, compare_teacher, headline
from .run_deterministic_news_v7 import _predict as predict_v7
from .run_deterministic_news_v8 import _predict as predict_v8


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a deterministic News authority with the disjoint Sol teacher corpus."
    )
    parser.add_argument("--teacher-root", type=Path, default=DEFAULT_TEACHER_ROOT)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--authority", choices=("v7", "v8"), default="v8")
    args = parser.parse_args()
    predictor, authority_name = (
        (predict_v8, "deterministic_v8")
        if args.authority == "v8"
        else (predict_v7, "deterministic_v7")
    )
    report = compare_teacher(
        args.teacher_root,
        predictor=predictor,
        authority_name=authority_name,
        workers=args.workers,
    )
    print(json.dumps(headline(report), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
