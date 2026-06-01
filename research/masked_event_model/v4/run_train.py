from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_compact_v1",
    "precomputed_chunk_root": r"D:\market-data\prepared\us_stocks_sip\v4_compact_event_chunks_v1",
    "reference_dir": None,
    "train_start_date": "2025-11-01",
    "train_end_date": "2025-11-30",
    "validation_start_date": "2025-12-01",
    "validation_end_date": "2025-12-05",
    "tickers": "ALL",
    "events_per_chunk": 128,
    "batch_size": 4096,
    "max_steps": 10000,
    "epochs": 1,
    "num_workers": 0,
    "device": "cuda",
    "mask_ratio": 0.70,
    "header_mask_ratio": 0.50,
    "d_byte": 24,
    "d_model": 128,
    "embedding_dim": 32,
    "n_heads": 4,
    "encoder_layers": 6,
    "decoder_layers": 2,
    "learning_rate": 2e-4,
    "wandb_project": "May2026-compact-byte-event-modeling",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch v4 compact byte MAE training with editable defaults.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    defaults = dict(DEFAULTS)
    defaults["reference_dir"] = str(repo_root() / "research" / "market_references" / "massive")
    argv = build_argv(defaults)
    if known.dry_run:
        argv.append("--dry-run")
    if known.fresh_start:
        argv.append("--fresh-start")
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.masked_event_model.v4.train"]
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return argv


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
