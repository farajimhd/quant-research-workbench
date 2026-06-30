from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.clickhouse_events import (
    DEFAULT_CONTEXT_EVENTS,
    EVENT_ROW_DTYPE,
    encode_unified_event_windows,
)
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.materialized_cache import (
    DEFAULT_MATERIALIZED_CACHE_ROOT,
    MATERIALIZED_CACHE_FORMAT,
    MATERIALIZED_CACHE_VERSION,
    TICKER_BYTE_WIDTH,
    timestamp_us_to_utc,
)


EVENT_COLUMNS: tuple[str, ...] = (
    "ordinal",
    "event_type",
    "sip_timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_tokens_packed",
)


@dataclass(frozen=True, slots=True)
class MaterializedCacheAuditConfig:
    cache_root: Path
    split: str = "train"
    start_timestamp_us: int = 0
    end_timestamp_us: int = 0
    sample_shards: int = 3
    samples_per_shard: int = 2
    zero_sample_rows: int = 64
    seed: int = 17
    source_checks: bool = True
    hash_files: bool = False
    fail_on_warning: bool = False
    output_path: Path | None = None
    sample_output_path: Path | None = None
    clickhouse_url: str = ""
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    database: str = ""
    events_table: str = ""
    max_threads: int = 4
    max_memory_usage: str = "8G"
    min_source_coverage_ratio: float = 0.90
    max_source_overage_ratio: float = 1.01


@dataclass(slots=True)
class AuditIssue:
    severity: str
    code: str
    message: str
    shard_index: int | None = None
    sample_index: int | None = None
    tensor: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AuditResult:
    ok: bool
    status: str
    cache_root: str
    split: str
    started_at: str
    completed_at: str
    elapsed_seconds: float
    summary: dict[str, Any]
    issues: list[AuditIssue]
    report_path: str
    sample_report_path: str

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "cache_root": self.cache_root,
            "split": self.split,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
            "report_path": self.report_path,
            "sample_report_path": self.sample_report_path,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit rolling materialized training cache shards.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_MATERIALIZED_CACHE_ROOT)
    parser.add_argument("--cache-id", default="", help="Cache id under --cache-root. Omit when --cache-root is the cache directory.")
    parser.add_argument("--cache-path", type=Path, default=None, help="Explicit cache directory containing manifest.json.")
    parser.add_argument("--split", default="train")
    parser.add_argument("--start-timestamp-us", type=int, default=0)
    parser.add_argument("--end-timestamp-us", type=int, default=0)
    parser.add_argument("--sample-shards", type=int, default=3)
    parser.add_argument("--samples-per-shard", type=int, default=2)
    parser.add_argument("--zero-sample-rows", type=int, default=64)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--source-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--hash-files", action="store_true", help="Hash full shard binaries. This is expensive for 16 GiB shards.")
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--sample-output-path", type=Path, default=None)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default="")
    parser.add_argument("--events-table", default="")
    parser.add_argument("--max-threads", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="8G")
    parser.add_argument("--min-source-coverage-ratio", type=float, default=0.90)
    parser.add_argument("--max-source-overage-ratio", type=float, default=1.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded = load_env_files(env_files)
    if loaded:
        print("Loaded .env files: " + ", ".join(str(path) for path in loaded), flush=True)
    cache_root = _resolve_cache_root(args)
    config = MaterializedCacheAuditConfig(
        cache_root=cache_root,
        split=args.split,
        start_timestamp_us=max(0, int(args.start_timestamp_us)),
        end_timestamp_us=max(0, int(args.end_timestamp_us)),
        sample_shards=max(0, int(args.sample_shards)),
        samples_per_shard=max(0, int(args.samples_per_shard)),
        zero_sample_rows=max(0, int(args.zero_sample_rows)),
        seed=int(args.seed),
        source_checks=bool(args.source_checks),
        hash_files=bool(args.hash_files),
        fail_on_warning=bool(args.fail_on_warning),
        output_path=args.output_path,
        sample_output_path=args.sample_output_path,
        clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
        clickhouse_user=args.user or default_clickhouse_user(),
        clickhouse_password=args.password or default_clickhouse_password(),
        database=args.database,
        events_table=args.events_table,
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
        min_source_coverage_ratio=max(0.0, float(args.min_source_coverage_ratio)),
        max_source_overage_ratio=max(1.0, float(args.max_source_overage_ratio)),
    )
    result = run_audit(config)
    print(json.dumps(_compact_console_summary(result), indent=2, sort_keys=True), flush=True)
    return 0 if result.ok else 1


def run_audit(config: MaterializedCacheAuditConfig) -> AuditResult:
    started = time.perf_counter()
    started_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    cache_root = Path(config.cache_root)
    issues: list[AuditIssue] = []
    manifest = _read_json(cache_root / "manifest.json")
    split = str(config.split)
    source = dict(manifest.get("source") or {})
    database = str(config.database or source.get("database") or "market_sip_compact")
    events_table = str(config.events_table or source.get("events_table") or "events")
    start_us = int(config.start_timestamp_us or (manifest.get("date_range") or {}).get("start_timestamp_us") or 0)
    end_us = int(config.end_timestamp_us or (manifest.get("date_range") or {}).get("end_timestamp_us") or 0)
    summary: dict[str, Any] = {
        "manifest_status": manifest.get("status", ""),
        "manifest_format": manifest.get("format", ""),
        "manifest_version": manifest.get("version", 0),
        "period": {
            "start_timestamp_us": start_us,
            "end_timestamp_us": end_us,
            "start_utc": timestamp_us_to_utc(start_us),
            "end_utc": timestamp_us_to_utc(end_us),
        },
        "source_checks_enabled": bool(config.source_checks),
        "hash_files": bool(config.hash_files),
    }

    _check_manifest(manifest, issues)
    shards = _load_shard_sidecars(cache_root=cache_root, split=split, manifest=manifest, issues=issues)
    _check_shard_sequence(shards, issues)
    file_summary = _check_shard_files(cache_root=cache_root, shards=shards, issues=issues, hash_files=bool(config.hash_files))
    tensor_summary = _check_tensor_layouts(cache_root=cache_root, shards=shards, issues=issues)
    period_summary = _check_origin_periods(cache_root=cache_root, shards=shards, start_us=start_us, end_us=end_us, issues=issues)
    identity_summary = _check_sample_identities(cache_root=cache_root, shards=shards, issues=issues)
    selected = _select_sample_points(shards, sample_shards=config.sample_shards, samples_per_shard=config.samples_per_shard, seed=config.seed)
    content_summary, sample_rows = _check_sample_content(
        cache_root=cache_root,
        shards=shards,
        selected=selected,
        zero_sample_rows=config.zero_sample_rows,
        issues=issues,
    )
    cache_config = _config_from_manifest(manifest)
    sample_stride_events = max(1, int(cache_config.sample_stride_events))
    low_coverage_severity = _source_coverage_low_severity(manifest)
    source_coverage: dict[str, Any] = {
        "skipped": not bool(config.source_checks),
        "reason": "source_checks_disabled" if not bool(config.source_checks) else "",
    }
    source_summary: dict[str, Any] = {"checked_samples": 0, "checked_windows": 0, "skipped": not bool(config.source_checks)}
    client: ClickHouseHttpClient | None = None
    if config.source_checks:
        client = ClickHouseHttpClient(config.clickhouse_url or default_clickhouse_url(), config.clickhouse_user, config.clickhouse_password)
        source_coverage = _check_source_period_coverage(
            client=client,
            database=database,
            events_table=events_table,
            start_us=start_us,
            end_us=end_us,
            materialized_samples=int(identity_summary.get("samples_checked") or 0),
            sample_stride_events=sample_stride_events,
            issues=issues,
            max_threads=max(1, int(config.max_threads)),
            max_memory_usage=str(config.max_memory_usage),
            min_coverage_ratio=float(config.min_source_coverage_ratio),
            max_overage_ratio=float(config.max_source_overage_ratio),
            low_coverage_severity=low_coverage_severity,
        )
    if config.source_checks and selected and client is not None:
        events_per_chunk, context_lags = _context_geometry_from_manifest(manifest)
        source_summary = _check_source_event_windows(
            client=client,
            database=database,
            events_table=events_table,
            events_per_chunk=events_per_chunk,
            context_lags=context_lags,
            cache_root=cache_root,
            shards=shards,
            selected=selected,
            sample_rows=sample_rows,
            issues=issues,
            max_threads=max(1, int(config.max_threads)),
            max_memory_usage=str(config.max_memory_usage),
        )
    summary.update(
        {
            "shards": {
                "count": len(shards),
                "manifest_count": len(manifest.get("shards") or []),
            },
            "files": file_summary,
            "tensors": tensor_summary,
            "origin_periods": period_summary,
            "sample_identities": identity_summary,
            "sample_content": content_summary,
            "source_period_coverage": source_coverage,
            "source_event_windows": source_summary,
            "issue_counts": _issue_counts(issues),
        }
    )
    if config.fail_on_warning and any(issue.severity == "warning" for issue in issues):
        issues.append(AuditIssue(severity="error", code="warning_escalated", message="--fail-on-warning converted warnings to audit failure."))
    completed_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    ok = not any(issue.severity == "error" for issue in issues)
    report_path = Path(config.output_path) if config.output_path is not None else cache_root / f"{split}_audit_report.json"
    sample_report_path = Path(config.sample_output_path) if config.sample_output_path is not None else cache_root / f"{split}_audit_samples_checked.jsonl"
    result = AuditResult(
        ok=ok,
        status="passed" if ok else "failed",
        cache_root=str(cache_root),
        split=split,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=time.perf_counter() - started,
        summary=summary,
        issues=issues,
        report_path=str(report_path),
        sample_report_path=str(sample_report_path),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(_jsonable(result.to_json()), indent=2, sort_keys=True), encoding="utf-8")
    if sample_rows:
        sample_report_path.parent.mkdir(parents=True, exist_ok=True)
        with sample_report_path.open("w", encoding="utf-8") as handle:
            for row in sample_rows:
                handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")
    return result


def _check_manifest(manifest: Mapping[str, Any], issues: list[AuditIssue]) -> None:
    if manifest.get("format") != MATERIALIZED_CACHE_FORMAT:
        issues.append(AuditIssue("error", "manifest_format", f"Unexpected manifest format: {manifest.get('format')!r}"))
    if int(manifest.get("version") or 0) != MATERIALIZED_CACHE_VERSION:
        issues.append(AuditIssue("error", "manifest_version", f"Unexpected manifest version: {manifest.get('version')!r}"))
    status = str(manifest.get("status") or "")
    if status and status != "complete":
        issues.append(AuditIssue("error", "manifest_status", f"Manifest status is not complete: {status!r}"))


def _load_shard_sidecars(
    *,
    cache_root: Path,
    split: str,
    manifest: Mapping[str, Any],
    issues: list[AuditIssue],
) -> list[dict[str, Any]]:
    split_dir = cache_root / split
    if not split_dir.exists():
        issues.append(AuditIssue("error", "split_dir_missing", f"Missing split directory: {split_dir}"))
        return []
    sidecars: list[dict[str, Any]] = []
    for path in sorted(split_dir.glob("shard_*.json")):
        try:
            row = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            issues.append(AuditIssue("error", "sidecar_read_failed", f"Could not read shard sidecar {path}: {exc!r}"))
            continue
        row["_sidecar_path"] = str(path)
        sidecars.append(row)
    sidecars.sort(key=lambda item: _int_value(item.get("shard_index"), -1))
    manifest_count = len(manifest.get("shards") or [])
    if manifest_count and manifest_count != len(sidecars):
        issues.append(AuditIssue("error", "manifest_sidecar_count", f"Manifest shard count {manifest_count:,} != sidecar count {len(sidecars):,}."))
    tmp_files = sorted((cache_root / split).glob("shard_*.tmp")) + sorted((cache_root / split).glob("shard_*.bin.tmp"))
    if tmp_files:
        issues.append(AuditIssue("error", "tmp_files_present", f"Found unfinished shard temp files: {len(tmp_files):,}."))
    return sidecars


def _check_shard_sequence(shards: list[dict[str, Any]], issues: list[AuditIssue]) -> None:
    actual = [_int_value(row.get("shard_index"), -1) for row in shards]
    expected = list(range(len(shards)))
    if actual != expected:
        issues.append(AuditIssue("error", "shard_sequence", "Shard indices are not contiguous.", details={"actual_head": actual[:20], "expected_head": expected[:20]}))
    for row in shards:
        if row.get("format") != MATERIALIZED_CACHE_FORMAT:
            issues.append(AuditIssue("error", "shard_format", f"Unexpected shard format: {row.get('format')!r}", shard_index=_int_value(row.get("shard_index"), -1)))
        if int(row.get("version") or 0) != MATERIALIZED_CACHE_VERSION:
            issues.append(AuditIssue("error", "shard_version", f"Unexpected shard version: {row.get('version')!r}", shard_index=_int_value(row.get("shard_index"), -1)))


def _check_shard_files(*, cache_root: Path, shards: list[dict[str, Any]], issues: list[AuditIssue], hash_files: bool) -> dict[str, Any]:
    total_bytes = 0
    hashed = 0
    for row in shards:
        shard_index = _int_value(row.get("shard_index"), -1)
        path = _shard_bin_path(cache_root, row)
        if not path.exists():
            issues.append(AuditIssue("error", "bin_missing", f"Missing shard binary: {path}", shard_index=shard_index))
            continue
        stat_bytes = int(path.stat().st_size)
        expected_bytes = int(row.get("actual_shard_bytes") or 0)
        total_bytes += stat_bytes
        if expected_bytes != stat_bytes:
            issues.append(AuditIssue("error", "bin_size_mismatch", f"Shard binary size {stat_bytes:,} != sidecar actual_shard_bytes {expected_bytes:,}.", shard_index=shard_index))
        if hash_files:
            digest = _sha256_file(path)
            hashed += 1
            if digest != str(row.get("sha256") or ""):
                issues.append(AuditIssue("error", "bin_hash_mismatch", "Shard binary sha256 does not match sidecar.", shard_index=shard_index))
    return {"total_bytes": total_bytes, "hashed_files": hashed}


def _check_tensor_layouts(*, cache_root: Path, shards: list[dict[str, Any]], issues: list[AuditIssue]) -> dict[str, Any]:
    required = {"headers_uint8", "events_uint8", "ticker", "origin_ordinal", "origin_timestamp_us"}
    tensor_names: set[str] = set()
    checked_chunks = 0
    for row in shards:
        shard_index = _int_value(row.get("shard_index"), -1)
        path = _shard_bin_path(cache_root, row)
        file_size = path.stat().st_size if path.exists() else 0
        tensors = dict(row.get("tensors") or {})
        tensor_names.update(str(name) for name in tensors)
        missing = sorted(required - set(tensors))
        if missing:
            issues.append(AuditIssue("error", "required_tensor_missing", f"Missing required tensors: {missing}", shard_index=shard_index))
        num_samples = int(row.get("num_samples") or 0)
        if num_samples <= 0:
            issues.append(AuditIssue("error", "empty_shard", "Shard has no samples.", shard_index=shard_index))
        if int(row.get("tensor_count") or 0) != len(tensors):
            issues.append(AuditIssue("error", "tensor_count_mismatch", "tensor_count does not match tensor metadata.", shard_index=shard_index))
        for name, meta in tensors.items():
            try:
                dtype = np.dtype(str(meta.get("dtype")))
            except TypeError:
                issues.append(AuditIssue("error", "tensor_dtype_invalid", f"Invalid tensor dtype {meta.get('dtype')!r}.", shard_index=shard_index, tensor=str(name)))
                continue
            shape = tuple(int(value) for value in (meta.get("shape") or []))
            if not shape or shape[0] != num_samples:
                issues.append(AuditIssue("error", "tensor_shape_samples", f"Tensor shape does not match shard sample count: {shape}", shard_index=shard_index, tensor=str(name)))
            expected_sample_bytes = int(np.prod(shape[1:], dtype=np.int64) if len(shape) > 1 else 1) * int(dtype.itemsize)
            if int(meta.get("sample_bytes") or 0) != expected_sample_bytes:
                issues.append(AuditIssue("error", "tensor_sample_bytes", "Tensor sample_bytes does not match dtype and shape.", shard_index=shard_index, tensor=str(name)))
        chunk_tensor_bytes: dict[str, int] = {str(name): 0 for name in tensors}
        for chunk in row.get("chunks") or []:
            checked_chunks += 1
            sample_start = int(chunk.get("sample_start") or 0)
            sample_count = int(chunk.get("sample_count") or 0)
            if sample_start < 0 or sample_count <= 0 or sample_start + sample_count > num_samples:
                issues.append(AuditIssue("error", "chunk_sample_bounds", "Chunk sample bounds are invalid.", shard_index=shard_index, details={"sample_start": sample_start, "sample_count": sample_count}))
            for name, chunk_meta in (chunk.get("tensors") or {}).items():
                tensor_meta = tensors.get(name)
                if tensor_meta is None:
                    issues.append(AuditIssue("error", "chunk_unknown_tensor", "Chunk references unknown tensor.", shard_index=shard_index, tensor=str(name)))
                    continue
                byte_offset = int(chunk_meta.get("byte_offset") or 0)
                byte_size = int(chunk_meta.get("byte_size") or 0)
                if byte_offset < 0 or byte_size <= 0 or byte_offset + byte_size > file_size:
                    issues.append(AuditIssue("error", "chunk_byte_bounds", "Chunk byte bounds are outside shard file.", shard_index=shard_index, tensor=str(name)))
                chunk_shape = tuple(int(value) for value in (chunk_meta.get("shape") or []))
                dtype = np.dtype(str(tensor_meta.get("dtype")))
                expected = int(np.prod(chunk_shape, dtype=np.int64)) * int(dtype.itemsize) if chunk_shape else 0
                if expected != byte_size:
                    issues.append(AuditIssue("error", "chunk_byte_size", "Chunk byte_size does not match dtype and shape.", shard_index=shard_index, tensor=str(name)))
                chunk_tensor_bytes[str(name)] = chunk_tensor_bytes.get(str(name), 0) + byte_size
        for name, byte_size in chunk_tensor_bytes.items():
            expected = int((tensors.get(name) or {}).get("byte_size") or 0)
            if expected != byte_size:
                issues.append(AuditIssue("error", "tensor_chunk_bytes", f"Tensor chunk bytes {byte_size:,} != tensor byte_size {expected:,}.", shard_index=shard_index, tensor=str(name)))
    return {"tensor_count_union": len(tensor_names), "checked_chunks": checked_chunks, "tensor_names": sorted(tensor_names)}


def _check_origin_periods(
    *,
    cache_root: Path,
    shards: list[dict[str, Any]],
    start_us: int,
    end_us: int,
    issues: list[AuditIssue],
) -> dict[str, Any]:
    global_min: int | None = None
    global_max: int | None = None
    for row in shards:
        shard_index = _int_value(row.get("shard_index"), -1)
        if "origin_timestamp_us" not in (row.get("tensors") or {}):
            continue
        values = _read_tensor_all(cache_root, row, "origin_timestamp_us").astype(np.int64, copy=False).reshape(-1)
        if values.size == 0:
            continue
        actual_min = int(values.min())
        actual_max = int(values.max())
        global_min = actual_min if global_min is None else min(global_min, actual_min)
        global_max = actual_max if global_max is None else max(global_max, actual_max)
        meta_min = int(row.get("first_origin_timestamp_us") or 0)
        meta_max = int(row.get("last_origin_timestamp_us") or 0)
        if meta_min != actual_min or meta_max != actual_max:
            issues.append(AuditIssue("warning", "origin_sidecar_range", "Sidecar origin timestamp range does not match tensor contents. Tensor contents are used for period validation.", shard_index=shard_index, details={"sidecar_min": meta_min, "sidecar_max": meta_max, "actual_min": actual_min, "actual_max": actual_max}))
        if start_us and actual_min < start_us:
            issues.append(AuditIssue("error", "origin_before_start", "Shard contains samples before requested start.", shard_index=shard_index, details={"actual_min": actual_min, "start_us": start_us}))
        if end_us and actual_max >= end_us:
            issues.append(AuditIssue("error", "origin_at_or_after_end", "Shard contains samples at/after requested end.", shard_index=shard_index, details={"actual_max": actual_max, "end_us": end_us}))
    return {
        "first_origin_timestamp_us": global_min or 0,
        "last_origin_timestamp_us": global_max or 0,
        "first_origin_utc": timestamp_us_to_utc(global_min or 0),
        "last_origin_utc": timestamp_us_to_utc(global_max or 0),
    }


def _check_sample_identities(*, cache_root: Path, shards: list[dict[str, Any]], issues: list[AuditIssue]) -> dict[str, Any]:
    seen: dict[tuple[str, int], tuple[int, int, int]] = {}
    duplicates: list[dict[str, Any]] = []
    sample_count = 0
    for row in shards:
        tensors = row.get("tensors") or {}
        shard_index = _int_value(row.get("shard_index"), -1)
        if not {"ticker", "origin_ordinal", "origin_timestamp_us"}.issubset(tensors):
            continue
        tickers = _read_tensor_all(cache_root, row, "ticker").reshape(-1)
        ordinals = _read_tensor_all(cache_root, row, "origin_ordinal").astype(np.int64, copy=False).reshape(-1)
        timestamps = _read_tensor_all(cache_root, row, "origin_timestamp_us").astype(np.int64, copy=False).reshape(-1)
        count = min(int(tickers.shape[0]), int(ordinals.shape[0]), int(timestamps.shape[0]))
        sample_count += count
        for sample_index in range(count):
            ticker = _decode_ticker_value(tickers[sample_index])
            origin_ordinal = int(ordinals[sample_index])
            origin_timestamp_us = int(timestamps[sample_index])
            key = (ticker, origin_ordinal)
            previous = seen.get(key)
            if previous is not None:
                prev_shard, prev_sample, prev_timestamp_us = previous
                duplicate = {
                    "ticker": ticker,
                    "origin_ordinal": origin_ordinal,
                    "origin_timestamp_us": origin_timestamp_us,
                    "shard_index": shard_index,
                    "sample_index": sample_index,
                    "previous_shard_index": prev_shard,
                    "previous_sample_index": prev_sample,
                    "previous_origin_timestamp_us": prev_timestamp_us,
                }
                duplicates.append(duplicate)
                if len(duplicates) <= 10:
                    issues.append(
                        AuditIssue(
                            "error",
                            "duplicate_sample_identity",
                            "Duplicate ticker/origin_ordinal sample identity found across materialized cache.",
                            shard_index=shard_index,
                            sample_index=sample_index,
                            details=duplicate,
                        )
                    )
                continue
            seen[key] = (shard_index, sample_index, origin_timestamp_us)
    if len(duplicates) > 10:
        issues.append(
            AuditIssue(
                "error",
                "duplicate_sample_identity_overflow",
                "Additional duplicate sample identities found beyond the first 10 reported examples.",
                details={"additional_duplicates": len(duplicates) - 10},
            )
        )
    return {
        "samples_checked": int(sample_count),
        "unique_ticker_origin_ordinals": int(len(seen)),
        "duplicate_count": int(len(duplicates)),
        "duplicate_examples": duplicates[:10],
    }


def _check_source_period_coverage(
    *,
    client: ClickHouseHttpClient,
    database: str,
    events_table: str,
    start_us: int,
    end_us: int,
    materialized_samples: int,
    sample_stride_events: int,
    issues: list[AuditIssue],
    max_threads: int,
    max_memory_usage: str,
    min_coverage_ratio: float,
    max_overage_ratio: float,
    low_coverage_severity: str,
) -> dict[str, Any]:
    if int(start_us) <= 0 or int(end_us) <= int(start_us):
        return {"skipped": True, "reason": "missing_period_bounds"}
    stride = max(1, int(sample_stride_events))
    settings = f" SETTINGS max_threads = {int(max_threads)}"
    if str(max_memory_usage).strip() != "0":
        settings += f", max_memory_usage = {parse_size_bytes(str(max_memory_usage))}"
    start_date = _date_from_us(int(start_us)).isoformat()
    end_date = _exclusive_date_from_us(int(end_us)).isoformat()
    query = f"""
SELECT
    sum(c) AS event_count,
    count() AS ticker_count,
    sum(intDiv(c + {stride - 1}, {stride})) AS stride_expected_samples
FROM
(
    SELECT
        ticker,
        count() AS c
    FROM {quote_ident(database)}.{quote_ident(events_table)}
    PREWHERE event_date >= toDate({sql_string(start_date)})
      AND event_date < toDate({sql_string(end_date)})
    WHERE sip_timestamp_us >= {int(start_us)}
      AND sip_timestamp_us < {int(end_us)}
    GROUP BY ticker
)
{settings}
FORMAT JSONEachRow
"""
    rows = [json.loads(line) for line in client.execute(query).splitlines() if line.strip()]
    row = rows[0] if rows else {}
    event_count = int(row.get("event_count") or 0)
    ticker_count = int(row.get("ticker_count") or 0)
    expected = int(row.get("stride_expected_samples") or 0)
    materialized = int(materialized_samples)
    missing = max(0, expected - materialized)
    extra = max(0, materialized - expected)
    coverage_ratio = float(materialized / expected) if expected > 0 else (1.0 if materialized == 0 else float("inf"))
    missing_ratio = float(missing / expected) if expected > 0 else 0.0
    overage_ratio = float(materialized / expected) if expected > 0 else (1.0 if materialized == 0 else float("inf"))
    summary = {
        "skipped": False,
        "database": str(database),
        "events_table": str(events_table),
        "start_timestamp_us": int(start_us),
        "end_timestamp_us": int(end_us),
        "start_utc": timestamp_us_to_utc(int(start_us)),
        "end_utc": timestamp_us_to_utc(int(end_us)),
        "event_date_start": start_date,
        "event_date_end_exclusive": end_date,
        "source_event_count": event_count,
        "source_ticker_count": ticker_count,
        "sample_stride_events": stride,
        "stride_expected_samples": expected,
        "materialized_samples": materialized,
        "missing_samples_vs_stride_expected": missing,
        "extra_samples_vs_stride_expected": extra,
        "coverage_ratio": coverage_ratio,
        "missing_ratio": missing_ratio,
        "overage_ratio": overage_ratio,
        "low_coverage_severity": str(low_coverage_severity),
    }
    if expected > 0 and materialized > int(expected * float(max_overage_ratio)):
        issues.append(
            AuditIssue(
                "error",
                "source_sample_coverage_overrun",
                "Materialized sample count exceeds stride-adjusted source event count for the requested period.",
                details=summary,
            )
        )
    elif expected > 0 and coverage_ratio < float(min_coverage_ratio):
        issues.append(
            AuditIssue(
                str(low_coverage_severity),
                "source_sample_coverage_low",
                "Materialized sample count is far below stride-adjusted source event count for the requested period.",
                details=summary,
            )
        )
    return summary


def _select_sample_points(shards: list[dict[str, Any]], *, sample_shards: int, samples_per_shard: int, seed: int) -> list[tuple[int, int]]:
    if not shards or sample_shards <= 0 or samples_per_shard <= 0:
        return []
    rng = random.Random(int(seed))
    indices = [_int_value(row.get("shard_index"), 0) for row in shards]
    selected_shards: list[int] = []
    for value in (indices[0], indices[len(indices) // 2], indices[-1]):
        if value not in selected_shards:
            selected_shards.append(value)
    remaining = [value for value in indices if value not in selected_shards]
    rng.shuffle(remaining)
    selected_shards = (selected_shards + remaining)[: max(1, int(sample_shards))]
    by_index = {_int_value(row.get("shard_index"), 0): row for row in shards}
    points: list[tuple[int, int]] = []
    for shard_index in selected_shards:
        count = int(by_index[shard_index].get("num_samples") or 0)
        if count <= 0:
            continue
        picks = {0, count // 2, count - 1}
        while len(picks) < max(1, int(samples_per_shard)) and len(picks) < count:
            picks.add(rng.randrange(0, count))
        for sample_index in sorted(picks)[: max(1, int(samples_per_shard))]:
            points.append((int(shard_index), int(sample_index)))
    return points


def _check_sample_content(
    *,
    cache_root: Path,
    shards: list[dict[str, Any]],
    selected: list[tuple[int, int]],
    zero_sample_rows: int,
    issues: list[AuditIssue],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_index = {_int_value(row.get("shard_index"), 0): row for row in shards}
    sample_rows: list[dict[str, Any]] = []
    zero_ratios: dict[str, list[float]] = {}
    mask_true_ratios: dict[str, list[float]] = {}
    required_nonzero = {"headers_uint8", "events_uint8", "origin_ordinal", "origin_timestamp_us"}
    for shard_index, sample_index in selected:
        row = by_index.get(shard_index)
        if row is None:
            continue
        tensors = row.get("tensors") or {}
        identity: dict[str, Any] = {"shard_index": shard_index, "sample_index": sample_index}
        for name in ("ticker", "origin_ordinal", "origin_timestamp_us"):
            if name in tensors:
                value = _read_tensor_sample(cache_root, row, name, sample_index)
                identity[name] = _scalar_or_ticker(value, name)
        if "origin_timestamp_us" in identity:
            identity["origin_utc"] = timestamp_us_to_utc(int(identity["origin_timestamp_us"]))
        sample_rows.append(identity)
        for name in tensors:
            value = _read_tensor_sample(cache_root, row, str(name), sample_index)
            arr = np.asarray(value)
            if arr.size == 0:
                continue
            if str(name) in required_nonzero and not bool(np.any(arr != 0)):
                issues.append(AuditIssue("error", "required_tensor_all_zero", "Required tensor sample is all zero.", shard_index=shard_index, sample_index=sample_index, tensor=str(name)))
            if arr.dtype == np.bool_ or str(name).endswith("_mask") or str(name).startswith("input_availability/"):
                mask_true_ratios.setdefault(str(name), []).append(float(np.count_nonzero(arr)) / float(arr.size))
            elif arr.dtype.kind in {"u", "i", "f", "b"}:
                zero_ratios.setdefault(str(name), []).append(float(np.count_nonzero(arr == 0)) / float(arr.size))
    for row in shards:
        if zero_sample_rows <= 0:
            continue
        shard_index = _int_value(row.get("shard_index"), -1)
        sample_count = min(int(row.get("num_samples") or 0), int(zero_sample_rows))
        if sample_count <= 0:
            continue
        for name in ("headers_uint8", "events_uint8"):
            if name not in (row.get("tensors") or {}):
                continue
            sample_indices = np.linspace(0, int(row.get("num_samples") or 1) - 1, num=sample_count, dtype=np.int64)
            all_zero_count = 0
            for sample_index in sample_indices.tolist():
                arr = _read_tensor_sample(cache_root, row, name, int(sample_index))
                all_zero_count += 1 if not bool(np.any(arr != 0)) else 0
            if all_zero_count:
                issues.append(AuditIssue("error", "required_tensor_zero_rows", f"{name} has all-zero sampled rows.", shard_index=shard_index, tensor=name, details={"all_zero_count": all_zero_count, "sampled_rows": sample_count}))
    return (
        {
            "checked_samples": len(selected),
            "sample_rows_for_zero_scan": int(zero_sample_rows),
            "zero_ratio_mean": {name: float(np.mean(values)) for name, values in sorted(zero_ratios.items()) if values},
            "mask_true_ratio_mean": {name: float(np.mean(values)) for name, values in sorted(mask_true_ratios.items()) if values},
        },
        sample_rows,
    )


def _check_source_event_windows(
    *,
    client: ClickHouseHttpClient,
    database: str,
    events_table: str,
    events_per_chunk: int,
    context_lags: tuple[int, ...],
    cache_root: Path,
    shards: list[dict[str, Any]],
    selected: list[tuple[int, int]],
    sample_rows: list[dict[str, Any]],
    issues: list[AuditIssue],
    max_threads: int,
    max_memory_usage: str,
) -> dict[str, Any]:
    by_index = {_int_value(row.get("shard_index"), 0): row for row in shards}
    lags = tuple(int(value) for value in context_lags)
    context = int(events_per_chunk)
    checked_windows = 0
    checked_samples = 0
    settings = f" SETTINGS max_threads = {int(max_threads)}"
    if str(max_memory_usage).strip() != "0":
        settings += f", max_memory_usage = {parse_size_bytes(str(max_memory_usage))}"
    identities = {(int(row["shard_index"]), int(row["sample_index"])): row for row in sample_rows}
    for shard_index, sample_index in selected:
        shard = by_index.get(shard_index)
        identity = identities.get((shard_index, sample_index))
        if shard is None or identity is None:
            continue
        ticker = str(identity.get("ticker") or "").upper()
        origin_ordinal = int(identity.get("origin_ordinal") or -1)
        origin_timestamp_us = int(identity.get("origin_timestamp_us") or 0)
        if not ticker or origin_ordinal < 0:
            issues.append(AuditIssue("error", "source_identity_missing", "Sample identity is missing ticker or origin ordinal.", shard_index=shard_index, sample_index=sample_index))
            continue
        headers = _read_tensor_sample(cache_root, shard, "headers_uint8", sample_index)
        events = _read_tensor_sample(cache_root, shard, "events_uint8", sample_index)
        if headers.shape[0] != len(lags) or events.shape[0] != len(lags):
            issues.append(AuditIssue("error", "context_lag_shape_mismatch", "Saved context chunks do not match configured context lags.", shard_index=shard_index, sample_index=sample_index, details={"headers_shape": list(headers.shape), "events_shape": list(events.shape), "lags": len(lags)}))
            continue
        source_rows = _fetch_source_rows_before_origin(
            client=client,
            database=database,
            events_table=events_table,
            ticker=ticker,
            origin_ordinal=origin_ordinal,
            row_limit=max(lags, default=0) + context + 2,
            settings=settings,
        )
        if source_rows.size == 0:
            issues.append(AuditIssue("error", "source_rows_missing", "No source rows returned for sampled origin.", shard_index=shard_index, sample_index=sample_index, details={"ticker": ticker, "origin_ordinal": origin_ordinal}))
            continue
        matches = np.flatnonzero(source_rows["ordinal"].astype(np.int64, copy=False) == origin_ordinal)
        if matches.size == 0:
            issues.append(AuditIssue("error", "origin_ordinal_missing", "Source rows did not include the sampled origin ordinal.", shard_index=shard_index, sample_index=sample_index, details={"ticker": ticker, "origin_ordinal": origin_ordinal}))
            continue
        origin_offset = int(matches[-1])
        source_origin_ts = int(source_rows["sip_timestamp_us"][origin_offset])
        if source_origin_ts != origin_timestamp_us:
            issues.append(AuditIssue("error", "origin_timestamp_mismatch", "Saved origin timestamp does not match source event.", shard_index=shard_index, sample_index=sample_index, details={"ticker": ticker, "origin_ordinal": origin_ordinal, "saved": origin_timestamp_us, "source": source_origin_ts}))
            continue
        windows = np.zeros((len(lags), context), dtype=EVENT_ROW_DTYPE)
        previous = np.full((len(lags),), -1, dtype=np.int64)
        valid_layout = True
        for context_index, lag in enumerate(lags):
            chunk_origin = origin_offset - int(lag)
            start = chunk_origin - context + 1
            end = chunk_origin + 1
            if start < 0 or end > source_rows.shape[0]:
                valid_layout = False
                issues.append(AuditIssue("error", "source_window_bounds", "Source query did not return enough rows for a context window.", shard_index=shard_index, sample_index=sample_index, details={"ticker": ticker, "lag": lag, "context_index": context_index}))
                continue
            window = source_rows[start:end]
            ordinals = window["ordinal"].astype(np.int64, copy=False)
            if ordinals.shape[0] != context or not bool(np.all(ordinals[1:] == ordinals[:-1] + 1)):
                valid_layout = False
                issues.append(AuditIssue("error", "source_window_ordinal_gap", "Source context window has non-contiguous ordinals.", shard_index=shard_index, sample_index=sample_index, details={"ticker": ticker, "lag": lag, "context_index": context_index, "first_ordinal": int(ordinals[0]) if ordinals.size else -1, "last_ordinal": int(ordinals[-1]) if ordinals.size else -1}))
                continue
            windows[context_index] = window
            if start > 0:
                previous[context_index] = int(source_rows["sip_timestamp_us"][start - 1])
        if not valid_layout:
            continue
        expected_headers, expected_events, valid, reasons = encode_unified_event_windows(windows, previous_sip_us=previous)
        if not bool(np.all(valid)):
            bad = np.flatnonzero(~valid)
            issues.append(AuditIssue("error", "source_window_invalid_encode", "Fresh source event windows are not encodable.", shard_index=shard_index, sample_index=sample_index, details={"bad_context_indices": bad[:10].astype(int).tolist(), "reasons": [str(reasons[index]) for index in bad[:10].tolist()]}))
            continue
        header_equal = np.all(expected_headers == headers)
        event_equal = np.all(expected_events == events)
        if not bool(header_equal):
            diff = np.argwhere(expected_headers != headers)
            changed_columns = {int(item[1]) for item in diff.tolist()} if diff.size else set()
            if bool(event_equal) and changed_columns and changed_columns.issubset({9, 10}):
                issues.append(AuditIssue("warning", "source_header_start_gap_boundary", "Only the header start-gap bytes differ from full-history source encoding; event bytes are identical.", shard_index=shard_index, sample_index=sample_index, details={"diff_columns": sorted(changed_columns)}))
            else:
                issues.append(AuditIssue("error", "source_header_mismatch", "Saved headers are not byte-identical to fresh source encoding.", shard_index=shard_index, sample_index=sample_index, details={"first_diff": diff[0].astype(int).tolist() if diff.size else []}))
        if not bool(event_equal):
            diff = np.argwhere(expected_events != events)
            issues.append(AuditIssue("error", "source_event_mismatch", "Saved events are not byte-identical to fresh source encoding.", shard_index=shard_index, sample_index=sample_index, details={"first_diff": diff[0].astype(int).tolist() if diff.size else []}))
        checked_windows += int(len(lags))
        checked_samples += 1
    return {"checked_samples": checked_samples, "checked_windows": checked_windows, "context_lags": len(lags), "events_per_chunk": context, "skipped": False}


def _fetch_source_rows_before_origin(
    *,
    client: ClickHouseHttpClient,
    database: str,
    events_table: str,
    ticker: str,
    origin_ordinal: int,
    row_limit: int,
    settings: str,
) -> np.ndarray:
    columns = ",\n    ".join(EVENT_COLUMNS)
    query = f"""
SELECT
    {columns}
FROM {quote_ident(database)}.{quote_ident(events_table)}
PREWHERE ticker = {sql_string(ticker)}
WHERE ordinal <= {int(origin_ordinal)}
ORDER BY ordinal DESC
LIMIT {max(1, int(row_limit))}
{settings}
FORMAT JSONEachRow
"""
    rows = []
    for line in client.execute(query).splitlines():
        if line.strip():
            rows.append(json.loads(line))
    rows.reverse()
    out = np.zeros((len(rows),), dtype=EVENT_ROW_DTYPE)
    for index, item in enumerate(rows):
        for name in EVENT_ROW_DTYPE.names or ():
            if name in item:
                out[name][index] = item[name]
    return out


def _read_tensor_all(cache_root: Path, shard: Mapping[str, Any], tensor_name: str) -> np.ndarray:
    tensors = shard.get("tensors") or {}
    meta = tensors[tensor_name]
    dtype = np.dtype(str(meta["dtype"]))
    shape = tuple(int(value) for value in meta["shape"])
    out = np.empty(shape, dtype=dtype)
    path = _shard_bin_path(cache_root, shard)
    with path.open("rb") as handle:
        for chunk in shard.get("chunks") or []:
            chunk_meta = (chunk.get("tensors") or {}).get(tensor_name)
            if chunk_meta is None:
                continue
            sample_start = int(chunk_meta.get("sample_start") or chunk.get("sample_start") or 0)
            sample_count = int(chunk_meta.get("sample_count") or chunk.get("sample_count") or 0)
            byte_size = int(chunk_meta["byte_size"])
            handle.seek(int(chunk_meta["byte_offset"]))
            payload = handle.read(byte_size)
            array = np.frombuffer(payload, dtype=dtype).reshape(tuple(int(value) for value in chunk_meta["shape"]))
            out[sample_start : sample_start + sample_count] = array
    return out


def _read_tensor_sample(cache_root: Path, shard: Mapping[str, Any], tensor_name: str, sample_index: int) -> np.ndarray:
    tensors = shard.get("tensors") or {}
    meta = tensors[tensor_name]
    dtype = np.dtype(str(meta["dtype"]))
    sample_bytes = int(meta["sample_bytes"])
    path = _shard_bin_path(cache_root, shard)
    for chunk in shard.get("chunks") or []:
        sample_start = int(chunk.get("sample_start") or 0)
        sample_count = int(chunk.get("sample_count") or 0)
        if sample_start <= sample_index < sample_start + sample_count:
            chunk_meta = (chunk.get("tensors") or {})[tensor_name]
            local = int(sample_index) - sample_start
            tail_shape = tuple(int(value) for value in chunk_meta["shape"][1:])
            with path.open("rb") as handle:
                handle.seek(int(chunk_meta["byte_offset"]) + local * sample_bytes)
                payload = handle.read(sample_bytes)
            return np.frombuffer(payload, dtype=dtype).copy().reshape(tail_shape)
    raise IndexError(f"Sample {sample_index} not found in shard {shard.get('shard_index')} tensor {tensor_name}")


def _scalar_or_ticker(value: np.ndarray, name: str) -> Any:
    arr = np.asarray(value)
    if name == "ticker":
        if arr.shape == ():
            item = arr.item()
        else:
            item = arr.reshape(-1)[0]
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="ignore").rstrip("\x00")
        return str(item)
    if arr.shape == ():
        return arr.item()
    if arr.size == 1:
        return arr.reshape(-1)[0].item()
    return arr.tolist()


def _decode_ticker_value(value: Any) -> str:
    if isinstance(value, np.ndarray):
        if value.shape == ():
            value = value.item()
        else:
            value = value.reshape(-1)[0].item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").rstrip("\x00").upper()
    return str(value or "").rstrip("\x00").upper()


def _date_from_us(timestamp_us: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).date()


def _exclusive_date_from_us(timestamp_us: int) -> dt.date:
    return _date_from_us(max(0, int(timestamp_us) - 1)) + dt.timedelta(days=1)


def _source_coverage_low_severity(manifest: Mapping[str, Any]) -> str:
    raw_config = dict(manifest.get("config") or {})
    if bool(raw_config.get("one_shard")):
        return "warning"
    summary = dict(manifest.get("summary") or {})
    target_gib = _float_value(raw_config.get("target_cache_gib"), 0.0)
    target_bytes = int(target_gib * 1024**3) if target_gib > 0 else 0
    bytes_written = _int_value(summary.get("bytes_written"), 0)
    if target_bytes > 0 and bytes_written >= int(target_bytes * 0.99):
        return "warning"
    return "error"


def _config_from_manifest(manifest: Mapping[str, Any]) -> RollingMarketDataConfig:
    raw = dict(manifest.get("config") or {})
    allowed: dict[str, Any] = {}
    for key in (
        "database",
        "sec_context_database",
        "events_table",
        "macro_bars_table",
        "news_token_table",
        "sec_filing_text_token_table",
        "sec_xbrl_context_table",
        "category_reference_table",
        "sample_stride_events",
        "builder_batch_size",
        "max_ready_samples",
        "max_threads",
        "max_memory_usage",
        "macro_lookback_days",
        "label_lookahead_days",
        "q_live_contexts",
        "news_lookback_days",
        "sec_lookback_days",
        "xbrl_lookback_days",
        "ticker_news_items",
        "market_news_items",
        "sec_filing_items",
        "xbrl_items",
    ):
        if key in raw:
            allowed[key] = raw[key]
    remap = {
        "builder_batch_size": "batch_size",
        "ticker_news_items": "news_max_items",
        "market_news_items": "market_news_max_items",
        "sec_filing_items": "sec_max_items",
        "xbrl_items": "xbrl_max_items",
    }
    kwargs: dict[str, Any] = {}
    for key, value in allowed.items():
        kwargs[remap.get(key, key)] = value
    if "q_live_contexts" in kwargs and isinstance(kwargs["q_live_contexts"], list):
        kwargs["q_live_contexts"] = tuple(str(item) for item in kwargs["q_live_contexts"])
    return RollingMarketDataConfig(**kwargs)


def _context_geometry_from_manifest(manifest: Mapping[str, Any]) -> tuple[int, tuple[int, ...]]:
    raw = dict(manifest.get("config") or {})
    if raw.get("context_lags"):
        lags = tuple(int(value) for value in raw.get("context_lags") or ())
        events_per_chunk = int(raw.get("events_per_chunk") or DEFAULT_CONTEXT_EVENTS)
        return events_per_chunk, lags
    config = _config_from_manifest(manifest)
    return int(config.events_per_chunk), tuple(int(value) for value in config.context_lags)


def _shard_bin_path(cache_root: Path, shard: Mapping[str, Any]) -> Path:
    return Path(cache_root) / str(shard.get("path") or "").replace("\\", "/")


def _resolve_cache_root(args: argparse.Namespace) -> Path:
    if args.cache_path is not None:
        return Path(args.cache_path)
    root = Path(args.cache_root)
    if str(args.cache_id).strip():
        return root / str(args.cache_id).strip()
    return root


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _int_value(value: Any, default: int = 0) -> int:
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _float_value(value: Any, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _issue_counts(issues: Iterable[AuditIssue]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[str(issue.severity)] = counts.get(str(issue.severity), 0) + 1
    return counts


def _compact_console_summary(result: AuditResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "ok": result.ok,
        "elapsed_seconds": result.elapsed_seconds,
        "cache_root": result.cache_root,
        "split": result.split,
        "report_path": result.report_path,
        "issue_counts": result.summary.get("issue_counts", {}),
        "shards": result.summary.get("shards", {}),
        "origin_periods": result.summary.get("origin_periods", {}),
        "sample_identities": result.summary.get("sample_identities", {}),
        "source_period_coverage": result.summary.get("source_period_coverage", {}),
        "source_event_windows": result.summary.get("source_event_windows", {}),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
