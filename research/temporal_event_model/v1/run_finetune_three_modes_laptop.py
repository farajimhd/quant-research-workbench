from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "checkpoint_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_finetune_laptop",
    "wandb_project": "June2026-event-encoder-linear-probes-finetune",
    "batch_size": 1024,
    "epochs": 5,
    "learning_rate": 4e-4,
    "lr_decay": 0.9,
    "validation_batches": 10,
    "modes": "bottleneck,full,scratch_full",
    "run_prefix": "tickextrema",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the three temporal v1 tick-extrema fine-tuning comparisons sequentially. "
            "Each child process runs in this terminal, so the normal Rich training UI remains visible. "
            "The default modes are bottleneck, full, and scratch_full."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", action="append", default=[], help="Explicit trained probe checkpoint; repeat if needed.")
    parser.add_argument("--checkpoint-root", default=DEFAULTS["checkpoint_root"])
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--modes", default=DEFAULTS["modes"], help="Comma-separated subset/order of: bottleneck, full, scratch_full.")
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--lr-decay", type=float, default=DEFAULTS["lr_decay"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--run-prefix", default=DEFAULTS["run_prefix"])
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--preload-shards-to-ram", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    requested_modes = [part.strip() for part in str(args.modes).split(",") if part.strip()]
    allowed_modes = {"bottleneck", "full", "scratch_full"}
    invalid_modes = [mode for mode in requested_modes if mode not in allowed_modes]
    if invalid_modes:
        raise ValueError(f"Unsupported --modes values: {invalid_modes}; expected only {sorted(allowed_modes)}")
    if not requested_modes:
        raise ValueError("--modes must contain at least one mode")

    script = Path(__file__).with_name("finetune_cache_probe_checkpoints.py")
    exit_code = 0
    for mode in requested_modes:
        command = [
            sys.executable,
            str(script),
            "--checkpoint-root",
            args.checkpoint_root,
            "--cache-root",
            args.cache_root,
            "--output-root",
            args.output_root,
            "--wandb-project",
            args.wandb_project,
            "--mode",
            mode,
            "--batch-size",
            str(args.batch_size),
            "--epochs",
            str(args.epochs),
            "--learning-rate",
            str(args.learning_rate),
            "--lr-decay",
            str(args.lr_decay),
            "--validation-batches",
            str(args.validation_batches),
            "--amp-dtype",
            args.amp_dtype,
            "--run-prefix",
            args.run_prefix,
        ]
        command.append("--preload-shards-to-ram" if args.preload_shards_to_ram else "--no-preload-shards-to-ram")
        if args.max_batches_per_shard:
            command.extend(["--max-batches-per-shard", str(args.max_batches_per_shard)])
        for checkpoint in args.checkpoint:
            command.extend(["--checkpoint", checkpoint])
        print("=" * 100, flush=True)
        print(f"Fine-tune mode: {mode}", flush=True)
        print("Equivalent command:", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
        print("=" * 100, flush=True)
        if args.print_only:
            continue
        exit_code = subprocess.call(command)
        if exit_code != 0:
            print(f"STOP mode={mode} failed with exit_code={exit_code}", flush=True)
            return int(exit_code)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
