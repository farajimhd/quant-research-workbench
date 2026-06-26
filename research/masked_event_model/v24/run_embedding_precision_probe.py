from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PRETRAIN_CACHE_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_pretrain_xonly_20260621_140813\train")
DEFAULT_PRETRAIN_VALIDATION_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_20260617_112833\validation")
DEFAULT_LABELED_CACHE_ROOT = Path(r"D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422")
DEFAULT_PRETRAIN_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\masked_event_model\\v24\pretrain_capacity_tests")
DEFAULT_PROBE_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_capacity_tests")
DEFAULT_PROBE_SCRIPT = Path(r"D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\cache_probe.py")
DEFAULT_PROBE_SCRIPT_FROM_LAPTOP = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\cache_probe.py"
)
DEFAULT_PRETRAIN_WANDB_PROJECT = "June2026-event-token-mae-capacity-tests"
DEFAULT_PROBE_WANDB_PROJECT = "June2026-event-encoder-linear-probes"
DEFAULT_RUN_PREFIX = "v24-capacity-1shard5ep"


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    embedding_dim: int
    bottleneck_force_fp32: bool


VARIANTS = (
    Variant(name="emb32-bf16", embedding_dim=32, bottleneck_force_fp32=False),
    Variant(name="emb32-fp32bottleneck", embedding_dim=32, bottleneck_force_fp32=True),
    Variant(name="emb128-bf16", embedding_dim=128, bottleneck_force_fp32=False),
    Variant(name="emb128-fp32bottleneck", embedding_dim=128, bottleneck_force_fp32=True),
)


def main() -> int:
    args = parse_args()
    selected = select_variants(args.only)
    commands: list[tuple[str, str, list[str]]] = []
    for variant in selected:
        pretrain_run_name = pretrain_run_name_for(args, variant)
        probe_run_name = probe_run_name_for(args, variant)
        checkpoint = checkpoint_path(args.pretrain_output_root, pretrain_run_name, args.epochs)
        commands.append(("pretrain", variant.name, build_pretrain_command(args, variant, pretrain_run_name)))
        commands.append(("linear_probe", variant.name, build_probe_command(args, variant, probe_run_name, checkpoint)))

    print("=" * 104, flush=True)
    print("v24 embedding-dimension / bottleneck-precision probe", flush=True)
    print(f"variants={','.join(variant.name for variant in selected)}", flush=True)
    print(f"pretrain_cache={args.pretrain_cache_root}", flush=True)
    print(f"labeled_cache={args.labeled_cache_root}", flush=True)
    print(f"batch_size={args.batch_size:,} pretrain_epochs={args.epochs:,} probe_epochs={args.probe_epochs:,}", flush=True)
    print("=" * 104, flush=True)
    for stage, variant_name, command in commands:
        print(f"\nCOMMAND [{stage} {variant_name}]", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0

    for variant in selected:
        pretrain_run_name = pretrain_run_name_for(args, variant)
        probe_run_name = probe_run_name_for(args, variant)
        checkpoint = checkpoint_path(args.pretrain_output_root, pretrain_run_name, args.epochs)
        if args.skip_existing_pretrain and checkpoint.exists():
            print(f"SKIP PRETRAIN [{variant.name}] existing_checkpoint={checkpoint}", flush=True)
        else:
            rc = run_command("pretrain", variant.name, build_pretrain_command(args, variant, pretrain_run_name))
            if rc != 0:
                return rc
        resolved_checkpoint = resolve_checkpoint(args.pretrain_output_root, pretrain_run_name, args.epochs)
        rc = run_command("linear_probe", variant.name, build_probe_command(args, variant, probe_run_name, resolved_checkpoint))
        if rc != 0:
            return rc
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sequentially pretrain v24 embedding/precision variants on one x-only shard, "
            "then run the same temporal v1 linear probe for each resulting checkpoint."
        )
    )
    parser.add_argument("--only", default="all", help="Comma list of variants or 'all'. Variants: " + ",".join(v.name for v in VARIANTS))
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
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16"), default="bf16")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repeatable-randomness", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--seed", type=int, default=17)
    return parser.parse_args()


def select_variants(value: str) -> list[Variant]:
    if value.strip().lower() == "all":
        return list(VARIANTS)
    requested = {part.strip() for part in value.split(",") if part.strip()}
    by_name = {variant.name: variant for variant in VARIANTS}
    unknown = sorted(requested.difference(by_name))
    if unknown:
        raise SystemExit(f"Unknown variants: {unknown}. Valid variants: {sorted(by_name)}")
    return [variant for variant in VARIANTS if variant.name in requested]


def pretrain_run_name_for(args: argparse.Namespace, variant: Variant) -> str:
    precision = "fp32bn" if variant.bottleneck_force_fp32 else "bf16bn"
    return f"{args.run_prefix}-{precision}-emb{variant.embedding_dim}-bs{args.batch_size}-pretrain"


def probe_run_name_for(args: argparse.Namespace, variant: Variant) -> str:
    precision = "fp32bn" if variant.bottleneck_force_fp32 else "bf16bn"
    return f"{args.run_prefix}-{precision}-emb{variant.embedding_dim}-bs{args.batch_size}-linearprobe"


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


def build_pretrain_command(args: argparse.Namespace, variant: Variant, run_name: str) -> list[str]:
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
        str(variant.embedding_dim),
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
    ]
    command.append("--compile-model" if args.compile_model else "--no-compile-model")
    command.append("--repeatable-randomness" if args.repeatable_randomness else "--no-repeatable-randomness")
    command.append("--bottleneck-force-fp32" if variant.bottleneck_force_fp32 else "--no-bottleneck-force-fp32")
    return command


def build_probe_command(args: argparse.Namespace, variant: Variant, run_name: str, checkpoint: Path) -> list[str]:
    validation_frequency_steps = validation_frequency_steps_for(args)
    script = resolve_probe_script(args.probe_script)
    command = [
        sys.executable,
        str(script),
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
        "v24",
        "--encoder-checkpoint",
        str(checkpoint),
        "--encoder-embedding-dim",
        str(variant.embedding_dim),
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
    ]
    command.append("--encoder-bottleneck-force-fp32" if variant.bottleneck_force_fp32 else "--no-encoder-bottleneck-force-fp32")
    return command


def validation_frequency_steps_for(args: argparse.Namespace) -> int:
    samples = int(args.validation_frequency_samples)
    if samples <= 0:
        return 0
    return max(1, (samples + int(args.batch_size) - 1) // int(args.batch_size))


def resolve_probe_script(configured: Path) -> Path:
    configured = Path(configured)
    if configured.exists():
        return configured
    if DEFAULT_PROBE_SCRIPT_FROM_LAPTOP.exists():
        return DEFAULT_PROBE_SCRIPT_FROM_LAPTOP
    repo_root = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
    fallback = repo_root / "research" / "temporal_event_model" / "v1" / "cache_probe.py"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"Temporal probe script not found. Tried {configured} and {fallback}")


def run_command(stage: str, variant_name: str, command: list[str]) -> int:
    print("\n" + "=" * 104, flush=True)
    print(f"RUN START [{stage} {variant_name}]", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    print("=" * 104, flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        print(f"RUN FAILED [{stage} {variant_name}] returncode={completed.returncode}", flush=True)
    else:
        print(f"RUN DONE [{stage} {variant_name}]", flush=True)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
