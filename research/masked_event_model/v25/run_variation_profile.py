from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()),
    Path(__file__).resolve().parents[3],
)


DEFAULTS: dict[str, Any] = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache",
    "run_prefix": "v25-focused-fixedlr-profile",
    "steps": 200,
    "learning_rate": 2e-4,
    "scheduler": "none",
    "wandb_project": "June2026-event-token-mae-v25-variation-profile",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
    "trainer_progress_layout": "text",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run five focused v25 profile variations sequentially. Each variation "
            "trains for 200 steps by default, uses a fixed learning rate, and writes "
            "subprocess logs plus JSONL/CSV summaries for comparison."
        )
    )
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--run-prefix", default=DEFAULTS["run_prefix"])
    parser.add_argument("--steps", type=int, default=DEFAULTS["steps"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-entity", default=DEFAULTS["wandb_entity"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--trainer-progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["trainer_progress_layout"])
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()

    command = [
        sys.executable,
        "-u",
        str(REPO_ROOT / "research" / "masked_event_model" / "v25" / "train_model_size_sweep.py"),
        "--profile-set",
        "focused",
        "--steps",
        str(known.steps),
        "--run-prefix",
        known.run_prefix,
        "--cache-root",
        known.cache_root,
        "--learning-rate",
        str(known.learning_rate),
        "--scheduler",
        DEFAULTS["scheduler"],
        "--wandb-project",
        known.wandb_project,
        "--wandb-entity",
        known.wandb_entity,
        "--wandb-mode",
        known.wandb_mode,
        "--trainer-progress-layout",
        known.trainer_progress_layout,
    ]
    if known.fresh_start:
        command.append("--fresh-start")
    if known.dry_run:
        command.append("--dry-run")
    if known.print_only:
        command.append("--print-only")
    command.extend(extra)

    print("=" * 96, flush=True)
    print("v25 focused variation profile", flush=True)
    print("Variations:", flush=True)
    print("  1. medium      emb32 batch4096", flush=True)
    print("  2. medium      emb32 batch8192", flush=True)
    print("  3. medium      emb64 batch4096", flush=True)
    print("  4. medium_plus emb32 batch2048", flush=True)
    print("  5. large       emb32 batch1024", flush=True)
    print("Fixed LR:", known.learning_rate, flush=True)
    print("Equivalent command:", flush=True)
    print(" ".join(command), flush=True)
    print("=" * 96, flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(command, cwd=str(REPO_ROOT)))


if __name__ == "__main__":
    main()
