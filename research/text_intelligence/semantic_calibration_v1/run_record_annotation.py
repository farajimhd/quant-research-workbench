from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from research.mlops.paths import MLOpsPathConfig

from .storage import append_annotation


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    default_root = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )
    parser = argparse.ArgumentParser(description="Persist one manually reviewed News annotation.")
    parser.add_argument(
        "annotation",
        help="Path to a completed annotation JSON file, or '-' to read JSON from stdin",
    )
    parser.add_argument("--runtime-root", type=Path, default=default_root)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    annotation = (
        json.load(sys.stdin)
        if args.annotation == "-"
        else json.loads(Path(args.annotation).read_text(encoding="utf-8"))
    )
    digest = append_annotation(args.runtime_root, annotation)
    print(f"RECORDED {annotation['sample_id']} sha256={digest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
