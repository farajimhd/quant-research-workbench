from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from research.bar_gpt.v1.config import (
    BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT,
    MODEL_SIZE_PRESETS,
    PRODUCTION_MODEL_TRAINING_PRESETS,
)
from research.bar_gpt.v1.run_train import default_argv
from research.bar_gpt.v1.model_discovery import (
    build_discovery_manifest,
    discovery_data_config,
    load_discovery_manifest,
)
from research.bar_gpt.v1.train import main as train_main


@dataclass(frozen=True, slots=True)
class ComparisonRun:
    model_size: str
    microbatch: int
    accumulation: int
    length_bucket_batches: int

    @property
    def effective_blocks(self) -> int:
        return self.microbatch * self.accumulation


# These are the safe v12 winners from the completed end-to-end workstation
# sweep. Accumulation follows the profiler recommendation for at least 32
# blocks per update; the measured microbatches make that 40 blocks for each.
COMPARISON_RUNS: dict[str, ComparisonRun] = {
    model_size: ComparisonRun(
        model_size,
        microbatch=preset.microbatch,
        accumulation=preset.accumulation,
        length_bucket_batches=preset.length_bucket_batches,
    )
    for model_size, preset in PRODUCTION_MODEL_TRAINING_PRESETS.items()
}
DEFAULT_WANDB_MODE = "online"
DEFAULT_SHARD_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\model_comparison")
COMPARISON_TRAIN_ORIGINS = 100_000_000
COMPARISON_MONITOR_ORIGINS = 1_000_000
COMPARISON_VALIDATION_ORIGINS = 5_000_000
COMPARISON_SEED = 17
COMPARISON_MONITOR_INTERVAL_ORIGINS = 25_000_000
COMPARISON_MANIFEST_NAME = "fixed_panels_v2.json"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or execute one of the three one-epoch BarGPT model comparisons."
    )
    parser.add_argument("--model-size", choices=("all", *COMPARISON_RUNS), default="all")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--run-stamp",
        default="",
        help="Optional shared run suffix. Defaults to the current YYYYmmdd-HHMMSS timestamp.",
    )
    parser.add_argument(
        "--wandb-mode",
        choices=("auto", "online", "offline", "disabled"),
        default=DEFAULT_WANDB_MODE,
        help="override the normal online W&B logging mode",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def comparison_run_name(model_size: str, run_stamp: str) -> str:
    run = COMPARISON_RUNS[model_size]
    return (
        f"bar-gpt-v1-epoch1-{model_size}-micro{run.microbatch}-"
        f"accum{run.accumulation}-bucket{run.length_bucket_batches}-{run_stamp}"
    )


def trainer_argv(
    model_size: str,
    *,
    run_stamp: str,
    wandb_mode: str = DEFAULT_WANDB_MODE,
    shard_root: Path = DEFAULT_SHARD_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    manifest_path: Path | None = None,
) -> list[str]:
    run = COMPARISON_RUNS[model_size]
    model = MODEL_SIZE_PRESETS[model_size]
    argv = [
        *default_argv(),
        "--run-name",
        comparison_run_name(model_size, run_stamp),
        "--wandb-project",
        BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT,
        "--offline-shard-root",
        str(shard_root),
        "--experiment-manifest",
        str(manifest_path or output_root / COMPARISON_MANIFEST_NAME),
        "--output-root",
        str(output_root / "runs"),
        "--epochs",
        "1",
        "--max-samples",
        "0",
        "--batch-size",
        str(run.microbatch),
        "--gradient-accumulation-steps",
        str(run.accumulation),
        "--offline-length-bucket-batches",
        str(run.length_bucket_batches),
        # A batch-count cap exposes different numbers of fixed validation
        # blocks at MB20 and MB10. Zero consumes the complete identical panel.
        "--validation-batches",
        "0",
        "--warmup-samples",
        "4000000",
        "--scheduler-mode",
        "single-cosine",
        "--validation-runs-per-epoch",
        "4",
        "--validation-interval-samples",
        str(COMPARISON_MONITOR_INTERVAL_ORIGINS),
        "--validation-initial-samples",
        str(COMPARISON_MONITOR_INTERVAL_ORIGINS),
        "--d-model",
        str(model["d_model"]),
        "--n-layers",
        str(model["n_layers"]),
        "--n-heads",
        str(model["n_heads"]),
        "--n-kv-heads",
        str(model["n_kv_heads"]),
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        argv.extend(("--wandb-mode", wandb_mode))
    checkpoint = (
        output_root
        / "runs"
        / comparison_run_name(model_size, run_stamp)
        / "checkpoints"
        / "checkpoint_latest.pt"
    )
    if checkpoint.is_file():
        argv.extend(("--resume-checkpoint", str(checkpoint)))
    return argv


def _launcher_command(
    model_size: str,
    *,
    run_stamp: str,
    wandb_mode: str,
    execute: bool,
    shard_root: Path = DEFAULT_SHARD_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> list[str]:
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.run_train_model_comparison",
        "--model-size",
        model_size,
        "--run-stamp",
        run_stamp,
        "--shard-root",
        str(shard_root),
        "--output-root",
        str(output_root),
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        command.extend(("--wandb-mode", wandb_mode))
    if execute:
        command.append("--execute")
    return command


def _validate_comparison_manifest(manifest: dict[str, Any], *, all_tickers: tuple[str, ...]) -> None:
    expected_targets = {
        "train_origins_per_epoch": COMPARISON_TRAIN_ORIGINS,
        "monitor_origins": COMPARISON_MONITOR_ORIGINS,
        "validation_origins": COMPARISON_VALIDATION_ORIGINS,
        "locked_test_origins": 0,
    }
    if manifest.get("targets") != expected_targets:
        raise RuntimeError("model-comparison manifest has the wrong origin targets")
    expected_ranges = {
        "train": ["2019-01-01", "2026-01-01"],
        "held_out": ["2026-01-01", "2026-08-01"],
    }
    if manifest.get("ranges") != expected_ranges:
        raise RuntimeError("model-comparison manifest has the wrong temporal ranges")
    cohorts = manifest.get("cohorts")
    if not isinstance(cohorts, dict):
        raise RuntimeError("model-comparison manifest has no ticker cohorts")
    if cohorts.get("training_tickers") != sorted(all_tickers) or cohorts.get(
        "evaluation_tickers"
    ) != sorted(all_tickers):
        raise RuntimeError("model-comparison manifest does not include every catalog ticker")
    available_dates = cohorts.get("evaluation_available_ticker_dates")
    if not isinstance(available_dates, dict) or set(available_dates) != set(all_tickers):
        raise RuntimeError("model-comparison manifest has incomplete evaluation availability")
    expected_monitor_tickers = {
        ticker for ticker in all_tickers if int(available_dates[ticker]) >= 2
    }
    panels = manifest.get("panels")
    if not isinstance(panels, dict):
        raise RuntimeError("model-comparison manifest has no panels")
    panel_tickers: dict[str, set[str]] = {}
    panel_dates: dict[str, set[tuple[str, str]]] = {}
    minimum_origins = {
        "train": COMPARISON_TRAIN_ORIGINS,
        "monitor": COMPARISON_MONITOR_ORIGINS,
        "validation": COMPARISON_VALIDATION_ORIGINS,
    }
    for name in ("train", "monitor", "validation"):
        rows = panels.get(name)
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"model-comparison manifest panel {name!r} is empty")
        panel_tickers[name] = {str(row["ticker"]) for row in rows}
        panel_dates[name] = {(str(row["ticker"]), str(row["local_date"])) for row in rows}
        expected_tickers = expected_monitor_tickers if name == "monitor" else set(all_tickers)
        if panel_tickers[name] != expected_tickers:
            raise RuntimeError(f"model-comparison panel {name!r} does not represent every ticker")
        origins = sum(int(row["origins"]) for row in rows)
        if origins < minimum_origins[name]:
            raise RuntimeError(f"model-comparison panel {name!r} is below its origin target")
        start, end = expected_ranges["train" if name == "train" else "held_out"]
        if any(not start <= day < end for _ticker, day in panel_dates[name]):
            raise RuntimeError(f"model-comparison panel {name!r} contains an out-of-range date")
    if panel_dates["monitor"] & panel_dates["validation"]:
        raise RuntimeError("model-comparison monitor and validation ticker-dates overlap")


def ensure_comparison_manifest(*, shard_root: Path, output_root: Path) -> Path:
    manifest_path = output_root / COMPARISON_MANIFEST_NAME
    config = discovery_data_config(shard_root)
    all_tickers = tuple(config.tickers)
    if manifest_path.is_file():
        manifest = load_discovery_manifest(manifest_path, shard_root=shard_root, config=config)
        _validate_comparison_manifest(manifest, all_tickers=all_tickers)
        print(f"Reusing verified model-comparison manifest: {manifest_path}", flush=True)
    else:
        print("Building deterministic all-ticker model-comparison panels...", flush=True)
        manifest = build_discovery_manifest(
            shard_root=shard_root,
            output_path=manifest_path,
            train_origins=COMPARISON_TRAIN_ORIGINS,
            monitor_origins=COMPARISON_MONITOR_ORIGINS,
            validation_origins=COMPARISON_VALIDATION_ORIGINS,
            locked_test_origins=0,
            seed=COMPARISON_SEED,
            training_tickers=all_tickers,
            evaluation_tickers=all_tickers,
        )
        _validate_comparison_manifest(manifest, all_tickers=all_tickers)
    summaries = manifest["summaries"]
    for name in ("train", "monitor", "validation"):
        summary = summaries[name]
        print(
            f"{name}: origins={int(summary['origins']):,} blocks={int(summary['blocks']):,} "
            f"tickers={int(summary['tickers']):,}",
            flush=True,
        )
    return manifest_path


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    run_stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
    shard_root = Path(args.shard_root)
    output_root = Path(args.output_root)
    selected = tuple(COMPARISON_RUNS) if args.model_size == "all" else (args.model_size,)
    print(f"W&B project: {BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT}", flush=True)
    print(
        "Fixed population: >=100M train origins from 2019-2025; "
        ">=1M monitor and >=5M validation origins from disjoint 2026 ticker-dates; "
        "all catalog tickers in training and validation, with monitor reserving each "
        "ticker's only 2026 date for validation",
        flush=True,
    )
    print(
        "Metric cadence: monitor_* uses the complete 1M panel at 25M, 50M, and 75M; "
        "validation_* uses the complete 5M panel once at the 100M epoch boundary",
        flush=True,
    )
    for model_size in selected:
        run = COMPARISON_RUNS[model_size]
        model = MODEL_SIZE_PRESETS[model_size]
        print(
            f"{model_size}: d_model={model['d_model']} layers={model['n_layers']} "
            f"heads={model['n_heads']} kv_heads={model['n_kv_heads']} "
            f"microbatch={run.microbatch} accumulation={run.accumulation} "
            f"effective_blocks={run.effective_blocks} "
            f"length_bucket_batches={run.length_bucket_batches} validation=complete_fixed_panel",
            flush=True,
        )
        print(
            "Command: "
            + " ".join(
                shlex.quote(item)
                for item in _launcher_command(
                    model_size,
                    run_stamp=run_stamp,
                    wandb_mode=args.wandb_mode,
                    execute=True,
                    shard_root=shard_root,
                    output_root=output_root,
                )
            ),
            flush=True,
        )
    if not args.execute:
        return 0
    if args.model_size == "all":
        child_env = os.environ.copy()
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        for index, model_size in enumerate(selected, start=1):
            command = _launcher_command(
                model_size,
                run_stamp=run_stamp,
                wandb_mode=args.wandb_mode,
                execute=True,
                shard_root=shard_root,
                output_root=output_root,
            )
            print(
                f"Starting comparison run {index}/{len(selected)}: {model_size}",
                flush=True,
            )
            completed = subprocess.run(command, env=child_env, check=False)
            if completed.returncode:
                print(
                    f"Comparison stopped: {model_size} exited with code {completed.returncode}; "
                    "later model sizes were not started.",
                    flush=True,
                )
                return int(completed.returncode)
            print(f"Completed comparison run {index}/{len(selected)}: {model_size}", flush=True)
        return 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    manifest_path = ensure_comparison_manifest(shard_root=shard_root, output_root=output_root)
    resolved = trainer_argv(
        selected[0],
        run_stamp=run_stamp,
        wandb_mode=args.wandb_mode,
        shard_root=shard_root,
        output_root=output_root,
        manifest_path=manifest_path,
    )
    equivalent = [sys.executable, "-B", "-m", "research.bar_gpt.v1.train", *resolved]
    print("Equivalent trainer command: " + " ".join(shlex.quote(item) for item in equivalent), flush=True)
    return int(train_main(resolved) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
