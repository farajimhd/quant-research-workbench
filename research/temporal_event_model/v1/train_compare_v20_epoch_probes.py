from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe",
    "wandb_project": "June2026-event-encoder-linear-probes",
    "train_start_shard": 0,
    "train_max_shards": 10,
    "validation_start_shard": 10,
    "validation_max_shards": 1,
    "validation_batches": 32,
    "batch_size": 512,
    "epochs": 3,
    "epoch1_checkpoint": (
        r"D:\TradingML\runtimes\masked_event_model\v20\pretrain"
        r"\v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs"
        r"\checkpoints\checkpoint_step_000130176.pt"
    ),
    "epoch2_checkpoint": (
        r"D:\TradingML\runtimes\masked_event_model\v20\pretrain"
        r"\v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs"
        r"\checkpoints\checkpoint_step_000260352.pt"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run two temporal v1 cache-probe experiments with identical data and "
            "different frozen v20 checkpoints."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--train-start-shard", type=int, default=DEFAULTS["train_start_shard"])
    parser.add_argument("--train-max-shards", type=int, default=DEFAULTS["train_max_shards"])
    parser.add_argument("--validation-start-shard", type=int, default=DEFAULTS["validation_start_shard"])
    parser.add_argument("--validation-max-shards", type=int, default=DEFAULTS["validation_max_shards"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--epoch1-checkpoint", default=DEFAULTS["epoch1_checkpoint"])
    parser.add_argument("--epoch2-checkpoint", default=DEFAULTS["epoch2_checkpoint"])
    args = parser.parse_args()

    script = Path(__file__).with_name("cache_probe.py")
    runs = [
        ("v1-cache-probe-v20-epoch1-ychunk-bce-bs512", args.epoch1_checkpoint),
        ("v1-cache-probe-v20-epoch2-ychunk-bce-bs512", args.epoch2_checkpoint),
    ]
    for run_name, checkpoint in runs:
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
            "v20",
            "--encoder-checkpoint",
            checkpoint,
            "--run-name",
            run_name,
            "--wandb-project",
            args.wandb_project,
            "--output-root",
            args.output_root,
        ]
        if args.max_batches_per_shard:
            command.extend(["--max-batches-per-shard", str(args.max_batches_per_shard)])
        print("=" * 100, flush=True)
        print(subprocess.list2cmdline(command), flush=True)
        if args.print_only:
            continue
        result = subprocess.call(command)
        if result != 0:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
