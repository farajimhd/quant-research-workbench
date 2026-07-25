from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from research.news_reaction_model.v15.config import LoaderConfig


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
    "canonical_news_id": "canonical_news_id.s64.npy",
    "ticker": "ticker.s32.npy",
    "published_at_utc": "published_at_utc.s40.npy",
    "published_at_us": "published_at_us.i64.npy",
    "publication_session": "publication_session.s16.npy",
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
        "canonical_news_id": np.dtype("S64"),
        "ticker": np.dtype("S32"),
        "published_at_utc": np.dtype("S40"),
        "published_at_us": np.dtype("<i8"),
        "publication_session": np.dtype("S16"),
    }


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
            f"Missing V15 prepared manifest {manifest_path}. "
            "Run python -m research.news_reaction_model.v15.run_prepare_data --execute first."
        )
    manifest = load_json(manifest_path)
    if require_complete and manifest.get("status") != "complete":
        raise RuntimeError(f"V15 prepared dataset is not complete: {manifest}")
    if manifest.get("dataset_version") != config.prepared_dataset_version:
        raise RuntimeError(
            "V15 prepared dataset version mismatch: "
            f"{manifest.get('dataset_version')!r} != {config.prepared_dataset_version!r}."
        )
    rows = int(manifest.get("rows") or 0)
    if rows <= 0:
        raise RuntimeError(f"V15 prepared dataset has invalid row count: {rows}.")
    expected = expected_shapes(config, rows)
    dtypes = expected_dtypes()
    arrays: dict[str, np.ndarray] = {}
    for name, filename in ARRAY_FILES.items():
        path = config.prepared_dataset_root / filename
        if not path.exists():
            raise RuntimeError(f"Missing V15 prepared array: {path}.")
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
