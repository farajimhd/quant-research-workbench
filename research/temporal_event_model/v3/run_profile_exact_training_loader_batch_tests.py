from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/exact_loader_batch_tests")

DEFAULT_ARGS: dict[str, str | int | float | bool] = {
    "cache-root": str(DEFAULT_CACHE_ROOT),
    "months": "2019-02",
    "data-groups": (
        "events,intraday_labels,corporate_action_labels,intraday_bars,scanner_context,"
        "daily_bars,global_daily_bars,ticker_news_embeddings,market_news_embeddings,"
        "sec_filing_embeddings,xbrl,corporate_actions"
    ),
    "warmup-batches": 1,
    "batches": 8,
    "read-workers": 16,
    "materialize-workers": 32,
    "loaded-parts-per-group": 256,
    "prefetch-batches": 64,
    "time-window-seconds": 60.0,
    "frontier-max-origins-per-window": 0,
    "ticker-cache-capacity": 15_000,
    "origin-cursor-chunk-rows": 1024,
    "warm-all-ticker-caches": True,
    "scanner-index-cache-entries": 4,
    "prefetch-scanner-indexes": True,
    "scanner-prefetch-workers": 8,
    "coverage-auto-plan": True,
    "coverage-auto-ticker-limit": 256,
    "coverage-max-skip-batches": 512,
    "coverage-min-fraction": 1e-9,
    "require-all-input-coverage": True,
    "device": "cuda",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exact v3 training-loader tests for large batch sizes.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--batch-sizes", default="1024,2048")
    parser.add_argument("--batches", type=int, default=int(DEFAULT_ARGS["batches"]))
    parser.add_argument("--warmup-batches", type=int, default=int(DEFAULT_ARGS["warmup-batches"]))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("overrides", nargs=argparse.REMAINDER, help="Extra run_profile_exact_training_loader.py args after --.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_sizes = _split_ints(str(args.batch_sizes))
    if not batch_sizes:
        raise SystemExit("--batch-sizes is empty.")
    root = Path(args.output_root) / f"batch_tests_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    overrides = list(args.overrides)
    if overrides and overrides[0] == "--":
        overrides = overrides[1:]
    for batch_size in batch_sizes:
        run_name = f"exact-loader-bs{batch_size}"
        command = _command(
            output_root=root,
            cache_root=Path(args.cache_root),
            run_name=run_name,
            batch_size=batch_size,
            batches=int(args.batches),
            warmup_batches=int(args.warmup_batches),
            overrides=overrides,
        )
        print("RUN", " ".join(shlex.quote(part) for part in command), flush=True)
        started = time.perf_counter()
        status = 0 if bool(args.dry_run) else int(subprocess.call(command))
        elapsed = time.perf_counter() - started
        row = _summarize(root=root, run_name=run_name, batch_size=batch_size, status=status, elapsed=elapsed)
        rows.append(row)
        _write_outputs(root, rows)
        if status != 0 and not bool(args.continue_on_error):
            return int(status)
    _write_outputs(root, rows)
    print(f"BATCH TEST SUMMARY {root}", flush=True)
    return 0 if all(int(row.get("status", 1)) == 0 for row in rows) else 1


def _command(
    *,
    output_root: Path,
    cache_root: Path,
    run_name: str,
    batch_size: int,
    batches: int,
    warmup_batches: int,
    overrides: list[str],
) -> list[str]:
    script = Path(__file__).with_name("run_profile_exact_training_loader.py")
    command = [
        sys.executable,
        "-u",
        str(script),
        "--output-root",
        str(output_root),
        "--run-name",
        run_name,
        "--cache-root",
        str(cache_root),
        "--batch-size",
        str(int(batch_size)),
        "--materialize-chunk-size",
        str(int(batch_size)),
        "--batches",
        str(int(batches)),
        "--warmup-batches",
        str(int(warmup_batches)),
    ]
    for key, value in DEFAULT_ARGS.items():
        if key in {"cache-root", "output-root", "run-name", "batch-size", "materialize-chunk-size", "batches", "warmup-batches"}:
            continue
        flag = f"--{key}"
        if isinstance(value, bool):
            command.append(flag if value else f"--no-{key}")
        else:
            command.extend([flag, str(value)])
    command.extend(overrides)
    return command


def _summarize(*, root: Path, run_name: str, batch_size: int, status: int, elapsed: float) -> dict[str, Any]:
    summary_path = root / run_name / "exact_training_loader_summary.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            summary = {"summary_read_error": repr(exc)}
    averages = dict(summary.get("averages") or {})
    p95 = dict(summary.get("p95") or {})
    coverage = dict(summary.get("coverage") or {})
    return {
        "batch_size": int(batch_size),
        "status": int(status),
        "elapsed_seconds": float(elapsed),
        "run_name": run_name,
        "run_dir": str(root / run_name),
        "profiler_status": summary.get("status"),
        "coverage_status": summary.get("coverage_status"),
        "missing_required_coverage": summary.get("missing_required_coverage"),
        "coverage_seek_batches": int(summary.get("coverage_seek_batches") or 0),
        "measured_batches": int(summary.get("measured_batches") or 0),
        "measured_samples": int(summary.get("measured_samples") or 0),
        "avg_next_batch_seconds": averages.get("next_batch_seconds"),
        "p95_next_batch_seconds": p95.get("next_batch_seconds"),
        "avg_samples_per_second": averages.get("samples_per_second"),
        "coverage_max_fraction": coverage.get("max_fraction"),
    }


def _write_outputs(root: Path, rows: list[dict[str, Any]]) -> None:
    jsonl_path = root / "exact_loader_batch_test_results.jsonl"
    json_path = root / "exact_loader_batch_test_summary.json"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    json_path.write_text(json.dumps({"runs": rows}, indent=2, sort_keys=True), encoding="utf-8")


def _split_ints(value: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in str(value or "").split(","):
        text = part.strip()
        if text:
            out.append(int(text))
    return tuple(out)


if __name__ == "__main__":
    raise SystemExit(main())
