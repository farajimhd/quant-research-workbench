from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "checkpoint_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop_eval",
    "validation_start_shard": 1,
    "validation_batches": 10,
    "batch_size": 1024,
    "seed": 20260621,
    "amp_dtype": "bf16",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Laptop launcher for fixed temporal v1 cache-probe evaluation. "
            "By default it evaluates the three newest probe checkpoint_latest.pt files "
            "on the same 10x1024 samples from the second cache shard."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", action="append", default=[], help="Explicit checkpoint path; repeat for each model.")
    parser.add_argument("--checkpoint-root", default=DEFAULTS["checkpoint_root"])
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--run-name", default="fixed-shard1-10x1024")
    parser.add_argument("--validation-start-shard", type=int, default=DEFAULTS["validation_start_shard"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default=DEFAULTS["amp_dtype"])
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    script = Path(__file__).with_name("evaluate_cache_probe_checkpoints.py")
    command = [
        sys.executable,
        str(script),
        "--checkpoint-root",
        args.checkpoint_root,
        "--cache-root",
        args.cache_root,
        "--output-root",
        args.output_root,
        "--run-name",
        args.run_name,
        "--validation-start-shard",
        str(args.validation_start_shard),
        "--validation-batches",
        str(args.validation_batches),
        "--batch-size",
        str(args.batch_size),
        "--seed",
        str(args.seed),
        "--amp-dtype",
        args.amp_dtype,
        "--device",
        args.device,
    ]
    for checkpoint in args.checkpoint:
        command.extend(["--checkpoint", checkpoint])
    print("Equivalent command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
