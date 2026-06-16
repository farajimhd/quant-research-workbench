from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse_events import ClickHouseEventsDataConfig, PersistentClickHouseBytesClient, normalized_config  # noqa: E402
from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_user  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.mlops.event_sample_cache import (  # noqa: E402
    SAMPLE_BYTES,
    EventSampleCacheDataConfig,
    decode_sample_records,
    discover_event_sample_shards,
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
    print(f"sample_bytes={SAMPLE_BYTES}", flush=True)
    print("=" * 96, flush=True)
    rng = random.Random(args.seed)
    report: dict[str, Any] = {"cache_root": str(root), "splits": {}, "errors": []}
    for split in [value.strip() for value in args.splits.split(",") if value.strip()]:
        result = validate_split(root, split, args, rng)
        report["splits"][split] = result
    if args.audit_clickhouse_checks > 0:
        report["audit"] = validate_audit_samples(root, args, rng)
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
    clickhouse_url = args.clickhouse_url or default_clickhouse_url_with_network_fallback() or "http://localhost:18123"
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


def read_one_record(path: Path, sample_index: int) -> np.ndarray:
    with path.open("rb") as handle:
        handle.seek(sample_index * SAMPLE_BYTES)
        payload = handle.read(SAMPLE_BYTES)
    if len(payload) != SAMPLE_BYTES:
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


if __name__ == "__main__":
    main()
