from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()),
    Path(__file__).resolve().parents[3],
)
TRAIN = Path(__file__).with_name("train.py")


DEFAULT_PROJECT = "June2026-temporal-v1-v6-v8-encoder-compare"
DEFAULT_CHECKPOINT_NAME = "checkpoint_step_000020340.pt"
DEFAULT_V6_RUN = "v6-semantic-sumdivbatch-emb32-bs4096-10shards"
DEFAULT_V8_RUN = "v8-semantic-sumdivbatch-emb32-bs4096-10shards-fixedmaskedratio"
DEFAULT_RUNTIMES_ROOT = Path(r"D:\TradingML\runtimes")
LAPTOP_WORKSTATION_RUNTIMES_ROOT = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train temporal_event_model/v1 twice under identical settings, once "
            "with the v6 event encoder checkpoint and once with the v8 fixed-mask "
            "event encoder checkpoint."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-missing-checkpoints", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--models", default="v6,v8", help="Comma-separated subset/order: v6,v8")
    parser.add_argument("--runtimes-root", default=str(DEFAULT_RUNTIMES_ROOT))
    parser.add_argument("--checkpoint-name", default=DEFAULT_CHECKPOINT_NAME)
    parser.add_argument("--v6-run-name", default=DEFAULT_V6_RUN)
    parser.add_argument("--v8-run-name", default=DEFAULT_V8_RUN)
    parser.add_argument("--v6-checkpoint", default="")
    parser.add_argument("--v8-checkpoint", default="")
    parser.add_argument("--wandb-project", default=DEFAULT_PROJECT)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--v6-temporal-run-name", default="temporal-v1-v6-sumdivbatch-step20340")
    parser.add_argument("--v8-temporal-run-name", default="temporal-v1-v8-fixedmask-step20340")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--context-chunks", type=int, default=64)
    parser.add_argument("--target-chunks", type=int, default=1)
    parser.add_argument("--window-days", type=int, default=15)
    parser.add_argument("--context-lag-schedule", choices=("dense_geometric", "consecutive"), default="dense_geometric")
    parser.add_argument("--context-dense-fraction", type=float, default=0.5)
    parser.add_argument("--context-max-lag-steps", type=int, default=512)
    parser.add_argument("--train-stride-choices", default="16,32,64,128")
    parser.add_argument("--validation-stride-choices", default="16,32,64,128")
    parser.add_argument("--validation-frequency", type=int, default=250)
    parser.add_argument("--validation-blocks", type=int, default=8)
    parser.add_argument("--validation-batches-per-block", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--scheduler-t0-steps", type=int, default=1000)
    parser.add_argument("--scheduler-t-mult", type=int, default=2)
    parser.add_argument("--scheduler-eta-min", type=float, default=1e-6)
    parser.add_argument("--checkpoint-latest-steps", type=int, default=25)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=5000)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--detailed-metrics-steps", type=int, default=100)
    parser.add_argument("--progress-layout", choices=("auto", "text", "none"), default="auto")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--freeze-encoder", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fresh-start", action="store_true")
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    selected = parse_models(args.models)
    checkpoints = {
        "v6": Path(args.v6_checkpoint) if args.v6_checkpoint else checkpoint_path(args, "v6"),
        "v8": Path(args.v8_checkpoint) if args.v8_checkpoint else checkpoint_path(args, "v8"),
    }
    run_names = {
        "v6": args.v6_temporal_run_name,
        "v8": args.v8_temporal_run_name,
    }

    print("=" * 112, flush=True)
    print("temporal_event_model/v1 paired encoder comparison", flush=True)
    print(f"wandb_project={args.wandb_project} models={','.join(selected)}", flush=True)
    print(f"common seed={args.seed} batch_size={args.batch_size} max_steps={args.max_steps} epochs={args.epochs}", flush=True)
    print("encoder architecture forced to d_byte=40 d_model=256 emb=32 heads=8 enc=10 dec=4 ffn_mult=4 dropout=0.08", flush=True)
    for model_name in selected:
        raw = checkpoints[model_name]
        resolved = resolve_existing_checkpoint(raw)
        status = "found" if resolved is not None else "missing"
        print(f"{model_name} checkpoint={raw} status={status}", flush=True)
        if resolved is not None and resolved != raw:
            print(f"  found via laptop share: {resolved}", flush=True)
    print("=" * 112, flush=True)

    if not args.allow_missing_checkpoints and not args.print_only:
        missing = [name for name in selected if resolve_existing_checkpoint(checkpoints[name]) is None]
        if missing:
            details = "\n".join(f"  {name}: {checkpoints[name]}" for name in missing)
            raise SystemExit(
                "Missing required encoder checkpoint(s). Re-run after the checkpoint exists, "
                "or pass explicit --v6-checkpoint/--v8-checkpoint.\n" + details
            )

    commands = [(name, build_train_command(args, name, checkpoints[name], run_names[name])) for name in selected]
    for name, command in commands:
        print(f"\n{name.upper()} equivalent command:", flush=True)
        print(format_command(command), flush=True)

    if args.print_only:
        return

    for name, command in commands:
        print("\n" + "=" * 112, flush=True)
        print(f"START {name.upper()} temporal run: {run_names[name]}", flush=True)
        print("=" * 112, flush=True)
        exit_code = subprocess.call(command, cwd=str(REPO_ROOT))
        if exit_code != 0:
            message = f"{name.upper()} temporal run failed with exit code {exit_code}."
            if args.continue_on_error:
                print("WARN " + message, flush=True)
                continue
            raise SystemExit(message)
        print(f"FINISH {name.upper()} temporal run: {run_names[name]}", flush=True)


def parse_models(value: str) -> list[str]:
    models = [part.strip().lower() for part in value.split(",") if part.strip()]
    allowed = {"v6", "v8"}
    invalid = [model for model in models if model not in allowed]
    if invalid:
        raise SystemExit(f"Unsupported models in --models: {invalid}. Expected v6 and/or v8.")
    if not models:
        raise SystemExit("--models must include at least one of v6,v8.")
    return models


def checkpoint_path(args: argparse.Namespace, model_name: str) -> Path:
    run_name = args.v6_run_name if model_name == "v6" else args.v8_run_name
    return Path(args.runtimes_root) / "masked_event_model" / model_name / "pretrain" / run_name / "checkpoints" / args.checkpoint_name


def resolve_existing_checkpoint(path: Path) -> Path | None:
    if path.exists():
        return path
    text = str(path)
    workstation_prefix = str(DEFAULT_RUNTIMES_ROOT)
    if text.lower().startswith(workstation_prefix.lower()):
        mapped = LAPTOP_WORKSTATION_RUNTIMES_ROOT / Path(text[len(workstation_prefix) :].lstrip("\\/"))
        if mapped.exists():
            return mapped
    return None


def build_train_command(args: argparse.Namespace, model_name: str, checkpoint: Path, run_name: str) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(TRAIN),
        "--encoder-version",
        model_name,
        "--encoder-checkpoint",
        str(checkpoint),
        "--encoder-d-byte",
        "40",
        "--encoder-d-model",
        "256",
        "--embedding-dim",
        "32",
        "--encoder-n-heads",
        "8",
        "--encoder-layers",
        "10",
        "--encoder-decoder-layers",
        "4",
        "--encoder-ffn-mult",
        "4",
        "--encoder-dropout",
        "0.08",
        "--batch-size",
        str(args.batch_size),
        "--max-steps",
        str(args.max_steps),
        "--epochs",
        str(args.epochs),
        "--seed",
        str(args.seed),
        "--device",
        args.device,
        "--context-chunks",
        str(args.context_chunks),
        "--target-chunks",
        str(args.target_chunks),
        "--window-days",
        str(args.window_days),
        "--context-lag-schedule",
        args.context_lag_schedule,
        "--context-dense-fraction",
        str(args.context_dense_fraction),
        "--context-max-lag-steps",
        str(args.context_max_lag_steps),
        "--train-stride-choices",
        str(args.train_stride_choices),
        "--validation-stride-choices",
        str(args.validation_stride_choices),
        "--validation-frequency",
        str(args.validation_frequency),
        "--validation-blocks",
        str(args.validation_blocks),
        "--validation-batches-per-block",
        str(args.validation_batches_per_block),
        "--learning-rate",
        str(args.learning_rate),
        "--scheduler-t0-steps",
        str(args.scheduler_t0_steps),
        "--scheduler-t-mult",
        str(args.scheduler_t_mult),
        "--scheduler-eta-min",
        str(args.scheduler_eta_min),
        "--checkpoint-latest-steps",
        str(args.checkpoint_latest_steps),
        "--checkpoint-archive-steps",
        str(args.checkpoint_archive_steps),
        "--logging-steps",
        str(args.logging_steps),
        "--detailed-metrics-steps",
        str(args.detailed_metrics_steps),
        "--progress-layout",
        args.progress_layout,
        "--wandb-project",
        args.wandb_project,
        "--wandb-entity",
        args.wandb_entity,
        "--wandb-mode",
        args.wandb_mode,
        "--wandb-run-name",
        run_name,
    ]
    command.append("--amp" if args.amp else "--no-amp")
    command.append("--compile-model" if args.compile_model else "--no-compile-model")
    command.append("--freeze-encoder" if args.freeze_encoder else "--no-freeze-encoder")
    if args.fresh_start:
        command.append("--fresh-start")
    if args.dry_run:
        command.append("--dry-run")
    command.extend(args.extra)
    return command


def format_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if any(char.isspace() for char in part) else part for part in command)


if __name__ == "__main__":
    main()
