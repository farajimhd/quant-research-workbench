from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
JOB = HERE / "news_reaction_deterministic_v2.py"

# Safe, complete defaults for the versioned 2019-2025 training / 2026 holdout job.
DEFAULT_ARGS = [
    "--start-date", "2019-01-01",
    "--end-date", "2027-01-01",
    "--train-start-date", "2019-01-01",
    "--train-end-date", "2026-01-01",
    "--holdout-start-date", "2026-01-01",
    "--holdout-end-date", "2027-01-01",
    "--workers", "2",
    "--max-threads-per-query", "6",
    "--max-memory-usage", "16G",
    "--progress-layout", "auto",
]


def main() -> int:
    command = [sys.executable, "-u", str(JOB), *DEFAULT_ARGS, *sys.argv[1:]]
    print("Equivalent command:", subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command, cwd=str(HERE.parents[2]), env=os.environ.copy())


if __name__ == "__main__":
    raise SystemExit(main())
