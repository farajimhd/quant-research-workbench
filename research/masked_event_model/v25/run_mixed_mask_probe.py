from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_PRETRAIN_CACHE_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_pretrain_xonly_20260621_140813\train")
DEFAULT_PRETRAIN_VALIDATION_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_20260617_112833\validation")
DEFAULT_LABELED_CACHE_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422")
DEFAULT_PRETRAIN_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\masked_event_model\v25\pretrain_mask_schedule_tests")
DEFAULT_PROBE_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_v25_mask_tests")
DEFAULT_PROBE_SCRIPT = Path(r"D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\cache_probe.py")
DEFAULT_PRETRAIN_WANDB_PROJECT = "June2026-event-token-mae-capacity-tests"
DEFAULT_PROBE_WANDB_PROJECT = "June2026-event-encoder-linear-probes"
DEFAULT_RUN_PREFIX = "v25-mixedmask-emb32-1shard5ep"


def main() -> int:
    args = parse_args()
    pretrain_run_name = f"{args.run_prefix}-pretrain"
    probe_run_name = f"{args.run_prefix}-linearprobe"
    checkpoint = checkpoint_path(args.pretrain_output_root, pretrain_run_name, args.epochs)
    commands = [
        ("pretrain", build_pretrain_command(args, pretrain_run_name)),
        ("linear_probe", build_probe_command(args, probe_run_name, checkpoint)),
    ]

    print("=" * 104, flush=True)
    print("v25 mixed-mask emb32 pretrain + linear probe", flush=True)
    print("mask_schedule=mixed high=70% U[0.50,0.80], zero=10%, low=20% U[0.01,0.50]", flush=True)
    print(f"pretrain_cache={args.pretrain_cache_root}", flush=True)
    print(f"labeled_cache={args.labeled_cache_root}", flush=True)
    print(f"batch_size={args.batch_size:,} pretrain_epochs={args.epochs:,} probe_epochs={args.probe_epochs:,}", flush=True)
    print("=" * 104, flush=True)
    for stage, command in commands:
        print(f"\nCOMMAND [{stage}]", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0

    if args.skip_existing_pretrain and checkpoint.exists():
        print(f"SKIP PRETRAIN existing_checkpoint={checkpoint}", flush=True)
    else:
        rc = run_command("pretrain", build_pretrain_command(args, pretrain_run_name))
        if rc != 0:
            return rc
    resolved_checkpoint = resolve_checkpoint(args.pretrain_output_root, pretrain_run_name, args.epochs)
    return run_command("linear_probe", build_probe_command(args, probe_run_name, resolved_checkpoint))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train v25 emb32 with a mixed event-mask schedule on one pretraining shard for 5 epochs, "
            "then run the standard temporal v1 labeled-cache linear probe."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--skip-existing-pretrain", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--pretrain-cache-root", type=Path, default=DEFAULT_PRETRAIN_CACHE_ROOT)
    parser.add_argument("--pretrain-validation-root", type=Path, default=DEFAULT_PRETRAIN_VALIDATION_ROOT)
    parser.add_argument("--labeled-cache-root", type=Path, default=DEFAULT_LABELED_CACHE_ROOT)
    parser.add_argument("--pretrain-output-root", type=Path, default=DEFAULT_PRETRAIN_OUTPUT_ROOT)
    parser.add_argument("--probe-output-root", type=Path, default=DEFAULT_PROBE_OUTPUT_ROOT)
    parser.add_argument("--probe-script", type=Path, default=DEFAULT_PROBE_SCRIPT)
    parser.add_argument("--pretrain-wandb-project", default=DEFAULT_PRETRAIN_WANDB_PROJECT)
    parser.add_argument("--probe-wandb-project", default=DEFAULT_PROBE_WANDB_PROJECT)
    parser.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--probe-epochs", type=int, default=5)
    parser.add_argument("--pretrain-train-start-shard", type=int, default=0)
    parser.add_argument("--pretrain-train-shards", type=int, default=1)
    parser.add_argument("--pretrain-validation-shard-index", type=int, default=0)
    parser.add_argument("--pretrain-validation-batches", type=int, default=2)
    parser.add_argument("--probe-train-start-shard", type=int, default=0)
    parser.add_argument("--probe-train-max-shards", type=int, default=1)
    parser.add_argument("--probe-validation-start-shard", type=int, default=1)
    parser.add_argument("--probe-validation-max-shards", type=int, default=1)
    parser.add_argument("--probe-validation-batches", type=int, default=10)
    parser.add_argument("--validation-frequency-samples", type=int, default=512_000)
    parser.add_argument("--learning-rate", type=float, default=4e-4)
    parser.add_argument("--probe-learning-rate", type=float, default=1e-3)
    parser.add_argument("--header-loss-weight", type=float, default=0.25)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16"), default="bf16")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--seed", type=int, default=17)
    return parser.parse_args()


def build_pretrain_command(args: argparse.Namespace, run_name: str) -> list[str]:
    script = Path(__file__).with_name("train_full_pretrain.py")
    command = [
        sys.executable,
        str(script),
        "--cache-root",
        str(args.pretrain_cache_root),
        "--validation-cache-root",
        str(args.pretrain_validation_root),
        "--train-start-shard",
        str(args.pretrain_train_start_shard),
        "--train-shards",
        str(args.pretrain_train_shards),
        "--validation-shard-index",
        str(args.pretrain_validation_shard_index),
        "--validation-batches",
        str(args.pretrain_validation_batches),
        "--epochs",
        str(args.epochs),
        "--batch-size",
        str(args.batch_size),
        "--embedding-dim",
        "32",
        "--event-mask-schedule",
        "mixed",
        "--event-mask-high-probability",
        "0.70",
        "--event-mask-high-min",
        "0.50",
        "--event-mask-high-max",
        "0.80",
        "--event-mask-zero-probability",
        "0.10",
        "--event-mask-low-probability",
        "0.20",
        "--event-mask-low-min",
        "0.01",
        "--event-mask-low-max",
        "0.50",
        "--header-loss-weight",
        str(args.header_loss_weight),
        "--learning-rate",
        str(args.learning_rate),
        "--amp-dtype",
        str(args.amp_dtype),
        "--run-name",
        run_name,
        "--wandb-project",
        str(args.pretrain_wandb_project),
        "--wandb-mode",
        str(args.wandb_mode),
        "--progress-layout",
        str(args.progress_layout),
        "--output-root",
        str(args.pretrain_output_root),
        "--seed",
        str(args.seed),
        "--fresh-start",
        "--warm-start-checkpoint",
        "",
        "--no-warm-start-load-optimizer",
        "--no-bottleneck-force-fp32",
    ]
    command.append("--compile-model" if args.compile_model else "--no-compile-model")
    command.append("--repeatable-randomness" if args.repeatable_randomness else "--no-repeatable-randomness")
    return command


def build_probe_command(args: argparse.Namespace, run_name: str, checkpoint: Path) -> list[str]:
    validation_frequency_steps = validation_frequency_steps_for(args.validation_frequency_samples, args.batch_size)
    command = [
        sys.executable,
        str(args.probe_script),
        "--cache-root",
        str(args.labeled_cache_root),
        "--train-start-shard",
        str(args.probe_train_start_shard),
        "--train-max-shards",
        str(args.probe_train_max_shards),
        "--validation-start-shard",
        str(args.probe_validation_start_shard),
        "--validation-max-shards",
        str(args.probe_validation_max_shards),
        "--validation-batches",
        str(args.probe_validation_batches),
        "--batch-size",
        str(args.batch_size),
        "--epochs",
        str(args.probe_epochs),
        "--validation-frequency-steps",
        str(validation_frequency_steps),
        "--validation-frequency-shards",
        "0",
        "--encoder-version",
        "v25",
        "--encoder-checkpoint",
        str(checkpoint),
        "--extra-research-root",
        str(version_runtime_root()),
        "--encoder-embedding-dim",
        "32",
        "--run-name",
        run_name,
        "--wandb-project",
        str(args.probe_wandb_project),
        "--wandb-mode",
        str(args.wandb_mode),
        "--output-root",
        str(args.probe_output_root),
        "--learning-rate",
        str(args.probe_learning_rate),
        "--amp-dtype",
        str(args.amp_dtype),
        "--seed",
        str(args.seed),
        "--preload-shards-to-ram",
        "--no-encoder-bottleneck-force-fp32",
    ]
    return command


def checkpoint_path(output_root: Path, run_name: str, epochs: int) -> Path:
    return Path(output_root) / run_name / "checkpoints" / f"checkpoint_epoch_{int(epochs):03d}.pt"


def resolve_checkpoint(output_root: Path, run_name: str, epochs: int) -> Path:
    expected = checkpoint_path(output_root, run_name, epochs)
    if expected.exists():
        return expected
    latest = Path(output_root) / run_name / "checkpoints" / "checkpoint_latest.pt"
    if latest.exists():
        return latest
    raise FileNotFoundError(f"No pretrain checkpoint found for {run_name}: tried {expected} and {latest}")


def validation_frequency_steps_for(samples: int, batch_size: int) -> int:
    samples = int(samples)
    if samples <= 0:
        return 0
    return max(1, (samples + int(batch_size) - 1) // int(batch_size))


def version_runtime_root() -> Path:
    return next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])


def run_command(label: str, command: list[str]) -> int:
    print("\n" + "=" * 104, flush=True)
    print(f"RUN START [{label}]", flush=True)
    print("=" * 104, flush=True)
    rc = subprocess.call(command)
    if rc != 0:
        print(f"RUN FAILED [{label}] returncode={rc}", flush=True)
    else:
        print(f"RUN DONE [{label}]", flush=True)
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
