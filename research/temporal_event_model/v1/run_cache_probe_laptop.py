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
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop",
    "wandb_project": "June2026-event-encoder-linear-probes",
    "train_start_shard": 0,
    "train_max_shards": 1,
    "validation_start_shard": 1,
    "validation_max_shards": 1,
    "validation_batches": 10,
    "batch_size": 512,
    "epochs": 5,
    "checkpoint": "epoch2",
    "validation_frequency_steps": 500,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Laptop launcher for temporal v1 cache probe. Reads the v2 cache and "
            "v20 checkpoints from the workstation shared drive."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", choices=tuple(CHECKPOINTS), default=DEFAULTS["checkpoint"])
    parser.add_argument("--encoder-checkpoint", default="", help="Optional explicit checkpoint path; overrides --checkpoint.")
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--run-name", default="", help="Optional explicit W&B/run name.")
    parser.add_argument("--train-start-shard", type=int, default=DEFAULTS["train_start_shard"])
    parser.add_argument("--train-max-shards", type=int, default=DEFAULTS["train_max_shards"])
    parser.add_argument("--validation-start-shard", type=int, default=DEFAULTS["validation_start_shard"])
    parser.add_argument("--validation-max-shards", type=int, default=DEFAULTS["validation_max_shards"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--validation-frequency-steps", type=int, default=DEFAULTS["validation_frequency_steps"])
    parser.add_argument("--validation-frequency-shards", type=int, default=0)
    parser.add_argument("--preload-shards-to-ram", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--flat-threshold-bps", type=float, default=2.0)
    parser.add_argument("--strong-threshold-bps", type=float, default=20.0)
    args = parser.parse_args()

    checkpoint = args.encoder_checkpoint or CHECKPOINTS[args.checkpoint]
    run_name = args.run_name or f"v1-cache-probe-v20-{args.checkpoint}-1train1val-5ep-bs{args.batch_size}-laptop"
    script = Path(__file__).with_name("cache_probe.py")
    command = [
        sys.executable,
        str(script),
        "--cache-root",
        args.cache_root,
        "--train-start-shard",
        str(args.train_start_shard),
        "--train-max-shards",
        str(args.train_max_shards),
        "--validation-start-shard",
        str(args.validation_start_shard),
        "--validation-max-shards",
        str(args.validation_max_shards),
        "--validation-batches",
        str(args.validation_batches),
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.epochs),
        "--validation-frequency-steps",
        str(args.validation_frequency_steps),
        "--validation-frequency-shards",
        str(args.validation_frequency_shards),
        "--encoder-version",
        "v20",
        "--encoder-checkpoint",
        checkpoint,
        "--run-name",
        run_name,
        "--wandb-project",
        args.wandb_project,
        "--output-root",
        args.output_root,
        "--learning-rate",
        str(args.learning_rate),
        "--flat-threshold-bps",
        str(args.flat_threshold_bps),
        "--strong-threshold-bps",
        str(args.strong_threshold_bps),
    ]
    command.append("--preload-shards-to-ram" if args.preload_shards_to_ram else "--no-preload-shards-to-ram")
    if args.max_batches_per_shard:
        command.extend(["--max-batches-per-shard", str(args.max_batches_per_shard)])
    print("Equivalent command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
