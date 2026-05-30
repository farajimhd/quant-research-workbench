from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


RUNTIME_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model" / "v1" / "train.py").exists()
    ),
    Path(__file__).resolve().parents[3],
)
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from research.masked_event_model.v1 import train


TRAIN_ARGS: dict[str, Any] = {
    "cache_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2",
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2",
    "output_root": r"D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v1",
    "train_start_date": "2025-11-01",
    "train_end_date": "2025-11-30",
    "validation_start_date": "2025-12-01",
    "validation_end_date": "2025-12-05",
    "test_start_date": "2025-12-08",
    "test_end_date": "2025-12-12",
    "tickers": "ALL",
    "context_seconds": 30,
    "chunk_ms": 500,
    "batch_size": 1024,
    "epochs": 3,
    "num_workers": 8,
    "prefetch_factor": 4,
    "device": "cuda",
    "d_model": 512,
    "n_heads": 8,
    "quote_event_layers": 2,
    "trade_event_layers": 2,
    "temporal_layers": 8,
    "decoder_layers": 4,
    "ffn_mult": 4,
    "mask_ratio": 0.70,
    "logging_steps": 50,
    "checkpoint_steps": 1000,
    "probe_every_steps": 5000,
    "probe_train_steps": 200,
    "probe_train_windows": 20000,
    "probe_val_windows": 20000,
    "wandb_entity": "mehdifaraji",
    "wandb_project": "May2026-masked-event-modeling",
    "wandb_run_name": "mem-v1-d512-e2-t8-d4-mask70-chunk500-b1024-nov2025",
    "wandb_mode": "auto",
    "wandb_init_timeout": 120,
}

BOOLEAN_FLAGS = {"disable_probe", "fresh_start", "dry_run"}


def build_argv(overrides: dict[str, Any] | None = None) -> list[str]:
    values = dict(TRAIN_ARGS)
    if overrides:
        values.update(overrides)

    argv: list[str] = []
    for key, value in values.items():
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return argv


def parse_cli_overrides(raw_args: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    index = 0
    valid_keys = set(TRAIN_ARGS) | BOOLEAN_FLAGS
    while index < len(raw_args):
        raw = raw_args[index]
        if raw in {"-h", "--help"}:
            print_help()
            raise SystemExit(0)
        if not raw.startswith("--"):
            raise SystemExit(f"Unexpected argument {raw!r}. Use --key value or --boolean-flag.")
        key = raw[2:].replace("-", "_")
        if key not in valid_keys:
            raise SystemExit(f"Unknown override {raw!r}. Edit TRAIN_ARGS in this file or use one of: {sorted(valid_keys)}")
        if key in BOOLEAN_FLAGS:
            overrides[key] = True
            index += 1
            continue
        if index + 1 >= len(raw_args):
            raise SystemExit(f"Missing value for {raw!r}.")
        overrides[key] = raw_args[index + 1]
        index += 2
    return overrides


def print_help() -> None:
    print("Masked event v1 Python launcher")
    print()
    print("Run with the defaults embedded in this file:")
    print("  python research\\masked_event_model\\v1\\run_train.py")
    print()
    print("Optional one-off overrides:")
    print("  python research\\masked_event_model\\v1\\run_train.py --batch-size 512 --dry-run")
    print()
    print("Current defaults:")
    for key, value in TRAIN_ARGS.items():
        print(f"  --{key.replace('_', '-')} {value}")
    for key in sorted(BOOLEAN_FLAGS):
        print(f"  --{key.replace('_', '-')} false")


def run_train(**overrides: Any) -> None:
    argv = build_argv(overrides)
    print("Starting masked event v1 training from Python launcher.", flush=True)
    print(f"Runtime root: {RUNTIME_ROOT}", flush=True)
    print("Equivalent CLI:", flush=True)
    print("python -m research.masked_event_model.v1.train " + " ".join(argv), flush=True)
    train.main(argv)


if __name__ == "__main__":
    run_train(**parse_cli_overrides(sys.argv[1:]))
