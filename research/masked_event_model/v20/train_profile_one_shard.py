from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v20.train import main as train_main  # noqa: E402


DEFAULTS: dict[str, Any] = {
    "data_source": "sample_cache",
    "sample_cache_root": r"D:\market-data\prepared\event_sample_cache",
    "sample_cache_prefetch_shards": 1,
    "sample_cache_shuffle_records": True,
    "sample_cache_drop_last": True,
    "max_index_files": 1,
    "batch_size": 1024,
    "max_steps": 25,
    "epochs": 1,
    "pretrain_validation_frequency": 0,
    "pretrain_validation_steps": 0,
    "logging_steps": 1,
    "detailed_metrics_steps": 1,
    "profile_first_steps": 25,
    "profile_training_every_steps": 0,
    "profile_inference_every_steps": 1,
    "decoder_chunk_size": 0,
    "checkpoint_latest_steps": 0,
    "checkpoint_archive_steps": 0,
    "checkpoint_best_train": False,
    "checkpoint_best_val": False,
    "wandb_mode": "disabled",
    "wandb_run_name": "v20-one-shard-profile",
    "progress_layout": "auto",
    "device": "cuda",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile v20 training on one sample-cache shard.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--cache-root", default=DEFAULTS["sample_cache_root"])
    parser.add_argument("--steps", type=int, default=DEFAULTS["max_steps"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--device", default=DEFAULTS["device"])
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=DEFAULTS["progress_layout"])
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=DEFAULTS["wandb_mode"])
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    known, extra = parser.parse_known_args()
    known.extra = extra
    return known


def main() -> None:
    args = parse_args()
    values = dict(DEFAULTS)
    values["sample_cache_root"] = args.cache_root
    values["max_steps"] = args.steps
    values["profile_first_steps"] = args.steps
    values["batch_size"] = args.batch_size
    values["device"] = args.device
    values["progress_layout"] = args.progress_layout
    values["wandb_mode"] = args.wandb_mode
    argv = build_train_args(values)
    if args.compile_model:
        argv.append("--compile-model")
    if args.fresh_start:
        argv.append("--fresh-start")
    argv.extend(args.extra)
    print("Equivalent trainer args:", flush=True)
    print(" ".join(argv), flush=True)
    if args.print_only:
        return
    train_main(argv)


def build_train_args(values: dict[str, Any]) -> list[str]:
    argv: list[str] = []
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            argv.append(flag if value else "--no-" + key.replace("_", "-"))
        elif value is None or value == "":
            continue
        else:
            argv.extend([flag, str(value)])
    return argv


if __name__ == "__main__":
    main()
