from __future__ import annotations

import argparse
from pathlib import Path

from .compare import compare_runs
from .config import LabelingConfig


def main(argv: list[str] | None = None) -> int:
    base = LabelingConfig().runtime_root
    parser = argparse.ArgumentParser(
        description="Compare GPT-OSS 20B and 120B labels over one frozen news sample."
    )
    parser.add_argument("--sample-jsonl", type=Path, default=base / "shared" / "sample.jsonl")
    parser.add_argument("--first-root", type=Path, default=base / "models" / "20b")
    parser.add_argument("--second-root", type=Path, default=base / "models" / "120b")
    parser.add_argument("--output-root", type=Path, default=base / "comparison")
    parser.add_argument("--answer-key-jsonl", type=Path)
    parser.add_argument("--disagreement-limit", type=int, default=48)
    args = parser.parse_args(argv)
    report = compare_runs(
        sample_path=args.sample_jsonl,
        first_root=args.first_root,
        second_root=args.second_root,
        output_root=args.output_root,
        answer_key_path=args.answer_key_jsonl,
        disagreement_limit=args.disagreement_limit,
    )
    print(f"COMPLETED | report={report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
