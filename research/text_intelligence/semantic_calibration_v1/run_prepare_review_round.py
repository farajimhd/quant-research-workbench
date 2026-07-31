from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from research.mlops.paths import MLOpsPathConfig

from .review_round import (
    carry_forward_non_analyst_pilot,
    prepare_pilot_review_round,
    prepare_remaining_review_templates,
)
from .storage import assert_runtime_root


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    default_root = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )
    parser = argparse.ArgumentParser(
        description="Prepare immutable V1 pilot annotations for the explicit V2 review round."
    )
    parser.add_argument("--runtime-root", type=Path, default=default_root)
    parser.add_argument(
        "--carry-non-analyst",
        action="store_true",
        help="Persist mechanically unchanged non-analyst pilot records as V2 round 2.",
    )
    parser.add_argument(
        "--prepare-remaining",
        action="store_true",
        help="Create V2 first-pass templates for the remaining 900 blinded items.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    assert_runtime_root(args.runtime_root)
    summary = prepare_pilot_review_round(args.runtime_root)
    print(
        "PILOT REVIEW V2 | "
        f"total={summary['total']} "
        f"manual={summary['manual_review_required']} "
        f"carry={summary['ready_to_carry_forward']}",
        flush=True,
    )
    if args.carry_non_analyst:
        carried = carry_forward_non_analyst_pilot(args.runtime_root)
        print(
            "PILOT REVIEW V2 CARRY | "
            f"recorded={carried['carried']} existing={carried['already_present']}",
            flush=True,
        )
    if args.prepare_remaining:
        remaining = prepare_remaining_review_templates(args.runtime_root)
        print(
            "REMAINING REVIEW V2 | "
            f"prepared={remaining['prepared']} existing={remaining['existing']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
