from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PACKED_CACHE_FORMAT = "packed_market_block_cache"
PACKED_CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PackedBlockManifest:
    block_id: str
    month: str
    ticker: str
    ticker_dir_name: str
    source_cache_root: str
    event_path: str
    origin_path: str
    label_path: str | None
    event_feature_names: tuple[str, ...]
    event_rows: int
    origin_rows: int
    event_start_index: int
    event_end_index: int
    origin_start_index: int
    origin_end_index: int
    first_origin_timestamp_us: int | None
    last_origin_timestamp_us: int | None
    first_origin_ordinal: int | None
    last_origin_ordinal: int | None
    first_event_ordinal: int | None
    last_event_ordinal: int | None
    created_at_utc: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PackedBlockManifest":
        payload = dict(value)
        payload["event_feature_names"] = tuple(str(v) for v in payload.get("event_feature_names", ()))
        payload["metadata"] = dict(payload.get("metadata") or {})
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class PackedCacheManifest:
    format: str
    schema_version: int
    cache_id: str
    source_cache_root: str
    months: tuple[str, ...]
    block_count: int
    event_rows: int
    origin_rows: int
    created_at_utc: str
    builder: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PackedCacheManifest":
        payload = dict(value)
        payload["months"] = tuple(str(v) for v in payload.get("months", ()))
        payload["builder"] = dict(payload.get("builder") or {})
        return cls(**payload)


@dataclass(slots=True)
class PackedMarketBlock:
    block_manifest: PackedBlockManifest
    events: np.ndarray
    origin_positions: np.ndarray
    origin_ordinals: np.ndarray
    origin_timestamp_us: np.ndarray
    event_ordinals: np.ndarray
    event_timestamp_us: np.ndarray
    labels: dict[str, np.ndarray]
    label_masks: dict[str, np.ndarray]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def origin_count(self) -> int:
        return int(self.origin_positions.shape[0])

    @property
    def event_count(self) -> int:
        return int(self.events.shape[0])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{hashlib.blake2s(str(path).encode(), digest_size=6).hexdigest()}")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")


def stable_cache_fingerprint(root: Path) -> str:
    manifest = root / "manifest.json"
    block_files = sorted(root.glob("month=*/ticker=*/block_*/block_manifest.json"))
    hasher = hashlib.blake2b(digest_size=16)
    if manifest.exists():
        hasher.update(manifest.read_bytes())
    for path in block_files:
        stat = path.stat()
        hasher.update(str(path.relative_to(root)).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("ascii"))
        hasher.update(str(int(stat.st_mtime_ns)).encode("ascii"))
    return hasher.hexdigest()


def ticker_dir_to_symbol(ticker_dir_name: str) -> str:
    if ticker_dir_name.startswith("ticker="):
        ticker_dir_name = ticker_dir_name.split("=", 1)[1]
    try:
        return bytes.fromhex(ticker_dir_name).decode("utf-8")
    except ValueError:
        return ticker_dir_name


def symbol_to_ticker_dir(symbol: str) -> str:
    return str(symbol).encode("utf-8").hex()


def numeric_columns(frame: Any, *, exclude: set[str]) -> list[str]:
    names: list[str] = []
    for name, dtype in zip(frame.columns, frame.dtypes):
        dtype_text = str(dtype).lower()
        if str(name) in exclude:
            continue
        if any(token in dtype_text for token in ("int", "uint", "float", "bool")):
            names.append(str(name))
    return names


def choose_first_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None
