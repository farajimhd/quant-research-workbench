from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


RUNTIME_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model" / "v3" / "train.py").exists()
    ),
    Path(__file__).resolve().parents[3],
)
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from research.masked_event_model.v3 import train


TRAIN_ARGS: dict[str, Any] = {
    "cache_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2",
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2",
    "output_root": r"D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v3",
    "train_start_date": "2025-11-01",
    "train_end_date": "2025-11-30",
    "validation_start_date": "2025-12-01",
    "validation_end_date": "2025-12-05",
    "test_start_date": "2025-12-08",
    "test_end_date": "2025-12-12",
    "tickers": "ALL",
    "context_seconds": 30,
    "chunk_ms": 500,
    "row_block_size": 8192,
    "loader_progress_windows": 256,
    "batch_size": 256,
    "epochs": 3,
    "num_workers": 0,
    "prefetch_factor": 1,
    "device": "cuda",
    "d_model": 384,
    "embedding_dim": 256,
    "n_heads": 6,
    "quote_event_layers": 2,
    "trade_event_layers": 2,
    "temporal_layers": 8,
    "decoder_layers": 4,
    "ffn_mult": 4,
    "encoder_visible_ratio": 0.30,
    "mask_ratio": 0.70,
    "learning_rate": 2e-4,
    "weight_decay": 1e-4,
    "scheduler": "cosine_warm_restarts",
    "scheduler_t0_steps": 1000,
    "scheduler_t_mult": 2,
    "scheduler_eta_min": 1e-6,
    "logging_steps": 1,
    "detailed_metrics_steps": 10,
    "profile_training_every_steps": 10,
    "profile_inference_every_steps": 10,
    "pretrain_validation_frequency": 50,
    "pretrain_validation_steps": 4,
    "checkpoint_steps": 1000,
    "loader_prefetch_batches": 1,
    "probe_every_steps": 5000,
    "probe_train_steps": 200,
    "probe_train_windows": 20000,
    "probe_val_windows": 20000,
    "wandb_entity": "mehdifaraji",
    "wandb_project": "May2026-masked-event-modeling",
    "wandb_run_name": "mem-v3-d384-emb256-e2-t8-d4-mask70-chunk500-b256-nov2025",
    "wandb_mode": "online",
    "wandb_init_timeout": 120,
    "compile_model": False,
}

BOOLEAN_FLAGS = {"compile_model", "disable_probe", "fresh_start", "dry_run"}


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
    print("masked event v3 Python launcher")
    print()
    print("Run with the defaults embedded in this file:")
    print("  python research\\masked_event_model\\v3\\run_train.py")
    print()
    print("Optional one-off overrides:")
    print("  python research\\masked_event_model\\v3\\run_train.py --batch-size 512 --dry-run")
    print()
    print("Current defaults:")
    for key, value in TRAIN_ARGS.items():
        print(f"  --{key.replace('_', '-')} {value}")
    for key in sorted(BOOLEAN_FLAGS):
        print(f"  --{key.replace('_', '-')} false")


def run_train(**overrides: Any) -> None:
    argv = build_argv(overrides)
    print("Starting masked event v3 training from Python launcher.", flush=True)
    print(f"Runtime root: {RUNTIME_ROOT}", flush=True)
    print("Equivalent CLI:", flush=True)
    print("python -m research.masked_event_model.v3.train " + " ".join(argv), flush=True)
    train.main(argv)


if __name__ == "__main__":
    run_train(**parse_cli_overrides(sys.argv[1:]))
