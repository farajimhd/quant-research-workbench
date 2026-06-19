from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_events import ClickHouseEventsDataConfig, PersistentClickHouseBytesClient, normalized_config  # noqa: E402
from research.mlops.clickhouse import CLICKHOUSE_ENDPOINT_ENV, CLICKHOUSE_URL_ENV, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.mlops.event_sample_cache import (  # noqa: E402
    DEFAULT_LABEL_CHUNKS,
    SAMPLE_CACHE_FORMAT_V2,
    SAMPLE_BYTES,
    EventSampleCacheDataConfig,
    decode_label_records,
    decode_sample_records,
    discover_event_sample_labeled_shards,
    discover_event_sample_shards,
    encode_single_labeled_audit_window,
    encode_single_audit_window,
    resolve_event_sample_cache_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate compact event sample-cache shards.")
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--splits", default="train,validation")
    parser.add_argument("--sample-record-checks", type=int, default=256)
    parser.add_argument("--verify-sha256", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="Validate finalized shard files even if manifest/audit files are not written yet.")
    parser.add_argument("--audit-clickhouse-checks", type=int, default=25)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--seed", type=int, default=17)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_files(discover_env_files(REPO_ROOT))
    started = time.perf_counter()
    root = resolve_validation_root(Path(args.cache_root), allow_partial=args.allow_partial)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists() and not args.allow_partial:
        raise FileNotFoundError(f"Missing cache manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"cache_id": root.name, "partial": True}
    print("=" * 96, flush=True)
    print("Validate compact event sample cache", flush=True)
    print(f"cache_root={root}", flush=True)
    print(f"cache_id={manifest.get('cache_id', '')}", flush=True)
    print(f"format={manifest.get('format', '')} version={manifest.get('version', '')}", flush=True)
    print(f"sample_bytes={SAMPLE_BYTES}", flush=True)
    print("=" * 96, flush=True)
    rng = random.Random(args.seed)
    report: dict[str, Any] = {"cache_root": str(root), "splits": {}, "errors": []}
    requested_splits = [value.strip() for value in args.splits.split(",") if value.strip()]
    is_labeled = (
        manifest.get("format") == SAMPLE_CACHE_FORMAT_V2
        or int(manifest.get("version", 1)) == 2
        or any(any((root / split).glob("shard_*.x.bin")) for split in requested_splits)
    )
    for split in requested_splits:
        result = validate_split_labeled(root, split, args, rng) if is_labeled else validate_split(root, split, args, rng)
        report["splits"][split] = result
    if args.audit_clickhouse_checks > 0:
        report["audit"] = validate_audit_samples_labeled(root, args, rng) if is_labeled else validate_audit_samples(root, args, rng)
    report["elapsed_seconds"] = time.perf_counter() - started
    report_path = root / "validation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"VALIDATION REPORT {report_path}", flush=True)
    if any(result.get("errors") for result in report["splits"].values()) or report.get("audit", {}).get("errors"):
        raise SystemExit(2)
    print(f"DONE elapsed_seconds={report['elapsed_seconds']:.1f}", flush=True)


def validate_split(root: Path, split: str, args: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    shards = discover_event_sample_shards(EventSampleCacheDataConfig(cache_root=root, split=split, batch_size=1))
    errors: list[str] = []
    sample_checks = 0
    total_samples = 0
    total_bytes = 0
    print(f"SPLIT {split}: shards={len(shards):,}", flush=True)
    for shard in shards:
        actual_size = shard.path.stat().st_size
        expected_size = shard.num_samples * SAMPLE_BYTES
        total_samples += shard.num_samples
        total_bytes += actual_size
        if actual_size != expected_size:
            errors.append(f"{shard.path}: size mismatch expected={expected_size} actual={actual_size}")
            continue
        if args.verify_sha256:
            digest = sha256_file(shard.path)
            if shard.sha256 and digest != shard.sha256:
                errors.append(f"{shard.path}: sha256 mismatch expected={shard.sha256} actual={digest}")
        checks_for_shard = min(args.sample_record_checks, max(1, shard.num_samples))
        if checks_for_shard > 0:
            records = np.memmap(shard.path, dtype=np.uint8, mode="r", shape=(shard.num_samples, SAMPLE_BYTES))
            indices = [rng.randrange(shard.num_samples) for _ in range(checks_for_shard)]
            checked = np.asarray(records[indices])
            headers, events = decode_sample_records(checked)
            if np.any(headers[:, 13] == 0):
                errors.append(f"{shard.path}: sampled records include empty header flags")
            if np.any(events[:, :, 0] == 0):
                errors.append(f"{shard.path}: sampled records include missing event presence bytes")
            sample_checks += checks_for_shard
            del records
        print(
            f"  shard={shard.shard_index:06d} samples={shard.num_samples:,} "
            f"gib={actual_size / 1024**3:.2f} errors={len(errors)}",
            flush=True,
        )
    result = {
        "shard_count": len(shards),
        "total_samples": total_samples,
        "total_gib": total_bytes / 1024**3,
        "sample_record_checks": sample_checks,
        "errors": errors,
    }
    print(f"SPLIT {split} SUMMARY {json.dumps(result, separators=(',', ':'))}", flush=True)
    return result


def validate_split_labeled(root: Path, split: str, args: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    shards = discover_event_sample_labeled_shards(EventSampleCacheDataConfig(cache_root=root, split=split, batch_size=1))
    errors: list[str] = []
    sample_checks = 0
    total_samples = 0
    total_bytes = 0
    print(f"SPLIT {split}: labeled_shards={len(shards):,}", flush=True)
    for shard in shards:
        x_size = shard.x_path.stat().st_size
        y_size = shard.y_path.stat().st_size
        expected_x_size = shard.num_samples * shard.x_sample_bytes
        expected_y_size = shard.num_samples * shard.y_sample_bytes
        total_samples += shard.num_samples
        total_bytes += x_size + y_size
        if x_size != expected_x_size:
            errors.append(f"{shard.x_path}: x size mismatch expected={expected_x_size} actual={x_size}")
            continue
        if y_size != expected_y_size:
            errors.append(f"{shard.y_path}: y size mismatch expected={expected_y_size} actual={y_size}")
            continue
        if shard.label_path is not None:
            if not shard.label_path.exists():
                errors.append(f"{shard.label_path}: labels sidecar missing")
                continue
            label_rows = parquet_row_count(shard.label_path)
            if label_rows != shard.num_samples:
                errors.append(f"{shard.label_path}: label row mismatch expected={shard.num_samples} actual={label_rows}")
                continue
        if args.verify_sha256:
            x_digest = sha256_file(shard.x_path)
            y_digest = sha256_file(shard.y_path)
            if shard.x_sha256 and x_digest != shard.x_sha256:
                errors.append(f"{shard.x_path}: x sha256 mismatch expected={shard.x_sha256} actual={x_digest}")
            if shard.y_sha256 and y_digest != shard.y_sha256:
                errors.append(f"{shard.y_path}: y sha256 mismatch expected={shard.y_sha256} actual={y_digest}")
        checks_for_shard = min(args.sample_record_checks, max(1, shard.num_samples))
        if checks_for_shard > 0:
            x_records = np.memmap(shard.x_path, dtype=np.uint8, mode="r", shape=(shard.num_samples, SAMPLE_BYTES))
            y_records = np.memmap(shard.y_path, dtype=np.uint8, mode="r", shape=(shard.num_samples, shard.y_sample_bytes))
            indices = [rng.randrange(shard.num_samples) for _ in range(checks_for_shard)]
            x_checked = np.asarray(x_records[indices])
            y_checked = np.asarray(y_records[indices])
            x_headers, x_events = decode_sample_records(x_checked)
            y_headers, y_events = decode_label_records(y_checked, label_chunks=shard.label_chunks)
            if np.any(x_headers[:, 13] == 0):
                errors.append(f"{shard.x_path}: sampled x records include empty header flags")
            if np.any(x_events[:, :, 0] == 0):
                errors.append(f"{shard.x_path}: sampled x records include missing event presence bytes")
            if np.any(y_headers[:, :, 13] == 0):
                errors.append(f"{shard.y_path}: sampled y records include empty header flags")
            if np.any(y_events[:, :, :, 0] == 0):
                errors.append(f"{shard.y_path}: sampled y records include missing event presence bytes")
            sample_checks += checks_for_shard
            del x_records
            del y_records
        print(
            f"  shard={shard.shard_index:06d} samples={shard.num_samples:,} "
            f"x_gib={x_size / 1024**3:.2f} y_gib={y_size / 1024**3:.2f} label_chunks={shard.label_chunks} errors={len(errors)}",
            flush=True,
        )
    result = {
        "shard_count": len(shards),
        "total_samples": total_samples,
        "total_gib": total_bytes / 1024**3,
        "sample_record_checks": sample_checks,
        "errors": errors,
    }
    print(f"SPLIT {split} SUMMARY {json.dumps(result, separators=(',', ':'))}", flush=True)
    return result


def resolve_validation_root(path: Path, *, allow_partial: bool) -> Path:
    if not allow_partial:
        return resolve_event_sample_cache_root(path)
    if (path / "manifest.json").exists() or any((path / split).exists() for split in ("train", "validation")):
        return path
    candidates = [
        child
        for child in path.iterdir()
        if child.is_dir() and ((child / "manifest.json").exists() or (child / "train").exists() or (child / "validation").exists())
    ] if path.exists() else []
    if not candidates:
        return path
    candidates.sort(key=lambda value: value.stat().st_mtime, reverse=True)
    return candidates[0]


def validate_audit_samples(root: Path, args: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    audit_rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*_audit_samples.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    audit_rows.append(json.loads(line))
    if not audit_rows:
        if args.allow_partial:
            return {"checked": 0, "errors": [], "note": "No audit samples found; skipped because --allow-partial is set."}
        return {"checked": 0, "errors": ["No audit samples found."]}
    rng.shuffle(audit_rows)
    checks = audit_rows[: args.audit_clickhouse_checks]
    clickhouse_url = args.clickhouse_url or sample_cache_default_clickhouse_url()
    config = normalized_config(
        ClickHouseEventsDataConfig(
            clickhouse_url=clickhouse_url,
            user=args.user or default_clickhouse_user(),
            password=args.password or default_clickhouse_password(),
            database=args.database,
            events_table=args.events_table,
            batch_size=1,
            num_spans=1,
            origins_per_span=1,
            max_threads=args.max_threads,
            max_memory_usage=args.max_memory_usage,
        )
    )
    client = PersistentClickHouseBytesClient(config.clickhouse_url, config.user, config.password)
    errors: list[str] = []
    try:
        for index, row in enumerate(checks, start=1):
            shard_path = root / row["split"] / f"shard_{int(row['shard_index']):06d}.samples.bin"
            sample_index = int(row["sample_index_in_shard"])
            expected = read_one_record(shard_path, sample_index)
            actual = encode_single_audit_window(
                client=client,
                config=config,
                ticker=str(row["ticker"]),
                origin_ordinal=int(row["origin_ordinal"]),
            )
            if not np.array_equal(expected, actual):
                errors.append(f"Audit mismatch {row['ticker']}:{row['origin_ordinal']} shard={shard_path.name} sample={sample_index}")
            print(f"AUDIT [{index}/{len(checks)}] ticker={row['ticker']} ordinal={row['origin_ordinal']} errors={len(errors)}", flush=True)
    finally:
        client.close()
    return {"checked": len(checks), "available": len(audit_rows), "errors": errors}


def validate_audit_samples_labeled(root: Path, args: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    audit_rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*_audit_samples.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    audit_rows.append(json.loads(line))
    if not audit_rows:
        if args.allow_partial:
            return {"checked": 0, "errors": [], "note": "No audit samples found; skipped because --allow-partial is set."}
        return {"checked": 0, "errors": ["No audit samples found."]}
    rng.shuffle(audit_rows)
    checks = audit_rows[: args.audit_clickhouse_checks]
    clickhouse_url = args.clickhouse_url or sample_cache_default_clickhouse_url()
    config = normalized_config(
        ClickHouseEventsDataConfig(
            clickhouse_url=clickhouse_url,
            user=args.user or default_clickhouse_user(),
            password=args.password or default_clickhouse_password(),
            database=args.database,
            events_table=args.events_table,
            batch_size=1,
            num_spans=1,
            origins_per_span=1,
            max_threads=args.max_threads,
            max_memory_usage=args.max_memory_usage,
            return_torch=False,
        )
    )
    client = PersistentClickHouseBytesClient(config.clickhouse_url, config.user, config.password)
    errors: list[str] = []
    try:
        for index, row in enumerate(checks, start=1):
            split = str(row["split"])
            shard_index = int(row["shard_index"])
            sample_index = int(row["sample_index_in_shard"])
            meta_path = root / split / f"shard_{shard_index:06d}.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            x_path = root / str(meta["x_path"])
            y_path = root / str(meta["y_path"])
            label_chunks = int(meta.get("label_chunks", row.get("label_chunks", DEFAULT_LABEL_CHUNKS)))
            expected_x = read_one_record(x_path, sample_index)
            expected_y = read_one_record(y_path, sample_index, sample_bytes=label_chunks * SAMPLE_BYTES)
            actual_x, actual_y = encode_single_labeled_audit_window(
                client=client,
                config=config,
                ticker=str(row["ticker"]),
                origin_ordinal=int(row["origin_ordinal"]),
                label_chunks=label_chunks,
            )
            if not np.array_equal(expected_x, actual_x):
                errors.append(f"Audit x mismatch {row['ticker']}:{row['origin_ordinal']} shard={x_path.name} sample={sample_index}")
            if not np.array_equal(expected_y, actual_y):
                errors.append(f"Audit y mismatch {row['ticker']}:{row['origin_ordinal']} shard={y_path.name} sample={sample_index}")
            print(
                f"AUDIT V2 [{index}/{len(checks)}] ticker={row['ticker']} ordinal={row['origin_ordinal']} "
                f"label_chunks={label_chunks} errors={len(errors)}",
                flush=True,
            )
    finally:
        client.close()
    return {"checked": len(checks), "available": len(audit_rows), "errors": errors}


def read_one_record(path: Path, sample_index: int, *, sample_bytes: int = SAMPLE_BYTES) -> np.ndarray:
    with path.open("rb") as handle:
        handle.seek(sample_index * sample_bytes)
        payload = handle.read(sample_bytes)
    if len(payload) != sample_bytes:
        raise RuntimeError(f"Could not read full sample from {path}:{sample_index}")
    return np.frombuffer(payload, dtype=np.uint8).copy()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024 * 64)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def parquet_row_count(path: Path) -> int:
    import polars as pl

    return int(pl.scan_parquet(str(path)).select(pl.len()).collect().item())


def sample_cache_default_clickhouse_url() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url()
        or "http://localhost:18123"
    )


if __name__ == "__main__":
    main()
