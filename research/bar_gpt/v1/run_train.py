from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.train import main


DEFAULT_ARGS: dict[str, str] = {
    "--start-date": "2020-01-01",
    "--end-date": "2027-01-01",
    "--validation-start-date": "2026-01-01",
    "--context-bars-1s": "2048",
    "--origin-bars-1s": "512",
    "--batch-size": "2",
    "--loader-workers": "4",
    "--ready-queue-blocks": "4",
    "--d-model": "384",
    "--n-layers": "8",
    "--n-heads": "8",
    "--n-kv-heads": "4",
    "--max-samples": "50000000",
    "--amp-dtype": "bf16",
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
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.train", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
