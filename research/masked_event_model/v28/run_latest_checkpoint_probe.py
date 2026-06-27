from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_CHECKPOINT = Path(
    r"D:\TradingML\runtimes\masked_event_model\v28\pretrain"
    r"\v28-fullpretrain-dualcache-headerrecon-emb32-bs8192-lr4e4-1epoch-freshrng"
    r"\checkpoints\checkpoint_latest.pt"
)
DEFAULT_LABELED_CACHE_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422")
DEFAULT_PROBE_SCRIPT = Path(r"D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\cache_probe.py")
DEFAULT_PROBE_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_v28")
DEFAULT_WANDB_PROJECT = "June2026-event-encoder-linear-probes"
DEFAULT_RUN_NAME = "v1-cache-probe-v28-dualcache-step43731-1train1val-5ep-bs8192"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a temporal v1 linear probe on the latest v28 dual-cache "
            "pretraining checkpoint. The v28 encoder is frozen; only the probe "
            "head is trained."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", "--encoder-checkpoint", dest="checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_LABELED_CACHE_ROOT)
    parser.add_argument("--probe-script", type=Path, default=DEFAULT_PROBE_SCRIPT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PROBE_OUTPUT_ROOT)
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--wandb-project", default=DEFAULT_WANDB_PROJECT)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--train-start-shard", type=int, default=0)
    parser.add_argument("--train-max-shards", type=int, default=1)
    parser.add_argument("--validation-start-shard", type=int, default=1)
    parser.add_argument("--validation-max-shards", type=int, default=1)
    parser.add_argument("--validation-batches", type=int, default=10)
    parser.add_argument("--validation-frequency-samples", type=int, default=512_000)
    parser.add_argument("--batch-size", type=int, default=8192)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "off"), default="auto")
    parser.add_argument("--preload-shards-to-ram", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    command = build_command(args)
    print("=" * 104, flush=True)
    print("v28 latest-checkpoint temporal linear probe", flush=True)
    print(f"checkpoint={args.checkpoint}", flush=True)
    print(f"cache_root={args.cache_root}", flush=True)
    print(f"batch_size={args.batch_size:,} epochs={args.epochs:,}", flush=True)
    print(f"run_name={args.run_name} wandb_project={args.wandb_project} mode={args.wandb_mode}", flush=True)
    print("Equivalent command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    print("=" * 104, flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


def build_command(args: argparse.Namespace) -> list[str]:
    validation_frequency_steps = validation_frequency_steps_for(args.validation_frequency_samples, args.batch_size)
    command = [
        sys.executable,
        str(args.probe_script),
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
        str(validation_frequency_steps),
        "--validation-frequency-shards",
        "0",
        "--encoder-version",
        "v28",
        "--encoder-checkpoint",
        str(args.checkpoint),
        "--extra-research-root",
        str(version_runtime_root()),
        "--encoder-embedding-dim",
        "32",
        "--run-name",
        args.run_name,
        "--wandb-project",
        args.wandb_project,
        "--wandb-mode",
        args.wandb_mode,
        "--output-root",
        str(args.output_root),
        "--learning-rate",
        str(args.learning_rate),
        "--amp-dtype",
        args.amp_dtype,
        "--seed",
        str(args.seed),
        "--progress-layout",
        args.progress_layout,
        "--no-encoder-bottleneck-force-fp32",
    ]
    if bool(args.preload_shards_to_ram):
        command.append("--preload-shards-to-ram")
    else:
        command.append("--no-preload-shards-to-ram")
    return command


def validation_frequency_steps_for(samples: int, batch_size: int) -> int:
    samples = int(samples)
    if samples <= 0:
        return 0
    return max(1, (samples + int(batch_size) - 1) // int(batch_size))


def version_runtime_root() -> Path:
    return next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])


if __name__ == "__main__":
    raise SystemExit(main())
