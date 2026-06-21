from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "train_start_shard": 0,
    "train_max_shards": 10,
    "validation_start_shard": 10,
    "validation_max_shards": 1,
    "validation_batches": 32,
    "batch_size": 512,
    "epochs": 3,
    "encoder_version": "v20",
    "encoder_checkpoint": (
        r"D:\TradingML\runtimes\masked_event_model\v20\pretrain"
        r"\v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs"
        r"\checkpoints\checkpoint_latest.pt"
    ),
    "run_name": "v1-cache-probe-v20-latest-ychunk-bce-bs512",
    "wandb_project": "June2026-event-encoder-linear-probes",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Launcher for temporal v1 v2-cache price probe.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--train-start-shard", type=int, default=DEFAULTS["train_start_shard"])
    parser.add_argument("--train-max-shards", type=int, default=DEFAULTS["train_max_shards"])
    parser.add_argument("--validation-start-shard", type=int, default=DEFAULTS["validation_start_shard"])
    parser.add_argument("--validation-max-shards", type=int, default=DEFAULTS["validation_max_shards"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--encoder-version", default=DEFAULTS["encoder_version"])
    parser.add_argument("--encoder-checkpoint", default=DEFAULTS["encoder_checkpoint"])
    parser.add_argument("--run-name", default=DEFAULTS["run_name"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--flat-threshold-bps", type=float, default=2.0)
    parser.add_argument("--strong-threshold-bps", type=float, default=20.0)
    args = parser.parse_args()

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
        "--encoder-version",
        args.encoder_version,
        "--encoder-checkpoint",
        args.encoder_checkpoint,
        "--run-name",
        args.run_name,
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
    if args.max_batches_per_shard:
        command.extend(["--max-batches-per-shard", str(args.max_batches_per_shard)])
    print("Equivalent command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
