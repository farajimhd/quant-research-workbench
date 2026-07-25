from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from research.news_reaction_model.v16.config import LoaderConfig
from research.news_reaction_model.v16.market_context import (
    CURRENT_MARKET_RETURN_INDICES,
    MARKET_NEWS_RETURN_INDICES,
    MARKET_RETURN_LIMIT,
)


MANIFEST_FILE = "manifest.json"
BUILD_STATE_FILE = "build_state.json"
ARRAY_FILES = {
    "openai_embedding": "openai_embedding.f32.npy",
    "stock_state": "stock_state.f32.npy",
    "time_features": "time_features.f32.npy",
    "return_targets": "return_targets.f32.npy",
    "label_mask": "label_mask.bool.npy",
    "context_indices": "context_indices.i32.npy",
    "context_features": "context_features.f32.npy",
    "context_mask": "context_mask.bool.npy",
    "current_market_features": "current_market_features.returnlog.f32.npy",
    "market_context_indices": "market_context_indices.i32.npy",
    "market_context_features": "market_context_features.returnlog.f16.npy",
    "market_context_mask": "market_context_mask.bool.npy",
    "market_leader_features": "market_leader_features.returnlog.f16.npy",
    "market_leader_mask": "market_leader_mask.bool.npy",
    "canonical_news_id": "canonical_news_id.s64.npy",
    "ticker": "ticker.s32.npy",
    "published_at_utc": "published_at_utc.s40.npy",
    "published_at_us": "published_at_us.i64.npy",
    "publication_session": "publication_session.s16.npy",
}
LEGACY_MARKET_ARRAY_FILES = {
    "current_market_features": "current_market_features.f32.npy",
    "market_context_features": "market_context_features.f16.npy",
    "market_leader_features": "market_leader_features.f16.npy",
}
MARKET_RETURN_INDICES = {
    "current_market_features": CURRENT_MARKET_RETURN_INDICES,
    "market_context_features": MARKET_NEWS_RETURN_INDICES,
    "market_leader_features": CURRENT_MARKET_RETURN_INDICES,
}


def expected_shapes(config: LoaderConfig, rows: int) -> dict[str, tuple[int, ...]]:
    horizons = len(config.horizons)
    return {
        "openai_embedding": (rows, config.openai_embedding_dim),
        "stock_state": (rows, config.stock_state_dim),
        "time_features": (rows, config.time_feature_dim),
        "return_targets": (rows, horizons, 3),
        "label_mask": (rows, horizons),
        "context_indices": (rows, config.context_size),
        "context_features": (rows, config.context_size, config.context_feature_dim),
        "context_mask": (rows, config.context_size),
        "current_market_features": (rows, config.current_market_feature_dim),
        "market_context_indices": (rows, config.market_context_size),
        "market_context_features": (
            rows,
            config.market_context_size,
            config.market_news_feature_dim,
        ),
        "market_context_mask": (rows, config.market_context_size),
        "market_leader_features": (
            rows,
            config.market_leader_size,
            config.market_leader_feature_dim,
        ),
        "market_leader_mask": (rows, config.market_leader_size),
        "canonical_news_id": (rows,),
        "ticker": (rows,),
        "published_at_utc": (rows,),
        "published_at_us": (rows,),
        "publication_session": (rows,),
    }


def expected_dtypes() -> dict[str, np.dtype[Any]]:
    return {
        "openai_embedding": np.dtype("<f4"),
        "stock_state": np.dtype("<f4"),
        "time_features": np.dtype("<f4"),
        "return_targets": np.dtype("<f4"),
        "label_mask": np.dtype("?"),
        "context_indices": np.dtype("<i4"),
        "context_features": np.dtype("<f4"),
        "context_mask": np.dtype("?"),
        "current_market_features": np.dtype("<f4"),
        "market_context_indices": np.dtype("<i4"),
        "market_context_features": np.dtype("<f2"),
        "market_context_mask": np.dtype("?"),
        "market_leader_features": np.dtype("<f2"),
        "market_leader_mask": np.dtype("?"),
        "canonical_news_id": np.dtype("S64"),
        "ticker": np.dtype("S32"),
        "published_at_utc": np.dtype("S40"),
        "published_at_us": np.dtype("<i8"),
        "publication_session": np.dtype("S16"),
    }


def expected_storage_bytes(config: LoaderConfig, rows: int) -> int:
    shapes = expected_shapes(config, rows)
    dtypes = expected_dtypes()
    return int(
        sum(
            int(np.prod(shape, dtype=np.int64)) * int(dtypes[name].itemsize)
            for name, shape in shapes.items()
        )
    )


def migrate_legacy_market_return_arrays(
    config: LoaderConfig,
    rows: int,
    *,
    chunk_rows: int = 256,
) -> None:
    """Migrate completed or resumable raw-return arrays without rebuilding V16.

    Every destination is written to a separate memmap and atomically promoted
    before its legacy source is removed. An interrupted migration can therefore
    restart the active array while retaining every previously completed array.
    """
    root = config.prepared_dataset_root
    shapes = expected_shapes(config, rows)
    dtypes = expected_dtypes()
    legacy_sizes = [
        (root / legacy_filename).stat().st_size
        for name, legacy_filename in LEGACY_MARKET_ARRAY_FILES.items()
        if (root / legacy_filename).exists()
        and not (root / ARRAY_FILES[name]).exists()
    ]
    required_extra = max(legacy_sizes, default=0)
    free = int(shutil.disk_usage(root).free)
    if free < int(required_extra * 1.05):
        raise RuntimeError(
            "V16 return-encoding migration requires enough temporary space for "
            f"the largest market array ({required_extra / 2**30:.1f} GiB), but "
            f"only {free / 2**30:.1f} GiB is free under {root}."
        )

    for name, legacy_filename in LEGACY_MARKET_ARRAY_FILES.items():
        legacy_path = root / legacy_filename
        destination = root / ARRAY_FILES[name]
        temporary = destination.with_suffix(destination.suffix + ".migrating")
        if destination.exists():
            migrated = np.load(destination, mmap_mode="r", allow_pickle=False)
            if migrated.shape != shapes[name] or migrated.dtype != dtypes[name]:
                raise RuntimeError(
                    f"Migrated V16 array {destination} has shape/dtype "
                    f"{migrated.shape}/{migrated.dtype}; expected "
                    f"{shapes[name]}/{dtypes[name]}."
                )
            mmap = getattr(migrated, "_mmap", None)
            if mmap is not None:
                mmap.close()
            del migrated
            if legacy_path.exists():
                legacy_path.unlink()
            if temporary.exists():
                temporary.unlink()
            print(f"MIGRATION READY | {name}", flush=True)
            continue
        if not legacy_path.exists():
            raise RuntimeError(
                f"Cannot migrate V16 {name}: neither {legacy_path} nor "
                f"{destination} exists."
            )
        if temporary.exists():
            temporary.unlink()

        source = np.load(legacy_path, mmap_mode="r", allow_pickle=False)
        if source.shape != shapes[name] or source.dtype != dtypes[name]:
            raise RuntimeError(
                f"Legacy V16 array {legacy_path} has shape/dtype "
                f"{source.shape}/{source.dtype}; expected "
                f"{shapes[name]}/{dtypes[name]}."
            )
        target = np.lib.format.open_memmap(
            temporary,
            mode="w+",
            dtype=dtypes[name],
            shape=shapes[name],
        )
        indices = np.asarray(MARKET_RETURN_INDICES[name], dtype=np.int64)
        next_report = 0.1
        for start in range(0, rows, max(1, int(chunk_rows))):
            end = min(rows, start + max(1, int(chunk_rows)))
            block = np.asarray(source[start:end]).copy()
            raw = block[..., indices].astype(np.float32, copy=False)
            if np.isnan(raw).any():
                coordinates = np.argwhere(np.isnan(raw))[0]
                raise RuntimeError(
                    f"V16 {name} contains NaN at migration chunk row "
                    f"{start + int(coordinates[0])}; refusing lossy repair."
                )
            encoded = np.sign(raw) * np.minimum(
                np.log1p(np.abs(raw)),
                np.float32(MARKET_RETURN_LIMIT),
            )
            if not np.isfinite(encoded).all():
                raise RuntimeError(
                    f"V16 {name} return migration produced non-finite values."
                )
            block[..., indices] = encoded.astype(block.dtype, copy=False)
            target[start:end] = block
            fraction = end / rows
            if fraction >= next_report or end == rows:
                print(
                    f"MIGRATING {name} | {end:,}/{rows:,} "
                    f"({fraction:.0%})",
                    flush=True,
                )
                next_report += 0.1
        target.flush()
        del target
        source_mmap = getattr(source, "_mmap", None)
        if source_mmap is not None:
            source_mmap.close()
        del source
        temporary.replace(destination)
        legacy_path.unlink()
        print(f"MIGRATED | {name} -> {destination.name}", flush=True)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_arrays(config: LoaderConfig, rows: int) -> dict[str, np.memmap]:
    root = config.prepared_dataset_root
    root.mkdir(parents=True, exist_ok=True)
    required = expected_storage_bytes(config, rows)
    free = int(shutil.disk_usage(root).free)
    if free < int(required * 1.10):
        raise RuntimeError(
            f"V16 prepared arrays require {required / 2**30:.1f} GiB plus safety "
            f"margin, but {free / 2**30:.1f} GiB is free under {root}."
        )
    shapes = expected_shapes(config, rows)
    dtypes = expected_dtypes()
    arrays: dict[str, np.memmap] = {}
    for name, filename in ARRAY_FILES.items():
        arrays[name] = np.lib.format.open_memmap(
            root / filename,
            mode="w+",
            dtype=dtypes[name],
            shape=shapes[name],
        )
    arrays["context_indices"].fill(-1)
    arrays["context_features"].fill(0)
    arrays["context_mask"].fill(False)
    arrays["market_context_indices"].fill(-1)
    arrays["market_context_features"].fill(0)
    arrays["market_context_mask"].fill(False)
    arrays["market_leader_features"].fill(0)
    arrays["market_leader_mask"].fill(False)
    return arrays


def open_arrays(
    config: LoaderConfig,
    *,
    mode: str = "r",
    require_complete: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    manifest_path = config.prepared_dataset_root / MANIFEST_FILE
    if not manifest_path.exists():
        raise RuntimeError(
            f"Missing V16 prepared manifest {manifest_path}. "
            "Run python -m research.news_reaction_model.v16.run_prepare_data --execute first."
        )
    manifest = load_json(manifest_path)
    if require_complete and manifest.get("status") != "complete":
        raise RuntimeError(f"V16 prepared dataset is not complete: {manifest}")
    if manifest.get("dataset_version") != config.prepared_dataset_version:
        raise RuntimeError(
            "V16 prepared dataset version mismatch: "
            f"{manifest.get('dataset_version')!r} != {config.prepared_dataset_version!r}."
        )
    rows = int(manifest.get("rows") or 0)
    if rows <= 0:
        raise RuntimeError(f"V16 prepared dataset has invalid row count: {rows}.")
    expected = expected_shapes(config, rows)
    dtypes = expected_dtypes()
    arrays: dict[str, np.ndarray] = {}
    for name, filename in ARRAY_FILES.items():
        path = config.prepared_dataset_root / filename
        if not path.exists():
            raise RuntimeError(f"Missing V16 prepared array: {path}.")
        array = np.load(path, mmap_mode=mode, allow_pickle=False)
        if array.shape != expected[name] or array.dtype != dtypes[name]:
            raise RuntimeError(
                f"Prepared array {name} has shape/dtype {array.shape}/{array.dtype}; "
                f"expected {expected[name]}/{dtypes[name]}."
            )
        arrays[name] = array
    return arrays, manifest


def close_arrays(arrays: dict[str, np.ndarray]) -> None:
    """Release NumPy memory maps deterministically, including on Windows."""
    for array in arrays.values():
        mmap = getattr(array, "_mmap", None)
        if mmap is not None:
            mmap.close()
    arrays.clear()


@dataclass(slots=True)
class PreparedAudit:
    rows: int
    train_rows: int
    validation_rows: int
    context_rows: int
    context_slots: int
    causal_reaction_slots: int
    representation_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "train_rows": self.train_rows,
            "validation_rows": self.validation_rows,
            "context_rows": self.context_rows,
            "context_slots": self.context_slots,
            "causal_reaction_slots": self.causal_reaction_slots,
            "representation_sha256": self.representation_sha256,
        }
