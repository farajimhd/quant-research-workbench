from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_ARGS: dict[str, str | int | float | bool] = {
    "cache-root": "D:/market-data/prepared/rolling_ticker_month_cache/train_201902_201912_ticker_month",
    "output-root": "D:/TradingML/runtimes/temporal_event_model/v3/train",
    "dataset-id": "temporal_v3_201902_201912_v1",
    "split": "train",
    "val-split": "validation",
    "months": "2019-02,2019-03",
    "batch-size": 256,
    "max-steps": 1000,
    "max-origins-per-epoch": 1000000,
    "read-workers": 4,
    "materialize-workers": 8,
    "loaded-parts-per-group": 8,
    "d-model": 256,
    "event-layers": 4,
    "event-heads": 8,
    "fusion-layers": 3,
    "fusion-heads": 8,
    "learning-rate": 3e-4,
    "weight-decay": 0.01,
    "validation-steps": 2000,
    "validation-batches": 8,
    "checkpoint-latest-steps": 100,
    "checkpoint-archive-steps": 2000,
    "progress-layout": "rich",
    "wandb-project": "temporal-event-model-v3",
    "wandb-mode": "auto",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch temporal_event_model v3 training with visible defaults.")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved command without executing it.")
    parser.add_argument("overrides", nargs=argparse.REMAINDER, help="Arguments passed through to train.py after --.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = Path(__file__).with_name("train.py")
    command = [sys.executable, "-u", str(script)]
    for key, value in DEFAULT_ARGS.items():
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                command.append(flag)
            continue
        command.extend([flag, str(value)])
    overrides = list(args.overrides)
    if overrides and overrides[0] == "--":
        overrides = overrides[1:]
    command.extend(overrides)
    printable = " ".join(shlex.quote(part) for part in command)
    print(printable, flush=True)
    if args.dry_run:
        return 0
    return int(subprocess.call(command))


if __name__ == "__main__":
    raise SystemExit(main())
