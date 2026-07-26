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


def v16_identity_sha256(
    arrays: Mapping[str, np.ndarray],
    *,
    chunk_rows: int = 4096,
) -> str:
    """Hash every ordered V16 article identity without binding target data to features."""
    digest = hashlib.sha256()
    rows = int(arrays["canonical_news_id"].shape[0])
    for offset in range(0, rows, max(1, int(chunk_rows))):
        upper = min(rows, offset + max(1, int(chunk_rows)))
        values = np.empty(upper - offset, dtype="<u8")
        for local_index, row_index in enumerate(range(offset, upper)):
            values[local_index] = row_key_hash(
                _decode(arrays["canonical_news_id"][row_index]),
                _decode(arrays["ticker"][row_index]),
                _decode(arrays["published_at_utc"][row_index]),
            )
        digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def audit_all_row_identities(
    v16_arrays: Mapping[str, np.ndarray],
    target_hashes: np.ndarray,
    *,
    chunk_rows: int = 4096,
) -> None:
    rows = int(v16_arrays["canonical_news_id"].shape[0])
    if int(target_hashes.shape[0]) != rows:
        raise RuntimeError("V17 identity audit row-count mismatch.")
    for offset in range(0, rows, max(1, int(chunk_rows))):
        upper = min(rows, offset + max(1, int(chunk_rows)))
        expected = np.empty(upper - offset, dtype="<u8")
        for local_index, row_index in enumerate(range(offset, upper)):
            expected[local_index] = row_key_hash(
                _decode(v16_arrays["canonical_news_id"][row_index]),
                _decode(v16_arrays["ticker"][row_index]),
                _decode(v16_arrays["published_at_utc"][row_index]),
            )
        actual = np.asarray(target_hashes[offset:upper], dtype="<u8")
        if not np.array_equal(actual, expected):
            mismatch = int(np.flatnonzero(actual != expected)[0]) + offset
            raise RuntimeError(f"V17 target row identity does not match V16 at row {mismatch}.")


def audit_target_arrays(
    v16_arrays: Mapping[str, np.ndarray],
    target_arrays: Mapping[str, np.ndarray],
    *,
    chunk_rows: int = 4096,
) -> dict[str, int]:
    """Exhaustively certify identity, masks, metrics, and class contracts."""
    audit_all_row_identities(v16_arrays, target_arrays["row_key_hash"], chunk_rows=chunk_rows)
    rows = int(target_arrays["window_mask"].shape[0])
    populated_windows = 0
    persistence_rows = 0
    for offset in range(0, rows, max(1, int(chunk_rows))):
        upper = min(rows, offset + max(1, int(chunk_rows)))
        mask = np.asarray(target_arrays["window_mask"][offset:upper], dtype=np.bool_)
        raw = np.asarray(target_arrays["raw_metrics"][offset:upper], dtype=np.float32)
        direction = np.asarray(target_arrays["direction"][offset:upper], dtype=np.int8)
        path = np.asarray(target_arrays["path"][offset:upper], dtype=np.int8)
        flow = np.asarray(target_arrays["flow"][offset:upper], dtype=np.int8)
        persistence = np.asarray(target_arrays["persistence"][offset:upper], dtype=np.int8)
        persistence_mask = np.asarray(
            target_arrays["persistence_mask"][offset:upper], dtype=np.bool_
        )
        selected = raw[mask]
        if not np.isfinite(selected).all():
            raise RuntimeError(f"V17 populated raw metrics contain non-finite values near row {offset}.")
        if np.any(selected[:, 0] <= 0):
            raise RuntimeError(f"V17 populated windows contain invalid anchors near row {offset}.")
        if np.any(selected[:, 2] > selected[:, 3]) or np.any(selected[:, 3] > selected[:, 1]):
            raise RuntimeError(f"V17 low/terminal/high ordering failed near row {offset}.")
        if np.any((direction[mask] < 0) | (direction[mask] > 2)):
            raise RuntimeError(f"V17 direction classes are invalid near row {offset}.")
        if np.any((path[mask] < 0) | (path[mask] > 5)):
            raise RuntimeError(f"V17 path classes are invalid near row {offset}.")
        if np.any((flow[mask] < 0) | (flow[mask] > 2)):
            raise RuntimeError(f"V17 flow classes are invalid near row {offset}.")
        if np.any(direction[~mask] != -1) or np.any(path[~mask] != -1) or np.any(flow[~mask] != -1):
            raise RuntimeError(f"V17 unobserved windows contain classes near row {offset}.")
        if np.any(persistence_mask != mask.any(axis=1)):
            raise RuntimeError(f"V17 persistence mask disagrees with window coverage near row {offset}.")
        if np.any((persistence[persistence_mask] < 0) | (persistence[persistence_mask] > 5)):
            raise RuntimeError(f"V17 persistence classes are invalid near row {offset}.")
        if np.any(persistence[~persistence_mask] != -1):
            raise RuntimeError(f"V17 unobserved rows contain persistence classes near row {offset}.")
        populated_windows += int(mask.sum())
        persistence_rows += int(persistence_mask.sum())
    return {
        "rows": rows,
        "populated_windows": populated_windows,
        "persistence_rows": persistence_rows,
    }


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
    Complete ordered V16 article identity vector and row count. Target identity
    is intentionally independent of embedding and market-feature revisions.
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
        current_identity_sha256 = v16_identity_sha256(v16_arrays)
        if manifest.get("v16_identity_sha256") != current_identity_sha256:
            raise RuntimeError("V17 targets were built against a different V16 article identity vector.")
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
        audit_all_row_identities(v16_arrays, target_arrays["row_key_hash"])
        return v16_arrays, target_arrays, v16_manifest, manifest
    except Exception:
        close_v16_arrays(v16_arrays)
        raise


def close_arrays(*groups: dict[str, np.ndarray]) -> None:
    for arrays in groups:
        close_v16_arrays(arrays)
