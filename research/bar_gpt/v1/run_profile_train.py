from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.profile_train import main


DEFAULT_ARGS: tuple[str, ...] = (
    "--start-date", "2025-10-01",
    "--end-date", "2026-01-01",
    "--candidates",
    (
        "4096:16:2:12:1:0,4096:16:2:16:1:0,4096:16:2:24:1:0"
    ),
    "--ready-queue-blocks", "512",
    "--worker-prefetch-batches", "4",
    "--warmup-steps", "2",
    "--measured-steps", "12",
    "--progress-layout", "auto",
)


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = [*DEFAULT_ARGS, *sys.argv[1:]]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.profile_train", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
