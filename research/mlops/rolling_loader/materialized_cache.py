from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from research.mlops.data.contracts import RollingTrainingBatch
from research.mlops.data.rolling import RollingReadyIndexBlock
from research.mlops.rolling_loader.streaming_training import batch_nbytes


MATERIALIZED_CACHE_FORMAT = "rolling_materialized_training_cache"
MATERIALIZED_CACHE_VERSION = 1
DEFAULT_MATERIALIZED_CACHE_ROOT = Path("D:/market-data/prepared/rolling_materialized_cache")


@dataclass(slots=True)
class TensorFileState:
    name: str
    dtype: str
    tail_shape: tuple[int, ...]
    relative_path: str
    file: Any
    sha: Any = field(default_factory=hashlib.sha256)
    byte_size: int = 0


@dataclass(frozen=True, slots=True)
class MaterializedShardEstimate:
    sample_bytes: int
    raw_target_samples: int
    aligned_target_samples: int
    target_shard_bytes: int
    sample_multiple: int


class RollingMaterializedShardWriter:
    """Append fully materialized rolling samples into 4096-aligned shard directories.

    A shard is a directory containing one raw binary file per numeric tensor plus
    a JSON sidecar. The directory is written as ``shard_N.tmp`` and atomically
    renamed to ``shard_N`` only after all tensor files are flushed and fsynced.
    """

    def __init__(
        self,
        *,
        cache_root: Path,
        split: str,
        target_shard_bytes: int,
        sample_multiple: int = 4096,
        start_shard_index: int = 0,
        existing_shards: list[dict[str, Any]] | None = None,
        audit_sample_limit: int = 256,
        audit_rng: random.Random | None = None,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.split = str(split)
        self.split_dir = self.cache_root / self.split
        self.split_dir.mkdir(parents=True, exist_ok=True)
        self.target_shard_bytes = max(1, int(target_shard_bytes))
        self.sample_multiple = max(1, int(sample_multiple))
        self.shards: list[dict[str, Any]] = list(existing_shards or [])
        self.audit_sample_limit = max(0, int(audit_sample_limit))
        self.audit_rng = audit_rng or random.Random(17)
        self.audit_rows: list[dict[str, Any]] = []
        self._shard_index = int(start_shard_index)
        self._shard_samples = 0
        self._shard_bytes = 0
        self._shard_sample_target = 0
        self._tmp_dir: Path | None = None
        self._final_dir: Path | None = None
        self._tensor_files: dict[str, TensorFileState] = {}
        self._tensor_order: list[str] = []
        self._first_origin_us: int | None = None
        self._last_origin_us: int | None = None
        self._first_ticker: str = ""
        self._last_ticker: str = ""
        self._source_blocks: list[dict[str, Any]] = []
        self._active_source_block: dict[str, Any] | None = None
        self._global_sample_index = sum(int(row.get("num_samples", 0) or 0) for row in self.shards)
        self._last_estimate: MaterializedShardEstimate | None = None

    @property
    def current_shard_samples(self) -> int:
        return int(self._shard_samples)

    @property
    def current_shard_target_samples(self) -> int:
        return int(self._shard_sample_target)

    @property
    def current_shard_bytes(self) -> int:
        return int(self._shard_bytes)

    def close(self) -> None:
        self._finalize_current_shard()
        if self.audit_rows:
            audit_path = self.cache_root / f"{self.split}_audit_samples.jsonl"
            with audit_path.open("w", encoding="utf-8") as handle:
                for row in self.audit_rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def current_shard_status(self) -> dict[str, Any]:
        disk_bytes = _directory_size(self._tmp_dir) if self._tmp_dir is not None and self._tmp_dir.exists() else 0
        return {
            "split": self.split,
            "shard_index": self._shard_index,
            "tmp_dir": str(self._tmp_dir or ""),
            "final_dir": str(self._final_dir or ""),
            "samples_in_current_shard": self._shard_samples,
            "target_samples_in_current_shard": self._shard_sample_target,
            "bytes_in_current_shard": self._shard_bytes,
            "disk_bytes_in_current_shard": disk_bytes,
            "target_shard_bytes": self.target_shard_bytes,
            "completed_shards": len(self.shards),
            "estimate": _estimate_to_dict(self._last_estimate),
        }

    def write_batch(self, batch: RollingTrainingBatch) -> int:
        tensors = flatten_training_batch(batch)
        sample_count = _infer_sample_count(tensors)
        if sample_count <= 0:
            return 0
        offset = 0
        while offset < sample_count:
            if self._tmp_dir is None:
                self._open_next_shard(tensors, sample_count=sample_count, batch_nbytes_value=batch_nbytes(batch))
            remaining = max(0, self._shard_sample_target - self._shard_samples)
            if remaining <= 0:
                self._finalize_current_shard()
                continue
            take = min(remaining, sample_count - offset)
            self._write_tensor_slice(tensors, offset=offset, take=take)
            self._record_sample_metadata(tensors, offset=offset, take=take)
            offset += take
            if self._shard_samples >= self._shard_sample_target:
                self._finalize_current_shard()
        return sample_count

    def set_source_block(
        self,
        *,
        block_index: int,
        start_timestamp_us: int,
        end_timestamp_us: int,
        start_label: str = "",
        end_label: str = "",
    ) -> None:
        self._active_source_block = {
            "block_index": int(block_index),
            "start_timestamp_us": int(start_timestamp_us),
            "end_timestamp_us": int(end_timestamp_us),
            "start_utc": timestamp_us_to_utc(int(start_timestamp_us)),
            "end_utc": timestamp_us_to_utc(int(end_timestamp_us)),
            "start_label": str(start_label),
            "end_label": str(end_label),
        }

    def _open_next_shard(self, tensors: Mapping[str, np.ndarray], *, sample_count: int, batch_nbytes_value: int) -> None:
        estimate = estimate_shard_samples(
            sample_bytes=max(1, int(batch_nbytes_value) // max(1, int(sample_count))),
            target_shard_bytes=self.target_shard_bytes,
            sample_multiple=self.sample_multiple,
        )
        self._last_estimate = estimate
        self._shard_sample_target = estimate.aligned_target_samples
        stem = f"shard_{self._shard_index:06d}"
        self._final_dir = self.split_dir / stem
        self._tmp_dir = self.split_dir / f"{stem}.tmp"
        if self._final_dir.exists():
            raise FileExistsError(f"Refusing to overwrite existing shard directory: {self._final_dir}")
        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir)
        self._tmp_dir.mkdir(parents=True, exist_ok=False)
        self._tensor_files = {}
        self._tensor_order = sorted(tensors)
        self._shard_samples = 0
        self._shard_bytes = 0
        self._first_origin_us = None
        self._last_origin_us = None
        self._first_ticker = ""
        self._last_ticker = ""
        self._source_blocks = []
        for name in self._tensor_order:
            array = tensors[name]
            relative_path = f"tensors/{_safe_tensor_name(name)}.bin"
            path = self._tmp_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            state = TensorFileState(
                name=name,
                dtype=str(array.dtype),
                tail_shape=tuple(int(value) for value in array.shape[1:]),
                relative_path=relative_path,
                file=path.open("wb"),
            )
            self._tensor_files[name] = state

    def _write_tensor_slice(self, tensors: Mapping[str, np.ndarray], *, offset: int, take: int) -> None:
        expected = set(self._tensor_files)
        actual = set(tensors)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"Tensor set changed while writing shard. missing={missing} extra={extra}")
        for name in self._tensor_order:
            array = np.ascontiguousarray(tensors[name][offset : offset + take])
            state = self._tensor_files[name]
            if str(array.dtype) != state.dtype or tuple(array.shape[1:]) != state.tail_shape:
                raise ValueError(
                    f"Tensor {name!r} changed shape/dtype. expected dtype={state.dtype} tail={state.tail_shape}; "
                    f"got dtype={array.dtype} tail={tuple(array.shape[1:])}"
                )
            payload = array.tobytes(order="C")
            state.file.write(payload)
            state.sha.update(payload)
            state.byte_size += len(payload)
            self._shard_bytes += len(payload)
        self._shard_samples += int(take)

    def _record_sample_metadata(self, tensors: Mapping[str, np.ndarray], *, offset: int, take: int) -> None:
        origin = tensors.get("origin_timestamp_us")
        ticker = tensors.get("ticker")
        ordinal = tensors.get("origin_ordinal")
        if origin is not None and origin.size:
            values = np.asarray(origin[offset : offset + take], dtype=np.int64)
            first = int(values[0])
            last = int(values[-1])
            self._first_origin_us = first if self._first_origin_us is None else min(self._first_origin_us, first)
            self._last_origin_us = last if self._last_origin_us is None else max(self._last_origin_us, last)
        ticker_values = _decode_ticker_array(ticker[offset : offset + take]) if ticker is not None and ticker.size else []
        if ticker_values:
            if not self._first_ticker:
                self._first_ticker = ticker_values[0]
            self._last_ticker = ticker_values[-1]
        if self._active_source_block is not None:
            if not self._source_blocks or self._source_blocks[-1] != self._active_source_block:
                self._source_blocks.append(dict(self._active_source_block))
        if len(self.audit_rows) < self.audit_sample_limit and origin is not None and ordinal is not None:
            for local in range(take):
                if len(self.audit_rows) >= self.audit_sample_limit:
                    break
                if self.audit_rng.random() > 0.01 and self._global_sample_index > self.audit_sample_limit:
                    self._global_sample_index += 1
                    continue
                ticker_text = ticker_values[local] if local < len(ticker_values) else ""
                self.audit_rows.append(
                    {
                        "split": self.split,
                        "shard_index": self._shard_index,
                        "sample_index_in_shard": self._shard_samples - int(take) + local,
                        "global_sample_index": self._global_sample_index,
                        "ticker": ticker_text,
                        "origin_ordinal": int(np.asarray(ordinal)[offset + local]),
                        "origin_timestamp_us": int(np.asarray(origin)[offset + local]),
                        "origin_utc": timestamp_us_to_utc(int(np.asarray(origin)[offset + local])),
                    }
                )
                self._global_sample_index += 1
        else:
            self._global_sample_index += int(take)

    def _finalize_current_shard(self) -> None:
        if self._tmp_dir is None:
            return
        for state in self._tensor_files.values():
            state.file.flush()
            os.fsync(state.file.fileno())
            state.file.close()
        if self._shard_samples == 0:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
            self._final_dir = None
            self._tensor_files = {}
            return
        assert self._final_dir is not None
        self._tmp_dir.replace(self._final_dir)
        tensors_meta = {
            name: {
                "path": str((self._final_dir / state.relative_path).relative_to(self.cache_root)),
                "dtype": state.dtype,
                "shape": [int(self._shard_samples), *state.tail_shape],
                "byte_size": int(state.byte_size),
                "sha256": state.sha.hexdigest(),
            }
            for name, state in self._tensor_files.items()
        }
        meta = {
            "format": MATERIALIZED_CACHE_FORMAT,
            "version": MATERIALIZED_CACHE_VERSION,
            "split": self.split,
            "shard_index": self._shard_index,
            "path": str(self._final_dir.relative_to(self.cache_root)),
            "num_samples": int(self._shard_samples),
            "sample_multiple": int(self.sample_multiple),
            "target_shard_bytes": int(self.target_shard_bytes),
            "target_samples": int(self._shard_sample_target),
            "actual_shard_bytes": int(self._shard_bytes),
            "first_origin_timestamp_us": int(self._first_origin_us or 0),
            "last_origin_timestamp_us": int(self._last_origin_us or 0),
            "first_origin_utc": timestamp_us_to_utc(int(self._first_origin_us or 0)),
            "last_origin_utc": timestamp_us_to_utc(int(self._last_origin_us or 0)),
            "first_ticker": self._first_ticker,
            "last_ticker": self._last_ticker,
            "source_blocks": self._source_blocks,
            "tensor_count": len(tensors_meta),
            "tensors": tensors_meta,
            "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        }
        meta_path = self.split_dir / f"shard_{self._shard_index:06d}.json"
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        self.shards.append(meta)
        self._shard_index += 1
        self._shard_samples = 0
        self._shard_bytes = 0
        self._tmp_dir = None
        self._final_dir = None
        self._tensor_files = {}
        self._tensor_order = []


def estimate_shard_samples(*, sample_bytes: int, target_shard_bytes: int, sample_multiple: int) -> MaterializedShardEstimate:
    raw = max(1, int(target_shard_bytes) // max(1, int(sample_bytes)))
    multiple = max(1, int(sample_multiple))
    aligned = (raw // multiple) * multiple
    if aligned <= 0:
        aligned = multiple
    return MaterializedShardEstimate(
        sample_bytes=int(sample_bytes),
        raw_target_samples=int(raw),
        aligned_target_samples=int(aligned),
        target_shard_bytes=int(target_shard_bytes),
        sample_multiple=int(multiple),
    )


def flatten_training_batch(batch: RollingTrainingBatch) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {
        "headers_uint8": np.asarray(batch.headers_uint8),
        "events_uint8": np.asarray(batch.events_uint8),
        "ticker": _encode_ticker_array(batch.ticker),
        "origin_ordinal": np.asarray(batch.origin_ordinal),
        "origin_timestamp_us": np.asarray(batch.origin_timestamp_us),
        "ticker_macro_bars": np.asarray(batch.ticker_macro_bars),
        "ticker_macro_bar_mask": np.asarray(batch.ticker_macro_bar_mask),
        "global_market_bars": np.asarray(batch.global_market_bars),
        "global_market_bar_mask": np.asarray(batch.global_market_bar_mask),
        "future_macro_bars": np.asarray(batch.future_macro_bars),
        "future_macro_bar_mask": np.asarray(batch.future_macro_bar_mask),
        "future_intraday_bars": np.asarray(batch.future_intraday_bars),
        "future_intraday_bar_mask": np.asarray(batch.future_intraday_bar_mask),
    }
    _add_array_group(out, "time_features", batch.time_features)
    _add_array_group(out, "chunk_time_features", batch.chunk_time_features)
    _add_array_group(out, "macro_features", batch.macro_features)
    _add_array_group(out, "global_features", batch.global_features)
    _add_array_group(out, "labels", batch.labels)
    for context_name, group in batch.text_inputs.items():
        _add_array_group(out, f"text_inputs/{context_name}", group)
    _add_array_group(out, "xbrl_inputs", batch.xbrl_inputs)
    return {
        key: value
        for key, value in out.items()
        if isinstance(value, np.ndarray) and value.ndim >= 1 and int(value.shape[0]) > 0
    }


def partition_ready_blocks(blocks: Iterable[RollingReadyIndexBlock], workers: int) -> list[list[RollingReadyIndexBlock]]:
    count = max(1, int(workers))
    partitions: list[list[RollingReadyIndexBlock]] = [[] for _ in range(count)]
    weights = [0 for _ in range(count)]
    for block in sorted(blocks, key=lambda item: item.sample_count, reverse=True):
        index = min(range(count), key=lambda value: weights[value])
        partitions[index].append(block)
        weights[index] += int(block.sample_count)
    return partitions


def cleanup_orphan_materialized_tmp(split_dir: Path) -> int:
    removed = 0
    if not split_dir.exists():
        return 0
    for tmp_dir in sorted(split_dir.glob("shard_*.tmp")):
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            removed += 1
    return removed


def load_existing_materialized_shards(cache_root: Path, split: str) -> list[dict[str, Any]]:
    split_dir = Path(cache_root) / split
    rows: list[dict[str, Any]] = []
    if not split_dir.exists():
        return rows
    for meta_path in sorted(split_dir.glob("shard_*.json")):
        row = json.loads(meta_path.read_text(encoding="utf-8"))
        if row.get("format") != MATERIALIZED_CACHE_FORMAT:
            continue
        if str(row.get("split", "")) != split:
            raise RuntimeError(f"Shard split mismatch in {meta_path}: {row.get('split')!r}")
        shard_dir = Path(cache_root) / str(row.get("path", "")).replace("\\", "/")
        if not shard_dir.exists():
            raise RuntimeError(f"Missing materialized shard directory referenced by {meta_path}: {shard_dir}")
        rows.append(row)
    rows.sort(key=lambda item: int(item["shard_index"]))
    actual = [int(row["shard_index"]) for row in rows]
    expected = list(range(len(rows)))
    if actual != expected:
        raise RuntimeError(f"Cannot resume {split}: shard indices are not contiguous. actual={actual[:10]} expected={expected[:10]}")
    return rows


def timestamp_us_to_utc(timestamp_us: int) -> str:
    if int(timestamp_us) <= 0:
        return ""
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).isoformat()


def _add_array_group(out: dict[str, np.ndarray], prefix: str, group: Mapping[str, np.ndarray]) -> None:
    for name, value in group.items():
        if isinstance(value, np.ndarray):
            out[f"{prefix}/{name}"] = np.asarray(value)


def _safe_tensor_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "__" for ch in name)


def _infer_sample_count(tensors: Mapping[str, np.ndarray]) -> int:
    counts = {int(value.shape[0]) for value in tensors.values() if isinstance(value, np.ndarray) and value.ndim >= 1}
    if not counts:
        return 0
    if len(counts) != 1:
        raise ValueError(f"Tensor sample count mismatch: {sorted(counts)}")
    return counts.pop()


def _encode_ticker_array(values: np.ndarray) -> np.ndarray:
    text = [str(value).upper() for value in np.asarray(values).tolist()]
    width = max(8, min(32, max((len(value.encode("utf-8")) for value in text), default=1)))
    return np.asarray(text, dtype=f"S{width}")


def _decode_ticker_array(values: np.ndarray) -> list[str]:
    out: list[str] = []
    for value in np.asarray(values).tolist():
        if isinstance(value, bytes):
            out.append(value.decode("utf-8", errors="ignore").rstrip("\x00"))
        else:
            out.append(str(value))
    return out


def _directory_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return int(total)


def _estimate_to_dict(value: MaterializedShardEstimate | None) -> dict[str, Any]:
    if value is None:
        return {}
    return {
        "sample_bytes": value.sample_bytes,
        "raw_target_samples": value.raw_target_samples,
        "aligned_target_samples": value.aligned_target_samples,
        "target_shard_bytes": value.target_shard_bytes,
        "sample_multiple": value.sample_multiple,
    }
