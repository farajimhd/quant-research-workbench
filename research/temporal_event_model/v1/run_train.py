from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.temporal_event_model.v1.config import LossConfig

TRAIN = Path(__file__).with_name("train.py")
LOSS_DEFAULTS = LossConfig()


DEFAULTS = {
    "batch_size": 256,
    "max_steps": 10000,
    "epochs": 1,
    "context_chunks": 64,
    "target_chunks": 1,
    "window_days": 15,
    "context_lag_schedule": "dense_geometric",
    "context_dense_fraction": 0.5,
    "context_max_lag_steps": 512,
    "train_stride_choices": "16,32,64,128",
    "validation_stride_choices": "16,32,64,128",
    "encoder_version": "v7",
    "encoder_checkpoint": "",
    "event_weight": LOSS_DEFAULTS.event_weight,
    "header_weight": LOSS_DEFAULTS.header_weight,
    "wandb_project": "June2026-single-ticker-temporal-event-model",
    "wandb_mode": "online",
    "device": "cuda",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for temporal_event_model/v1 training.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--max-steps", type=int, default=DEFAULTS["max_steps"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    parser.add_argument("--encoder-version", choices=("v6", "v7", "v8"), default=DEFAULTS["encoder_version"])
    parser.add_argument("--encoder-checkpoint", default=DEFAULTS["encoder_checkpoint"])
    parser.add_argument("--context-chunks", type=int, default=DEFAULTS["context_chunks"])
    parser.add_argument("--context-lag-schedule", choices=("dense_geometric", "consecutive"), default=DEFAULTS["context_lag_schedule"])
    parser.add_argument("--context-dense-fraction", type=float, default=DEFAULTS["context_dense_fraction"])
    parser.add_argument("--context-max-lag-steps", type=int, default=DEFAULTS["context_max_lag_steps"])
    parser.add_argument("--event-weight", type=float, default=DEFAULTS["event_weight"])
    parser.add_argument("--header-weight", type=float, default=DEFAULTS["header_weight"])
    parser.add_argument("--wandb-project", default=DEFAULTS["wandb_project"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--run-name", default="")
    parser.add_argument("--device", default=DEFAULTS["device"])
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    command = [
        sys.executable,
        "-u",
        str(TRAIN),
        "--batch-size",
        str(args.batch_size),
        "--max-steps",
        str(args.max_steps),
        "--epochs",
        str(args.epochs),
        "--context-chunks",
        str(args.context_chunks),
        "--target-chunks",
        str(DEFAULTS["target_chunks"]),
        "--window-days",
        str(DEFAULTS["window_days"]),
        "--context-lag-schedule",
        str(args.context_lag_schedule),
        "--context-dense-fraction",
        str(args.context_dense_fraction),
        "--context-max-lag-steps",
        str(args.context_max_lag_steps),
        "--train-stride-choices",
        str(DEFAULTS["train_stride_choices"]),
        "--validation-stride-choices",
        str(DEFAULTS["validation_stride_choices"]),
        "--encoder-version",
        args.encoder_version,
        "--event-weight",
        str(args.event_weight),
        "--header-weight",
        str(args.header_weight),
        "--wandb-project",
        args.wandb_project,
        "--wandb-mode",
        args.wandb_mode,
        "--device",
        args.device,
    ]
    if args.encoder_checkpoint:
        command.extend(["--encoder-checkpoint", args.encoder_checkpoint])
    if args.run_name:
        command.extend(["--wandb-run-name", args.run_name])
    if args.dry_run:
        command.append("--dry-run")
    command.extend(args.extra)
    print("Equivalent command:", flush=True)
    print(" ".join(f'"{part}"' if " " in part else part for part in command), flush=True)
    if args.print_only:
        return
    raise SystemExit(subprocess.call(command, cwd=str(REPO_ROOT)))


if __name__ == "__main__":
    main()
