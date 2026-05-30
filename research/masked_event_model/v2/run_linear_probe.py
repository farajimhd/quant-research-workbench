from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


RUNTIME_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model" / "v2" / "train_linear_probe.py").exists()
    ),
    Path(__file__).resolve().parents[3],
)
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from research.masked_event_model.v2 import train_linear_probe


PROBE_ARGS: dict[str, Any] = {
    "output_root": r"D:\TradingData\quant-research-workbench\market_data\models\masked_event_model\v2",
    "pretrain_run_name": "mem-v2-d256-e2-t8-d4-mask70-chunk500-b256-nov2025",
    "cache_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v2",
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_v2",
    "batch_size": 256,
    "train_steps": 200,
    "train_windows": 20000,
    "val_windows": 20000,
    "hidden_dim": 0,
    "learning_rate": 1e-3,
    "num_workers": 0,
    "device": "cuda",
    "seed": 17,
    "wandb_entity": "mehdifaraji",
    "wandb_project": "May2026-masked-event-modeling",
    "wandb_mode": "online",
    "wandb_init_timeout": 120,
}

BOOLEAN_FLAGS = {"dry_run"}


def build_argv(overrides: dict[str, Any] | None = None) -> list[str]:
    values = dict(PROBE_ARGS)
    if overrides:
        values.update(overrides)
    argv: list[str] = []
    for key, value in values.items():
        if value is None or value is False or value == "":
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
    valid_keys = set(PROBE_ARGS) | BOOLEAN_FLAGS | {"checkpoint_path", "wandb_run_name"}
    while index < len(raw_args):
        raw = raw_args[index]
        if raw in {"-h", "--help"}:
            print_help()
            raise SystemExit(0)
        if not raw.startswith("--"):
            raise SystemExit(f"Unexpected argument {raw!r}. Use --key value or --boolean-flag.")
        key = raw[2:].replace("-", "_")
        if key not in valid_keys:
            raise SystemExit(f"Unknown override {raw!r}. Edit PROBE_ARGS in this file or use one of: {sorted(valid_keys)}")
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
    print("masked event v2 linear-probe launcher")
    print()
    print("Run with defaults:")
    print("  python research\\masked_event_model\\v2\\run_linear_probe.py")
    print()
    print("Optional overrides:")
    print("  python research\\masked_event_model\\v2\\run_linear_probe.py --checkpoint-path D:\\path\\checkpoint_last.pt")
    print()
    print("Current defaults:")
    for key, value in PROBE_ARGS.items():
        print(f"  --{key.replace('_', '-')} {value}")
    print("  --checkpoint-path <auto>")
    print("  --wandb-run-name <auto>")
    for key in sorted(BOOLEAN_FLAGS):
        print(f"  --{key.replace('_', '-')} false")


def run_probe(**overrides: Any) -> None:
    argv = build_argv(overrides)
    print("Starting masked event v2 linear probe from Python launcher.", flush=True)
    print(f"Runtime root: {RUNTIME_ROOT}", flush=True)
    print("Equivalent CLI:", flush=True)
    print("python -m research.masked_event_model.v2.train_linear_probe " + " ".join(argv), flush=True)
    train_linear_probe.main(argv)


if __name__ == "__main__":
    run_probe(**parse_cli_overrides(sys.argv[1:]))
