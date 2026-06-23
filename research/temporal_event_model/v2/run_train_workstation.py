from __future__ import annotations

import subprocess
import sys
from pathlib import Path


DEFAULT_ARGS = {
    "run_name": "v2-return-horizon-v20-latest-ws",
    "batch_size": 512,
    "epochs": 5,
    "blocks_per_epoch": 128,
    "context_chunks": 64,
    "window_days": 15,
    "encoder_checkpoint": "latest",
    "encoder_checkpoint_search_root": r"D:\TradingML\runtimes\masked_event_model\v20\pretrain",
    "wandb_project": "June2026-market-ai-temporal-v2",
    "amp_dtype": "bf16",
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
        "--window-days",
        str(DEFAULT_ARGS["window_days"]),
        "--encoder-checkpoint",
        DEFAULT_ARGS["encoder_checkpoint"],
        "--encoder-checkpoint-search-root",
        DEFAULT_ARGS["encoder_checkpoint_search_root"],
        "--wandb-project",
        DEFAULT_ARGS["wandb_project"],
        "--amp-dtype",
        DEFAULT_ARGS["amp_dtype"],
        *sys.argv[1:],
    ]
    print("Equivalent command:", flush=True)
    print(" ".join(command), flush=True)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
