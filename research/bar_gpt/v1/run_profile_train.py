from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.profile_train import DEFAULT_JOINT_CANDIDATES, main


DEFAULT_ARGS: tuple[str, ...] = (
    "--data-source", "offline",
    "--offline-shard-root", r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v2",
    "--start-date", "2019-01-01",
    "--end-date", "2019-02-01",
    "--candidates",
    DEFAULT_JOINT_CANDIDATES,
    "--ready-queue-blocks", "512",
    "--worker-prefetch-batches", "4",
    "--target-effective-blocks", "32",
    "--warmup-steps", "1",
    "--measured-steps", "3",
    "--progress-layout", "auto",
)


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = [*DEFAULT_ARGS, *sys.argv[1:]]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.profile_train", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
