from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_SWEEP: dict[str, str | int | float | bool] = {
    "cache-root": "D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02",
    "output-root": "D:/TradingML/runtimes/temporal_event_model/v3/profile_sweeps",
    "dataset-id": "temporal_v3_201902_daily_index_sweep_v1",
    "months": "2019-02",
    "batches": 4,
    "warmup-batches": 1,
    "max-origins-per-epoch": 200000,
    "read-workers": 4,
    "materialize-workers": 8,
    "loaded-parts-per-group": 8,
    "amp": True,
    "amp-dtype": "bf16",
    "fresh-start": True,
    "coverage-mode": "require-requested",
    "audit-profile-batches": 1,
    "audit-samples-per-batch": 4,
    "audit-rest-samples": 0,
    "profile-production-paths": True,
    "profile-production-batches": 1,
    "progress-layout": "text",
}

MODEL_PRESETS: dict[str, dict[str, int]] = {
    "tiny": {"d-model": 128, "event-layers": 2, "event-heads": 4, "fusion-layers": 2, "fusion-heads": 4},
    "small": {"d-model": 256, "event-layers": 4, "event-heads": 8, "fusion-layers": 3, "fusion-heads": 8},
    "medium": {"d-model": 384, "event-layers": 6, "event-heads": 8, "fusion-layers": 4, "fusion-heads": 8},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run temporal v3 training-profile sweeps across model and batch sizes.")
    parser.add_argument("--output-root", default=str(DEFAULT_SWEEP["output-root"]))
    parser.add_argument("--cache-root", default=str(DEFAULT_SWEEP["cache-root"]))
    parser.add_argument("--models", default="tiny,small", help=f"Comma-separated presets from {','.join(MODEL_PRESETS)}.")
    parser.add_argument("--batch-sizes", default="64,128,256")
    parser.add_argument("--batches", type=int, default=int(DEFAULT_SWEEP["batches"]))
    parser.add_argument("--warmup-batches", type=int, default=int(DEFAULT_SWEEP["warmup-batches"]))
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("overrides", nargs=argparse.REMAINDER, help="Extra run_profile_training.py args after --.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = Path(__file__).with_name("run_profile_training.py")
    sweep_root = Path(args.output_root) / time.strftime("sweep_%Y%m%d_%H%M%S")
    sweep_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    run_index = 0
    overrides = list(args.overrides)
    if overrides and overrides[0] == "--":
        overrides = overrides[1:]
    for model_name in _split_csv(args.models):
        preset = MODEL_PRESETS.get(model_name)
        if preset is None:
            raise SystemExit(f"Unknown model preset {model_name!r}; choose from {sorted(MODEL_PRESETS)}")
        for batch_size in [int(value) for value in _split_csv(args.batch_sizes)]:
            run_index += 1
            if args.max_runs and run_index > int(args.max_runs):
                break
            run_name = f"sweep-{model_name}-bs{batch_size}"
            command = _profile_command(
                script=script,
                output_root=sweep_root,
                cache_root=Path(args.cache_root),
                run_name=run_name,
                model_preset=preset,
                batch_size=batch_size,
                batches=int(args.batches),
                warmup_batches=int(args.warmup_batches),
                overrides=overrides,
            )
            print("RUN", " ".join(shlex.quote(part) for part in command), flush=True)
            status = 0
            started = time.perf_counter()
            if not args.dry_run:
                status = int(subprocess.call(command))
            elapsed = time.perf_counter() - started
            row = _summarize_run(sweep_root=sweep_root, run_name=run_name, status=status, elapsed=elapsed)
            row.update({"model_preset": model_name, "batch_size": batch_size, "run_index": run_index})
            rows.append(row)
            _write_outputs(sweep_root, rows)
            if status != 0 and not bool(args.continue_on_error):
                return status
        if args.max_runs and run_index >= int(args.max_runs):
            break
    _write_outputs(sweep_root, rows)
    print(f"SWEEP SUMMARY {sweep_root}", flush=True)
    return 0 if all(int(row.get("status", 1)) == 0 for row in rows) else 1


def _profile_command(
    *,
    script: Path,
    output_root: Path,
    cache_root: Path,
    run_name: str,
    model_preset: dict[str, int],
    batch_size: int,
    batches: int,
    warmup_batches: int,
    overrides: list[str],
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(script),
        "--cache-root",
        str(cache_root),
        "--output-root",
        str(output_root),
        "--run-name",
        run_name,
        "--batch-size",
        str(batch_size),
        "--batches",
        str(batches),
        "--warmup-batches",
        str(warmup_batches),
    ]
    for key, value in DEFAULT_SWEEP.items():
        if key in {"cache-root", "output-root", "batch-size", "batches", "warmup-batches"}:
            continue
        flag = f"--{key}"
        if isinstance(value, bool):
            command.append(flag if value else f"--no-{key}")
        else:
            command.extend([flag, str(value)])
    for key, value in model_preset.items():
        command.extend([f"--{key}", str(value)])
    command.extend(overrides)
    return command


def _summarize_run(*, sweep_root: Path, run_name: str, status: int, elapsed: float) -> dict[str, Any]:
    run_dir = sweep_root / run_name
    summary_path = run_dir / "training_profile_summary.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    averages = summary.get("averages") if isinstance(summary.get("averages"), dict) else {}
    p95 = summary.get("p95") if isinstance(summary.get("p95"), dict) else {}
    return {
        "run_name": run_name,
        "status": int(status),
        "elapsed_seconds": float(elapsed),
        "measured_samples": int(summary.get("measured_samples", 0) or 0),
        "samples_per_second": float(averages.get("samples_per_second", 0.0) or 0.0),
        "step_seconds": float(averages.get("step_seconds", 0.0) or 0.0),
        "loader_wait_seconds": float(averages.get("loader_wait_seconds", 0.0) or 0.0),
        "forward_seconds": float(averages.get("forward_seconds", 0.0) or 0.0),
        "production_cache_encode_seconds": float(averages.get("production/cache_encode_wall_seconds", 0.0) or 0.0),
        "production_cached_predict_seconds": float(averages.get("production/cached_predict_wall_seconds", 0.0) or 0.0),
        "production_cached_predict_samples_per_second": float(averages.get("production/cached_predict_samples_per_second", 0.0) or 0.0),
        "backward_seconds": float(averages.get("backward_seconds", 0.0) or 0.0),
        "loss": float(averages.get("loss", 0.0) or 0.0),
        "gpu_memory_peak_gib_p95": float(p95.get("gpu_memory_peak_gib", 0.0) or 0.0),
        "cpu_rss_gib_p95": float(p95.get("cpu_rss_gib", 0.0) or 0.0),
        "profile_summary": str(summary_path),
    }


def _write_outputs(root: Path, rows: list[dict[str, Any]]) -> None:
    jsonl_path = root / "sweep_results.jsonl"
    csv_path = root / "sweep_results.csv"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


if __name__ == "__main__":
    raise SystemExit(main())
