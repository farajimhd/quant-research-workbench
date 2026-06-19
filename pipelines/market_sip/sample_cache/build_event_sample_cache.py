from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_events import (  # noqa: E402
    ClickHouseEventsDataConfig,
    PersistentClickHouseBytesClient,
    build_clickhouse_events_batch,
    load_event_index_rows,
    normalized_config,
)
from research.mlops.clickhouse import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_URL_ENV,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url as default_clickhouse_url_from_env,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.event_sample_cache import (  # noqa: E402
    DEFAULT_LABEL_CHUNKS,
    DEFAULT_SAMPLE_CACHE_ROOT,
    LABELED_SAMPLE_BYTES,
    SAMPLE_CACHE_FORMAT,
    SAMPLE_CACHE_FORMAT_V2,
    SAMPLE_CACHE_VERSION,
    SAMPLE_CACHE_VERSION_V2,
    SAMPLE_BYTES,
    EventSampleLabeledShardWriter,
    EventSampleShardWriter,
)


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE = "events"
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"
_THREAD_LOCAL = threading.local()


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
    parser.add_argument("--cache-version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--label-chunks", type=int, default=DEFAULT_LABEL_CHUNKS, help="v2 only: number of next 128-event chunks stored as labels.")
    parser.add_argument("--train-cache-gib", type=float, default=128.0)
    parser.add_argument("--validation-cache-gib", type=float, default=4.0)
    parser.add_argument(
        "--splits",
        default="train,validation",
        help="Comma-separated splits to build. Use 'validation' to build only validation shards.",
    )
    parser.add_argument("--shard-size-gib", type=float, default=16.0)
    parser.add_argument("--builder-micro-batch-samples", type=int, default=65536)
    parser.add_argument("--origins-per-span", type=int, default=512)
    parser.add_argument("--min-origin-stride", type=int, default=1)
    parser.add_argument("--max-origin-stride", type=int, default=16)
    parser.add_argument("--query-bundle-spans", type=int, default=64)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--pending-multiplier",
        type=int,
        default=1,
        help="Maximum queued microbatches as workers * pending_multiplier. Keep this at 1 for large labeled v2 samples.",
    )
    parser.add_argument("--eta-recent-window", type=int, default=50, help="Completed microbatches used for rolling-rate ETA.")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0, help="Print pending-worker status if no microbatch completes within this interval.")
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
    requested_splits = parse_requested_splits(args.splits)
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
    print(f"splits={','.join(requested_splits)}", flush=True)
    print(f"cache_version={args.cache_version} label_chunks={args.label_chunks if args.cache_version == 2 else 0}", flush=True)
    print(f"train_cache_gib={args.train_cache_gib} validation_cache_gib={args.validation_cache_gib} shard_size_gib={args.shard_size_gib}", flush=True)
    print(
        f"workers={args.workers} pending_multiplier={args.pending_multiplier} "
        f"micro_batch_samples={args.builder_micro_batch_samples}",
        flush=True,
    )
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
        "format": SAMPLE_CACHE_FORMAT_V2 if args.cache_version == 2 else SAMPLE_CACHE_FORMAT,
        "version": SAMPLE_CACHE_VERSION_V2 if args.cache_version == 2 else SAMPLE_CACHE_VERSION,
        "cache_id": cache_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "clickhouse_url": clickhouse_url,
            "database": args.database,
            "events_table": args.events_table,
            "train_index_table": args.train_index_table,
            "validation_index_table": args.validation_index_table,
        },
        "config": vars(args)
        | {
            "cache_root": str(cache_root),
            "sample_bytes": SAMPLE_BYTES,
            "labeled_sample_bytes": LABELED_SAMPLE_BYTES if args.cache_version == 2 and args.label_chunks == DEFAULT_LABEL_CHUNKS else SAMPLE_BYTES + args.label_chunks * SAMPLE_BYTES,
        },
        "splits": {},
        "shards": [],
        "profiles": [],
    }
    if args.dry_run:
        print("DRY RUN: cache directory created only; no ClickHouse queries.", flush=True)
        (cache_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    split_specs = {
        "train": (args.train_cache_gib, args.train_index_table),
        "validation": (args.validation_cache_gib, args.validation_index_table),
    }
    for split in requested_splits:
        target_gib, index_table = split_specs[split]
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


def parse_requested_splits(raw: str) -> tuple[str, ...]:
    splits = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    valid = {"train", "validation"}
    invalid = [split for split in splits if split not in valid]
    if invalid:
        raise ValueError(f"--splits contains invalid value(s): {invalid}. Valid values are: train, validation")
    if not splits:
        raise ValueError("--splits must include at least one split: train or validation")
    return splits


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
    sample_bytes_on_disk = SAMPLE_BYTES if int(args.cache_version) == 1 else SAMPLE_BYTES + int(args.label_chunks) * SAMPLE_BYTES
    target_samples = max(1, int((target_gib * 1024**3) // sample_bytes_on_disk))
    shard_samples = max(1, int((args.shard_size_gib * 1024**3) // sample_bytes_on_disk))
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
            label_chunks=int(args.label_chunks) if int(args.cache_version) == 2 else 0,
            return_torch=False,
        )
    )
    text_client = ClickHouseHttpClient(data_config.clickhouse_url, data_config.user, data_config.password)
    print(f"SPLIT {split}: loading index rows from {args.database}.{index_table}", flush=True)
    index_rows = load_event_index_rows(text_client, data_config)
    print(
        f"SPLIT {split}: index_rows={len(index_rows):,} target_samples={target_samples:,} "
        f"target_gib={(target_samples * sample_bytes_on_disk) / 1024**3:.2f} shard_samples={shard_samples:,} "
        f"cache_version={args.cache_version} sample_bytes_on_disk={sample_bytes_on_disk:,}",
        flush=True,
    )
    if int(args.cache_version) == 2:
        x_only_equivalent = int((target_gib * 1024**3) // SAMPLE_BYTES)
        print(
            f"SPLIT {split}: v2 labeled samples store x_bytes={SAMPLE_BYTES:,} "
            f"y_bytes={int(args.label_chunks) * SAMPLE_BYTES:,} label_chunks={int(args.label_chunks):,}; "
            f"same GiB would hold {x_only_equivalent:,} x-only v1 samples",
            flush=True,
        )
    if int(args.cache_version) == 2:
        writer = EventSampleLabeledShardWriter(
            cache_root=cache_root,
            split=split,
            shard_sample_target=shard_samples,
            label_chunks=int(args.label_chunks),
            audit_sample_limit=args.audit_samples_per_split,
            audit_rng=random.Random(args.seed + (0 if split == "train" else 1_000_000)),
        )
    else:
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
    recent_points: deque[tuple[float, int]] = deque([(split_started, 0)], maxlen=max(2, int(args.eta_recent_window) + 1))
    max_pending_jobs = max(1, int(args.workers)) * max(1, int(args.pending_multiplier))
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        next_job = 0

        def submit_until_full() -> None:
            nonlocal next_job
            while samples_written + len(futures) * micro_batch_samples < target_samples and len(futures) < max_pending_jobs:
                job_seed = args.seed + next_job * 1_009 + (10_000_000 if split == "validation" else 0)
                job_id = next_job + 1
                future = executor.submit(build_micro_batch, data_config, index_rows, job_seed, job_id)
                futures[future] = {
                    "job_id": job_id,
                    "seed": job_seed,
                    "submitted_at": time.perf_counter(),
                }
                next_job += 1

        submit_until_full()
        while samples_written < target_samples and futures:
            done, _pending = wait(set(futures), timeout=max(1.0, float(args.heartbeat_seconds)), return_when=FIRST_COMPLETED)
            if not done:
                elapsed = time.perf_counter() - split_started
                now = time.perf_counter()
                oldest_age_seconds = max((now - meta["submitted_at"] for meta in futures.values()), default=0.0)
                oldest_job_id = max(futures.values(), key=lambda meta: now - meta["submitted_at"])["job_id"] if futures else 0
                print(
                    f"{split.upper()} HEARTBEAT samples={samples_written:,}/{target_samples:,} "
                    f"completed_microbatches={micro_batches_completed:,} pending_workers={len(futures):,} "
                    f"oldest_pending_job={oldest_job_id} oldest_pending_minutes={oldest_age_seconds / 60.0:.1f} "
                    f"elapsed_minutes={elapsed / 60.0:.1f}",
                    flush=True,
                )
                write_split_progress(
                    cache_root,
                    split=split,
                    target_samples=target_samples,
                    samples_written=samples_written,
                    micro_batches_completed=micro_batches_completed,
                    elapsed_seconds=elapsed,
                    rate_recent_samples_per_sec=0.0,
                    eta_recent_seconds=0.0,
                    rate_total_samples_per_sec=0.0,
                    eta_total_seconds=0.0,
                    writer=writer,
                    pending_workers=len(futures),
                )
                continue
            for future in done:
                job_meta = futures.pop(future)
                result = future.result()
                take_batch = result["batch"]
                if samples_written + int(take_batch["header_uint8"].shape[0]) > target_samples:
                    keep = target_samples - samples_written
                    take_batch = trim_batch(take_batch, keep)
                written = writer.write_batch(take_batch)
                samples_written += written
                micro_batches_completed += 1
                profiles.append(result["profile"])
                profile = result.get("profile", {})
                for key, value in result.get("reject_counts", {}).items():
                    split_errors[key] = split_errors.get(key, 0) + int(value)
                now = time.perf_counter()
                elapsed = now - split_started
                recent_points.append((now, samples_written))
                rate_total = samples_written / max(elapsed, 1e-9)
                first_recent_time, first_recent_samples = recent_points[0]
                rate_recent = (samples_written - first_recent_samples) / max(now - first_recent_time, 1e-9)
                remaining = max(0, target_samples - samples_written)
                eta_total = remaining / max(rate_total, 1e-9)
                eta_recent = remaining / max(rate_recent, 1e-9)
                print(
                    f"{split.upper()} [{micro_batches_completed:,}] samples={samples_written:,}/{target_samples:,} "
                    f"({100.0 * samples_written / target_samples:.2f}%) "
                    f"rate_recent={rate_recent:,.0f}/s eta_recent_hours={eta_recent / 3600.0:.1f} "
                    f"rate_total={rate_total:,.0f}/s eta_total_hours={eta_total / 3600.0:.1f} "
                    f"shards={len(writer.shards):,} "
                    f"job={int(result.get('job_id', job_meta['job_id']))} "
                    f"q={float(profile.get('data/query_seconds', 0.0)):.2f}s "
                    f"enc={float(profile.get('data/encode_seconds', 0.0)):.2f}s "
                    f"queries={float(profile.get('data/query_count', 0.0)):.0f}",
                    flush=True,
                )
                write_split_progress(
                    cache_root,
                    split=split,
                    target_samples=target_samples,
                    samples_written=samples_written,
                    micro_batches_completed=micro_batches_completed,
                    elapsed_seconds=elapsed,
                    rate_recent_samples_per_sec=rate_recent,
                    eta_recent_seconds=eta_recent,
                    rate_total_samples_per_sec=rate_total,
                    eta_total_seconds=eta_total,
                    writer=writer,
                    pending_workers=len(futures),
                )
                submit_until_full()
    writer.close()
    summary = {
        "target_samples": target_samples,
        "samples_written": samples_written,
        "target_gib": target_gib,
        "actual_gib": samples_written * sample_bytes_on_disk / 1024**3,
        "cache_version": int(args.cache_version),
        "label_chunks": int(args.label_chunks) if int(args.cache_version) == 2 else 0,
        "sample_bytes_on_disk": sample_bytes_on_disk,
        "shard_count": len(writer.shards),
        "micro_batches_completed": micro_batches_completed,
        "elapsed_seconds": time.perf_counter() - split_started,
        "reject_counts": split_errors,
    }
    print(f"SPLIT {split} DONE {json.dumps(summary, separators=(',', ':'))}", flush=True)
    return {"summary": summary, "shards": writer.shards, "profiles": profiles}


def write_split_progress(
    cache_root: Path,
    *,
    split: str,
    target_samples: int,
    samples_written: int,
    micro_batches_completed: int,
    elapsed_seconds: float,
    rate_recent_samples_per_sec: float,
    eta_recent_seconds: float,
    rate_total_samples_per_sec: float,
    eta_total_seconds: float,
    writer: EventSampleShardWriter | EventSampleLabeledShardWriter,
    pending_workers: int,
) -> None:
    progress = {
        "split": split,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "target_samples": target_samples,
        "samples_written": samples_written,
        "progress_pct": 100.0 * samples_written / max(1, target_samples),
        "micro_batches_completed": micro_batches_completed,
        "pending_workers": pending_workers,
        "elapsed_seconds": elapsed_seconds,
        "rate_recent_samples_per_sec": rate_recent_samples_per_sec,
        "eta_recent_seconds": eta_recent_seconds,
        "eta_recent_hours": eta_recent_seconds / 3600.0,
        "rate_total_samples_per_sec": rate_total_samples_per_sec,
        "eta_total_seconds": eta_total_seconds,
        "eta_total_hours": eta_total_seconds / 3600.0,
        "samples_per_second": rate_total_samples_per_sec,
        "eta_seconds": eta_total_seconds,
        "eta_hours": eta_total_seconds / 3600.0,
        "eta_minutes": eta_total_seconds / 60.0,
        "current_shard": writer.current_shard_status(),
    }
    tmp_path = cache_root / f"{split}_progress.json.tmp"
    final_path = cache_root / f"{split}_progress.json"
    tmp_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)


def build_micro_batch(config: ClickHouseEventsDataConfig, index_rows: list[Any], seed: int, job_id: int = 0) -> dict[str, Any]:
    rng = random.Random(seed)
    client = thread_local_client(config)
    started = time.perf_counter()
    batch = build_clickhouse_events_batch(index_rows, config, client, rng)
    return {
        "job_id": job_id,
        "job_seconds": time.perf_counter() - started,
        "batch": batch,
        "profile": batch.get("profile", {}),
        "reject_counts": batch.get("reject_counts", {}),
    }


def thread_local_client(config: ClickHouseEventsDataConfig) -> PersistentClickHouseBytesClient:
    key = (config.clickhouse_url, config.user, config.password)
    current = getattr(_THREAD_LOCAL, "clickhouse_client_key", None)
    client = getattr(_THREAD_LOCAL, "clickhouse_client", None)
    if client is None or current != key:
        if client is not None:
            client.close()
        client = PersistentClickHouseBytesClient(config.clickhouse_url, config.user, config.password)
        _THREAD_LOCAL.clickhouse_client = client
        _THREAD_LOCAL.clickhouse_client_key = key
    return client


def trim_batch(batch: dict[str, Any], keep: int) -> dict[str, Any]:
    out = dict(batch)
    for key in ("header_uint8", "events_uint8", "label_header_uint8", "label_events_uint8", "origin_timestamp_ns", "origin_ordinal"):
        if key in out:
            out[key] = out[key][:keep]
    if "ticker" in out:
        out["ticker"] = list(out["ticker"])[:keep]
    return out


def parse_tickers(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return values or ("ALL",)


def default_clickhouse_url() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url_from_env()
        or "http://localhost:18123"
    )


if __name__ == "__main__":
    main()
