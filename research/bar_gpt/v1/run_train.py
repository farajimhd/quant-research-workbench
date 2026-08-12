from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.config import (
    BAR_GPT_WANDB_PROJECT,
    OFFLINE_PRODUCTION_BATCH_SIZE,
    OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES,
    OFFLINE_PRODUCTION_LOADER_WORKERS,
    OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
    PRODUCTION_MODEL_TRAINING_PRESETS,
)
from research.bar_gpt.v1.train import main


DEFAULT_ARGS: dict[str, str] = {
    "--data-source": "offline",
    "--offline-shard-root": r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12",
    "--offline-train-start-date": "2019-01-01",
    # The immutable catalog is built once through 2026-08-01. Training owns
    # 2019-2025 and the loader selects January-July 2026 as the fixed OOT view.
    "--offline-train-end-date": "2026-01-01",
    "--offline-validation-start-date": "2026-01-01",
    "--offline-validation-end-date": "2026-08-01",
    "--start-date": "2019-01-01",
    "--end-date": "2026-08-01",
    "--validation-start-date": "2026-01-01",
    "--origin-bars-1s": "4096",
    "--coverage-mode": "sequential",
    "--coverage-blocks-per-unit": "16",
    "--origin-fetch-candidate-blocks": "16",
    "--origin-emit-blocks-per-chunk": "16",
    "--validation-blocks-per-slice": "2",
    # Loader-owned shape retained from the full-universe profile. Offline
    # shards contain 4,096-origin blocks but never this batch dimension; tune
    # it from the launcher without rebuilding storage.
    "--batch-size": str(OFFLINE_PRODUCTION_BATCH_SIZE),
    "--loader-workers": str(OFFLINE_PRODUCTION_LOADER_WORKERS),
    # The completed end-to-end v12 grid selected eight workers, one prefetched
    # batch per worker, and bucket window sixteen for Current.
    "--ready-queue-blocks": str(OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS),
    "--worker-prefetch-batches": str(OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES),
    "--offline-length-bucket-batches": str(OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES),
    "--clickhouse-max-threads-per-worker": "1",
    "--clickhouse-query-days": "7",
    "--clickhouse-prefetch-pages": "4",
    "--clickhouse-max-bytes-before-external-sort": "1073741824",
    "--clickhouse-retry-attempts": "5",
    "--clickhouse-retry-initial-seconds": "0.5",
    "--clickhouse-retry-max-seconds": "8",
    "--d-model": "384",
    "--n-layers": "8",
    "--n-heads": "8",
    "--n-kv-heads": "4",
    "--max-samples": "0",
    "--gradient-accumulation-steps": str(
        PRODUCTION_MODEL_TRAINING_PRESETS["current"].accumulation
    ),
    "--epochs": "1",
    "--learning-rate": "0.0003",
    "--warmup-samples": "0",
    "--warmup-fraction": "0.01",
    "--minimum-learning-rate": "0.00003",
    "--cosine-cycle-samples": "100000000",
    "--cosine-restart-decay": "0.98",
    "--validation-interval-samples": "0",
    "--validation-initial-samples": "33554432",
    "--validation-runs-per-epoch": "100",
    "--validation-batches": "16",
    "--checkpoint-validation-evaluations": "1",
    "--logging-samples": "1048576",
    "--training-metrics-interval-samples": "8388608",
    "--condition-positive-weight": "32",
    "--amp-dtype": "bf16",
    "--progress-layout": "auto",
    "--wandb-project": BAR_GPT_WANDB_PROJECT,
    "--wandb-mode": "online",
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
