from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import torch

from research.mlops.clickhouse_events import (
    ClickHouseEventsDataConfig,
    EventSpan,
    PersistentClickHouseBytesClient,
    encode_span_samples,
    fetch_spans,
    normalized_config,
)
from research.mlops.compact_events import DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES, HEADER_BYTES


SAMPLE_CACHE_FORMAT = "compact_byte_event_sample_cache"
SAMPLE_CACHE_FORMAT_V2 = "compact_byte_event_sample_cache_with_labels"
LABEL_SIDECAR_FORMAT = "compact_byte_event_sample_cache_labels_parquet"
SAMPLE_CACHE_VERSION = 1
SAMPLE_CACHE_VERSION_V2 = 2
SAMPLE_BYTES = HEADER_BYTES + DEFAULT_EVENTS_PER_CHUNK * EVENT_BYTES
DEFAULT_LABEL_CHUNKS = 2
LABEL_SAMPLE_BYTES = DEFAULT_LABEL_CHUNKS * SAMPLE_BYTES
LABELED_SAMPLE_BYTES = SAMPLE_BYTES + LABEL_SAMPLE_BYTES
DEFAULT_SAMPLE_CACHE_ROOT = Path("D:/market-data/prepared/event_sample_cache")


@dataclass(frozen=True, slots=True)
class EventSampleCacheDataConfig:
    cache_root: Path
    split: str = "train"
    batch_size: int = 4096
    events_per_chunk: int = DEFAULT_EVENTS_PER_CHUNK
    seed: int = 17
    prefetch_shards: int = 2
    start_shard_index: int = 0
    max_shards: int = 0
    max_samples: int = 0
    max_batches_per_shard: int = 0
    shuffle_records: bool = True
    drop_last: bool = True
    interleave_shards: int = 1


@dataclass(frozen=True, slots=True)
class EventSampleShard:
    split: str
    shard_index: int
    path: Path
    meta_path: Path
    num_samples: int
    sample_bytes: int
    sha256: str = ""
    byte_size: int = 0


@dataclass(frozen=True, slots=True)
class EventSampleLabeledShard:
    split: str
    shard_index: int
    x_path: Path
    y_path: Path
    meta_path: Path
    num_samples: int
    x_sample_bytes: int
    y_sample_bytes: int
    label_chunks: int
    x_sha256: str = ""
    y_sha256: str = ""
    x_byte_size: int = 0
    y_byte_size: int = 0
    label_path: Path | None = None


class EventSampleShardWriter:
    def __init__(
        self,
        *,
        cache_root: Path,
        split: str,
        shard_sample_target: int,
        audit_sample_limit: int,
        audit_rng: random.Random,
    ) -> None:
        self.cache_root = cache_root
        self.split = split
        self.split_dir = cache_root / split
        self.split_dir.mkdir(parents=True, exist_ok=True)
        self.shard_sample_target = max(1, int(shard_sample_target))
        self.audit_sample_limit = max(0, int(audit_sample_limit))
        self.audit_rng = audit_rng
        self.shards: list[dict[str, Any]] = []
        self.audit_rows: list[dict[str, Any]] = []
        self._file = None
        self._sha = hashlib.sha256()
        self._shard_index = 0
        self._shard_samples = 0
        self._shard_bytes = 0
        self._shard_tmp_path: Path | None = None
        self._shard_path: Path | None = None
        self._global_sample_index = 0
        self._open_next_shard()

    def close(self) -> None:
        self._finalize_current_shard()
        if self.audit_rows:
            audit_path = self.cache_root / f"{self.split}_audit_samples.jsonl"
            with audit_path.open("w", encoding="utf-8") as handle:
                for row in self.audit_rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def write_batch(self, batch: dict[str, Any]) -> int:
        headers = to_numpy_uint8(batch["header_uint8"])
        events = to_numpy_uint8(batch["events_uint8"])
        if headers.ndim != 2 or headers.shape[1] != HEADER_BYTES:
            raise ValueError(f"Expected headers shape [N,{HEADER_BYTES}], got {headers.shape}")
        if events.ndim != 3 or events.shape[1:] != (DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES):
            raise ValueError(f"Expected events shape [N,{DEFAULT_EVENTS_PER_CHUNK},{EVENT_BYTES}], got {events.shape}")
        if headers.shape[0] != events.shape[0]:
            raise ValueError("Header/event sample counts do not match")
        records = encode_sample_records(headers, events)
        tickers = list(batch.get("ticker", []))
        origin_ordinals = to_numpy_int64(batch.get("origin_ordinal", np.zeros((headers.shape[0],), dtype=np.int64)))
        origin_timestamps = to_numpy_int64(batch.get("origin_timestamp_ns", np.zeros((headers.shape[0],), dtype=np.int64)))
        offset = 0
        while offset < records.shape[0]:
            remaining = self.shard_sample_target - self._shard_samples
            take = min(remaining, records.shape[0] - offset)
            self._write_records(
                records[offset : offset + take],
                tickers[offset : offset + take] if tickers else [],
                origin_ordinals[offset : offset + take],
                origin_timestamps[offset : offset + take],
            )
            offset += take
            if self._shard_samples >= self.shard_sample_target:
                self._finalize_current_shard()
                if offset < records.shape[0]:
                    self._open_next_shard()
        return int(records.shape[0])

    def _open_next_shard(self) -> None:
        self._shard_path = self.split_dir / f"shard_{self._shard_index:06d}.samples.bin"
        self._shard_tmp_path = self.split_dir / f"shard_{self._shard_index:06d}.samples.bin.tmp"
        self._sha = hashlib.sha256()
        self._shard_samples = 0
        self._shard_bytes = 0
        self._file = self._shard_tmp_path.open("wb")

    def _write_records(self, records: np.ndarray, tickers: list[str], origin_ordinals: np.ndarray, origin_timestamps: np.ndarray) -> None:
        if self._file is None:
            self._open_next_shard()
        payload = np.ascontiguousarray(records).tobytes()
        self._file.write(payload)
        self._sha.update(payload)
        sample_count = int(records.shape[0])
        if len(self.audit_rows) < self.audit_sample_limit:
            for local_index in range(sample_count):
                self._maybe_record_audit_sample(tickers, origin_ordinals, origin_timestamps, local_index)
                self._global_sample_index += 1
        else:
            self._global_sample_index += sample_count
        self._shard_samples += sample_count
        self._shard_bytes += len(payload)

    def current_shard_status(self) -> dict[str, Any]:
        path = self._shard_tmp_path if self._file is not None else self._shard_path
        disk_bytes = path.stat().st_size if path is not None and path.exists() else 0
        return {
            "split": self.split,
            "shard_index": self._shard_index,
            "tmp_path": str(self._shard_tmp_path) if self._shard_tmp_path is not None else "",
            "final_path": str(self._shard_path) if self._shard_path is not None else "",
            "samples_in_current_shard": self._shard_samples,
            "bytes_in_current_shard": self._shard_bytes,
            "disk_bytes_in_current_shard": disk_bytes,
            "target_samples_per_shard": self.shard_sample_target,
            "target_bytes_per_shard": self.shard_sample_target * SAMPLE_BYTES,
            "completed_shards": len(self.shards),
        }

    def _maybe_record_audit_sample(self, tickers: list[str], origin_ordinals: np.ndarray, origin_timestamps: np.ndarray, local_index: int) -> None:
        if len(self.audit_rows) >= self.audit_sample_limit:
            return
        remaining_needed = self.audit_sample_limit - len(self.audit_rows)
        remaining_budget = max(1, self.audit_sample_limit * 100 - self._global_sample_index)
        if self.audit_rng.random() > min(1.0, remaining_needed / remaining_budget):
            return
        ticker = tickers[local_index] if tickers else ""
        self.audit_rows.append(
            {
                "split": self.split,
                "shard_index": self._shard_index,
                "sample_index_in_shard": self._shard_samples + local_index,
                "global_sample_index": self._global_sample_index,
                "ticker": ticker,
                "origin_ordinal": int(origin_ordinals[local_index]) if origin_ordinals.size else 0,
                "origin_timestamp_ns": int(origin_timestamps[local_index]) if origin_timestamps.size else 0,
            }
        )

    def _finalize_current_shard(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._file = None
        assert self._shard_path is not None and self._shard_tmp_path is not None
        if self._shard_samples == 0:
            self._shard_tmp_path.unlink(missing_ok=True)
            return
        self._shard_tmp_path.replace(self._shard_path)
        meta = {
            "format": SAMPLE_CACHE_FORMAT,
            "version": SAMPLE_CACHE_VERSION,
            "split": self.split,
            "shard_index": self._shard_index,
            "path": str(self._shard_path.relative_to(self.cache_root)),
            "num_samples": self._shard_samples,
            "sample_bytes": SAMPLE_BYTES,
            "header_bytes": HEADER_BYTES,
            "event_bytes": EVENT_BYTES,
            "events_per_chunk": DEFAULT_EVENTS_PER_CHUNK,
            "byte_size": self._shard_bytes,
            "sha256": self._sha.hexdigest(),
        }
        meta_path = self._shard_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.shards.append(meta)
        self._shard_index += 1
        self._shard_samples = 0
        self._shard_bytes = 0


class EventSampleLabeledShardWriter:
    def __init__(
        self,
        *,
        cache_root: Path,
        split: str,
        shard_sample_target: int,
        label_chunks: int,
        audit_sample_limit: int,
        audit_rng: random.Random,
    ) -> None:
        self.cache_root = cache_root
        self.split = split
        self.split_dir = cache_root / split
        self.split_dir.mkdir(parents=True, exist_ok=True)
        self.shard_sample_target = max(1, int(shard_sample_target))
        self.label_chunks = max(1, int(label_chunks))
        self.y_sample_bytes = self.label_chunks * SAMPLE_BYTES
        self.audit_sample_limit = max(0, int(audit_sample_limit))
        self.audit_rng = audit_rng
        self.shards: list[dict[str, Any]] = []
        self.audit_rows: list[dict[str, Any]] = []
        self._x_file = None
        self._y_file = None
        self._x_sha = hashlib.sha256()
        self._y_sha = hashlib.sha256()
        self._label_columns: dict[str, list[np.ndarray | list[str]]] = {}
        self._shard_index = 0
        self._shard_samples = 0
        self._x_shard_bytes = 0
        self._y_shard_bytes = 0
        self._x_tmp_path: Path | None = None
        self._y_tmp_path: Path | None = None
        self._x_path: Path | None = None
        self._y_path: Path | None = None
        self._global_sample_index = 0
        self._open_next_shard()

    def close(self) -> None:
        self._finalize_current_shard()
        if self.audit_rows:
            audit_path = self.cache_root / f"{self.split}_audit_samples.jsonl"
            with audit_path.open("w", encoding="utf-8") as handle:
                for row in self.audit_rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def write_batch(self, batch: dict[str, Any]) -> int:
        headers = to_numpy_uint8(batch["header_uint8"])
        events = to_numpy_uint8(batch["events_uint8"])
        label_headers = to_numpy_uint8(batch["label_header_uint8"])
        label_events = to_numpy_uint8(batch["label_events_uint8"])
        labels = batch.get("labels")
        if headers.ndim != 2 or headers.shape[1] != HEADER_BYTES:
            raise ValueError(f"Expected headers shape [N,{HEADER_BYTES}], got {headers.shape}")
        if events.ndim != 3 or events.shape[1:] != (DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES):
            raise ValueError(f"Expected events shape [N,{DEFAULT_EVENTS_PER_CHUNK},{EVENT_BYTES}], got {events.shape}")
        if label_headers.ndim != 3 or label_headers.shape[1:] != (self.label_chunks, HEADER_BYTES):
            raise ValueError(f"Expected label headers shape [N,{self.label_chunks},{HEADER_BYTES}], got {label_headers.shape}")
        if label_events.ndim != 4 or label_events.shape[1:] != (self.label_chunks, DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES):
            raise ValueError(
                f"Expected label events shape [N,{self.label_chunks},{DEFAULT_EVENTS_PER_CHUNK},{EVENT_BYTES}], got {label_events.shape}"
            )
        if not (headers.shape[0] == events.shape[0] == label_headers.shape[0] == label_events.shape[0]):
            raise ValueError("X/Y sample counts do not match")
        x_records = encode_sample_records(headers, events)
        y_records = encode_label_records(label_headers, label_events)
        tickers = list(batch.get("ticker", []))
        origin_ordinals = to_numpy_int64(batch.get("origin_ordinal", np.zeros((headers.shape[0],), dtype=np.int64)))
        origin_timestamps = to_numpy_int64(batch.get("origin_timestamp_ns", np.zeros((headers.shape[0],), dtype=np.int64)))
        offset = 0
        while offset < x_records.shape[0]:
            remaining = self.shard_sample_target - self._shard_samples
            take = min(remaining, x_records.shape[0] - offset)
            self._write_records(
                x_records[offset : offset + take],
                y_records[offset : offset + take],
                tickers[offset : offset + take] if tickers else [],
                origin_ordinals[offset : offset + take],
                origin_timestamps[offset : offset + take],
                labels,
                offset,
                take,
            )
            offset += take
            if self._shard_samples >= self.shard_sample_target:
                self._finalize_current_shard()
                if offset < x_records.shape[0]:
                    self._open_next_shard()
        return int(x_records.shape[0])

    def _open_next_shard(self) -> None:
        stem = f"shard_{self._shard_index:06d}"
        self._x_path = self.split_dir / f"{stem}.x.bin"
        self._y_path = self.split_dir / f"{stem}.y.bin"
        self._x_tmp_path = self.split_dir / f"{stem}.x.bin.tmp"
        self._y_tmp_path = self.split_dir / f"{stem}.y.bin.tmp"
        self._x_sha = hashlib.sha256()
        self._y_sha = hashlib.sha256()
        self._label_columns = {}
        self._shard_samples = 0
        self._x_shard_bytes = 0
        self._y_shard_bytes = 0
        self._x_file = self._x_tmp_path.open("wb")
        self._y_file = self._y_tmp_path.open("wb")

    def _write_records(
        self,
        x_records: np.ndarray,
        y_records: np.ndarray,
        tickers: list[str],
        origin_ordinals: np.ndarray,
        origin_timestamps: np.ndarray,
        labels: object,
        label_offset: int,
        label_take: int,
    ) -> None:
        if self._x_file is None or self._y_file is None:
            self._open_next_shard()
        x_payload = np.ascontiguousarray(x_records).tobytes()
        y_payload = np.ascontiguousarray(y_records).tobytes()
        self._x_file.write(x_payload)
        self._y_file.write(y_payload)
        self._x_sha.update(x_payload)
        self._y_sha.update(y_payload)
        if isinstance(labels, dict):
            append_writer_label_columns(self._label_columns, labels, label_offset, label_take)
        sample_count = int(x_records.shape[0])
        if len(self.audit_rows) < self.audit_sample_limit:
            for local_index in range(sample_count):
                self._maybe_record_audit_sample(tickers, origin_ordinals, origin_timestamps, local_index)
                self._global_sample_index += 1
        else:
            self._global_sample_index += sample_count
        self._shard_samples += sample_count
        self._x_shard_bytes += len(x_payload)
        self._y_shard_bytes += len(y_payload)

    def current_shard_status(self) -> dict[str, Any]:
        x_path = self._x_tmp_path if self._x_file is not None else self._x_path
        y_path = self._y_tmp_path if self._y_file is not None else self._y_path
        x_disk_bytes = x_path.stat().st_size if x_path is not None and x_path.exists() else 0
        y_disk_bytes = y_path.stat().st_size if y_path is not None and y_path.exists() else 0
        return {
            "split": self.split,
            "shard_index": self._shard_index,
            "x_tmp_path": str(self._x_tmp_path) if self._x_tmp_path is not None else "",
            "y_tmp_path": str(self._y_tmp_path) if self._y_tmp_path is not None else "",
            "samples_in_current_shard": self._shard_samples,
            "x_bytes_in_current_shard": self._x_shard_bytes,
            "y_bytes_in_current_shard": self._y_shard_bytes,
            "x_disk_bytes_in_current_shard": x_disk_bytes,
            "y_disk_bytes_in_current_shard": y_disk_bytes,
            "target_samples_per_shard": self.shard_sample_target,
            "completed_shards": len(self.shards),
        }

    def _maybe_record_audit_sample(self, tickers: list[str], origin_ordinals: np.ndarray, origin_timestamps: np.ndarray, local_index: int) -> None:
        if len(self.audit_rows) >= self.audit_sample_limit:
            return
        remaining_needed = self.audit_sample_limit - len(self.audit_rows)
        remaining_budget = max(1, self.audit_sample_limit * 100 - self._global_sample_index)
        if self.audit_rng.random() > min(1.0, remaining_needed / remaining_budget):
            return
        ticker = tickers[local_index] if tickers else ""
        self.audit_rows.append(
            {
                "split": self.split,
                "shard_index": self._shard_index,
                "sample_index_in_shard": self._shard_samples + local_index,
                "global_sample_index": self._global_sample_index,
                "ticker": ticker,
                "origin_ordinal": int(origin_ordinals[local_index]) if origin_ordinals.size else 0,
                "origin_timestamp_ns": int(origin_timestamps[local_index]) if origin_timestamps.size else 0,
                "label_chunks": self.label_chunks,
                "stored_future_y_chunks": self.label_chunks,
                "stored_future_y_events": self.label_chunks * DEFAULT_EVENTS_PER_CHUNK,
            }
        )

    def _finalize_current_shard(self) -> None:
        if self._x_file is None or self._y_file is None:
            return
        self._x_file.flush()
        self._y_file.flush()
        os.fsync(self._x_file.fileno())
        os.fsync(self._y_file.fileno())
        self._x_file.close()
        self._y_file.close()
        self._x_file = None
        self._y_file = None
        assert self._x_path is not None and self._y_path is not None
        assert self._x_tmp_path is not None and self._y_tmp_path is not None
        if self._shard_samples == 0:
            self._x_tmp_path.unlink(missing_ok=True)
            self._y_tmp_path.unlink(missing_ok=True)
            return
        self._x_tmp_path.replace(self._x_path)
        self._y_tmp_path.replace(self._y_path)
        meta = {
            "format": SAMPLE_CACHE_FORMAT_V2,
            "version": SAMPLE_CACHE_VERSION_V2,
            "split": self.split,
            "shard_index": self._shard_index,
            "x_path": str(self._x_path.relative_to(self.cache_root)),
            "y_path": str(self._y_path.relative_to(self.cache_root)),
            "num_samples": self._shard_samples,
            "x_sample_bytes": SAMPLE_BYTES,
            "y_sample_bytes": self.y_sample_bytes,
            "label_chunks": self.label_chunks,
            "stored_future_y_chunks": self.label_chunks,
            "stored_future_y_events": self.label_chunks * DEFAULT_EVENTS_PER_CHUNK,
            "header_bytes": HEADER_BYTES,
            "event_bytes": EVENT_BYTES,
            "events_per_chunk": DEFAULT_EVENTS_PER_CHUNK,
            "x_byte_size": self._x_shard_bytes,
            "y_byte_size": self._y_shard_bytes,
            "x_sha256": self._x_sha.hexdigest(),
            "y_sha256": self._y_sha.hexdigest(),
        }
        labels_path = self._write_labels_sidecar()
        if labels_path is not None:
            meta["label_path"] = str(labels_path.relative_to(self.cache_root))
            meta["label_format"] = LABEL_SIDECAR_FORMAT
            meta["label_byte_size"] = labels_path.stat().st_size
            meta["label_columns"] = sorted(self._label_columns.keys())
        meta_path = self.split_dir / f"shard_{self._shard_index:06d}.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.shards.append(meta)
        self._shard_index += 1
        self._shard_samples = 0
        self._x_shard_bytes = 0
        self._y_shard_bytes = 0
        self._label_columns = {}

    def _write_labels_sidecar(self) -> Path | None:
        if not self._label_columns:
            return None
        import polars as pl

        label_data = finalize_writer_label_columns(self._label_columns)
        if not label_data:
            return None
        row_counts = {len(values) for values in label_data.values()}
        if row_counts != {self._shard_samples}:
            raise ValueError(f"Label sidecar row count mismatch for shard {self._shard_index}: {row_counts} expected={self._shard_samples}")
        path = self.split_dir / f"shard_{self._shard_index:06d}.labels.parquet"
        tmp_path = self.split_dir / f"shard_{self._shard_index:06d}.labels.parquet.tmp"
        pl.DataFrame(label_data).write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(path)
        return path


def encode_sample_records(headers: np.ndarray, events: np.ndarray) -> np.ndarray:
    records = np.empty((headers.shape[0], SAMPLE_BYTES), dtype=np.uint8)
    records[:, :HEADER_BYTES] = headers
    records[:, HEADER_BYTES:] = events.reshape(headers.shape[0], DEFAULT_EVENTS_PER_CHUNK * EVENT_BYTES)
    return records


def encode_label_records(label_headers: np.ndarray, label_events: np.ndarray) -> np.ndarray:
    if label_headers.ndim != 3 or label_headers.shape[2] != HEADER_BYTES:
        raise ValueError(f"Expected label_headers shape [N,K,{HEADER_BYTES}], got {label_headers.shape}")
    if label_events.ndim != 4 or label_events.shape[1] != label_headers.shape[1] or label_events.shape[2:] != (DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES):
        raise ValueError(
            f"Expected label_events shape [N,K,{DEFAULT_EVENTS_PER_CHUNK},{EVENT_BYTES}], got {label_events.shape}; "
            f"label_headers={label_headers.shape}"
        )
    chunks = []
    for chunk_index in range(label_headers.shape[1]):
        chunks.append(encode_sample_records(label_headers[:, chunk_index, :], label_events[:, chunk_index, :, :]))
    return np.concatenate(chunks, axis=1)


def decode_label_records(records: np.ndarray, *, label_chunks: int = DEFAULT_LABEL_CHUNKS) -> tuple[np.ndarray, np.ndarray]:
    expected = int(label_chunks) * SAMPLE_BYTES
    if records.ndim != 2 or records.shape[1] != expected:
        raise ValueError(f"Expected label records shape [N,{expected}], got {records.shape}")
    chunks = records.reshape(records.shape[0], int(label_chunks), SAMPLE_BYTES)
    headers = chunks[:, :, :HEADER_BYTES]
    events = chunks[:, :, HEADER_BYTES:].reshape(records.shape[0], int(label_chunks), DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES)
    return headers, events


def decode_sample_records(records: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if records.ndim != 2 or records.shape[1] != SAMPLE_BYTES:
        raise ValueError(f"Expected records shape [N,{SAMPLE_BYTES}], got {records.shape}")
    headers = records[:, :HEADER_BYTES]
    events = records[:, HEADER_BYTES:].reshape(records.shape[0], DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES)
    return headers, events


def append_writer_label_columns(
    target: dict[str, list[np.ndarray | list[str]]],
    labels: dict[str, Any],
    offset: int,
    take: int,
) -> None:
    for key, values in labels.items():
        if isinstance(values, np.ndarray):
            target.setdefault(key, []).append(values[offset : offset + take].copy())
        else:
            target.setdefault(key, []).append(list(values[offset : offset + take]))


def finalize_writer_label_columns(columns: dict[str, list[np.ndarray | list[str]]]) -> dict[str, np.ndarray | list[str]]:
    out: dict[str, np.ndarray | list[str]] = {}
    for key, chunks in columns.items():
        if not chunks:
            continue
        first = chunks[0]
        if isinstance(first, np.ndarray):
            out[key] = np.concatenate([chunk for chunk in chunks if isinstance(chunk, np.ndarray)])
        else:
            merged: list[str] = []
            for chunk in chunks:
                merged.extend(str(value) for value in chunk)
            out[key] = merged
    return out


def discover_event_sample_shards(config: EventSampleCacheDataConfig) -> list[EventSampleShard]:
    root = resolve_event_sample_cache_root(Path(config.cache_root), split=config.split)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shard_rows = [
            row
            for row in manifest.get("shards", [])
            if row.get("split") == config.split and (row.get("format", SAMPLE_CACHE_FORMAT) == SAMPLE_CACHE_FORMAT) and "path" in row
        ]
    else:
        shard_rows = []
        for meta_path in sorted((root / config.split).glob("shard_*.json")):
            row = json.loads(meta_path.read_text(encoding="utf-8"))
            if row.get("format", SAMPLE_CACHE_FORMAT) == SAMPLE_CACHE_FORMAT and "path" in row:
                shard_rows.append(row)
    shards: list[EventSampleShard] = []
    for row in shard_rows:
        path = root / row["path"]
        meta_path = path.with_suffix(".json")
        shards.append(
            EventSampleShard(
                split=str(row["split"]),
                shard_index=int(row["shard_index"]),
                path=path,
                meta_path=meta_path,
                num_samples=int(row["num_samples"]),
                sample_bytes=int(row["sample_bytes"]),
                sha256=str(row.get("sha256", "")),
                byte_size=int(row.get("byte_size", int(row["num_samples"]) * int(row["sample_bytes"]))),
            )
        )
    shards.sort(key=lambda value: value.shard_index)
    if config.start_shard_index > 0:
        shards = shards[int(config.start_shard_index) :]
    if config.max_shards > 0:
        shards = shards[: config.max_shards]
    if not shards:
        raise RuntimeError(f"No {config.split!r} sample-cache shards found under {root}")
    for shard in shards:
        if shard.sample_bytes != SAMPLE_BYTES:
            raise ValueError(f"Unsupported sample_bytes={shard.sample_bytes} in {shard.meta_path}; expected {SAMPLE_BYTES}")
    return shards


def discover_event_sample_labeled_shards(config: EventSampleCacheDataConfig) -> list[EventSampleLabeledShard]:
    root = resolve_event_sample_cache_root(Path(config.cache_root), split=config.split)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shard_rows = [
            row
            for row in manifest.get("shards", [])
            if row.get("split") == config.split and (row.get("format") == SAMPLE_CACHE_FORMAT_V2 or "x_path" in row or "y_path" in row)
        ]
    else:
        shard_rows = []
        for meta_path in sorted((root / config.split).glob("shard_*.json")):
            row = json.loads(meta_path.read_text(encoding="utf-8"))
            if row.get("format") == SAMPLE_CACHE_FORMAT_V2 or "x_path" in row or "y_path" in row:
                shard_rows.append(row)
    shards: list[EventSampleLabeledShard] = []
    for row in shard_rows:
        x_path = root / row["x_path"]
        y_path = root / row["y_path"]
        meta_path = root / config.split / f"shard_{int(row['shard_index']):06d}.json"
        shards.append(
            EventSampleLabeledShard(
                split=str(row["split"]),
                shard_index=int(row["shard_index"]),
                x_path=x_path,
                y_path=y_path,
                meta_path=meta_path,
                num_samples=int(row["num_samples"]),
                x_sample_bytes=int(row.get("x_sample_bytes", SAMPLE_BYTES)),
                y_sample_bytes=int(row["y_sample_bytes"]),
                label_chunks=int(row.get("label_chunks", int(row["y_sample_bytes"]) // SAMPLE_BYTES)),
                x_sha256=str(row.get("x_sha256", "")),
                y_sha256=str(row.get("y_sha256", "")),
                x_byte_size=int(row.get("x_byte_size", int(row["num_samples"]) * int(row.get("x_sample_bytes", SAMPLE_BYTES)))),
                y_byte_size=int(row.get("y_byte_size", int(row["num_samples"]) * int(row["y_sample_bytes"]))),
                label_path=(root / str(row["label_path"])) if row.get("label_path") else None,
            )
        )
    shards.sort(key=lambda value: value.shard_index)
    if config.start_shard_index > 0:
        shards = shards[int(config.start_shard_index) :]
    if config.max_shards > 0:
        shards = shards[: config.max_shards]
    if not shards:
        raise RuntimeError(f"No {config.split!r} labeled sample-cache shards found under {root}")
    for shard in shards:
        if shard.x_sample_bytes != SAMPLE_BYTES:
            raise ValueError(f"Unsupported x_sample_bytes={shard.x_sample_bytes} in {shard.meta_path}; expected {SAMPLE_BYTES}")
        if shard.y_sample_bytes != shard.label_chunks * SAMPLE_BYTES:
            raise ValueError(
                f"Unsupported y_sample_bytes={shard.y_sample_bytes} in {shard.meta_path}; "
                f"expected {shard.label_chunks * SAMPLE_BYTES}"
            )
    return shards


def resolve_event_sample_cache_root(path: Path, *, split: str = "train") -> Path:
    if (path / "manifest.json").exists() and cache_has_split(path, split):
        return path
    if has_event_sample_shards(path, split=split):
        return path
    candidates = [
        child
        for child in path.iterdir()
        if child.is_dir() and cache_has_split(child, split)
    ] if path.exists() else []
    if not candidates:
        return path
    candidates.sort(key=lambda value: event_sample_cache_mtime(value, split=split), reverse=True)
    return candidates[0]


def cache_has_split(path: Path, split: str) -> bool:
    manifest_path = path / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return any(row.get("split") == split for row in manifest.get("shards", []))
    return has_event_sample_shards(path, split=split)


def has_event_sample_shards(path: Path, *, split: str = "train") -> bool:
    return (path / split).exists() and (any((path / split).glob("shard_*.samples.json")) or any((path / split).glob("shard_*.json")))


def event_sample_cache_mtime(path: Path, *, split: str = "train") -> float:
    manifest = path / "manifest.json"
    if manifest.exists():
        return manifest.stat().st_mtime
    progress = path / f"{split}_progress.json"
    if progress.exists():
        return progress.stat().st_mtime
    shard_meta = list((path / split).glob("shard_*.samples.json")) + list((path / split).glob("shard_*.json"))
    if shard_meta:
        return max(item.stat().st_mtime for item in shard_meta)
    return path.stat().st_mtime


def iter_event_sample_cache_epoch_batches(
    config: EventSampleCacheDataConfig,
    *,
    epoch: int,
    shards: list[EventSampleShard] | None = None,
) -> Iterator[dict[str, Any]]:
    shards = list(shards or discover_event_sample_shards(config))
    if int(config.interleave_shards) > 1:
        yield from iter_interleaved_event_sample_cache_epoch_batches(config, epoch=epoch, shards=shards)
        return
    order = list(range(len(shards)))
    remaining_samples = int(config.max_samples) if config.max_samples > 0 else 0
    with ThreadPoolExecutor(max_workers=max(1, int(config.prefetch_shards))) as executor:
        future_by_index: dict[int, Future[tuple[EventSampleShard, np.ndarray, float]]] = {}
        next_submit = 0

        def submit_until_full() -> None:
            nonlocal next_submit
            while next_submit < len(order) and len(future_by_index) < max(1, int(config.prefetch_shards)):
                shard = shards[order[next_submit]]
                future_by_index[next_submit] = executor.submit(load_shard_into_memory, shard)
                next_submit += 1

        submit_until_full()
        for order_index in range(len(order)):
            future = future_by_index.pop(order_index)
            shard, records, load_seconds = future.result()
            submit_until_full()
            shuffle_seconds = 0.0
            if config.shuffle_records:
                print(
                    f"CACHE SHUFFLE START split={config.split} shard={shard.shard_index} "
                    f"samples={records.shape[0]:,}",
                    flush=True,
                )
                shuffle_started = time.perf_counter()
                np_rng = np.random.default_rng(int(config.seed) + epoch * 1_000_003 + shard.shard_index)
                np_rng.shuffle(records, axis=0)
                shuffle_seconds = time.perf_counter() - shuffle_started
                print(
                    f"CACHE SHUFFLE DONE split={config.split} shard={shard.shard_index} "
                    f"samples={records.shape[0]:,} seconds={shuffle_seconds:.2f}",
                    flush=True,
                )
            usable_samples = records.shape[0]
            if config.max_batches_per_shard > 0:
                usable_samples = min(usable_samples, int(config.max_batches_per_shard) * max(1, config.batch_size))
            if config.drop_last:
                usable_samples = (usable_samples // max(1, config.batch_size)) * config.batch_size
            if remaining_samples > 0:
                usable_samples = min(usable_samples, remaining_samples)
                if config.drop_last:
                    usable_samples = (usable_samples // max(1, config.batch_size)) * config.batch_size
            if usable_samples <= 0:
                if remaining_samples > 0:
                    break
                continue
            shard_steps = int(math.ceil(usable_samples / max(1, config.batch_size)))
            dropped_samples = records.shape[0] - usable_samples
            for shard_step, start in enumerate(range(0, usable_samples, config.batch_size), start=1):
                batch_records = records[start : start + config.batch_size]
                headers, events = decode_sample_records(batch_records)
                yield {
                    "header_uint8": torch.from_numpy(headers),
                    "events_uint8": torch.from_numpy(events),
                    "origin_timestamp_ns": torch.zeros((headers.shape[0],), dtype=torch.int64),
                    "shard_index": shard.shard_index + 1,
                    "shard_position": order_index + 1,
                    "shard_count": len(shards),
                    "shard_step": shard_step,
                    "shard_steps": shard_steps,
                    "profile": {
                        "data/shard_load_seconds": load_seconds,
                        "data/shard_shuffle_seconds": shuffle_seconds,
                        "data/shard_first_step": 1.0 if shard_step == 1 else 0.0,
                        "data/shard_samples": float(records.shape[0]),
                        "data/shard_usable_samples": float(usable_samples),
                        "data/shard_dropped_samples": float(dropped_samples),
                    },
                }
            if remaining_samples > 0:
                remaining_samples -= usable_samples
                if remaining_samples <= 0:
                    break


def iter_interleaved_event_sample_cache_epoch_batches(
    config: EventSampleCacheDataConfig,
    *,
    epoch: int,
    shards: list[EventSampleShard],
) -> Iterator[dict[str, Any]]:
    group_size = max(1, int(config.interleave_shards))
    remaining_samples = int(config.max_samples) if config.max_samples > 0 else 0
    for group_start in range(0, len(shards), group_size):
        group = shards[group_start : group_start + group_size]
        loaded = [load_shard_into_memory(shard) for shard in group]
        load_seconds = sum(item[2] for item in loaded)
        records = np.concatenate([item[1] for item in loaded], axis=0) if len(loaded) > 1 else loaded[0][1]
        shuffle_seconds = 0.0
        if config.shuffle_records:
            print(
                f"CACHE SHUFFLE START split={config.split} shard_group={group_start // group_size + 1} "
                f"shards={group[0].shard_index}-{group[-1].shard_index} samples={records.shape[0]:,}",
                flush=True,
            )
            shuffle_started = time.perf_counter()
            first_shard_index = group[0].shard_index
            np_rng = np.random.default_rng(int(config.seed) + epoch * 1_000_003 + first_shard_index)
            np_rng.shuffle(records, axis=0)
            shuffle_seconds = time.perf_counter() - shuffle_started
            print(
                f"CACHE SHUFFLE DONE split={config.split} shard_group={group_start // group_size + 1} "
                f"shards={group[0].shard_index}-{group[-1].shard_index} samples={records.shape[0]:,} "
                f"seconds={shuffle_seconds:.2f}",
                flush=True,
            )
        usable_samples = records.shape[0]
        if config.max_batches_per_shard > 0:
            usable_samples = min(usable_samples, int(config.max_batches_per_shard) * max(1, config.batch_size))
        if config.drop_last:
            usable_samples = (usable_samples // max(1, config.batch_size)) * config.batch_size
        if remaining_samples > 0:
            usable_samples = min(usable_samples, remaining_samples)
            if config.drop_last:
                usable_samples = (usable_samples // max(1, config.batch_size)) * config.batch_size
        if usable_samples <= 0:
            if remaining_samples > 0:
                break
            continue
        shard_steps = int(math.ceil(usable_samples / max(1, config.batch_size)))
        dropped_samples = records.shape[0] - usable_samples
        first_index = group[0].shard_index
        last_index = group[-1].shard_index
        for shard_step, start in enumerate(range(0, usable_samples, config.batch_size), start=1):
            batch_records = records[start : start + config.batch_size]
            headers, events = decode_sample_records(batch_records)
            yield {
                "header_uint8": torch.from_numpy(headers),
                "events_uint8": torch.from_numpy(events),
                "origin_timestamp_ns": torch.zeros((headers.shape[0],), dtype=torch.int64),
                "shard_index": (group_start // group_size) + 1,
                "shard_position": (group_start // group_size) + 1,
                "shard_count": int(math.ceil(len(shards) / group_size)),
                "shard_step": shard_step,
                "shard_steps": shard_steps,
                "profile": {
                    "data/shard_load_seconds": load_seconds,
                    "data/shard_shuffle_seconds": shuffle_seconds,
                    "data/shard_first_step": 1.0 if shard_step == 1 else 0.0,
                    "data/shard_samples": float(records.shape[0]),
                    "data/shard_usable_samples": float(usable_samples),
                    "data/shard_dropped_samples": float(dropped_samples),
                    "data/interleave_shards": float(len(group)),
                    "data/interleave_first_shard": float(first_index),
                    "data/interleave_last_shard": float(last_index),
                },
            }
        if remaining_samples > 0:
            remaining_samples -= usable_samples
            if remaining_samples <= 0:
                break


def load_shard_into_memory(shard: EventSampleShard) -> tuple[EventSampleShard, np.ndarray, float]:
    started = time.perf_counter()
    expected_size = shard.num_samples * SAMPLE_BYTES
    actual_size = shard.path.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(f"Shard size mismatch for {shard.path}: expected {expected_size:,}, got {actual_size:,}")
    print(
        f"CACHE LOAD START split={shard.split} shard={shard.shard_index} "
        f"samples={shard.num_samples:,} bytes={actual_size / 1024**3:.2f}GiB path={shard.path}",
        flush=True,
    )
    data = np.fromfile(shard.path, dtype=np.uint8).reshape(shard.num_samples, SAMPLE_BYTES)
    load_seconds = time.perf_counter() - started
    rate = (actual_size / 1024**3) / max(load_seconds, 1e-9)
    print(
        f"CACHE LOAD DONE split={shard.split} shard={shard.shard_index} "
        f"samples={shard.num_samples:,} seconds={load_seconds:.2f} rate={rate:.2f}GiB/s",
        flush=True,
    )
    return shard, data, load_seconds


def to_numpy_uint8(value: Any) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()
    return np.ascontiguousarray(value, dtype=np.uint8)


def to_numpy_int64(value: Any) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()
    return np.ascontiguousarray(value, dtype=np.int64)


def encode_single_audit_window(
    *,
    client: PersistentClickHouseBytesClient,
    config: ClickHouseEventsDataConfig,
    ticker: str,
    origin_ordinal: int,
) -> np.ndarray:
    config = normalized_config(config)
    low = int(origin_ordinal) - config.events_per_chunk + 1
    high = int(origin_ordinal)
    span = EventSpan(
        span_id=0,
        ticker=ticker,
        low_ordinal=low,
        high_ordinal=high,
        base_origin=high,
        stride=1,
        origins_per_span=1,
        expected_rows=config.events_per_chunk,
    )
    rows = fetch_spans(client, config, [span])
    encoded = encode_span_samples(rows, span, config)
    if isinstance(encoded, str):
        raise RuntimeError(f"Audit sample re-encode failed for {ticker}:{origin_ordinal}: {encoded}")
    headers, events, _origin_ts, _origin_ordinals = encoded
    return encode_sample_records(headers, events)[0]


def encode_single_labeled_audit_window(
    *,
    client: PersistentClickHouseBytesClient,
    config: ClickHouseEventsDataConfig,
    ticker: str,
    origin_ordinal: int,
    label_chunks: int = DEFAULT_LABEL_CHUNKS,
) -> tuple[np.ndarray, np.ndarray]:
    config = normalized_config(
        ClickHouseEventsDataConfig(
            clickhouse_url=config.clickhouse_url,
            user=config.user,
            password=config.password,
            database=config.database,
            events_table=config.events_table,
            train_index_table=config.train_index_table,
            validation_index_table=config.validation_index_table,
            index_table=config.index_table,
            split=config.split,
            tickers=config.tickers,
            events_per_chunk=config.events_per_chunk,
            batch_size=1,
            num_spans=1,
            origins_per_span=1,
            min_origin_stride=1,
            max_origin_stride=1,
            query_bundle_spans=1,
            max_threads=config.max_threads,
            max_memory_usage=config.max_memory_usage,
            seed=config.seed,
            max_index_rows=config.max_index_rows,
            max_span_attempt_multiplier=config.max_span_attempt_multiplier,
            strict_lossless=config.strict_lossless,
            label_chunks=int(label_chunks),
            return_torch=False,
        )
    )
    label_events = int(label_chunks) * config.events_per_chunk
    low = int(origin_ordinal) - config.events_per_chunk + 1
    high = int(origin_ordinal) + label_events
    span = EventSpan(
        span_id=0,
        ticker=ticker,
        low_ordinal=low,
        high_ordinal=high,
        base_origin=int(origin_ordinal),
        stride=1,
        origins_per_span=1,
        expected_rows=config.events_per_chunk + label_events,
    )
    rows = fetch_spans(client, config, [span])
    encoded = encode_span_samples(rows, span, config)
    if isinstance(encoded, str):
        raise RuntimeError(f"Audit labeled sample re-encode failed for {ticker}:{origin_ordinal}: {encoded}")
    headers, events, label_headers, label_event_bytes, _origin_ts, _origin_ordinals = encoded
    x_record = encode_sample_records(headers, events)[0]
    y_record = encode_label_records(label_headers, label_event_bytes)[0]
    return x_record, y_record
