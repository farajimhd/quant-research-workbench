from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


WORKSTATION_ROOT = r"\\DESKTOP-SAAI85T\Workstation-D"
V20_RUN = (
    WORKSTATION_ROOT
    + r"\TradingML\runtimes\masked_event_model\v20\pretrain"
    + r"\v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs"
)

CHECKPOINTS = {
    "epoch1": V20_RUN + r"\checkpoints\checkpoint_step_000130176.pt",
    "epoch2": V20_RUN + r"\checkpoints\checkpoint_step_000260352.pt",
    "latest": V20_RUN + r"\checkpoints\checkpoint_latest.pt",
}

DEFAULTS = {
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\window_embedding_probe_laptop",
    "wandb_project": "June2026-event-encoder-window-probes",
    "checkpoint": "latest",
    "batch_size": 512,
    "epochs": 2,
    "blocks_per_epoch": 24,
    "validation_blocks": 8,
    "validation_batches_per_block": 2,
    "recent_count": 16,
    "recent_stride": 1,
    "older_count": 16,
    "older_min_lag": 32,
    "older_max_lag": 1024,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Laptop launcher for the temporal v1 streaming-window embedding probe. "
            "It queries ticker windows from ClickHouse, builds rolling event embeddings, "
            "selects recent+distant context embeddings, and trains a small temporal head."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", choices=tuple(CHECKPOINTS), default=DEFAULTS["checkpoint"])
    parser.add_argument("--encoder-checkpoint", default="", help="Optional explicit checkpoint path; overrides --checkpoint.")
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--run-name", default="")
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--blocks-per-epoch", type=int, default=DEFAULTS["blocks_per_epoch"])
    parser.add_argument("--validation-blocks", type=int, default=DEFAULTS["validation_blocks"])
    parser.add_argument("--validation-batches-per-block", type=int, default=DEFAULTS["validation_batches_per_block"])
    parser.add_argument("--recent-count", type=int, default=DEFAULTS["recent_count"])
    parser.add_argument("--recent-stride", type=int, default=DEFAULTS["recent_stride"])
    parser.add_argument("--older-count", type=int, default=DEFAULTS["older_count"])
    parser.add_argument("--older-min-lag", type=int, default=DEFAULTS["older_min_lag"])
    parser.add_argument("--older-max-lag", type=int, default=DEFAULTS["older_max_lag"])
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--block-max-events", type=int, default=250_000)
    parser.add_argument("--learning-rate", type=float, default=8e-4)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    args = parser.parse_args()

    checkpoint = args.encoder_checkpoint or CHECKPOINTS[args.checkpoint]
    run_name = args.run_name or f"v1-window-probe-v20-{args.checkpoint}-ctx{args.recent_count + args.older_count}-bs{args.batch_size}"
    script = Path(__file__).with_name("window_embedding_probe.py")
    command = [
        sys.executable,
        str(script),
        "--output-root",
        args.output_root,
        "--wandb-project",
        args.wandb_project,
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.epochs),
        "--blocks-per-epoch",
        str(args.blocks_per_epoch),
        "--validation-blocks",
        str(args.validation_blocks),
        "--validation-batches-per-block",
        str(args.validation_batches_per_block),
        "--recent-count",
        str(args.recent_count),
        "--recent-stride",
        str(args.recent_stride),
        "--older-count",
        str(args.older_count),
        "--older-min-lag",
        str(args.older_min_lag),
        "--older-max-lag",
        str(args.older_max_lag),
        "--tickers",
        args.tickers,
        "--block-max-events",
        str(args.block_max_events),
        "--learning-rate",
        str(args.learning_rate),
        "--amp-dtype",
        args.amp_dtype,
        "--encoder-checkpoint",
        checkpoint,
        "--run-name",
        run_name,
    ]
    print("Equivalent command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
