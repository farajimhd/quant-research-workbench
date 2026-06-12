from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse_events import (  # noqa: E402
    ClickHouseEventsDataConfig,
    PersistentClickHouseBytesClient,
    build_clickhouse_events_batch,
    load_event_index_rows,
    normalized_config,
)
from research.mlops.clickhouse_ingest_sip_flatfiles import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_user  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.event_sample_cache import (  # noqa: E402
    DEFAULT_SAMPLE_CACHE_ROOT,
    SAMPLE_BYTES,
    EventSampleShardWriter,
)


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build reusable compact event sample-cache shards from ClickHouse.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--train-index-table", default=DEFAULT_TRAIN_INDEX_TABLE)
    parser.add_argument("--validation-index-table", default=DEFAULT_VALIDATION_INDEX_TABLE)
    parser.add_argument("--cache-root", default=str(DEFAULT_SAMPLE_CACHE_ROOT))
    parser.add_argument("--cache-id", default="", help="Folder name under --cache-root. Defaults to timestamp.")
    parser.add_argument("--train-cache-gib", type=float, default=128.0)
    parser.add_argument("--validation-cache-gib", type=float, default=4.0)
    parser.add_argument("--shard-size-gib", type=float, default=16.0)
    parser.add_argument("--builder-micro-batch-samples", type=int, default=4096)
    parser.add_argument("--origins-per-span", type=int, default=32)
    parser.add_argument("--min-origin-stride", type=int, default=1)
    parser.add_argument("--max-origin-stride", type=int, default=16)
    parser.add_argument("--query-bundle-spans", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--clickhouse-max-threads", type=int, default=8)
    parser.add_argument("--clickhouse-max-memory-usage", default="80G")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--audit-samples-per-split", type=int, default=256)
    parser.add_argument("--max-index-rows", type=int, default=0)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT))
    cache_id = args.cache_id or f"cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    cache_root = Path(args.cache_root) / cache_id
    cache_root.mkdir(parents=True, exist_ok=False)
    clickhouse_url = args.clickhouse_url or default_clickhouse_url()
    user = args.user or default_clickhouse_user()
    password = args.password or default_clickhouse_password()
    print("=" * 96, flush=True)
    print("Build compact event sample cache", flush=True)
    print(f"cache_root={cache_root}", flush=True)
    print(f"database={args.database} events_table={args.events_table}", flush=True)
    print(f"train_index_table={args.train_index_table} validation_index_table={args.validation_index_table}", flush=True)
    print(f"train_cache_gib={args.train_cache_gib} validation_cache_gib={args.validation_cache_gib} shard_size_gib={args.shard_size_gib}", flush=True)
    print(f"workers={args.workers} micro_batch_samples={args.builder_micro_batch_samples}", flush=True)
    print(
        "secret_status="
        + str(
            secret_status(
                [
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                    "CLICKHOUSE_URL",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_WORKSTATION_PASSWORD",
                    "CLICKHOUSE_USER",
                    "CLICKHOUSE_PASSWORD",
                ]
            )
        ),
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env]}", flush=True)
    print("=" * 96, flush=True)

    manifest: dict[str, Any] = {
        "format": "compact_byte_event_sample_cache",
        "version": 1,
        "cache_id": cache_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "clickhouse_url": clickhouse_url,
            "database": args.database,
            "events_table": args.events_table,
            "train_index_table": args.train_index_table,
            "validation_index_table": args.validation_index_table,
        },
        "config": vars(args) | {"cache_root": str(cache_root), "sample_bytes": SAMPLE_BYTES},
        "splits": {},
        "shards": [],
        "profiles": [],
    }
    if args.dry_run:
        print("DRY RUN: cache directory created only; no ClickHouse queries.", flush=True)
        (cache_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    for split, target_gib, index_table in (
        ("train", args.train_cache_gib, args.train_index_table),
        ("validation", args.validation_cache_gib, args.validation_index_table),
    ):
        split_result = build_split(
            args=args,
            cache_root=cache_root,
            split=split,
            target_gib=target_gib,
            index_table=index_table,
            clickhouse_url=clickhouse_url,
            user=user,
            password=password,
        )
        manifest["splits"][split] = split_result["summary"]
        manifest["shards"].extend(split_result["shards"])
        manifest["profiles"].extend(split_result["profiles"])
        (cache_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["completed_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["elapsed_seconds"] = time.perf_counter() - started
    (cache_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"DONE cache={cache_root} elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f}", flush=True)


def build_split(
    *,
    args: argparse.Namespace,
    cache_root: Path,
    split: str,
    target_gib: float,
    index_table: str,
    clickhouse_url: str,
    user: str,
    password: str,
) -> dict[str, Any]:
    target_samples = max(1, int((target_gib * 1024**3) // SAMPLE_BYTES))
    shard_samples = max(1, int((args.shard_size_gib * 1024**3) // SAMPLE_BYTES))
    micro_batch_samples = max(1, int(args.builder_micro_batch_samples))
    if micro_batch_samples % int(args.origins_per_span) != 0:
        raise ValueError("--builder-micro-batch-samples must be divisible by --origins-per-span")
    num_spans = micro_batch_samples // int(args.origins_per_span)
    split_started = time.perf_counter()
    data_config = normalized_config(
        ClickHouseEventsDataConfig(
            clickhouse_url=clickhouse_url,
            user=user,
            password=password,
            database=args.database,
            events_table=args.events_table,
            train_index_table=args.train_index_table,
            validation_index_table=args.validation_index_table,
            index_table=index_table,
            split=split,
            tickers=parse_tickers(args.tickers),
            batch_size=micro_batch_samples,
            num_spans=num_spans,
            origins_per_span=args.origins_per_span,
            min_origin_stride=args.min_origin_stride,
            max_origin_stride=args.max_origin_stride,
            query_bundle_spans=args.query_bundle_spans,
            max_threads=args.clickhouse_max_threads,
            max_memory_usage=args.clickhouse_max_memory_usage,
            seed=args.seed,
            max_index_rows=args.max_index_rows,
        )
    )
    text_client = ClickHouseHttpClient(data_config.clickhouse_url, data_config.user, data_config.password)
    print(f"SPLIT {split}: loading index rows from {args.database}.{index_table}", flush=True)
    index_rows = load_event_index_rows(text_client, data_config)
    print(
        f"SPLIT {split}: index_rows={len(index_rows):,} target_samples={target_samples:,} "
        f"target_gib={(target_samples * SAMPLE_BYTES) / 1024**3:.2f} shard_samples={shard_samples:,}",
        flush=True,
    )
    writer = EventSampleShardWriter(
        cache_root=cache_root,
        split=split,
        shard_sample_target=shard_samples,
        audit_sample_limit=args.audit_samples_per_split,
        audit_rng=random.Random(args.seed + (0 if split == "train" else 1_000_000)),
    )
    samples_written = 0
    micro_batches_completed = 0
    profiles: list[dict[str, Any]] = []
    split_errors: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures: set[Future[dict[str, Any]]] = set()
        next_job = 0

        def submit_until_full() -> None:
            nonlocal next_job
            while samples_written + len(futures) * micro_batch_samples < target_samples and len(futures) < max(1, int(args.workers)) * 2:
                job_seed = args.seed + next_job * 1_009 + (10_000_000 if split == "validation" else 0)
                futures.add(executor.submit(build_micro_batch, data_config, index_rows, job_seed))
                next_job += 1

        submit_until_full()
        while samples_written < target_samples and futures:
            for future in as_completed(futures):
                futures.remove(future)
                result = future.result()
                take_batch = result["batch"]
                if samples_written + int(take_batch["header_uint8"].shape[0]) > target_samples:
                    keep = target_samples - samples_written
                    take_batch = trim_batch(take_batch, keep)
                written = writer.write_batch(take_batch)
                samples_written += written
                micro_batches_completed += 1
                profiles.append(result["profile"])
                for key, value in result.get("reject_counts", {}).items():
                    split_errors[key] = split_errors.get(key, 0) + int(value)
                elapsed = time.perf_counter() - split_started
                rate = samples_written / max(elapsed, 1e-9)
                remaining = max(0, target_samples - samples_written)
                eta = remaining / max(rate, 1e-9)
                print(
                    f"{split.upper()} [{micro_batches_completed:,}] samples={samples_written:,}/{target_samples:,} "
                    f"({100.0 * samples_written / target_samples:.2f}%) rate={rate:,.0f}/s "
                    f"eta_minutes={eta / 60.0:.1f} shards={len(writer.shards):,}",
                    flush=True,
                )
                submit_until_full()
                break
    writer.close()
    summary = {
        "target_samples": target_samples,
        "samples_written": samples_written,
        "target_gib": target_gib,
        "actual_gib": samples_written * SAMPLE_BYTES / 1024**3,
        "shard_count": len(writer.shards),
        "micro_batches_completed": micro_batches_completed,
        "elapsed_seconds": time.perf_counter() - split_started,
        "reject_counts": split_errors,
    }
    print(f"SPLIT {split} DONE {json.dumps(summary, separators=(',', ':'))}", flush=True)
    return {"summary": summary, "shards": writer.shards, "profiles": profiles}


def build_micro_batch(config: ClickHouseEventsDataConfig, index_rows: list[Any], seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    client = PersistentClickHouseBytesClient(config.clickhouse_url, config.user, config.password)
    try:
        batch = build_clickhouse_events_batch(index_rows, config, client, rng)
    finally:
        client.close()
    return {
        "batch": batch,
        "profile": batch.get("profile", {}),
        "reject_counts": batch.get("reject_counts", {}),
    }


def trim_batch(batch: dict[str, Any], keep: int) -> dict[str, Any]:
    out = dict(batch)
    for key in ("header_uint8", "events_uint8", "origin_timestamp_ns", "origin_ordinal"):
        if key in out:
            out[key] = out[key][:keep]
    if "ticker" in out:
        out["ticker"] = list(out["ticker"])[:keep]
    return out


def parse_tickers(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return values or ("ALL",)


def default_clickhouse_url() -> str:
    return default_clickhouse_url_with_network_fallback() or "http://localhost:18123"


if __name__ == "__main__":
    main()
