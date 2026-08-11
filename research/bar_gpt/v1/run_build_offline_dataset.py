from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_5TB_300_ID,
    BAR_GPT_COHORT_5TB_300_SHA256,
    BAR_GPT_TRAINING_TICKERS,
)
from research.bar_gpt.v1.lock_offline_shard_catalog import lock_catalog
from research.bar_gpt.v1.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    shard_catalog_lock_path,
    verify_shard_catalog_lock,
)


DATASET_START_DATE = "2019-01-01"
DATASET_END_DATE = "2026-08-01"
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
AUDIT_SHARDS = 2
AUDIT_SEED = 17
LOCK_REASON = (
    "Completed and audited BarGPT direct-event 300-ticker authority for "
    "2019-01-01 through 2026-08-01; future data must use a new output root."
)


def _month_count(start_date: str, end_date: str) -> int:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    return (end.year - start.year) * 12 + end.month - start.month


EXPECTED_MONTHS = _month_count(DATASET_START_DATE, DATASET_END_DATE)
EXPECTED_UNITS = len(BAR_GPT_TRAINING_TICKERS) * EXPECTED_MONTHS


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build, audit, and permanently seal one direct-event BarGPT dataset for exactly "
            "300 tickers over 2019-01-01 through 2026-08-01. Training and 2026 validation "
            "are views over this one catalog, not separate build passes."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Run the restart-safe build and certification lifecycle.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0)
    parser.add_argument("--clickhouse-max-threads-per-query", type=int, default=2)
    parser.add_argument("--clickhouse-prefetch-pages", type=int, default=4)
    parser.add_argument("--clickhouse-max-concurrent-pages", type=int, default=0)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="rich")
    parser.add_argument("--audit-shards", type=int, default=AUDIT_SHARDS)
    parser.add_argument("--audit-seed", type=int, default=AUDIT_SEED)
    parser.add_argument(
        "--minimum-free-tb",
        type=float,
        default=5.5,
        help="Fresh-root decimal-TB safety floor; resumptions derive remaining space from certified bytes.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if (
        args.workers <= 0
        or args.clickhouse_max_threads_per_query <= 0
        or args.clickhouse_prefetch_pages <= 0
        or args.audit_shards <= 0
        or args.minimum_free_tb <= 0
    ):
        parser.error("worker, query, prefetch, and audit counts must be positive")
    return args


def _existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def required_free_bytes(root: Path, *, fresh_minimum_tb: float) -> tuple[int, str]:
    catalog_path = root / "manifest" / "catalog.json"
    if not catalog_path.is_file():
        return int(float(fresh_minimum_tb) * 1_000_000_000_000), "fresh-root safety floor"
    catalog = _read_json(catalog_path)
    counts = catalog.get("counts", {})
    completed = int(counts.get("units", 0))
    written = int(counts.get("bytes", 0))
    if completed <= 0 or written <= 0:
        return int(float(fresh_minimum_tb) * 1_000_000_000_000), "empty-catalog safety floor"
    remaining = max(0, EXPECTED_UNITS - completed)
    projected = int((written / completed) * remaining * 1.10)
    return projected, f"certified-rate projection with 10% reserve ({completed:,}/{EXPECTED_UNITS:,} units complete)"


def verify_output_capacity(root: Path, *, fresh_minimum_tb: float) -> None:
    required, basis = required_free_bytes(root, fresh_minimum_tb=fresh_minimum_tb)
    free = int(shutil.disk_usage(_existing_parent(root)).free)
    print(
        f"Storage preflight: free={free / 1e12:.2f} TB required={required / 1e12:.2f} TB basis={basis}",
        flush=True,
    )
    if free < required:
        raise RuntimeError(
            "insufficient free space for the immutable build: "
            f"free={free / 1e12:.2f} TB required={required / 1e12:.2f} TB ({basis})"
        )


def commands(args: argparse.Namespace) -> tuple[tuple[str, list[str]], ...]:
    tickers = ",".join(BAR_GPT_TRAINING_TICKERS)
    build = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_build_offline_shards",
        "--output-root", str(args.output_root),
        "--selection", "all",
        "--source-mode", "direct_events",
        "--tickers", tickers,
        "--start-date", DATASET_START_DATE,
        "--end-date", DATASET_END_DATE,
        "--workers", str(args.workers),
        "--cpu-threads-per-worker", str(args.cpu_threads_per_worker),
        "--clickhouse-max-threads-per-worker", str(args.clickhouse_max_threads_per_query),
        "--clickhouse-prefetch-pages", str(args.clickhouse_prefetch_pages),
        "--clickhouse-max-concurrent-pages", str(args.clickhouse_max_concurrent_pages),
        "--progress-layout", str(args.progress_layout),
    ]
    if args.execute:
        build.append("--execute")
    structural_audit = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.audit_offline_shards",
        "--root", str(args.output_root),
        "--tickers", tickers,
        "--start-date", DATASET_START_DATE,
        "--end-date", DATASET_END_DATE,
        "--max-shards", str(args.audit_shards),
        "--seed", str(args.audit_seed),
        "--verify-sha256",
        "--verify-direct-source",
    ]
    sampled_audit = [
        sys.executable, "-B", "-m", "research.bar_gpt.v1.run_audit_shard_data",
        "--root", str(args.output_root),
        "--output-root", str(args.output_root / "manifest" / "sample_audits"),
        "--tickers", tickers,
        "--max-shards", str(args.audit_shards),
        "--samples-per-shard", "1",
        "--clickhouse-samples", str(args.audit_shards),
        "--clickhouse-prefetch-pages", str(args.clickhouse_prefetch_pages),
        "--clickhouse-max-threads-per-query", str(args.clickhouse_max_threads_per_query),
        "--seed", str(args.audit_seed),
        "--verify-sha256",
    ]
    return (
        ("single 2019-2026 direct-event shard pass", build),
        ("bounded structural and source audit", structural_audit),
        ("bounded ClickHouse-to-tensor reconstruction audit", sampled_audit),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"required certified build artifact is unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def certify_complete_catalog(root: Path) -> dict[str, Any]:
    """Fail closed unless the one catalog exactly matches the immutable campaign."""
    root = root.resolve()
    build_plan = _read_json(root / "manifest" / "build_plan.json")
    catalog = _read_json(root / "manifest" / "catalog.json")
    expected_selection = {
        "tickers": list(BAR_GPT_TRAINING_TICKERS),
        "start_date": DATASET_START_DATE,
        "end_date": DATASET_END_DATE,
    }
    if build_plan.get("selection") != expected_selection:
        raise RuntimeError("refusing to lock: build-plan cohort or date range differs from the 300-ticker authority")
    if int(build_plan.get("planned_units", -1)) != EXPECTED_UNITS:
        raise RuntimeError(
            f"refusing to lock: expected {EXPECTED_UNITS:,} planned ticker-months, "
            f"observed {build_plan.get('planned_units')}"
        )
    if int(catalog.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION:
        raise RuntimeError("refusing to lock: catalog contract differs from the active offline-shard contract")
    if str(catalog.get("config_hash", "")) != str(build_plan.get("config_hash", "")):
        raise RuntimeError("refusing to lock: catalog and build-plan configuration hashes differ")
    counts = catalog.get("counts", {})
    units = catalog.get("units", [])
    certified = int(counts.get("complete", 0)) + int(counts.get("covered_empty", 0))
    if int(counts.get("units", -1)) != EXPECTED_UNITS or certified != EXPECTED_UNITS or len(units) != EXPECTED_UNITS:
        raise RuntimeError(
            "refusing to lock incomplete catalog: "
            f"expected={EXPECTED_UNITS:,} units={counts.get('units')} certified={certified:,} listed={len(units):,}"
        )
    expected_keys = {
        f"{ticker}:{year:04d}-{month:02d}"
        for ticker in BAR_GPT_TRAINING_TICKERS
        for year, month in _iter_months(DATASET_START_DATE, DATASET_END_DATE)
    }
    observed_keys = {str(item.get("unit_key", "")) for item in units if isinstance(item, dict)}
    if observed_keys != expected_keys:
        missing = sorted(expected_keys - observed_keys)[:5]
        extra = sorted(observed_keys - expected_keys)[:5]
        raise RuntimeError(f"refusing to lock: catalog unit keys differ; missing={missing} extra={extra}")
    return catalog


def _iter_months(start_date: str, end_date: str):
    cursor = dt.date.fromisoformat(start_date).replace(day=1)
    end = dt.date.fromisoformat(end_date)
    while cursor < end:
        yield cursor.year, cursor.month
        cursor = dt.date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.output_root.resolve()
    marker = shard_catalog_lock_path(root)
    print(
        f"Dataset: {BAR_GPT_COHORT_5TB_300_ID} | tickers={len(BAR_GPT_TRAINING_TICKERS)} "
        f"months/ticker={EXPECTED_MONTHS} units={EXPECTED_UNITS:,}",
        flush=True,
    )
    print(f"Cohort SHA-256: {BAR_GPT_COHORT_5TB_300_SHA256}", flush=True)
    print(f"One storage interval: {DATASET_START_DATE} <= session < {DATASET_END_DATE}", flush=True)
    print("Training and validation are selected later by the loader from this same catalog.", flush=True)
    print(f"Immutable output root: {root}", flush=True)
    if marker.exists():
        payload = verify_shard_catalog_lock(root)
        certify_complete_catalog(root)
        print(
            "Dataset is already complete and locked; no builder or audit was started. "
            f"Future data must use another folder. lock={marker} catalog_sha256={payload['catalog_sha256']}",
            flush=True,
        )
        return 0

    stages = commands(args)
    lifecycle_steps = len(stages) + 1
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Lifecycle {index}/{lifecycle_steps} - {label}:", flush=True)
        print(subprocess.list2cmdline(command), flush=True)
    print(f"Lifecycle {lifecycle_steps}/{lifecycle_steps} - certify exact catalog and create immutable lock", flush=True)
    if not args.execute:
        print("Plan only; add --execute to run the restart-safe build, bounded audits, and automatic lock.", flush=True)
        return 0

    verify_output_capacity(root, fresh_minimum_tb=float(args.minimum_free_tb))

    repo_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
    environment = dict(os.environ)
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for index, (label, command) in enumerate(stages, start=1):
        print(f"Starting lifecycle {index}/{lifecycle_steps}: {label}", flush=True)
        status = subprocess.call(command, cwd=repo_root, env=environment)
        if status:
            print(f"Lifecycle failed with exit code {status}: {label}; catalog remains unlocked.", flush=True)
            return int(status)

    catalog = certify_complete_catalog(root)
    payload = lock_catalog(root, reason=LOCK_REASON, execute=True)
    verify_shard_catalog_lock(root)
    print(
        "Dataset build and audits passed; catalog is now immutable. "
        f"units={catalog['counts']['units']:,} bytes={catalog['counts']['bytes']:,} "
        f"lock={marker} catalog_sha256={payload['catalog_sha256']}. "
        "Any future data must be built under a different --output-root.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
