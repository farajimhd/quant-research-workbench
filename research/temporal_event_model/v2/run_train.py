from __future__ import annotations

import subprocess
import sys
from pathlib import Path


DEFAULT_ARGS = {
    "run_name": "v2-return-horizon-local",
    "batch_size": 512,
    "epochs": 5,
    "blocks_per_epoch": 128,
    "context_chunks": 64,
    "encoder_checkpoint": "latest",
    "wandb_project": "June2026-market-ai-temporal-v2",
}


def main() -> int:
    here = Path(__file__).resolve().parent
    train_script = here / "train.py"
    command = [
        sys.executable,
        str(train_script),
        "--run-name",
        DEFAULT_ARGS["run_name"],
        "--batch-size",
        str(DEFAULT_ARGS["batch_size"]),
        "--epochs",
        str(DEFAULT_ARGS["epochs"]),
        "--blocks-per-epoch",
        str(DEFAULT_ARGS["blocks_per_epoch"]),
        "--context-chunks",
        str(DEFAULT_ARGS["context_chunks"]),
        "--encoder-checkpoint",
        DEFAULT_ARGS["encoder_checkpoint"],
        "--wandb-project",
        DEFAULT_ARGS["wandb_project"],
        *sys.argv[1:],
    ]
    print("Equivalent command:", flush=True)
    print(" ".join(command), flush=True)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())

