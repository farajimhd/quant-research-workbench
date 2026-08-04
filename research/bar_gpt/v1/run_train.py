from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.train import main


DEFAULT_ARGS: dict[str, str] = {
    "--start-date": "2020-01-01",
    "--end-date": "2026-08-01",
    "--validation-start-date": "2026-01-01",
    "--context-bars-1s": "2048",
    "--origin-bars-1s": "512",
    "--coverage-blocks-per-unit": "16",
    "--origin-fetch-candidate-blocks": "16",
    "--origin-emit-blocks-per-chunk": "16",
    "--validation-blocks-per-slice": "4",
    "--batch-size": "2",
    "--loader-workers": "8",
    "--ready-queue-blocks": "64",
    "--worker-prefetch-batches": "2",
    "--clickhouse-max-threads-per-worker": "1",
    "--clickhouse-query-days": "7",
    "--clickhouse-max-bytes-before-external-sort": "1073741824",
    "--d-model": "384",
    "--n-layers": "8",
    "--n-heads": "8",
    "--n-kv-heads": "4",
    "--max-samples": "0",
    "--gradient-accumulation-steps": "4",
    "--learning-rate": "0.0003",
    "--warmup-samples": "1048576",
    "--minimum-learning-rate": "0.00003",
    "--validation-interval-samples": "0",
    "--validation-runs-per-epoch": "4",
    "--validation-batches": "16",
    "--condition-positive-weight": "32",
    "--amp-dtype": "bf16",
    "--progress-layout": "auto",
}
DEFAULT_FLAGS: tuple[str, ...] = ("--amp", "--cuda-prefetch", "--no-compile-model")


def default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        argv.extend((key, value))
    return [*argv, *DEFAULT_FLAGS]


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = default_argv() + sys.argv[1:]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.train", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
