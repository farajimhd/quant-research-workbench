from __future__ import annotations

import sys
import shlex
from pathlib import Path

from research.news_reaction_model.v16.error_study import main as study_main


DEFAULT_ARGS = [
    "--start", "2026-01-01",
    "--end-exclusive", "2027-01-01",
    "--review-per-stratum", "100",
    "--minimum-slice-support", "100",
    "--neighbor-top-k", "5",
    "--neighbor-candidates", "128",
    "--neighbor-projection-dim", "64",
    "--neighbor-batch-size", "8192",
    "--neighbor-device", "auto",
    "--price-path-workers", "4",
]


def _discover_default_paths() -> list[str]:
    roots = (
        Path(r"D:\TradingML\runtimes\news-reaction-model\v16\train"),
        Path(r"D:\market-data\runtimes\news-reaction-model\v16\train"),
    )
    candidates = [
        checkpoint
        for root in roots
        if root.exists()
        for checkpoint in root.glob("*/checkpoints/checkpoint_best_val.pt")
    ]
    if not candidates:
        return []
    checkpoint = max(candidates, key=lambda value: value.stat().st_mtime)
    run_root = checkpoint.parent.parent
    prediction_candidates = (
        run_root / "evaluation" / "evaluation_predictions.jsonl.gz",
        run_root / "evaluation_best_val" / "evaluation_predictions.jsonl.gz",
    )
    prediction = next((value for value in prediction_candidates if value.exists()), None)
    if prediction is None:
        return ["--checkpoint", str(checkpoint)]
    return [
        "--checkpoint", str(checkpoint),
        "--predictions", str(prediction),
        "--output-dir", str(run_root / "error_study_2026"),
    ]


def main(args: list[str] | None = None) -> int:
    overrides = list(sys.argv[1:] if args is None else args)
    discovered = _discover_default_paths()
    missing = [
        name for name in ("checkpoint", "predictions", "output-dir")
        if f"--{name}" not in discovered and f"--{name}" not in overrides
    ]
    if missing:
        print(
            "No complete V16 best-checkpoint evaluation was discovered. Provide "
            + ", ".join(f"--{name}" for name in missing)
            + ".",
            file=sys.stderr,
        )
        return 2
    command = DEFAULT_ARGS + discovered + overrides
    print(
        "COMMAND python -m research.news_reaction_model.v16.error_study "
        + " ".join(shlex.quote(value) for value in command),
        flush=True,
    )
    return study_main(command)


if __name__ == "__main__":
    raise SystemExit(main())
