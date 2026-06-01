from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.build_compact_canonical import (  # noqa: E402
    CompactCanonicalConfig,
    available_sessions,
    config_from_payload as canonical_config_from_payload,
    find_flatfile,
    header_columns,
    normalize_quote_frame,
    normalize_trade_frame,
    parse_ticker_list,
    raw_columns,
)
from research.mlops.compact_events import (  # noqa: E402
    DEFAULT_REFERENCE_DIR,
    EVENT_BYTES,
    HEADER_BYTES,
    NANOSECONDS_PER_MICROSECOND,
    QUOTE_EVENT_TYPE,
    ReferenceMaps,
    TRADE_EVENT_TYPE,
    as_float,
    encode_events_chunk_from_frame,
    unified_event_columns,
)


ENCODING_VERSION = "v4_compact_byte_chunks_1"
DEFAULT_FLATFILES_ROOT = Path("D:/market-data/flatfiles/us_stocks_sip")
DEFAULT_OUTPUT_ROOT = DEFAULT_FLATFILES_ROOT / "derived" / "v4_compact_chunks_v1"
DEFAULT_ISSUE_ROOT = DEFAULT_FLATFILES_ROOT / "derived" / "v4_compact_chunks_v1_issues"
DEFAULT_STATE_ROOT = DEFAULT_FLATFILES_ROOT / "derived" / "v4_compact_chunks_v1_state"
DEFAULT_EVENT_SHARD_ROOT = DEFAULT_OUTPUT_ROOT / "event_shards"
DEFAULT_CHUNK_ROOT = DEFAULT_OUTPUT_ROOT / "chunks"
DEFAULT_INDEX_ROOT = DEFAULT_OUTPUT_ROOT / "indexes"
QUOTE_SIZE_UNIT_SWITCH_DATE = "2025-11-03"


@dataclass(slots=True)
class V4ChunkBuildConfig:
    flatfiles_root: Path = DEFAULT_FLATFILES_ROOT
    output_root: Path = DEFAULT_OUTPUT_ROOT
    event_shard_root: Path = DEFAULT_EVENT_SHARD_ROOT
    chunk_root: Path = DEFAULT_CHUNK_ROOT
    index_root: Path = DEFAULT_INDEX_ROOT
    state_root: Path = DEFAULT_STATE_ROOT
    issue_root: Path = DEFAULT_ISSUE_ROOT
    reference_dir: Path = DEFAULT_REFERENCE_DIR
    start_date: str = "2025-01-01"
    end_date: str = "2025-12-31"
    tickers: tuple[str, ...] = ("__ALL_TICKERS__",)
    bucket_count: int = 1024
    events_per_chunk: int = 128
    stride_events: int = 1
    chunk_rows_per_shard: int = 100_000
    session_timezone: str = "America/New_York"
    session_start_time_market: str = "04:00"
    session_end_time_market: str = "20:00"
    quote_size_lot_multiplier_before_2025_11_03: int = 100
    strict_lossless: bool = True
    rebuild: bool = False


def parse_args() -> argparse.Namespace:
    defaults = V4ChunkBuildConfig()
    parser = argparse.ArgumentParser(description="Build v4-ready compact byte chunk shards from raw SIP quote/trade flatfiles.")
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--output-root", default=str(defaults.output_root))
    parser.add_argument("--event-shard-root", default="")
    parser.add_argument("--chunk-root", default="")
    parser.add_argument("--index-root", default="")
    parser.add_argument("--state-root", default="")
    parser.add_argument("--issue-root", default="")
    parser.add_argument("--reference-dir", default=str(defaults.reference_dir))
    parser.add_argument("--start-date", default=defaults.start_date)
    parser.add_argument("--end-date", default=defaults.end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--bucket-count", type=int, default=defaults.bucket_count)
    parser.add_argument("--events-per-chunk", type=int, default=defaults.events_per_chunk)
    parser.add_argument("--stride-events", type=int, default=defaults.stride_events)
    parser.add_argument("--chunk-rows-per-shard", type=int, default=defaults.chunk_rows_per_shard)
    parser.add_argument("--processes", type=int, default=max(1, min(16, os.cpu_count() or 4)))
    parser.add_argument("--event-processes", type=int, default=0)
    parser.add_argument("--chunk-processes", type=int, default=0)
    parser.add_argument("--polars-threads-per-process", type=int, default=1)
    parser.add_argument("--max-pending", type=int, default=0)
    parser.add_argument("--max-tasks-per-worker", type=int, default=0)
    parser.add_argument("--session-timezone", default=defaults.session_timezone)
    parser.add_argument("--session-start-time-market", default=defaults.session_start_time_market)
    parser.add_argument("--session-end-time-market", default=defaults.session_end_time_market)
    parser.add_argument("--quote-size-lot-multiplier-before-2025-11-03", type=int, default=100)
    parser.add_argument("--stage", choices=("all", "events", "chunks"), default="all")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--strict-lossless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--manifest-name", default="v4_chunk_build_manifest.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    output_root = Path(args.output_root)
    config = V4ChunkBuildConfig(
        flatfiles_root=Path(args.flatfiles_root),
        output_root=output_root,
        event_shard_root=Path(args.event_shard_root) if args.event_shard_root else output_root / "event_shards",
        chunk_root=Path(args.chunk_root) if args.chunk_root else output_root / "chunks",
        index_root=Path(args.index_root) if args.index_root else output_root / "indexes",
        state_root=Path(args.state_root) if args.state_root else output_root / "_state",
        issue_root=Path(args.issue_root) if args.issue_root else output_root / "issues",
        reference_dir=Path(args.reference_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=parse_ticker_list(args.tickers),
        bucket_count=max(1, int(args.bucket_count)),
        events_per_chunk=max(2, int(args.events_per_chunk)),
        stride_events=max(1, int(args.stride_events)),
        chunk_rows_per_shard=max(1, int(args.chunk_rows_per_shard)),
        session_timezone=args.session_timezone,
        session_start_time_market=args.session_start_time_market,
        session_end_time_market=args.session_end_time_market,
        quote_size_lot_multiplier_before_2025_11_03=args.quote_size_lot_multiplier_before_2025_11_03,
        strict_lossless=bool(args.strict_lossless),
        rebuild=bool(args.rebuild),
    )
    sessions = available_sessions(config.flatfiles_root, config.start_date, config.end_date)
    event_processes = args.event_processes if args.event_processes > 0 else args.processes
    chunk_processes = args.chunk_processes if args.chunk_processes > 0 else args.processes
    manifest_path = config.output_root / args.manifest_name
    event_pending = args.max_pending if args.max_pending > 0 else event_processes * 2
    chunk_pending = args.max_pending if args.max_pending > 0 else chunk_processes * 2

    print("=" * 96, flush=True)
    print("V4 compact chunk dataset build", flush=True)
    print(f"flatfiles_root={config.flatfiles_root}", flush=True)
    print(f"event_shard_root={config.event_shard_root}", flush=True)
    print(f"chunk_root={config.chunk_root}", flush=True)
    print(f"index_root={config.index_root}", flush=True)
    print(f"state_root={config.state_root}", flush=True)
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions):,}", flush=True)
    print(f"bucket_count={config.bucket_count:,} events_per_chunk={config.events_per_chunk} stride_events={config.stride_events}", flush=True)
    print(f"event_processes={event_processes} chunk_processes={chunk_processes} polars_threads_per_process={args.polars_threads_per_process}", flush=True)
    print("=" * 96, flush=True)

    if args.dry_run:
        for session in sessions[:5]:
            print(f"{session}: quotes={find_flatfile(config.flatfiles_root, 'quotes', session)}")
            print(f"{session}: trades={find_flatfile(config.flatfiles_root, 'trades', session)}")
        return

    if config.rebuild:
        if args.stage in {"all", "events"}:
            remove_path(config.event_shard_root)
            remove_path(config.issue_root)
            remove_path(config.state_root / "events")
        if args.stage in {"all", "chunks"}:
            remove_path(config.chunk_root)
            remove_path(config.index_root)
            remove_path(config.state_root / "chunks")
    for path in (config.output_root, config.event_shard_root, config.chunk_root, config.index_root, config.state_root, config.issue_root):
        path.mkdir(parents=True, exist_ok=True)

    started = time.time()
    payload = config_to_payload(config)
    failed = 0
    if args.stage in {"all", "events"}:
        event_items = [{"session": session, "kind": kind} for session in sessions for kind in ("quotes", "trades")]
        failed += run_parallel(
            label="event_shards",
            items=event_items,
            submit=lambda executor, item: executor.submit(event_shard_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=event_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=event_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)

    if args.stage in {"all", "chunks"}:
        chunk_items = [{"bucket_id": bucket_id} for bucket_id in range(config.bucket_count)]
        failed += run_parallel(
            label="chunk_shards",
            items=chunk_items,
            submit=lambda executor, item: executor.submit(chunk_bucket_worker, item, payload, args.polars_threads_per_process),
            manifest_path=manifest_path,
            processes=chunk_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=chunk_pending,
            max_tasks_per_worker=args.max_tasks_per_worker,
        )
        if failed:
            raise SystemExit(1)

    print("=" * 96, flush=True)
    print(f"V4 compact chunk dataset build complete in {(time.time() - started) / 60.0:.1f} minutes.", flush=True)
    print(f"Manifest: {manifest_path}", flush=True)
    print("=" * 96, flush=True)


def event_shard_worker(item: dict[str, Any], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    session = str(item["session"])
    kind = str(item["kind"])
    work_id = f"events:{kind}:{session}"
    try:
        result = build_event_shards_for_session_kind(config, session=session, kind=kind, work_id=work_id)
        elapsed = time.time() - started
        print(
            f"FINISH event_shards {kind}:{session} status={result['status']} "
            f"rows={result['rows']:,} files={result['files']:,} elapsed={elapsed:.1f}s",
            flush=True,
        )
        return result_row("event_shards", work_id, result["status"], result["rows"], elapsed, result)
    except BaseException:
        return failed_row("event_shards", work_id, time.time() - started)


def chunk_bucket_worker(item: dict[str, Any], payload: dict[str, Any], polars_threads: int) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, polars_threads))
    started = time.time()
    config = config_from_payload(payload)
    bucket_id = int(item["bucket_id"])
    work_id = f"chunks:bucket={bucket_id:04d}"
    try:
        result = build_chunks_for_bucket(config, bucket_id=bucket_id, work_id=work_id)
        elapsed = time.time() - started
        print(
            f"FINISH chunk_shards bucket={bucket_id:04d} status={result['status']} "
            f"chunks={result['chunks']:,} files={result['files']:,} elapsed={elapsed:.1f}s",
            flush=True,
        )
        return result_row("chunk_shards", work_id, result["status"], result["chunks"], elapsed, result)
    except BaseException:
        return failed_row("chunk_shards", work_id, time.time() - started)


def build_event_shards_for_session_kind(config: V4ChunkBuildConfig, *, session: str, kind: str, work_id: str) -> dict[str, Any]:
    input_path = find_flatfile(config.flatfiles_root, kind, session)
    if input_path is None:
        raise FileNotFoundError(f"Missing {kind} flatfile for {session} under {config.flatfiles_root}")
    output_root = config.event_shard_root / f"session={session}" / f"kind={kind}"
    state_path = success_path(config, "events", f"{kind}_{session}")
    fingerprint = work_fingerprint(config, work_id, [input_path])
    if should_skip_success(state_path, fingerprint):
        return {"status": "skipped", "session": session, "kind": kind, "rows": success_rows(state_path), "files": success_files(state_path)}
    cleanup_work_outputs(output_root, state_path)
    print(f"START event_shards {kind}:{session} source={input_path}", flush=True)

    canonical_config = to_canonical_config(config)
    names = header_columns(input_path)
    required = {"ticker", "sip_timestamp", "sequence_number"}
    required |= {"bid_price", "ask_price", "bid_size", "ask_size"} if kind == "quotes" else {"price", "size"}
    missing = sorted(required - names)
    if missing:
        raise ValueError(f"{input_path} is missing required columns: {missing}")
    scan = pl.scan_csv(str(input_path), infer_schema_length=0, ignore_errors=True).with_row_index("raw_row_number", offset=2)
    selected = sorted((raw_columns(kind) & names) | required)
    raw = scan.select([pl.col("raw_row_number"), *[pl.col(column) for column in selected]])
    normalized = normalize_quote_frame(raw, names, canonical_config, session) if kind == "quotes" else normalize_trade_frame(raw, names, canonical_config, session)
    if config.tickers != ("__ALL_TICKERS__",):
        normalized = normalized.filter(pl.col("ticker").is_in(list(config.tickers)))
    event_frame = quote_event_frame(normalized) if kind == "quotes" else trade_event_frame(normalized)
    event_frame = event_frame.with_columns((pl.col("ticker").hash(seed=17) % config.bucket_count).cast(pl.UInt32).alias("bucket_id"))
    event_frame = event_frame.sort(["ticker", "sip_timestamp", "sequence_number", "event_type"])
    partition = pl.PartitionBy(output_root, key="bucket_id", include_key=True, max_rows_per_file=2_000_000)
    event_frame.sink_parquet(partition, compression="zstd", mkdir=True, maintain_order=True)
    files = list(output_root.rglob("*.parquet"))
    rows = sum_parquet_rows(files)
    write_success(
        state_path,
        {
            "stage": "event_shards",
            "work_id": work_id,
            "fingerprint": fingerprint,
            "input_files": [file_fingerprint(input_path)],
            "output_root": str(output_root),
            "row_count": rows,
            "file_count": len(files),
        },
    )
    return {"status": "ok", "session": session, "kind": kind, "rows": rows, "files": len(files), "output_root": str(output_root)}


def quote_event_frame(normalized: pl.LazyFrame) -> pl.LazyFrame:
    return (
        normalized.with_columns(
            pl.lit(QUOTE_EVENT_TYPE).cast(pl.UInt8).alias("event_type"),
            pl.lit(None, dtype=pl.Float64).alias("price"),
            pl.lit(None, dtype=pl.Float64).alias("size"),
            pl.lit(0, dtype=pl.Int32).alias("exchange"),
            pl.lit(0, dtype=pl.Int32).alias("correction"),
            pl.col("condition_1").cast(pl.Int32, strict=False).fill_null(0).alias("condition_first"),
        )
        .select(unified_event_columns())
    )


def trade_event_frame(normalized: pl.LazyFrame) -> pl.LazyFrame:
    return (
        normalized.with_columns(
            pl.lit(TRADE_EVENT_TYPE).cast(pl.UInt8).alias("event_type"),
            pl.lit(None, dtype=pl.Float64).alias("bid_price"),
            pl.lit(None, dtype=pl.Float64).alias("ask_price"),
            pl.lit(None, dtype=pl.Float64).alias("bid_size"),
            pl.lit(None, dtype=pl.Float64).alias("ask_size"),
            pl.lit(0, dtype=pl.Int32).alias("bid_exchange"),
            pl.lit(0, dtype=pl.Int32).alias("ask_exchange"),
            pl.col("condition_1").cast(pl.Int32, strict=False).fill_null(0).alias("condition_first"),
        )
        .select(unified_event_columns())
    )


def build_chunks_for_bucket(config: V4ChunkBuildConfig, *, bucket_id: int, work_id: str) -> dict[str, Any]:
    state_path = success_path(config, "chunks", f"bucket_{bucket_id:04d}")
    inputs = discover_event_shard_files(config, bucket_id)
    fingerprint = work_fingerprint(config, work_id, inputs)
    if should_skip_success(state_path, fingerprint):
        return {"status": "skipped", "bucket_id": bucket_id, "chunks": success_rows(state_path), "files": success_files(state_path)}
    output_dir = config.chunk_root / f"bucket={bucket_id:04d}"
    index_path = config.index_root / f"bucket={bucket_id:04d}.parquet"
    cleanup_work_outputs(output_dir, state_path, extra_paths=[index_path])
    output_dir.mkdir(parents=True, exist_ok=True)
    config.index_root.mkdir(parents=True, exist_ok=True)
    print(f"START chunk_shards bucket={bucket_id:04d} input_files={len(inputs):,}", flush=True)
    if not inputs:
        write_success(state_path, {"stage": "chunk_shards", "work_id": work_id, "fingerprint": fingerprint, "row_count": 0, "file_count": 0})
        return {"status": "ok", "bucket_id": bucket_id, "chunks": 0, "files": 0, "index_path": ""}

    references = ReferenceMaps.load(config.reference_dir)
    session_files = group_event_files_by_session(inputs)
    carries: dict[str, pl.DataFrame] = {}
    writers = ChunkShardWriter(config, output_dir=output_dir, index_path=index_path, bucket_id=bucket_id)
    input_rows = 0
    rejected = 0
    try:
        for ordinal, (session, paths) in enumerate(session_files, start=1):
            started = time.time()
            frame = read_bucket_session_events(paths)
            input_rows += frame.height
            if frame.height == 0:
                continue
            tickers = frame["ticker"].unique().sort().to_list()
            session_chunks = 0
            for ticker in tickers:
                ticker_frame = frame.filter(pl.col("ticker") == ticker)
                carry = carries.get(ticker)
                source = pl.concat([carry, ticker_frame], how="vertical_relaxed") if carry is not None and carry.height > 0 else ticker_frame
                result = write_chunks_for_ticker_frame(config, source, ticker, references, writers)
                session_chunks += result["chunks"]
                rejected += result["rejected"]
                carries[ticker] = source.tail(config.events_per_chunk - 1)
            print(
                f"BUCKET {bucket_id:04d} session {ordinal:,}/{len(session_files):,} {session} "
                f"rows={frame.height:,} tickers={len(tickers):,} chunks={session_chunks:,} "
                f"elapsed={time.time() - started:.1f}s total_chunks={writers.row_count:,}",
                flush=True,
            )
    finally:
        writers.close()
    write_success(
        state_path,
        {
            "stage": "chunk_shards",
            "work_id": work_id,
            "fingerprint": fingerprint,
            "input_file_count": len(inputs),
            "input_rows": input_rows,
            "row_count": writers.row_count,
            "file_count": writers.file_count,
            "rejected_chunks": rejected,
            "output_dir": str(output_dir),
            "index_path": str(index_path),
        },
    )
    return {
        "status": "ok",
        "bucket_id": bucket_id,
        "chunks": writers.row_count,
        "files": writers.file_count,
        "rejected_chunks": rejected,
        "index_path": str(index_path),
    }


def write_chunks_for_ticker_frame(
    config: V4ChunkBuildConfig,
    frame: pl.DataFrame,
    ticker: str,
    references: ReferenceMaps,
    writer: "ChunkShardWriter",
) -> dict[str, int]:
    if frame.height < config.events_per_chunk:
        return {"chunks": 0, "rejected": 0}
    chunks = 0
    rejected = 0
    min_origin = config.events_per_chunk - 1
    for origin_idx in range(min_origin, frame.height, config.stride_events):
        origin_ts = int(frame["sip_timestamp"][origin_idx])
        encoded = encode_events_chunk_from_frame(
            frame,
            origin_timestamp_ns=origin_ts,
            events_per_chunk=config.events_per_chunk,
            references=references,
            strict_lossless=config.strict_lossless,
        )
        if encoded is None:
            rejected += 1
            continue
        start_idx = origin_idx - config.events_per_chunk + 1
        window_sessions = frame["session_date"].slice(start_idx, config.events_per_chunk).unique().to_list()
        writer.add(
            ticker=ticker,
            origin_timestamp_ns=origin_ts,
            origin_session_date=str(frame["session_date"][origin_idx]),
            source_start_timestamp_ns=int(frame["sip_timestamp"][start_idx]),
            source_end_timestamp_ns=origin_ts,
            source_start_session_date=str(frame["session_date"][start_idx]),
            source_end_session_date=str(frame["session_date"][origin_idx]),
            crosses_session_boundary=len(window_sessions) > 1,
            header=encoded[0],
            events=encoded[1],
        )
        chunks += 1
    return {"chunks": chunks, "rejected": rejected}


class ChunkShardWriter:
    def __init__(self, config: V4ChunkBuildConfig, *, output_dir: Path, index_path: Path, bucket_id: int) -> None:
        self.config = config
        self.output_dir = output_dir
        self.index_path = index_path
        self.bucket_id = bucket_id
        self.rows: list[dict[str, Any]] = []
        self.index_rows: list[dict[str, Any]] = []
        self.row_count = 0
        self.file_count = 0
        self.closed = False

    def add(
        self,
        *,
        ticker: str,
        origin_timestamp_ns: int,
        origin_session_date: str,
        source_start_timestamp_ns: int,
        source_end_timestamp_ns: int,
        source_start_session_date: str,
        source_end_session_date: str,
        crosses_session_boundary: bool,
        header: np.ndarray,
        events: np.ndarray,
    ) -> None:
        chunk_id = chunk_id_for(ticker, origin_timestamp_ns, self.config.events_per_chunk)
        row_in_shard = len(self.rows)
        shard_name = f"part-{self.file_count:06d}.parquet"
        self.rows.append(
            {
                "chunk_id": chunk_id,
                "ticker": ticker,
                "origin_timestamp_ns": origin_timestamp_ns,
                "origin_session_date": origin_session_date,
                "source_start_timestamp_ns": source_start_timestamp_ns,
                "source_end_timestamp_ns": source_end_timestamp_ns,
                "source_start_session_date": source_start_session_date,
                "source_end_session_date": source_end_session_date,
                "crosses_session_boundary": bool(crosses_session_boundary),
                "header_uint8": bytes(header.astype(np.uint8, copy=False).reshape(HEADER_BYTES).tolist()),
                "events_uint8": events.astype(np.uint8, copy=False).reshape(self.config.events_per_chunk * EVENT_BYTES).tobytes(),
            }
        )
        self.index_rows.append(
            {
                "chunk_id": chunk_id,
                "bucket_id": self.bucket_id,
                "shard_path": str(self.output_dir / shard_name),
                "row_in_shard": row_in_shard,
                "ticker": ticker,
                "origin_timestamp_ns": origin_timestamp_ns,
                "origin_session_date": origin_session_date,
                "source_start_timestamp_ns": source_start_timestamp_ns,
                "source_end_timestamp_ns": source_end_timestamp_ns,
                "source_start_session_date": source_start_session_date,
                "source_end_session_date": source_end_session_date,
                "crosses_session_boundary": bool(crosses_session_boundary),
            }
        )
        if len(self.rows) >= self.config.chunk_rows_per_shard:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        shard_path = self.output_dir / f"part-{self.file_count:06d}.parquet"
        write_chunk_rows(shard_path, self.rows)
        self.row_count += len(self.rows)
        self.file_count += 1
        self.rows.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.flush()
        if self.index_rows:
            write_index_rows(self.index_path, self.index_rows)
        self.closed = True


def write_chunk_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    table = pa.table(
        {
            "chunk_id": [row["chunk_id"] for row in rows],
            "ticker": [row["ticker"] for row in rows],
            "origin_timestamp_ns": pa.array([row["origin_timestamp_ns"] for row in rows], type=pa.int64()),
            "origin_session_date": [row["origin_session_date"] for row in rows],
            "source_start_timestamp_ns": pa.array([row["source_start_timestamp_ns"] for row in rows], type=pa.int64()),
            "source_end_timestamp_ns": pa.array([row["source_end_timestamp_ns"] for row in rows], type=pa.int64()),
            "source_start_session_date": [row["source_start_session_date"] for row in rows],
            "source_end_session_date": [row["source_end_session_date"] for row in rows],
            "crosses_session_boundary": pa.array([row["crosses_session_boundary"] for row in rows], type=pa.bool_()),
            "header_uint8": pa.array([row["header_uint8"] for row in rows], type=pa.binary(HEADER_BYTES)),
            "events_uint8": pa.array([row["events_uint8"] for row in rows], type=pa.binary(len(rows[0]["events_uint8"]))),
        }
    )
    pq.write_table(table, temp, compression="zstd")
    os.replace(temp, path)


def write_index_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    table = pa.table(columns)
    pq.write_table(table, temp, compression="zstd")
    os.replace(temp, path)


def chunk_id_for(ticker: str, origin_timestamp_ns: int, events_per_chunk: int) -> str:
    payload = f"{ENCODING_VERSION}|{ticker}|{origin_timestamp_ns}|{events_per_chunk}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def discover_event_shard_files(config: V4ChunkBuildConfig, bucket_id: int) -> list[Path]:
    pattern = f"session=*/kind=*/bucket_id={bucket_id}/*.parquet"
    return sorted(config.event_shard_root.glob(pattern), key=event_shard_sort_key)


def event_shard_sort_key(path: Path) -> tuple[str, str, str]:
    session = ""
    kind = ""
    for part in path.parts:
        if part.startswith("session="):
            session = part.split("=", 1)[1]
        elif part.startswith("kind="):
            kind = part.split("=", 1)[1]
    return session, kind, path.name


def group_event_files_by_session(paths: list[Path]) -> list[tuple[str, list[Path]]]:
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        session = ""
        for part in path.parts:
            if part.startswith("session="):
                session = part.split("=", 1)[1]
                break
        grouped.setdefault(session, []).append(path)
    return [(session, sorted(items, key=event_shard_sort_key)) for session, items in sorted(grouped.items())]


def read_bucket_session_events(paths: list[Path]) -> pl.DataFrame:
    frames = [pl.scan_parquet(str(path)) for path in paths]
    if not frames:
        return pl.DataFrame()
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .select(unified_event_columns())
        .sort(["ticker", "sip_timestamp", "sequence_number", "event_type"])
        .collect()
    )


def to_canonical_config(config: V4ChunkBuildConfig) -> CompactCanonicalConfig:
    return CompactCanonicalConfig(
        flatfiles_root=config.flatfiles_root,
        start_date=config.start_date,
        end_date=config.end_date,
        tickers=config.tickers,
        session_timezone=config.session_timezone,
        session_start_time_market=config.session_start_time_market,
        session_end_time_market=config.session_end_time_market,
        quote_size_lot_multiplier_before_2025_11_03=config.quote_size_lot_multiplier_before_2025_11_03,
        rebuild=config.rebuild,
    )


def success_path(config: V4ChunkBuildConfig, stage: str, name: str) -> Path:
    return config.state_root / stage / f"{safe_name(name)}.SUCCESS.json"


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value)


def should_skip_success(path: Path, fingerprint: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "ok" and payload.get("fingerprint") == fingerprint


def success_rows(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("row_count", 0))
    except Exception:
        return 0


def success_files(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("file_count", 0))
    except Exception:
        return 0


def write_success(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "status": "ok",
        "encoding_version": ENCODING_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **payload,
    }
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(output, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temp, path)


def cleanup_work_outputs(output_path: Path, state_path: Path, extra_paths: list[Path] | None = None) -> None:
    if state_path.exists():
        state_path.unlink()
    remove_path(output_path)
    for path in extra_paths or []:
        remove_path(path)


def work_fingerprint(config: V4ChunkBuildConfig, work_id: str, inputs: Iterable[Path]) -> str:
    payload = {
        "work_id": work_id,
        "encoding_version": ENCODING_VERSION,
        "config": config_payload_for_hash(config),
        "inputs": [file_fingerprint(path) for path in sorted(inputs)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def config_payload_for_hash(config: V4ChunkBuildConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("flatfiles_root", "output_root", "event_shard_root", "chunk_root", "index_root", "state_root", "issue_root", "reference_dir"):
        payload[key] = str(payload[key])
    return payload


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def config_to_payload(config: V4ChunkBuildConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("flatfiles_root", "output_root", "event_shard_root", "chunk_root", "index_root", "state_root", "issue_root", "reference_dir"):
        payload[key] = str(payload[key])
    payload["tickers"] = list(config.tickers)
    return payload


def config_from_payload(payload: dict[str, Any]) -> V4ChunkBuildConfig:
    values = dict(payload)
    for key in ("flatfiles_root", "output_root", "event_shard_root", "chunk_root", "index_root", "state_root", "issue_root", "reference_dir"):
        values[key] = Path(values[key])
    values["tickers"] = tuple(values["tickers"])
    return V4ChunkBuildConfig(**values)


def sum_parquet_rows(paths: Iterable[Path]) -> int:
    rows = 0
    for path in paths:
        rows += pq.ParquetFile(path).metadata.num_rows
    return rows


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def run_parallel(
    *,
    label: str,
    items: list[Any],
    submit: Any,
    manifest_path: Path,
    processes: int,
    started: float,
    fail_fast: bool,
    heartbeat_seconds: float,
    max_pending: int,
    max_tasks_per_worker: int,
) -> int:
    if not items:
        print(f"{label}: no work items", flush=True)
        return 0
    failed = 0
    completed = 0
    submitted = 0
    pending_limit = max(1, max_pending)
    submitted_at: dict[Any, float] = {}
    labels: dict[Any, str] = {}
    item_iter = iter(enumerate(items, start=1))

    def submit_next(executor: concurrent.futures.ProcessPoolExecutor, pending: set[Any]) -> bool:
        nonlocal submitted
        try:
            index, item = next(item_iter)
        except StopIteration:
            return False
        future = submit(executor, item)
        pending.add(future)
        submitted += 1
        submitted_at[future] = time.time()
        labels[future] = item_label(item)
        print(f"[{index:,}/{len(items):,}] SUBMIT {label} {labels[future]}", flush=True)
        return True

    executor_kwargs: dict[str, Any] = {"max_workers": max(1, processes)}
    if max_tasks_per_worker > 0:
        executor_kwargs["max_tasks_per_child"] = max_tasks_per_worker
    with concurrent.futures.ProcessPoolExecutor(**executor_kwargs) as executor:
        pending: set[Any] = set()
        while len(pending) < min(pending_limit, len(items)) and submit_next(executor, pending):
            pass
        next_heartbeat = time.time() + max(1.0, heartbeat_seconds)
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=max(1.0, heartbeat_seconds), return_when=concurrent.futures.FIRST_COMPLETED)
            now = time.time()
            if not done:
                print_heartbeat(label, pending, labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
                continue
            for future in done:
                completed += 1
                item = labels[future]
                try:
                    result = future.result()
                except BaseException:
                    result = failed_row(label, item, time.time() - started)
                append_jsonl(manifest_path, result)
                if result.get("status") == "failed":
                    failed += 1
                    print(result.get("error", ""), flush=True)
                    if fail_fast:
                        raise SystemExit(f"{label} failed for {item}")
                print(format_progress(result, completed, len(items), time.time() - started), flush=True)
                while len(pending) < pending_limit and submitted < len(items):
                    if not submit_next(executor, pending):
                        break
            if now >= next_heartbeat and pending:
                print_heartbeat(label, pending, labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
    return failed


def item_label(item: Any) -> str:
    if isinstance(item, dict):
        if "session" in item and "kind" in item:
            return f"{item['kind']}:{item['session']}"
        if "bucket_id" in item:
            return f"bucket={int(item['bucket_id']):04d}"
    return str(item)


def print_heartbeat(label: str, pending: set[Any], labels: dict[Any, str], submitted_at: dict[Any, float], completed: int, total: int, started: float) -> None:
    now = time.time()
    longest = sorted(((now - submitted_at[future], labels[future]) for future in pending), reverse=True)[:5]
    formatted = ", ".join(f"{name}={seconds:.0f}s" for seconds, name in longest)
    print(
        f"HEARTBEAT {label}: completed={completed:,}/{total:,} running={len(pending):,} "
        f"elapsed_minutes={(now - started) / 60.0:.1f} longest=[{formatted}]",
        flush=True,
    )


def result_row(phase: str, key: str, status: str, rows: int, elapsed: float, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": phase,
        "key": key,
        "status": status,
        "rows": rows,
        "details": details,
        "elapsed_seconds": elapsed,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def failed_row(phase: str, key: str, elapsed: float) -> dict[str, Any]:
    return {
        "phase": phase,
        "key": key,
        "status": "failed",
        "rows": 0,
        "details": {},
        "elapsed_seconds": elapsed,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "error": traceback.format_exc(),
    }


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def format_progress(result: dict[str, Any], completed: int, total: int, elapsed: float) -> str:
    return (
        f"[{completed:,}/{total:,}] {result.get('phase')} {result.get('key')} "
        f"{result.get('status')} rows={int(result.get('rows') or 0):,} "
        f"item_seconds={float(result.get('elapsed_seconds') or 0.0):.1f} elapsed_minutes={elapsed / 60.0:.1f}"
    )


if __name__ == "__main__":
    main()
