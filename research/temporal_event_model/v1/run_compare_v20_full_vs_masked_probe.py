from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULTS = {
    "cache_root": r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422",
    "output_root": r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe",
    "encoder_checkpoint": (
        r"D:\TradingML\runtimes\masked_event_model\v20\pretrain"
        r"\v20-fullpretrain-xonly100-fixedmask070-emb32-bs8192-lr4e4-3epochs-freshrng-from-mixed-latest"
        r"\checkpoints\checkpoint_step_000305100.pt"
    ),
    "wandb_project": "June2026-event-encoder-linear-probes",
    "run_prefix": "v1-v20-xonly100-step305100",
    "train_start_shard": 0,
    "train_max_shards": 1,
    "validation_start_shard": 1,
    "validation_max_shards": 1,
    "validation_batches": 10,
    "batch_size": 1024,
    "epochs": 5,
    "learning_rate": 1e-3,
    "validation_frequency_steps": 500,
    "validation_frequency_shards": 0,
    "amp_dtype": "bf16",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run two temporal v1 linear probes on the same data/checkpoint: "
            "normal full-grid v20 encoding, then MAE-style random 70% masked encoding."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--only", choices=("both", "full", "masked70"), default="both")
    parser.add_argument("--cache-root", default=DEFAULTS["cache_root"])
    parser.add_argument("--output-root", default=DEFAULTS["output_root"])
    parser.add_argument("--encoder-checkpoint", default=DEFAULTS["encoder_checkpoint"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--run-prefix", default=DEFAULTS["run_prefix"])
    parser.add_argument("--train-start-shard", type=int, default=DEFAULTS["train_start_shard"])
    parser.add_argument("--train-max-shards", type=int, default=DEFAULTS["train_max_shards"])
    parser.add_argument("--validation-start-shard", type=int, default=DEFAULTS["validation_start_shard"])
    parser.add_argument("--validation-max-shards", type=int, default=DEFAULTS["validation_max_shards"])
    parser.add_argument("--validation-batches", type=int, default=DEFAULTS["validation_batches"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=DEFAULTS["learning_rate"])
    parser.add_argument("--validation-frequency-steps", type=int, default=DEFAULTS["validation_frequency_steps"])
    parser.add_argument("--validation-frequency-shards", type=int, default=DEFAULTS["validation_frequency_shards"])
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default=DEFAULTS["amp_dtype"])
    parser.add_argument("--preload-shards-to-ram", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    variants = []
    if args.only in {"both", "full"}:
        variants.append(("fullgrid", "full", 0.0))
    if args.only in {"both", "masked70"}:
        variants.append(("masked70", "random_visible", 0.70))

    commands = [build_command(args, suffix, mode, ratio) for suffix, mode, ratio in variants]
    print("=" * 100, flush=True)
    print("Temporal v1 v20 full-grid vs masked-visible linear probe comparison", flush=True)
    print(f"checkpoint={args.encoder_checkpoint}", flush=True)
    print(f"cache_root={args.cache_root}", flush=True)
    print(f"wandb_project={args.wandb_project}", flush=True)
    print("=" * 100, flush=True)
    for label, command in commands:
        print(f"\nCOMMAND [{label}]", flush=True)
        print(subprocess.list2cmdline(command), flush=True)

    if args.print_only:
        return 0

    for label, command in commands:
        print("\n" + "=" * 100, flush=True)
        print(f"RUN START [{label}]", flush=True)
        print("=" * 100, flush=True)
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            print(f"RUN FAILED [{label}] returncode={completed.returncode}", flush=True)
            return int(completed.returncode)
        print(f"RUN DONE [{label}]", flush=True)
    return 0


def build_command(args: argparse.Namespace, suffix: str, visible_mode: str, visible_mask_ratio: float) -> tuple[str, list[str]]:
    script = Path(__file__).with_name("cache_probe.py")
    run_name = f"{args.run_prefix}-{suffix}-1train1val-{args.epochs}ep-bs{args.batch_size}"
    command = [
        sys.executable,
        str(script),
        "--cache-root",
        str(args.cache_root),
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
        str(args.encoder_checkpoint),
        "--encoder-visible-mode",
        visible_mode,
        "--encoder-visible-mask-ratio",
        str(visible_mask_ratio),
        "--run-name",
        run_name,
        "--wandb-project",
        str(args.wandb_project),
        "--output-root",
        str(args.output_root),
        "--learning-rate",
        str(args.learning_rate),
        "--amp-dtype",
        str(args.amp_dtype),
    ]
    command.append("--preload-shards-to-ram" if args.preload_shards_to_ram else "--no-preload-shards-to-ram")
    if int(args.max_batches_per_shard) > 0:
        command.extend(["--max-batches-per-shard", str(args.max_batches_per_shard)])
    return suffix, command


if __name__ == "__main__":
    raise SystemExit(main())
