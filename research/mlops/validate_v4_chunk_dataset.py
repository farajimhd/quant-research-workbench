from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import polars as pl


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.build_v4_chunk_dataset import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    V4ChunkBuildConfig,
    chunk_id_for,
    discover_event_shard_files,
    group_event_files_by_session,
    read_bucket_session_events,
)
from research.mlops.compact_events import (  # noqa: E402
    EVENT_BYTES,
    HEADER_BYTES,
    ReferenceMaps,
    encode_events_chunk_from_frame,
    resolve_precomputed_chunk_root,
)


@dataclass(slots=True)
class ValidationConfig:
    prepared_root: Path = DEFAULT_OUTPUT_ROOT
    event_shard_root: Path | None = None
    chunk_root: Path | None = None
    output_root: Path | None = None
    reference_dir: Path = Path(__file__).resolve().parents[1] / "market_references" / "massive"
    start_date: str = "2025-01-01"
    end_date: str = "2025-12-31"
    events_per_chunk: int = 128
    stride_events: int = 1
    bucket_ids: tuple[int, ...] = ()
    mode: str = "all"
    structural_max_files: int = 0
    completeness_max_buckets: int = 0
    completeness_encode_expected: bool = False
    sample_chunks: int = 1000
    boundary_sample_chunks: int = 1000
    issue_limit: int = 10000
    seed: int = 17
    event_cache_size: int = 8


@dataclass(slots=True)
class ValidationIssue:
    mode: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class IssueWriter:
    def __init__(self, path: Path, *, limit: int) -> None:
        self.path = path
        self.limit = max(0, int(limit))
        self.count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()

    def write(self, issue: ValidationIssue) -> None:
        self.count += 1
        if self.limit and self.count > self.limit:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(issue), default=str) + "\n")


class EventFrameCache:
    def __init__(self, config: ValidationConfig) -> None:
        self.config = config
        self.cache: OrderedDict[tuple[int, str], pl.DataFrame] = OrderedDict()
        self.session_cache: dict[int, list[str]] = {}

    def get(self, bucket_id: int, session: str) -> pl.DataFrame:
        key = (bucket_id, session)
        if key in self.cache:
            frame = self.cache.pop(key)
            self.cache[key] = frame
            return frame
        paths = event_paths_for_bucket_session(self.config, bucket_id, session)
        frame = read_bucket_session_events(paths) if paths else pl.DataFrame()
        self.cache[key] = frame
        while len(self.cache) > max(1, self.config.event_cache_size):
            self.cache.popitem(last=False)
        return frame

    def sessions_between(self, bucket_id: int, start_session: str, end_session: str) -> list[str]:
        sessions = self.session_cache.get(bucket_id)
        if sessions is None:
            sessions = sorted(
                {
                    session_from_event_path(path)
                    for path in resolved_event_shard_root(self.config).glob(f"session=*/kind=*/bucket_id={bucket_id}/*.parquet")
                    if session_from_event_path(path)
                }
            )
            self.session_cache[bucket_id] = sessions
        return [session for session in sessions if start_session <= session <= end_session]


def parse_args() -> argparse.Namespace:
    defaults = ValidationConfig()
    parser = argparse.ArgumentParser(description="Validate v4 compact precomputed chunk dataset correctness and completeness.")
    parser.add_argument("--prepared-root", default=str(defaults.prepared_root))
    parser.add_argument("--event-shard-root", default="")
    parser.add_argument("--chunk-root", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--reference-dir", default=str(defaults.reference_dir))
    parser.add_argument("--start-date", default=defaults.start_date)
    parser.add_argument("--end-date", default=defaults.end_date)
    parser.add_argument("--events-per-chunk", type=int, default=defaults.events_per_chunk)
    parser.add_argument("--stride-events", type=int, default=defaults.stride_events)
    parser.add_argument("--bucket-ids", default="")
    parser.add_argument("--mode", choices=("all", "structural", "completeness", "sample-bytes"), default=defaults.mode)
    parser.add_argument("--structural-max-files", type=int, default=defaults.structural_max_files)
    parser.add_argument("--completeness-max-buckets", type=int, default=defaults.completeness_max_buckets)
    parser.add_argument("--completeness-encode-expected", action="store_true")
    parser.add_argument("--sample-chunks", type=int, default=defaults.sample_chunks)
    parser.add_argument("--boundary-sample-chunks", type=int, default=defaults.boundary_sample_chunks)
    parser.add_argument("--issue-limit", type=int, default=defaults.issue_limit)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--event-cache-size", type=int, default=defaults.event_cache_size)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ValidationConfig(
        prepared_root=Path(args.prepared_root),
        event_shard_root=Path(args.event_shard_root) if args.event_shard_root else None,
        chunk_root=Path(args.chunk_root) if args.chunk_root else None,
        output_root=Path(args.output_root) if args.output_root else None,
        reference_dir=Path(args.reference_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        events_per_chunk=max(2, int(args.events_per_chunk)),
        stride_events=max(1, int(args.stride_events)),
        bucket_ids=parse_bucket_ids(args.bucket_ids),
        mode=args.mode,
        structural_max_files=max(0, int(args.structural_max_files)),
        completeness_max_buckets=max(0, int(args.completeness_max_buckets)),
        completeness_encode_expected=bool(args.completeness_encode_expected),
        sample_chunks=max(0, int(args.sample_chunks)),
        boundary_sample_chunks=max(0, int(args.boundary_sample_chunks)),
        issue_limit=max(0, int(args.issue_limit)),
        seed=int(args.seed),
        event_cache_size=max(1, int(args.event_cache_size)),
    )
    output_root = config.output_root or (config.prepared_root / "validation")
    output_root.mkdir(parents=True, exist_ok=True)
    issues = IssueWriter(output_root / "validation_issues.jsonl", limit=config.issue_limit)
    report_path = output_root / "validation_report.json"
    started = time.time()

    print("=" * 96, flush=True)
    print("V4 compact chunk validation", flush=True)
    print(f"prepared_root={config.prepared_root}", flush=True)
    print(f"event_shard_root={resolved_event_shard_root(config)}", flush=True)
    print(f"chunk_root={resolved_chunk_root(config)}", flush=True)
    print(f"output_root={output_root}", flush=True)
    print(f"date_range={config.start_date} -> {config.end_date} mode={config.mode}", flush=True)
    print("=" * 96, flush=True)

    summary: dict[str, Any] = {"config": config_payload(config), "started_at": timestamp_text(), "modes": {}}
    try:
        if config.mode in {"all", "structural"}:
            summary["modes"]["structural"] = validate_structural(config, issues)
        if config.mode in {"all", "completeness"}:
            summary["modes"]["completeness"] = validate_completeness(config, issues)
        if config.mode in {"all", "sample-bytes"}:
            summary["modes"]["sample_bytes"] = validate_sample_bytes(config, issues)
    finally:
        summary["finished_at"] = timestamp_text()
        summary["elapsed_seconds"] = time.time() - started
        summary["issue_count"] = issues.count
        report_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("=" * 96, flush=True)
        print(f"Validation complete elapsed_minutes={(time.time() - started) / 60.0:.1f} issues={issues.count:,}", flush=True)
        print(f"Report: {report_path}", flush=True)
        print(f"Issues: {issues.path}", flush=True)
        print("=" * 96, flush=True)
    if issues.count:
        raise SystemExit(1)


def validate_structural(config: ValidationConfig, issues: IssueWriter) -> dict[str, Any]:
    chunk_files = discover_chunk_files(config)
    if config.structural_max_files:
        chunk_files = chunk_files[: config.structural_max_files]
    print(f"STRUCTURAL start files={len(chunk_files):,}", flush=True)
    totals = {"files": 0, "rows": 0, "bad_rows": 0, "duplicate_rows_in_shard": 0}
    required = required_chunk_columns()
    for index, path in enumerate(chunk_files, start=1):
        started = time.time()
        try:
            schema = pl.scan_parquet(str(path)).collect_schema()
            missing = sorted(required - set(schema.names()))
            if missing:
                issues.write(ValidationIssue("structural", "error", "missing required columns", {"path": str(path), "missing": missing}))
                totals["bad_rows"] += 1
                continue
            stats = (
                pl.scan_parquet(str(path))
                .select(
                    pl.len().alias("rows"),
                    pl.col("chunk_id").n_unique().alias("unique_chunk_ids"),
                    (pl.col("header_uint8").bin.size() != HEADER_BYTES).sum().alias("bad_header_size"),
                    (pl.col("events_uint8").bin.size() != config.events_per_chunk * EVENT_BYTES).sum().alias("bad_events_size"),
                    pl.col("ticker").is_null().sum().alias("null_ticker"),
                    pl.col("origin_timestamp_ns").is_null().sum().alias("null_origin_ts"),
                    pl.col("origin_session_date").is_null().sum().alias("null_origin_session"),
                    (pl.col("source_start_timestamp_ns") > pl.col("source_end_timestamp_ns")).sum().alias("bad_source_order"),
                    (pl.col("source_end_timestamp_ns") != pl.col("origin_timestamp_ns")).sum().alias("bad_source_end"),
                    (pl.col("crosses_session_boundary") != (pl.col("source_start_session_date") != pl.col("source_end_session_date"))).sum().alias("bad_boundary_flag"),
                )
                .collect()
                .row(0, named=True)
            )
        except Exception as exc:
            issues.write(ValidationIssue("structural", "error", "failed to read chunk shard", {"path": str(path), "error": repr(exc)}))
            continue
        rows = int(stats["rows"])
        duplicate_rows = rows - int(stats["unique_chunk_ids"])
        bad_rows = sum(
            int(stats[key])
            for key in (
                "bad_header_size",
                "bad_events_size",
                "null_ticker",
                "null_origin_ts",
                "null_origin_session",
                "bad_source_order",
                "bad_source_end",
                "bad_boundary_flag",
            )
        ) + max(0, duplicate_rows)
        totals["files"] += 1
        totals["rows"] += rows
        totals["bad_rows"] += bad_rows
        totals["duplicate_rows_in_shard"] += max(0, duplicate_rows)
        if bad_rows:
            issues.write(ValidationIssue("structural", "error", "bad structural rows found", {"path": str(path), **stats, "duplicate_rows": duplicate_rows}))
        if index == 1 or index % 1000 == 0 or index == len(chunk_files):
            print(f"STRUCTURAL [{index:,}/{len(chunk_files):,}] rows={rows:,} bad={bad_rows:,} elapsed={time.time() - started:.1f}s path={short_path(path)}", flush=True)
    return totals


def validate_completeness(config: ValidationConfig, issues: IssueWriter) -> dict[str, Any]:
    bucket_ids = selected_bucket_ids(config)
    if config.completeness_max_buckets:
        bucket_ids = bucket_ids[: config.completeness_max_buckets]
    print(
        f"COMPLETENESS start buckets={len(bucket_ids):,} encode_expected={config.completeness_encode_expected}",
        flush=True,
    )
    references = ReferenceMaps.load(config.reference_dir) if config.completeness_encode_expected else None
    totals = {"buckets": 0, "sessions": 0, "expected": 0, "saved": 0, "missing": 0, "extra": 0}
    for bucket_ordinal, bucket_id in enumerate(bucket_ids, start=1):
        bucket_started = time.time()
        event_inputs = discover_event_shard_files(build_config_for_bucket(config), bucket_id)
        session_files = group_event_files_by_session(event_inputs)
        carries: dict[str, pl.DataFrame] = {}
        totals["buckets"] += 1
        print(f"COMPLETENESS bucket={bucket_id:04d} [{bucket_ordinal:,}/{len(bucket_ids):,}] sessions={len(session_files):,}", flush=True)
        for session_ordinal, (session, paths) in enumerate(session_files, start=1):
            if session < config.start_date or session > config.end_date:
                continue
            session_started = time.time()
            frame = read_bucket_session_events(paths)
            if frame.height == 0:
                continue
            expected_rows: list[dict[str, Any]] = []
            tickers = frame["ticker"].unique().sort().to_list()
            for ticker in tickers:
                ticker_frame = frame.filter(pl.col("ticker") == ticker)
                carry = carries.get(ticker)
                source = pl.concat([carry, ticker_frame], how="vertical_relaxed") if carry is not None and carry.height > 0 else ticker_frame
                expected_rows.extend(expected_chunk_rows_for_source(config, source, ticker, references))
                carries[ticker] = source.tail(config.events_per_chunk - 1).clone()
                del source, ticker_frame
            saved = read_saved_bucket_session(config, bucket_id, session)
            expected = pl.DataFrame(expected_rows, schema={"chunk_id": pl.String, "ticker": pl.String, "origin_timestamp_ns": pl.Int64})
            result = compare_expected_saved(config, issues, bucket_id, session, expected, saved)
            totals["sessions"] += 1
            for key in ("expected", "saved", "missing", "extra"):
                totals[key] += int(result[key])
            print(
                f"COMPLETENESS bucket={bucket_id:04d} session={session} [{session_ordinal:,}/{len(session_files):,}] "
                f"expected={result['expected']:,} saved={result['saved']:,} missing={result['missing']:,} extra={result['extra']:,} "
                f"elapsed={time.time() - session_started:.1f}s",
                flush=True,
            )
            del frame, expected, saved
        carries.clear()
        print(f"COMPLETENESS bucket={bucket_id:04d} done elapsed={time.time() - bucket_started:.1f}s", flush=True)
    return totals


def validate_sample_bytes(config: ValidationConfig, issues: IssueWriter) -> dict[str, Any]:
    references = ReferenceMaps.load(config.reference_dir)
    samples = sample_chunk_rows(config, boundary_only=False, sample_count=config.sample_chunks)
    boundary_samples = sample_chunk_rows(config, boundary_only=True, sample_count=config.boundary_sample_chunks)
    combined = samples + boundary_samples
    print(f"SAMPLE_BYTES start regular={len(samples):,} boundary={len(boundary_samples):,}", flush=True)
    cache = EventFrameCache(config)
    totals = {"sampled": 0, "matched": 0, "mismatched": 0, "boundary_sampled": len(boundary_samples)}
    for index, row in enumerate(combined, start=1):
        result = validate_one_chunk_bytes(config, references, cache, row)
        totals["sampled"] += 1
        if result["ok"]:
            totals["matched"] += 1
        else:
            totals["mismatched"] += 1
            issues.write(ValidationIssue("sample-bytes", "error", result["message"], result["details"]))
        if index == 1 or index % 100 == 0 or index == len(combined):
            print(f"SAMPLE_BYTES [{index:,}/{len(combined):,}] matched={totals['matched']:,} mismatched={totals['mismatched']:,}", flush=True)
    return totals


def expected_chunk_rows_for_source(
    config: ValidationConfig,
    source: pl.DataFrame,
    ticker: str,
    references: ReferenceMaps | None,
) -> list[dict[str, Any]]:
    if source.height < config.events_per_chunk:
        return []
    rows: list[dict[str, Any]] = []
    for origin_idx in range(config.events_per_chunk - 1, source.height, config.stride_events):
        origin_ts = int(source["sip_timestamp"][origin_idx])
        if references is not None:
            encoded = encode_events_chunk_from_frame(
                source,
                origin_timestamp_ns=origin_ts,
                events_per_chunk=config.events_per_chunk,
                references=references,
            )
            if encoded is None:
                continue
        rows.append({"chunk_id": chunk_id_for(ticker, origin_ts, config.events_per_chunk), "ticker": ticker, "origin_timestamp_ns": origin_ts})
    return rows


def compare_expected_saved(
    config: ValidationConfig,
    issues: IssueWriter,
    bucket_id: int,
    session: str,
    expected: pl.DataFrame,
    saved: pl.DataFrame,
) -> dict[str, int]:
    if expected.height == 0 and saved.height == 0:
        return {"expected": 0, "saved": 0, "missing": 0, "extra": 0}
    if expected.height == 0:
        extra = saved.height
        write_frame_issues(issues, "completeness", "extra saved chunks", saved.head(20), {"bucket_id": bucket_id, "session": session})
        return {"expected": 0, "saved": saved.height, "missing": 0, "extra": extra}
    if saved.height == 0:
        missing = expected.height
        write_frame_issues(issues, "completeness", "missing saved chunks", expected.head(20), {"bucket_id": bucket_id, "session": session})
        return {"expected": expected.height, "saved": 0, "missing": missing, "extra": 0}
    expected = expected.unique("chunk_id")
    saved = saved.select("chunk_id", "ticker", "origin_timestamp_ns").unique("chunk_id")
    missing_frame = expected.join(saved.select("chunk_id"), on="chunk_id", how="anti")
    extra_frame = saved.join(expected.select("chunk_id"), on="chunk_id", how="anti")
    if missing_frame.height:
        write_frame_issues(issues, "completeness", "missing saved chunks", missing_frame.head(20), {"bucket_id": bucket_id, "session": session})
    if extra_frame.height:
        write_frame_issues(issues, "completeness", "extra saved chunks", extra_frame.head(20), {"bucket_id": bucket_id, "session": session})
    return {"expected": expected.height, "saved": saved.height, "missing": missing_frame.height, "extra": extra_frame.height}


def validate_one_chunk_bytes(
    config: ValidationConfig,
    references: ReferenceMaps,
    cache: EventFrameCache,
    row: dict[str, Any],
) -> dict[str, Any]:
    bucket_id = int(row["bucket_id"])
    ticker = str(row["ticker"])
    origin_ts = int(row["origin_timestamp_ns"])
    origin_session = str(row["origin_session_date"])
    source_start_session = str(row["source_start_session_date"])
    source = reconstruct_builder_source_for_sample(config, cache, bucket_id, ticker, origin_session)
    if source.height < config.events_per_chunk:
        return failed_sample(row, "source event context too short", {"source_rows": source.height})
    encoded = encode_events_chunk_from_frame(
        source,
        origin_timestamp_ns=origin_ts,
        events_per_chunk=config.events_per_chunk,
        references=references,
    )
    if encoded is None:
        return failed_sample(row, "recomputed encoder returned None", {"source_rows": source.height})
    timestamps = source["sip_timestamp"].to_numpy()
    origin_idx = int(np.searchsorted(timestamps, origin_ts, side="right") - 1)
    start_idx = origin_idx - config.events_per_chunk + 1
    if origin_idx < 0 or start_idx < 0:
        return failed_sample(row, "origin not found with enough context", {"origin_idx": origin_idx, "start_idx": start_idx})
    expected_start_ts = int(source["sip_timestamp"][start_idx])
    expected_end_ts = int(source["sip_timestamp"][origin_idx])
    expected_start_session = str(source["session_date"][start_idx])
    expected_end_session = str(source["session_date"][origin_idx])
    metadata_errors = {}
    if expected_start_ts != int(row["source_start_timestamp_ns"]):
        metadata_errors["source_start_timestamp_ns"] = {"saved": int(row["source_start_timestamp_ns"]), "expected": expected_start_ts}
    if expected_end_ts != int(row["source_end_timestamp_ns"]):
        metadata_errors["source_end_timestamp_ns"] = {"saved": int(row["source_end_timestamp_ns"]), "expected": expected_end_ts}
    if expected_start_session != source_start_session:
        metadata_errors["source_start_session_date"] = {"saved": source_start_session, "expected": expected_start_session}
    if expected_end_session != str(row["source_end_session_date"]):
        metadata_errors["source_end_session_date"] = {"saved": str(row["source_end_session_date"]), "expected": expected_end_session}
    expected_boundary = expected_start_session != expected_end_session
    if bool(row["crosses_session_boundary"]) != expected_boundary:
        metadata_errors["crosses_session_boundary"] = {"saved": bool(row["crosses_session_boundary"]), "expected": expected_boundary}
    saved_header = np.frombuffer(row["header_uint8"], dtype=np.uint8, count=HEADER_BYTES)
    saved_events = np.frombuffer(row["events_uint8"], dtype=np.uint8, count=config.events_per_chunk * EVENT_BYTES).reshape(config.events_per_chunk, EVENT_BYTES)
    header_ok = bool(np.array_equal(saved_header, encoded[0]))
    events_ok = bool(np.array_equal(saved_events, encoded[1]))
    if not header_ok or not events_ok or metadata_errors:
        return failed_sample(
            row,
            "sample byte or metadata mismatch",
            {
                "header_match": header_ok,
                "events_match": events_ok,
                "metadata_errors": metadata_errors,
                "header_diff_count": int(np.count_nonzero(saved_header != encoded[0])),
                "events_diff_count": int(np.count_nonzero(saved_events != encoded[1])),
            },
        )
    return {"ok": True, "message": "matched", "details": {}}


def reconstruct_builder_source_for_sample(
    config: ValidationConfig,
    cache: EventFrameCache,
    bucket_id: int,
    ticker: str,
    origin_session: str,
) -> pl.DataFrame:
    """Rebuild the per-ticker source frame the chunk builder had for origin_session.

    The chunk builder carries only the last events_per_chunk - 1 events from prior
    sessions. Reloading all sessions between source_start and origin can change
    the header start-gap calculation, so sampled byte validation needs the same
    carry semantics.
    """
    sessions = cache.sessions_between(bucket_id, "0000-00-00", origin_session)
    if not sessions:
        return pl.DataFrame()
    current_frame = cache.get(bucket_id, origin_session)
    current = current_frame.filter(pl.col("ticker") == ticker) if current_frame.height else pl.DataFrame()
    prior_frames: list[pl.DataFrame] = []
    prior_rows = 0
    for session in reversed(sessions[:-1]):
        frame = cache.get(bucket_id, session)
        if frame.height == 0:
            continue
        ticker_frame = frame.filter(pl.col("ticker") == ticker)
        if ticker_frame.height == 0:
            continue
        prior_frames.append(ticker_frame)
        prior_rows += ticker_frame.height
        if prior_rows >= config.events_per_chunk - 1:
            break
    frames: list[pl.DataFrame] = []
    if prior_frames:
        prior = pl.concat(list(reversed(prior_frames)), how="vertical_relaxed").tail(config.events_per_chunk - 1)
        frames.append(prior)
    if current.height:
        frames.append(current)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed").sort(["sip_timestamp", "sequence_number", "event_type"])


def sample_chunk_rows(config: ValidationConfig, *, boundary_only: bool, sample_count: int) -> list[dict[str, Any]]:
    if sample_count <= 0:
        return []
    rng = random.Random(config.seed + (97 if boundary_only else 0))
    files = discover_chunk_files(config)
    rng.shuffle(files)
    rows: list[dict[str, Any]] = []
    for path in files:
        if len(rows) >= sample_count:
            break
        bucket_id = bucket_id_from_path(path)
        filters = (pl.col("origin_session_date") >= config.start_date) & (pl.col("origin_session_date") <= config.end_date)
        if boundary_only:
            filters = filters & pl.col("crosses_session_boundary")
        try:
            frame = pl.scan_parquet(str(path)).filter(filters).collect()
        except Exception:
            continue
        if frame.height == 0:
            continue
        take = min(sample_count - len(rows), frame.height)
        indices = rng.sample(range(frame.height), k=take) if frame.height > take else list(range(frame.height))
        sampled = frame[indices]
        for item in sampled.iter_rows(named=True):
            item["_shard_path"] = str(path)
            item["bucket_id"] = bucket_id
            rows.append(item)
    return rows


def read_saved_bucket_session(config: ValidationConfig, bucket_id: int, session: str) -> pl.DataFrame:
    bucket_dir = resolved_chunk_root(config) / f"bucket={bucket_id:04d}"
    paths = sorted(bucket_dir.glob("part-*.parquet"))
    if not paths:
        return pl.DataFrame(schema={"chunk_id": pl.String, "ticker": pl.String, "origin_timestamp_ns": pl.Int64})
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("origin_session_date") == session)
        .select("chunk_id", "ticker", "origin_timestamp_ns")
        .collect()
    )


def discover_chunk_files(config: ValidationConfig) -> list[Path]:
    paths = sorted(resolved_chunk_root(config).glob("bucket=*/part-*.parquet"))
    if config.bucket_ids:
        allowed = set(config.bucket_ids)
        paths = [path for path in paths if bucket_id_from_path(path) in allowed]
    return paths


def selected_bucket_ids(config: ValidationConfig) -> list[int]:
    if config.bucket_ids:
        return list(config.bucket_ids)
    bucket_dirs = sorted({path.parent for path in discover_chunk_files(config)})
    ids = [bucket_id_from_path(path) for path in bucket_dirs]
    if ids:
        return ids
    event_root = resolved_event_shard_root(config)
    ids = sorted({int(path.parent.name.split("=", 1)[1]) for path in event_root.glob("session=*/kind=*/bucket_id=*/*.parquet")})
    return ids


def event_paths_for_bucket_session(config: ValidationConfig, bucket_id: int, session: str) -> list[Path]:
    root = resolved_event_shard_root(config)
    return sorted(root.glob(f"session={session}/kind=*/bucket_id={bucket_id}/*.parquet"))


def session_from_event_path(path: Path) -> str:
    for part in path.parts:
        if part.startswith("session="):
            return part.split("=", 1)[1]
    return ""


def build_config_for_bucket(config: ValidationConfig) -> V4ChunkBuildConfig:
    return V4ChunkBuildConfig(
        output_root=config.prepared_root,
        event_shard_root=resolved_event_shard_root(config),
        chunk_root=resolved_chunk_root(config),
        index_root=config.prepared_root / "indexes",
        reference_dir=config.reference_dir,
        start_date=config.start_date,
        end_date=config.end_date,
        events_per_chunk=config.events_per_chunk,
        stride_events=config.stride_events,
    )


def resolved_event_shard_root(config: ValidationConfig) -> Path:
    return config.event_shard_root or (config.prepared_root / "event_shards")


def resolved_chunk_root(config: ValidationConfig) -> Path:
    if config.chunk_root is not None:
        return resolve_precomputed_chunk_root(config.chunk_root)
    return resolve_precomputed_chunk_root(config.prepared_root)


def required_chunk_columns() -> set[str]:
    return {
        "chunk_id",
        "ticker",
        "origin_timestamp_ns",
        "origin_session_date",
        "source_start_timestamp_ns",
        "source_end_timestamp_ns",
        "source_start_session_date",
        "source_end_session_date",
        "crosses_session_boundary",
        "header_uint8",
        "events_uint8",
    }


def parse_bucket_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return tuple(out)


def bucket_id_from_path(path: Path) -> int:
    for part in path.parts:
        if part.startswith("bucket="):
            return int(part.split("=", 1)[1])
    if path.name.startswith("bucket="):
        return int(path.name.split("=", 1)[1])
    raise ValueError(f"Cannot parse bucket id from path: {path}")


def write_frame_issues(issues: IssueWriter, mode: str, message: str, frame: pl.DataFrame, common: dict[str, Any]) -> None:
    for row in frame.iter_rows(named=True):
        issues.write(ValidationIssue(mode, "error", message, {**common, **row}))


def failed_sample(row: dict[str, Any], message: str, extra: dict[str, Any]) -> dict[str, Any]:
    details = {
        "chunk_id": row.get("chunk_id"),
        "ticker": row.get("ticker"),
        "bucket_id": row.get("bucket_id"),
        "origin_timestamp_ns": row.get("origin_timestamp_ns"),
        "origin_session_date": row.get("origin_session_date"),
        "source_start_session_date": row.get("source_start_session_date"),
        "source_end_session_date": row.get("source_end_session_date"),
        "shard_path": row.get("_shard_path"),
        **extra,
    }
    return {"ok": False, "message": message, "details": details}


def config_payload(config: ValidationConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
        elif isinstance(value, tuple):
            payload[key] = list(value)
    return payload


def timestamp_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def short_path(path: Path) -> str:
    parts = path.parts
    return str(Path(*parts[-4:])) if len(parts) > 4 else str(path)


if __name__ == "__main__":
    main()
