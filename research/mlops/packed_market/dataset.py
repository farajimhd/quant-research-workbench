from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import polars as pl

from research.mlops.packed_market.cache import (
    PACKED_CACHE_FORMAT,
    PackedBlockManifest,
    PackedCacheManifest,
    PackedMarketBlock,
    choose_first_column,
    read_json,
    stable_cache_fingerprint,
)


@dataclass(slots=True)
class PackedMarketDatasetConfig:
    cache_root: Path
    months: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    shuffle_blocks: bool = False
    seed: int = 17
    max_blocks: int = 0
    strict: bool = True


@dataclass(slots=True)
class PackedMarketDatasetState:
    block_index: int = 0
    epoch: int = 0
    emitted_blocks: int = 0
    emitted_origins: int = 0
    manifest_fingerprint: str = ""

    def to_dict(self) -> dict[str, int | str]:
        return {
            "block_index": int(self.block_index),
            "epoch": int(self.epoch),
            "emitted_blocks": int(self.emitted_blocks),
            "emitted_origins": int(self.emitted_origins),
            "manifest_fingerprint": str(self.manifest_fingerprint),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "PackedMarketDatasetState":
        return cls(
            block_index=int(value.get("block_index", 0) or 0),
            epoch=int(value.get("epoch", 0) or 0),
            emitted_blocks=int(value.get("emitted_blocks", 0) or 0),
            emitted_origins=int(value.get("emitted_origins", 0) or 0),
            manifest_fingerprint=str(value.get("manifest_fingerprint", "") or ""),
        )


class PackedMarketDataset:
    def __init__(self, config: PackedMarketDatasetConfig) -> None:
        self.config = config
        self.cache_root = Path(config.cache_root)
        self.manifest = self._read_manifest()
        self.fingerprint = stable_cache_fingerprint(self.cache_root)
        self.blocks = self._discover_blocks()
        self.state = PackedMarketDatasetState(manifest_fingerprint=self.fingerprint)
        if not self.blocks and config.strict:
            raise RuntimeError(f"No packed market blocks found in {self.cache_root}")

    def _read_manifest(self) -> PackedCacheManifest | None:
        path = self.cache_root / "manifest.json"
        if not path.exists():
            if self.config.strict:
                raise RuntimeError(f"Missing packed cache manifest: {path}")
            return None
        raw = read_json(path)
        if raw.get("format") != PACKED_CACHE_FORMAT and self.config.strict:
            raise RuntimeError(f"Unsupported cache format in {path}: {raw.get('format')!r}")
        return PackedCacheManifest.from_dict(raw)

    def _discover_blocks(self) -> list[PackedBlockManifest]:
        selected_months = set(self.config.months)
        selected_tickers = {ticker.upper() for ticker in self.config.tickers}
        manifests: list[PackedBlockManifest] = []
        for path in sorted(self.cache_root.glob("month=*/ticker=*/block_*/block_manifest.json")):
            item = PackedBlockManifest.from_dict(read_json(path))
            if selected_months and item.month not in selected_months:
                continue
            if selected_tickers and item.ticker.upper() not in selected_tickers:
                continue
            manifests.append(item)
        if self.config.shuffle_blocks:
            rng = random.Random(int(self.config.seed))
            rng.shuffle(manifests)
        if int(self.config.max_blocks) > 0:
            manifests = manifests[: int(self.config.max_blocks)]
        return manifests

    def state_dict(self) -> dict[str, int | str]:
        return self.state.to_dict()

    def load_state_dict(self, value: dict[str, object]) -> None:
        state = PackedMarketDatasetState.from_dict(value)
        if state.manifest_fingerprint and state.manifest_fingerprint != self.fingerprint:
            raise ValueError("Packed market dataset fingerprint changed; refusing to resume with mismatched cache.")
        state.manifest_fingerprint = self.fingerprint
        self.state = state

    def telemetry_snapshot(self) -> dict[str, float | str]:
        total_origins = sum(int(block.origin_rows) for block in self.blocks)
        total_events = sum(int(block.event_rows) for block in self.blocks)
        return {
            "loader/state/phase": "packed_block_stream",
            "loader/cache/block_count": float(len(self.blocks)),
            "loader/cache/origin_count": float(total_origins),
            "loader/cache/event_rows": float(total_events),
            "loader/state/block_index": float(self.state.block_index),
            "loader/state/emitted_blocks": float(self.state.emitted_blocks),
            "loader/state/emitted_origins": float(self.state.emitted_origins),
            "loader/state/epoch": float(self.state.epoch),
        }

    def iter_blocks(self, *, repeat: bool = False) -> Iterator[PackedMarketBlock]:
        while True:
            while int(self.state.block_index) < len(self.blocks):
                manifest = self.blocks[int(self.state.block_index)]
                block = load_packed_block(self.cache_root, manifest)
                self.state.block_index += 1
                self.state.emitted_blocks += 1
                self.state.emitted_origins += int(block.origin_count)
                yield block
            if not repeat:
                return
            self.state.block_index = 0
            self.state.epoch += 1


def load_packed_block(cache_root: Path, manifest: PackedBlockManifest) -> PackedMarketBlock:
    event_path = cache_root / manifest.event_path
    origin_path = cache_root / manifest.origin_path
    label_path = cache_root / manifest.label_path if manifest.label_path else None
    event_start = int(getattr(manifest, "event_start_index", 0) or 0)
    event_end = int(getattr(manifest, "event_end_index", 0) or 0)
    event_len = max(0, event_end - event_start)
    events_df = pl.scan_parquet(event_path).slice(event_start, event_len).collect() if event_len > 0 else pl.read_parquet(event_path)
    origins_df = pl.read_parquet(origin_path)
    labels_df = pl.read_parquet(label_path) if label_path is not None and label_path.exists() else None
    event_names = tuple(manifest.event_feature_names)
    if not event_names:
        event_names = tuple(
            name
            for name in events_df.columns
            if name
            not in {
                "ordinal",
                "origin_ordinal",
                "timestamp_us",
                "sip_timestamp_us",
                "origin_timestamp_us",
                "origin_position",
                "origin_event_index",
            }
            and _is_numeric(events_df[name].dtype)
        )
    events = events_df.select(list(event_names)).to_numpy().astype(np.float32, copy=False)
    event_cols = set(events_df.columns)
    origin_cols = set(origins_df.columns)
    event_ordinal_col = choose_first_column(event_cols, ("ordinal", "event_ordinal"))
    event_time_col = choose_first_column(event_cols, ("timestamp_us", "sip_timestamp_us"))
    origin_ordinal_col = choose_first_column(origin_cols, ("origin_ordinal", "ordinal"))
    origin_time_col = choose_first_column(origin_cols, ("origin_timestamp_us", "timestamp_us", "sip_timestamp_us"))
    position_col = choose_first_column(origin_cols, ("origin_event_index", "origin_position", "event_index"))
    if event_ordinal_col is None or event_time_col is None or origin_ordinal_col is None or origin_time_col is None or position_col is None:
        raise RuntimeError(f"Packed block is missing required identity columns: {manifest.block_id}")
    labels, masks = _labels_from_frame(labels_df, origin_count=origins_df.height)
    origin_positions = origins_df[position_col].to_numpy().astype(np.int64, copy=False) - int(event_start)
    valid_positions = (origin_positions >= 0) & (origin_positions < int(events_df.height))
    if not bool(valid_positions.all()):
        raise RuntimeError(f"Origin positions are outside the event slice for packed block {manifest.block_id}.")
    return PackedMarketBlock(
        block_manifest=manifest,
        events=events,
        origin_positions=origin_positions,
        origin_ordinals=origins_df[origin_ordinal_col].to_numpy().astype(np.int64, copy=False),
        origin_timestamp_us=origins_df[origin_time_col].to_numpy().astype(np.int64, copy=False),
        event_ordinals=events_df[event_ordinal_col].to_numpy().astype(np.int64, copy=False),
        event_timestamp_us=events_df[event_time_col].to_numpy().astype(np.int64, copy=False),
        labels=labels,
        label_masks=masks,
        metadata={"ticker": manifest.ticker, "month": manifest.month, "block_id": manifest.block_id},
    )


def _labels_from_frame(frame: pl.DataFrame | None, *, origin_count: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    labels: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    if frame is None or frame.is_empty():
        masks["available"] = np.zeros((origin_count,), dtype=np.bool_)
        return labels, masks
    exclude = {
        "ordinal",
        "origin_ordinal",
        "timestamp_us",
        "origin_timestamp_us",
        "sip_timestamp_us",
        "origin_event_index",
        "origin_position",
    }
    for name in frame.columns:
        if name in exclude or not _is_numeric(frame[name].dtype):
            continue
        array = frame[name].to_numpy()
        if str(frame[name].dtype).lower() == "boolean":
            labels[name] = array.astype(np.float32, copy=False)
            masks[name] = np.ones_like(labels[name], dtype=np.bool_)
        else:
            labels[name] = array.astype(np.float32, copy=False)
            masks[name] = np.isfinite(labels[name])
    if "available" in frame.columns:
        masks["available"] = frame["available"].to_numpy().astype(np.bool_, copy=False)
    elif labels:
        masks["available"] = np.ones((origin_count,), dtype=np.bool_)
    else:
        masks["available"] = np.zeros((origin_count,), dtype=np.bool_)
    return labels, masks


def _is_numeric(dtype: object) -> bool:
    dtype_text = str(dtype).lower()
    return any(token in dtype_text for token in ("int", "uint", "float", "bool"))
