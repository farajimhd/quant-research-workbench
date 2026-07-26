from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from research.news_reaction_model.v15.prepared import close_arrays as close_v15_arrays
from research.news_reaction_model.v15.prepared import open_arrays as open_v15_arrays
from research.news_reaction_model.v18 import DATASET_VERSION
from research.news_reaction_model.v18.config import LoaderConfig
from research.news_reaction_model.v18.episode_contract import (
    CONTEXT_FEATURE_DIM,
    CONTEXT_SIZE,
    CURRENT_EPISODE_FEATURE_DIM,
)
from research.news_reaction_model.v18.targets import RAW_METRIC_NAMES


MANIFEST_FILE = "manifest.json"
BUILD_STATE_FILE = "build_state.json"
THRESHOLDS_FILE = "thresholds.json"
ARRAY_FILES = {
    "source_index": "source_index.i32.npy",
    "context_source_indices": "context_source_indices.i32.npy",
    "context_row_indices": "context_row_indices.i32.npy",
    "context_static": "context_static.f32.npy",
    "context_mask": "context_mask.bool.npy",
    "current_episode_features": "current_episode_features.f32.npy",
    "episode_id": "episode_id.s64.npy",
    "node_role": "node_role.i8.npy",
    "root_family": "root_family.i8.npy",
    "node_position": "node_position.i16.npy",
    "target_start_us": "target_start_us.i64.npy",
    "target_end_us": "target_end_us.i64.npy",
    "anchor_price": "anchor_price.f64.npy",
    "raw_metrics": "raw_metrics.f32.npy",
    "target_mask": "target_mask.bool.npy",
    "direction": "direction.i8.npy",
    "path": "path.i8.npy",
    "flow": "flow.i8.npy",
    "regression_targets": "regression_targets.f32.npy",
}


def expected_shapes(rows: int) -> dict[str, tuple[int, ...]]:
    return {
        "source_index": (rows,),
        "context_source_indices": (rows, CONTEXT_SIZE),
        "context_row_indices": (rows, CONTEXT_SIZE),
        "context_static": (rows, CONTEXT_SIZE, CONTEXT_FEATURE_DIM - 7),
        "context_mask": (rows, CONTEXT_SIZE),
        "current_episode_features": (rows, CURRENT_EPISODE_FEATURE_DIM),
        "episode_id": (rows,),
        "node_role": (rows,),
        "root_family": (rows,),
        "node_position": (rows,),
        "target_start_us": (rows,),
        "target_end_us": (rows,),
        "anchor_price": (rows,),
        "raw_metrics": (rows, len(RAW_METRIC_NAMES)),
        "target_mask": (rows,),
        "direction": (rows,),
        "path": (rows,),
        "flow": (rows,),
        "regression_targets": (rows, 3),
    }


def expected_dtypes() -> dict[str, np.dtype[Any]]:
    return {
        "source_index": np.dtype("<i4"),
        "context_source_indices": np.dtype("<i4"),
        "context_row_indices": np.dtype("<i4"),
        "context_static": np.dtype("<f4"),
        "context_mask": np.dtype("?"),
        "current_episode_features": np.dtype("<f4"),
        "episode_id": np.dtype("S64"),
        "node_role": np.dtype("i1"),
        "root_family": np.dtype("i1"),
        "node_position": np.dtype("<i2"),
        "target_start_us": np.dtype("<i8"),
        "target_end_us": np.dtype("<i8"),
        "anchor_price": np.dtype("<f8"),
        "raw_metrics": np.dtype("<f4"),
        "target_mask": np.dtype("?"),
        "direction": np.dtype("i1"),
        "path": np.dtype("i1"),
        "flow": np.dtype("i1"),
        "regression_targets": np.dtype("<f4"),
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def create_arrays(config: LoaderConfig, rows: int) -> dict[str, np.memmap]:
    config.prepared_dataset_root.mkdir(parents=True, exist_ok=True)
    shapes, dtypes = expected_shapes(rows), expected_dtypes()
    arrays = {
        name: np.lib.format.open_memmap(
            config.prepared_dataset_root / filename,
            mode="w+",
            dtype=dtypes[name],
            shape=shapes[name],
        )
        for name, filename in ARRAY_FILES.items()
    }
    arrays["context_source_indices"].fill(-1)
    arrays["context_row_indices"].fill(-1)
    arrays["context_static"].fill(0)
    arrays["context_mask"].fill(False)
    arrays["raw_metrics"].fill(np.nan)
    arrays["target_mask"].fill(False)
    arrays["direction"].fill(-1)
    arrays["path"].fill(-1)
    arrays["flow"].fill(-1)
    arrays["regression_targets"].fill(np.nan)
    return arrays


def open_arrays(
    config: LoaderConfig,
    *,
    mode: str = "r",
    require_complete: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    v15, v15_manifest = open_v15_arrays(config.v15_config(), mode=mode)
    try:
        path = config.prepared_dataset_root / MANIFEST_FILE
        if not path.exists():
            raise RuntimeError(
                f"Missing V18 manifest {path}. Run "
                "python -m research.news_reaction_model.v18.run_prepare_data --execute."
            )
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if require_complete and manifest.get("status") != "complete":
            raise RuntimeError(f"V18 prepared dataset is incomplete: {manifest}.")
        if manifest.get("dataset_version") != DATASET_VERSION:
            raise RuntimeError("V18 dataset-version mismatch.")
        rows = int(manifest.get("rows") or 0)
        arrays: dict[str, np.ndarray] = {}
        for name, filename in ARRAY_FILES.items():
            array = np.load(
                config.prepared_dataset_root / filename,
                mmap_mode=mode,
                allow_pickle=False,
            )
            if array.shape != expected_shapes(rows)[name] or array.dtype != expected_dtypes()[name]:
                raise RuntimeError(f"V18 array contract mismatch for {name}.")
            arrays[name] = array
        if np.any(arrays["source_index"] < 0) or np.any(
            arrays["source_index"] >= int(v15_manifest["rows"])
        ):
            raise RuntimeError("V18 source indices escape the V15 authority.")
        return v15, arrays, v15_manifest, manifest
    except Exception:
        close_v15_arrays(v15)
        raise


def close_arrays(*groups: dict[str, np.ndarray]) -> None:
    for arrays in groups:
        close_v15_arrays(arrays)
