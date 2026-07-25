from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from research.news_reaction_model.v16.prepared import close_arrays as close_v16_arrays
from research.news_reaction_model.v16.prepared import open_arrays as open_v16_arrays
from research.news_reaction_model.v17.config import LoaderConfig
from research.news_reaction_model.v17.targets import RAW_METRIC_NAMES, TARGET_VERSION


MANIFEST_FILE = "manifest.json"
BUILD_STATE_FILE = "build_state.json"
THRESHOLDS_FILE = "thresholds.json"
ARRAY_FILES = {
    "raw_metrics": "raw_metrics.f32.npy",
    "window_mask": "window_mask.bool.npy",
    "direction": "direction.i8.npy",
    "path": "path.i8.npy",
    "flow": "flow.i8.npy",
    "persistence": "persistence.i8.npy",
    "persistence_mask": "persistence_mask.bool.npy",
    "row_key_hash": "row_key_hash.u64.npy",
}


def row_key_hash(canonical_news_id: str, ticker: str, published_at_utc: str) -> np.uint64:
    digest = hashlib.blake2b(
        f"{canonical_news_id}\x1f{ticker}\x1f{published_at_utc}".encode("utf-8"),
        digest_size=8,
        person=b"news-v17",
    ).digest()
    return np.frombuffer(digest, dtype="<u8")[0]


def _decode(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def expected_shapes(rows: int, windows: int) -> dict[str, tuple[int, ...]]:
    return {
        "raw_metrics": (rows, windows, len(RAW_METRIC_NAMES)),
        "window_mask": (rows, windows),
        "direction": (rows, windows),
        "path": (rows, windows),
        "flow": (rows, windows),
        "persistence": (rows,),
        "persistence_mask": (rows,),
        "row_key_hash": (rows,),
    }


def expected_dtypes() -> dict[str, np.dtype[Any]]:
    return {
        "raw_metrics": np.dtype("<f4"),
        "window_mask": np.dtype("?"),
        "direction": np.dtype("i1"),
        "path": np.dtype("i1"),
        "flow": np.dtype("i1"),
        "persistence": np.dtype("i1"),
        "persistence_mask": np.dtype("?"),
        "row_key_hash": np.dtype("<u8"),
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def create_target_arrays(config: LoaderConfig, rows: int) -> dict[str, np.memmap]:
    config.target_root.mkdir(parents=True, exist_ok=True)
    shapes = expected_shapes(rows, len(config.response_windows))
    dtypes = expected_dtypes()
    arrays = {
        name: np.lib.format.open_memmap(
            config.target_root / filename,
            mode="w+",
            dtype=dtypes[name],
            shape=shapes[name],
        )
        for name, filename in ARRAY_FILES.items()
    }
    arrays["raw_metrics"].fill(np.nan)
    arrays["window_mask"].fill(False)
    arrays["direction"].fill(-1)
    arrays["path"].fill(-1)
    arrays["flow"].fill(-1)
    arrays["persistence"].fill(-1)
    arrays["persistence_mask"].fill(False)
    arrays["row_key_hash"].fill(0)
    return arrays


def open_target_arrays_for_resume(
    config: LoaderConfig,
    rows: int,
) -> dict[str, np.ndarray]:
    shapes = expected_shapes(rows, len(config.response_windows))
    dtypes = expected_dtypes()
    arrays: dict[str, np.ndarray] = {}
    for name, filename in ARRAY_FILES.items():
        path = config.target_root / filename
        if not path.exists():
            raise RuntimeError(f"Missing resumable V17 target array {path}.")
        array = np.load(path, mmap_mode="r+", allow_pickle=False)
        if array.shape != shapes[name] or array.dtype != dtypes[name]:
            raise RuntimeError(
                f"Resumable V17 target {name} has {array.shape}/{array.dtype}; "
                f"expected {shapes[name]}/{dtypes[name]}."
            )
        arrays[name] = array
    return arrays


def open_v17_arrays(
    config: LoaderConfig,
    *,
    mode: str = "r",
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    """Open V16 inputs and the small V17 target sidecar read-only.

    No V16 input array is copied into V17. The manifest binds both products by
    V16 representation hash, row count, and per-row identity hash.
    """
    v16_arrays, v16_manifest = open_v16_arrays(config, mode=mode)
    try:
        manifest_path = config.target_root / MANIFEST_FILE
        if not manifest_path.exists():
            raise RuntimeError(
                f"Missing V17 target manifest {manifest_path}. Run "
                "python -m research.news_reaction_model.v17.run_prepare_targets --execute."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise RuntimeError(f"V17 target sidecar is incomplete: {manifest}.")
        if manifest.get("target_version") != TARGET_VERSION:
            raise RuntimeError("V17 target-version mismatch.")
        if int(manifest.get("rows", -1)) != int(v16_manifest["rows"]):
            raise RuntimeError("V17 target rows do not match V16 prepared rows.")
        if manifest.get("v16_representation_sha256") != v16_manifest.get(
            "representation_sha256"
        ):
            raise RuntimeError("V17 targets were built against a different V16 representation.")
        shapes = expected_shapes(int(v16_manifest["rows"]), len(config.response_windows))
        dtypes = expected_dtypes()
        target_arrays: dict[str, np.ndarray] = {}
        for name, filename in ARRAY_FILES.items():
            array = np.load(config.target_root / filename, mmap_mode=mode, allow_pickle=False)
            if array.shape != shapes[name] or array.dtype != dtypes[name]:
                raise RuntimeError(
                    f"V17 target {name} has {array.shape}/{array.dtype}, "
                    f"expected {shapes[name]}/{dtypes[name]}."
                )
            target_arrays[name] = array
        row_count = int(v16_manifest["rows"])
        for index in sorted({0, row_count // 2, row_count - 1}):
            expected_hash = row_key_hash(
                _decode(v16_arrays["canonical_news_id"][index]),
                _decode(v16_arrays["ticker"][index]),
                _decode(v16_arrays["published_at_utc"][index]),
            )
            if target_arrays["row_key_hash"][index] != expected_hash:
                raise RuntimeError(
                    f"V17 target row identity does not match V16 at row {index}."
                )
        return v16_arrays, target_arrays, v16_manifest, manifest
    except Exception:
        close_v16_arrays(v16_arrays)
        raise


def close_arrays(*groups: dict[str, np.ndarray]) -> None:
    for arrays in groups:
        close_v16_arrays(arrays)
