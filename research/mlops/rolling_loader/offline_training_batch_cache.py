from __future__ import annotations

import dataclasses
import gc
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

try:  # pragma: no cover - import failure is reported by the runner.
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception:  # noqa: BLE001
    pa = None
    pq = None


OFFLINE_BATCH_CACHE_FORMAT = "temporal_v3_offline_training_batch_cache"
OFFLINE_BATCH_CACHE_VERSION = 1


@dataclass(slots=True)
class OfflineBatchCacheWriterConfig:
    output_root: Path
    cache_id: str
    batches_per_shard: int = 10
    max_samples_per_segment: int = 0
    compression: str | None = "zstd"
    overwrite: bool = False
    source_cache_root: Path | None = None
    loader_config: Mapping[str, Any] = field(default_factory=dict)
    run_args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OfflineShardStats:
    shard_path: Path
    day: str
    segment_id: int
    shard_id: int
    batches: int
    samples: int
    tensor_count: int
    tensor_payload_bytes: int
    parquet_bytes: int
    started_utc: str
    finished_utc: str
    first_origin: dict[str, Any]
    last_origin: dict[str, Any]


class OfflineBatchCacheWriter:
    def __init__(self, config: OfflineBatchCacheWriterConfig) -> None:
        self.config = config
        self.cache_root = Path(config.output_root) / str(config.cache_id)
        self.logs_dir = self.cache_root / "logs"
        self._current_day = ""
        self._current_segment_id = 0
        self._current_segment_samples = 0
        self._current_shard_id_by_day_segment: dict[tuple[str, int], int] = {}
        self._pending_batches: list[Any] = []
        self._pending_started_utc = ""
        self._shards: list[dict[str, Any]] = []
        self._days: dict[str, dict[str, Any]] = {}
        self._samples = 0
        self._batches = 0
        self._payload_bytes = 0
        self._parquet_bytes = 0
        self._created_utc = _now_iso()

    @property
    def batches(self) -> int:
        return int(self._batches)

    @property
    def samples(self) -> int:
        return int(self._samples)

    @property
    def shards(self) -> int:
        return int(len(self._shards))

    @property
    def tensor_payload_bytes(self) -> int:
        return int(self._payload_bytes)

    @property
    def parquet_bytes(self) -> int:
        return int(self._parquet_bytes)

    @property
    def pending_batches(self) -> int:
        return int(len(self._pending_batches))

    def prepare(self) -> None:
        if self.cache_root.exists():
            if not self.config.overwrite:
                raise FileExistsError(f"Offline batch cache already exists: {self.cache_root}. Use --overwrite to replace it.")
            shutil.rmtree(self.cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.write_root_manifest(status="running")

    def add_batch(self, batch: Any) -> OfflineShardStats | None:
        day = _batch_primary_day(batch)
        if self._pending_batches and day != self._current_day:
            stats = self.flush(status="complete")
            self._start_day(day)
            self._pending_batches.append(batch)
            return stats
        if not self._pending_batches:
            self._start_day(day)
        self._pending_batches.append(batch)
        if len(self._pending_batches) >= max(1, int(self.config.batches_per_shard)):
            return self.flush(status="complete")
        return None

    def flush(self, *, status: str = "complete") -> OfflineShardStats | None:
        if not self._pending_batches:
            return None
        if pa is None or pq is None:
            raise RuntimeError("pyarrow is required to write offline training batch parquet shards.")
        day = self._current_day or _batch_primary_day(self._pending_batches[0])
        segment_id = int(self._current_segment_id)
        key = (day, segment_id)
        shard_id = int(self._current_shard_id_by_day_segment.get(key, 0))
        self._current_shard_id_by_day_segment[key] = shard_id + 1
        rel_dir = Path(f"month={day[:7]}") / f"day={day}" / f"segment={segment_id:06d}" / f"shard={shard_id:06d}"
        final_dir = self.cache_root / rel_dir
        tmp_dir = self.cache_root / f".tmp_{day}_{segment_id:06d}_{shard_id:06d}_{os.getpid()}_{int(time.time() * 1000)}"
        if final_dir.exists():
            if not self.config.overwrite:
                raise FileExistsError(f"Shard already exists: {final_dir}")
            shutil.rmtree(final_dir)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=False)

        pending = list(self._pending_batches)
        tensor_rows_by_name: dict[str, list[dict[str, Any]]] = {}
        batch_summaries: list[dict[str, Any]] = []
        tensor_manifest: dict[str, dict[str, Any]] = {}
        total_payload_bytes = 0
        started_utc = self._pending_started_utc or _now_iso()
        try:
            for batch_index, batch in enumerate(pending):
                flat = flatten_batch(batch)
                batch_summaries.append(_batch_summary(batch, batch_index=batch_index))
                for name, value in flat.items():
                    if isinstance(value, np.ndarray):
                        encoded = _encode_array(value)
                        row = {
                            "batch_index": batch_index,
                            "encoding": encoded["encoding"],
                            "dtype": encoded["dtype"],
                            "shape": json.dumps(encoded["shape"], separators=(",", ":")),
                            "payload_nbytes": int(encoded["payload_nbytes"]),
                            "sha256": encoded["sha256"],
                            "data": encoded["data"],
                        }
                        tensor_rows_by_name.setdefault(name, []).append(row)
                        total_payload_bytes += int(encoded["payload_nbytes"])
                    else:
                        # Non-array fields are preserved in the per-batch manifest.
                        batch_summaries[-1].setdefault("metadata", {})[name] = _jsonable(value)

            tensors_root = tmp_dir / "tensors"
            tensors_root.mkdir(parents=True, exist_ok=True)
            parquet_bytes = 0
            for name in sorted(tensor_rows_by_name):
                rows = tensor_rows_by_name[name]
                rel_path = Path("tensors") / _tensor_rel_path(name)
                path = tmp_dir / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                sha = hashlib.sha256()
                for row in rows:
                    sha.update(str(row["batch_index"]).encode("ascii"))
                    sha.update(str(row["shape"]).encode("utf-8"))
                    sha.update(row["data"])
                table = pa.table(
                    {
                        "batch_index": pa.array([int(row["batch_index"]) for row in rows], type=pa.int32()),
                        "encoding": pa.array([str(row["encoding"]) for row in rows], type=pa.string()),
                        "dtype": pa.array([str(row["dtype"]) for row in rows], type=pa.string()),
                        "shape": pa.array([str(row["shape"]) for row in rows], type=pa.string()),
                        "payload_nbytes": pa.array([int(row["payload_nbytes"]) for row in rows], type=pa.int64()),
                        "sha256": pa.array([str(row["sha256"]) for row in rows], type=pa.string()),
                        "data": pa.array([row["data"] for row in rows], type=pa.binary()),
                    }
                )
                pq.write_table(table, path, compression=self.config.compression)
                size = int(path.stat().st_size)
                parquet_bytes += size
                tensor_manifest[name] = {
                    "path": _as_posix(rel_path),
                    "rows": len(rows),
                    "parquet_bytes": size,
                    "payload_bytes": int(sum(int(row["payload_nbytes"]) for row in rows)),
                    "dtype_values": sorted({str(row["dtype"]) for row in rows}),
                    "encoding_values": sorted({str(row["encoding"]) for row in rows}),
                    "shape_values": sorted({str(row["shape"]) for row in rows}),
                    "sha256": sha.hexdigest(),
                }

            sample_count = int(sum(int(getattr(batch, "sample_count", 0) or 0) for batch in pending))
            first_origin = dict(batch_summaries[0].get("first_origin") or {})
            last_origin = dict(batch_summaries[-1].get("last_origin") or {})
            shard_manifest = {
                "format": OFFLINE_BATCH_CACHE_FORMAT,
                "version": OFFLINE_BATCH_CACHE_VERSION,
                "status": status,
                "cache_id": str(self.config.cache_id),
                "source_cache_root": str(self.config.source_cache_root or ""),
                "relative_path": _as_posix(rel_dir),
                "day": day,
                "segment_id": segment_id,
                "shard_id": shard_id,
                "batches": len(pending),
                "samples": sample_count,
                "tensor_count": len(tensor_manifest),
                "tensor_payload_bytes": int(total_payload_bytes),
                "parquet_bytes": int(parquet_bytes),
                "started_utc": started_utc,
                "finished_utc": _now_iso(),
                "first_origin": first_origin,
                "last_origin": last_origin,
                "source_dates_present": sorted({str(summary.get("primary_day") or "") for summary in batch_summaries if summary.get("primary_day")}),
                "batches_summary": batch_summaries,
                "tensors": tensor_manifest,
            }
            _write_json(tmp_dir / "shard_manifest.json", shard_manifest)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir.rename(final_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        finally:
            self._pending_batches.clear()
            del pending
            gc.collect()

        stats = OfflineShardStats(
            shard_path=final_dir,
            day=day,
            segment_id=segment_id,
            shard_id=shard_id,
            batches=int(shard_manifest["batches"]),
            samples=int(shard_manifest["samples"]),
            tensor_count=int(shard_manifest["tensor_count"]),
            tensor_payload_bytes=int(shard_manifest["tensor_payload_bytes"]),
            parquet_bytes=int(shard_manifest["parquet_bytes"]),
            started_utc=str(shard_manifest["started_utc"]),
            finished_utc=str(shard_manifest["finished_utc"]),
            first_origin=first_origin,
            last_origin=last_origin,
        )
        self._record_shard(stats, rel_dir)
        self._maybe_advance_segment(day, stats.samples)
        self.write_root_manifest(status="running")
        return stats

    def write_root_manifest(self, *, status: str) -> None:
        manifest = {
            "format": OFFLINE_BATCH_CACHE_FORMAT,
            "version": OFFLINE_BATCH_CACHE_VERSION,
            "status": status,
            "cache_id": str(self.config.cache_id),
            "cache_root": str(self.cache_root),
            "source_cache_root": str(self.config.source_cache_root or ""),
            "created_utc": self._created_utc,
            "updated_utc": _now_iso(),
            "batches_per_shard": int(self.config.batches_per_shard),
            "max_samples_per_segment": int(self.config.max_samples_per_segment),
            "compression": str(self.config.compression or "none"),
            "batches": int(self._batches),
            "samples": int(self._samples),
            "shards": len(self._shards),
            "tensor_payload_bytes": int(self._payload_bytes),
            "parquet_bytes": int(self._parquet_bytes),
            "days": self._days,
            "shards_index": self._shards,
            "loader_config": _jsonable(self.config.loader_config),
            "run_args": _jsonable(self.config.run_args),
        }
        _write_json(self.cache_root / "manifest.json", manifest)

    def _start_day(self, day: str) -> None:
        if self._current_day != day:
            self._current_day = day
            self._current_segment_id = 0
            self._current_segment_samples = 0
        self._pending_started_utc = _now_iso()

    def _maybe_advance_segment(self, day: str, shard_samples: int) -> None:
        self._current_segment_samples += int(shard_samples)
        cap = int(self.config.max_samples_per_segment)
        if cap > 0 and self._current_segment_samples >= cap:
            self._current_segment_id += 1
            self._current_segment_samples = 0
            self._current_day = day

    def _record_shard(self, stats: OfflineShardStats, rel_dir: Path) -> None:
        self._batches += int(stats.batches)
        self._samples += int(stats.samples)
        self._payload_bytes += int(stats.tensor_payload_bytes)
        self._parquet_bytes += int(stats.parquet_bytes)
        row = {
            "relative_path": _as_posix(rel_dir),
            "day": stats.day,
            "segment_id": int(stats.segment_id),
            "shard_id": int(stats.shard_id),
            "batches": int(stats.batches),
            "samples": int(stats.samples),
            "tensor_count": int(stats.tensor_count),
            "tensor_payload_bytes": int(stats.tensor_payload_bytes),
            "parquet_bytes": int(stats.parquet_bytes),
            "started_utc": stats.started_utc,
            "finished_utc": stats.finished_utc,
            "first_origin": stats.first_origin,
            "last_origin": stats.last_origin,
        }
        self._shards.append(row)
        day_row = self._days.setdefault(
            stats.day,
            {"batches": 0, "samples": 0, "shards": 0, "parquet_bytes": 0, "tensor_payload_bytes": 0},
        )
        day_row["batches"] = int(day_row.get("batches", 0)) + int(stats.batches)
        day_row["samples"] = int(day_row.get("samples", 0)) + int(stats.samples)
        day_row["shards"] = int(day_row.get("shards", 0)) + 1
        day_row["parquet_bytes"] = int(day_row.get("parquet_bytes", 0)) + int(stats.parquet_bytes)
        day_row["tensor_payload_bytes"] = int(day_row.get("tensor_payload_bytes", 0)) + int(stats.tensor_payload_bytes)
        day_row["first_origin"] = day_row.get("first_origin") or stats.first_origin
        day_row["last_origin"] = stats.last_origin
        _append_jsonl(self.logs_dir / "shards.jsonl", row)


def flatten_batch(batch: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _flatten_value("", batch, out)
    return out


def load_shard_tensor_rows(shard_dir: Path, tensor_name: str) -> list[dict[str, Any]]:
    manifest = read_json(Path(shard_dir) / "shard_manifest.json")
    tensor = dict((manifest.get("tensors") or {}).get(tensor_name) or {})
    if not tensor:
        raise KeyError(f"Tensor {tensor_name!r} not found in {shard_dir}")
    path = Path(shard_dir) / str(tensor["path"])
    if pq is None:
        raise RuntimeError("pyarrow is required to read offline training batch parquet shards.")
    table = pq.read_table(path)
    rows: list[dict[str, Any]] = []
    for record in table.to_pylist():
        rows.append(
            {
                "batch_index": int(record["batch_index"]),
                "array": decode_array(
                    data=bytes(record["data"]),
                    encoding=str(record["encoding"]),
                    dtype=str(record["dtype"]),
                    shape=json.loads(str(record["shape"])),
                    expected_sha256=str(record["sha256"]),
                ),
            }
        )
    return rows


def decode_array(*, data: bytes, encoding: str, dtype: str, shape: Iterable[int], expected_sha256: str = "") -> np.ndarray:
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError("Tensor row checksum mismatch.")
    if encoding == "json":
        return np.asarray(json.loads(data.decode("utf-8")), dtype=object).reshape(tuple(int(v) for v in shape))
    if encoding != "raw":
        raise ValueError(f"Unsupported tensor encoding: {encoding}")
    array = np.frombuffer(data, dtype=np.dtype(dtype)).copy()
    return array.reshape(tuple(int(v) for v in shape))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _flatten_value(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, np.ndarray):
        out[prefix] = value
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            name = _join(prefix, field.name)
            _flatten_value(name, getattr(value, field.name), out)
        return
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item)):
            _flatten_value(_join(prefix, str(key)), value[key], out)
        return
    if isinstance(value, tuple):
        if _is_jsonable_sequence(value):
            out[prefix] = [_jsonable(item) for item in value]
        else:
            for idx, item in enumerate(value):
                _flatten_value(_join(prefix, str(idx)), item, out)
        return
    if isinstance(value, list):
        if _is_jsonable_sequence(value):
            out[prefix] = [_jsonable(item) for item in value]
        else:
            for idx, item in enumerate(value):
                _flatten_value(_join(prefix, str(idx)), item, out)
        return
    out[prefix] = _jsonable(value)


def _encode_array(value: np.ndarray) -> dict[str, Any]:
    array = np.asarray(value)
    shape = [int(dim) for dim in array.shape]
    if array.dtype == object or np.issubdtype(array.dtype, np.str_) or np.issubdtype(array.dtype, np.bytes_):
        payload = json.dumps(array.tolist(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return {
            "encoding": "json",
            "dtype": str(array.dtype),
            "shape": shape,
            "payload_nbytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "data": payload,
        }
    contiguous = np.ascontiguousarray(array)
    if contiguous.dtype.byteorder == ">":
        contiguous = contiguous.byteswap().newbyteorder("<")
    payload = contiguous.tobytes(order="C")
    return {
        "encoding": "raw",
        "dtype": str(contiguous.dtype),
        "shape": shape,
        "payload_nbytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "data": payload,
    }


def _tensor_rel_path(name: str) -> Path:
    parts = [_safe_path_segment(part) for part in str(name).split("/") if part]
    if not parts:
        parts = ["root"]
    parts[-1] = f"{parts[-1]}.parquet"
    return Path(*parts)


def _safe_path_segment(value: str) -> str:
    out = []
    for char in str(value):
        if char.isalnum() or char in ("-", "_", "."):
            out.append(char)
        else:
            out.append("_")
    return "".join(out) or "unnamed"


def _batch_primary_day(batch: Any) -> str:
    timestamps = np.asarray(getattr(batch, "origin_timestamp_us", np.asarray([], dtype=np.int64)))
    if timestamps.size == 0:
        return "unknown"
    timestamp_us = int(timestamps[0])
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc).date().isoformat()


def _batch_summary(batch: Any, *, batch_index: int) -> dict[str, Any]:
    timestamps = np.asarray(getattr(batch, "origin_timestamp_us", np.asarray([], dtype=np.int64)))
    ordinals = np.asarray(getattr(batch, "origin_ordinal", np.asarray([], dtype=np.int64)))
    tickers = np.asarray(getattr(batch, "ticker", np.asarray([], dtype=object)))
    days = []
    if timestamps.size:
        days = sorted({datetime.fromtimestamp(int(ts) / 1_000_000, tz=timezone.utc).date().isoformat() for ts in timestamps})
    return {
        "batch_index": int(batch_index),
        "samples": int(getattr(batch, "sample_count", 0) or 0),
        "primary_day": days[0] if days else "unknown",
        "source_dates_present": days,
        "first_origin": _origin_key(tickers, ordinals, timestamps, 0),
        "last_origin": _origin_key(tickers, ordinals, timestamps, int(timestamps.size) - 1),
        "min_timestamp_us": int(timestamps.min()) if timestamps.size else 0,
        "max_timestamp_us": int(timestamps.max()) if timestamps.size else 0,
        "unique_tickers": int(len(set(str(v) for v in tickers.tolist()))) if tickers.size else 0,
        "profile": _jsonable(getattr(batch, "profile", {}) or {}),
    }


def _origin_key(tickers: np.ndarray, ordinals: np.ndarray, timestamps: np.ndarray, idx: int) -> dict[str, Any]:
    if idx < 0 or idx >= int(timestamps.size):
        return {}
    timestamp_us = int(timestamps[idx])
    return {
        "ticker": str(tickers[idx]) if tickers.size > idx else "",
        "origin_ordinal": int(ordinals[idx]) if ordinals.size > idx else 0,
        "origin_timestamp_us": timestamp_us,
        "origin_utc": datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc).isoformat(timespec="microseconds"),
    }


def _is_jsonable_sequence(value: Iterable[Any]) -> bool:
    for item in value:
        if not _is_jsonable_scalar(item):
            return False
    return True


def _is_jsonable_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, np.integer, np.floating, np.bool_))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _join(prefix: str, name: str) -> str:
    if not prefix:
        return str(name)
    return f"{prefix}/{name}"


def _as_posix(path: Path) -> str:
    return Path(path).as_posix()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
