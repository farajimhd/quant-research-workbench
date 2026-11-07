"""Explicit phase-one training launcher with the previously agreed campaign settings.

Running this script starts training; importing it does not. A fresh --output is
required. Split and calibration default to a new, receipt-bound v6 inventory and
training-only statistics. Never reuse the old 21-feature split or checkpoint.
Additional CLI flags override these preset values, including bounded smoke runs.
"""

import os
from pathlib import Path
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("OMP_NUM_THREADS", "1")  # Bound each spawned worker's native thread pools.
os.environ.setdefault("MKL_NUM_THREADS", "1")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def campaign_arguments(overrides=()):
    """Return CLI strings for the large 50-full-epoch campaign, then caller overrides."""
    return [
        "--mode", "train", "--dataset", "D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6",
        "--model", "large", "--precision", "bf16", "--attention", "sdpa", "--attention-dropout", "0",
        "--batch-size", "4096", "--workers", "32", "--prefetch", "2",
        "--epochs", "50", "--train-steps", "0", "--validation-steps", "0",
        "--scheduler", "cosine", "--learning-rate", "1e-3", "--min-learning-rate", "5e-5",
        "--warmup-updates", "1000", "--metrics-every", "1000", "--loss-every", "10",
        "--checkpoint-every", "0", "--seed", "17", "--wandb-mode", "online",
        "--wandb-project", "bargpt-v5-mae-market-only-v6", "--panel",
        *overrides,
    ]  # Zero step limits request full passes; the cosine runner saves each epoch, best, and last.


if __name__ == "__main__":
    from research.bar_gpt.v5.run_v1 import main

    arguments = campaign_arguments(sys.argv[1:])
    print("Equivalent: " + subprocess.list2cmdline(
        [sys.executable, "-B", "-u", "-m", "research.bar_gpt.v5.run_v1", *arguments]), flush=True)
    raise SystemExit(main(arguments))
