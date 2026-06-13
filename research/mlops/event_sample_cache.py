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
SAMPLE_CACHE_VERSION = 1
SAMPLE_BYTES = HEADER_BYTES + DEFAULT_EVENTS_PER_CHUNK * EVENT_BYTES
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
        self._file.flush()
        self._sha.update(payload)
        sample_count = int(records.shape[0])
        for local_index in range(sample_count):
            self._maybe_record_audit_sample(tickers, origin_ordinals, origin_timestamps, local_index)
            self._global_sample_index += 1
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


def encode_sample_records(headers: np.ndarray, events: np.ndarray) -> np.ndarray:
    records = np.empty((headers.shape[0], SAMPLE_BYTES), dtype=np.uint8)
    records[:, :HEADER_BYTES] = headers
    records[:, HEADER_BYTES:] = events.reshape(headers.shape[0], DEFAULT_EVENTS_PER_CHUNK * EVENT_BYTES)
    return records


def decode_sample_records(records: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if records.ndim != 2 or records.shape[1] != SAMPLE_BYTES:
        raise ValueError(f"Expected records shape [N,{SAMPLE_BYTES}], got {records.shape}")
    headers = np.ascontiguousarray(records[:, :HEADER_BYTES])
    events = np.ascontiguousarray(records[:, HEADER_BYTES:].reshape(records.shape[0], DEFAULT_EVENTS_PER_CHUNK, EVENT_BYTES))
    return headers, events


def discover_event_sample_shards(config: EventSampleCacheDataConfig) -> list[EventSampleShard]:
    root = resolve_event_sample_cache_root(Path(config.cache_root))
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shard_rows = [row for row in manifest.get("shards", []) if row.get("split") == config.split]
    else:
        shard_rows = []
        for meta_path in sorted((root / config.split).glob("shard_*.json")):
            shard_rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
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


def resolve_event_sample_cache_root(path: Path) -> Path:
    if (path / "manifest.json").exists():
        return path
    if has_event_sample_shards(path):
        return path
    candidates = [child for child in path.iterdir() if child.is_dir() and ((child / "manifest.json").exists() or has_event_sample_shards(child))] if path.exists() else []
    if not candidates:
        return path
    candidates.sort(key=event_sample_cache_mtime, reverse=True)
    return candidates[0]


def has_event_sample_shards(path: Path) -> bool:
    return (path / "train").exists() and any((path / "train").glob("shard_*.samples.json"))


def event_sample_cache_mtime(path: Path) -> float:
    manifest = path / "manifest.json"
    if manifest.exists():
        return manifest.stat().st_mtime
    progress = path / "train_progress.json"
    if progress.exists():
        return progress.stat().st_mtime
    shard_meta = list((path / "train").glob("shard_*.samples.json"))
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
                shuffle_started = time.perf_counter()
                np_rng = np.random.default_rng(int(config.seed) + epoch * 1_000_003 + shard.shard_index)
                np_rng.shuffle(records, axis=0)
                shuffle_seconds = time.perf_counter() - shuffle_started
            usable_samples = records.shape[0]
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
                    "shard_count": len(shards),
                    "shard_step": shard_step,
                    "shard_steps": shard_steps,
                    "profile": {
                        "data/shard_load_seconds": load_seconds if shard_step == 1 else 0.0,
                        "data/shard_shuffle_seconds": shuffle_seconds if shard_step == 1 else 0.0,
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
            shuffle_started = time.perf_counter()
            first_shard_index = group[0].shard_index
            np_rng = np.random.default_rng(int(config.seed) + epoch * 1_000_003 + first_shard_index)
            np_rng.shuffle(records, axis=0)
            shuffle_seconds = time.perf_counter() - shuffle_started
        usable_samples = records.shape[0]
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
                "shard_count": int(math.ceil(len(shards) / group_size)),
                "shard_step": shard_step,
                "shard_steps": shard_steps,
                "profile": {
                    "data/shard_load_seconds": load_seconds if shard_step == 1 else 0.0,
                    "data/shard_shuffle_seconds": shuffle_seconds if shard_step == 1 else 0.0,
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
    data = np.fromfile(shard.path, dtype=np.uint8).reshape(shard.num_samples, SAMPLE_BYTES)
    return shard, data, time.perf_counter() - started


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
