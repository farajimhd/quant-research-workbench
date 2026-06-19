from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


NORMAL_DEFAULTS: dict[str, Any] = {
    "train_cache_gib": 2720.0,
    "validation_cache_gib": 64.0,
    "shard_size_gib": 16.0,
    "builder_micro_batch_samples": 65536,
    "origins_per_span": 512,
    "query_bundle_spans": 64,
    "workers": 8,
    "audit_samples_per_split": 256,
    "sample_record_checks": 256,
    "validation_clickhouse_checks": 25,
    "raw_audit_checks": 25,
}


SMOKE_DEFAULTS: dict[str, Any] = {
    "train_cache_gib": 0.05,
    "validation_cache_gib": 0.02,
    "shard_size_gib": 0.05,
    "builder_micro_batch_samples": 4096,
    "origins_per_span": 64,
    "query_bundle_spans": 8,
    "workers": 2,
    "audit_samples_per_split": 64,
    "sample_record_checks": 64,
    "validation_clickhouse_checks": 5,
    "raw_audit_checks": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, validate, and raw-audit a labeled event sample-cache v2 run.")
    parser.add_argument("--cache-root", default=r"D:\market-data\prepared\event_sample_cache")
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--splits", default="train,validation")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--train-index-table", default="train_2019_to_2025")
    parser.add_argument("--validation-index-table", default="validation_2026")
    parser.add_argument("--label-chunks", type=int, default=8)
    parser.add_argument("--train-cache-gib", type=float, default=None)
    parser.add_argument("--validation-cache-gib", type=float, default=None)
    parser.add_argument("--shard-size-gib", type=float, default=None)
    parser.add_argument("--builder-micro-batch-samples", type=int, default=None)
    parser.add_argument("--origins-per-span", type=int, default=None)
    parser.add_argument("--min-origin-stride", type=int, default=1)
    parser.add_argument("--max-origin-stride", type=int, default=16)
    parser.add_argument("--query-bundle-spans", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--clickhouse-max-threads", type=int, default=8)
    parser.add_argument("--clickhouse-max-memory-usage", default="80G")
    parser.add_argument("--audit-samples-per-split", type=int, default=None)
    parser.add_argument("--sample-record-checks", type=int, default=None)
    parser.add_argument("--validation-clickhouse-checks", type=int, default=None)
    parser.add_argument("--raw-audit-checks", type=int, default=None)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--smoke", action="store_true", help="Run a small end-to-end build/validate/audit cycle before the large task.")
    parser.add_argument("--skip-raw-audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Run builder dry-run only; validation/audit are skipped.")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    defaults = SMOKE_DEFAULTS if args.smoke else NORMAL_DEFAULTS
    cache_id = args.cache_id or default_cache_id(args.smoke)
    cache_path = Path(args.cache_root) / cache_id
    resolved = resolved_values(args, defaults)
    commands = [
        build_command(args, resolved, cache_id),
    ]
    if not args.dry_run:
        commands.append(validate_command(args, resolved, cache_path))
        if not args.skip_raw_audit:
            commands.append(raw_audit_command(args, resolved, cache_path))

    print("=" * 100, flush=True)
    print("Event sample-cache v2 cycle", flush=True)
    print(f"mode={'smoke' if args.smoke else 'full'}", flush=True)
    print(f"cache_path={cache_path}", flush=True)
    print(f"splits={args.splits}", flush=True)
    print(f"train_cache_gib={resolved['train_cache_gib']} validation_cache_gib={resolved['validation_cache_gib']}", flush=True)
    print(f"raw_audit={'off' if args.skip_raw_audit or args.dry_run else 'on'}", flush=True)
    print("=" * 100, flush=True)
    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {' '.join(command)}", flush=True)
    if args.print_only:
        return
    for index, command in enumerate(commands, start=1):
        print("=" * 100, flush=True)
        print(f"RUN [{index}/{len(commands)}] {' '.join(command)}", flush=True)
        print("=" * 100, flush=True)
        result = subprocess.call(command, cwd=str(repo_root()))
        if result != 0:
            raise SystemExit(result)
    print("=" * 100, flush=True)
    print(f"CYCLE DONE cache_path={cache_path}", flush=True)
    print("=" * 100, flush=True)


def resolved_values(args: argparse.Namespace, defaults: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, default in defaults.items():
        values[key] = getattr(args, key) if getattr(args, key) is not None else default
    return values


def build_command(args: argparse.Namespace, values: dict[str, Any], cache_id: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pipelines.market_sip.sample_cache.build_event_sample_cache",
        "--database",
        args.database,
        "--events-table",
        args.events_table,
        "--train-index-table",
        args.train_index_table,
        "--validation-index-table",
        args.validation_index_table,
        "--cache-root",
        args.cache_root,
        "--cache-id",
        cache_id,
        "--cache-version",
        "2",
        "--label-chunks",
        str(args.label_chunks),
        "--splits",
        args.splits,
        "--train-cache-gib",
        str(values["train_cache_gib"]),
        "--validation-cache-gib",
        str(values["validation_cache_gib"]),
        "--shard-size-gib",
        str(values["shard_size_gib"]),
        "--builder-micro-batch-samples",
        str(values["builder_micro_batch_samples"]),
        "--origins-per-span",
        str(values["origins_per_span"]),
        "--min-origin-stride",
        str(args.min_origin_stride),
        "--max-origin-stride",
        str(args.max_origin_stride),
        "--query-bundle-spans",
        str(values["query_bundle_spans"]),
        "--workers",
        str(values["workers"]),
        "--clickhouse-max-threads",
        str(args.clickhouse_max_threads),
        "--clickhouse-max-memory-usage",
        args.clickhouse_max_memory_usage,
        "--audit-samples-per-split",
        str(values["audit_samples_per_split"]),
        "--tickers",
        args.tickers,
    ] + (["--dry-run"] if args.dry_run else [])


def validate_command(args: argparse.Namespace, values: dict[str, Any], cache_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pipelines.market_sip.sample_cache.validate_event_sample_cache",
        "--cache-root",
        str(cache_path),
        "--splits",
        args.splits,
        "--sample-record-checks",
        str(values["sample_record_checks"]),
        "--audit-clickhouse-checks",
        str(values["validation_clickhouse_checks"]),
        "--database",
        args.database,
        "--events-table",
        args.events_table,
        "--max-threads",
        str(args.clickhouse_max_threads),
        "--max-memory-usage",
        args.clickhouse_max_memory_usage,
    ]


def raw_audit_command(args: argparse.Namespace, values: dict[str, Any], cache_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pipelines.market_sip.sample_cache.audit_event_sample_cache_against_raw",
        "--cache-root",
        str(cache_path),
        "--splits",
        args.splits,
        "--checks",
        str(values["raw_audit_checks"]),
        "--database",
        args.database,
        "--events-table",
        args.events_table,
        "--max-threads",
        str(args.clickhouse_max_threads),
        "--max-memory-usage",
        args.clickhouse_max_memory_usage,
    ]


def default_cache_id(smoke: bool) -> str:
    prefix = "smoke_v2_cycle" if smoke else "cache_v2_cycle"
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
