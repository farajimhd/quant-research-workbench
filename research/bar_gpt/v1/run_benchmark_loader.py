from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.benchmark_loader import main


DEFAULT_ARGS: dict[str, str] = {
    "--split": "train",
    "--start-date": "2025-10-01",
    "--end-date": "2026-01-01",
    "--batch-size": "2",
    "--loader-workers": "2",
    "--ready-queue-blocks": "2",
    "--origin-fetch-candidate-blocks": "4",
    "--origin-emit-blocks-per-chunk": "2",
    "--warmup-batches": "16",
    "--measured-batches": "128",
    "--device": "auto",
    "--progress-layout": "auto",
}


def default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        argv.extend((key, value))
    return argv


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = default_argv() + sys.argv[1:]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.benchmark_loader", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
