"""Verified retained projections, never an unchecked upstream hash shortcut.

A miss requires the original full V6 certification. A hit consumes a local
derived copy whose byte hash, input envelope and producer seals are checked.
Changing a producer seal or physical dependency creates a different cache key.
"""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import polars as pl

from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError

VERSION = "verified-v7-projection-v1"


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            value.update(chunk)
    return value.hexdigest()


def producer_seals(root, previous, runtime):
    """Read small immutable producer seals; this does not verify bank arrays.

    Their exact bytes identify a copy that was built after full verification.
    Runtime containment is enforced on hits as well as misses.
    """
    runtime = Path(runtime).resolve()
    roots = [Path(root).resolve()]
    if previous is not None:
        roots.append(Path(previous).resolve())
    if (
        not runtime.is_dir()
        or len(set(roots)) != len(roots)
        or any(not r.is_relative_to(runtime) for r in roots)
    ):
        raise EncodingError("V6 session roots must be distinct runtime directories")
    return {
        str(r): {
            name: file_hash(r / name)
            for name in ("plan.json", "complete.json", "bank/complete.json")
        }
        for r in roots
    }


def cache_path(config, funnel, dependencies, seals):
    signature = {
        "version": VERSION,
        "session": asdict(config),
        "funnel": asdict(funnel),
        "dependencies": [asdict(f) for f in dependencies],
        "producer_seals": seals,
    }
    key = hashlib.sha256(
        json.dumps(signature, sort_keys=True, default=str).encode()
    ).hexdigest()
    return config.runtime / "v7_projection" / key, key


def load_projection(folder, key, prepared, dependencies, seals):
    """Verify the retained projected copy every load; corruption is an error."""
    metadata = json.loads((folder / "complete.json").read_text(encoding="utf-8"))
    if (
        metadata["version"] != VERSION
        or metadata["key"] != key
        or metadata["market_source_key"] != prepared.source_key
        or metadata["producer_seals"] != seals
    ):
        raise EncodingError("V7 projection cache input binding mismatch")
    name = metadata["file"]
    if Path(name).name != name:
        raise EncodingError("Invalid V7 projection cache filename")
    path = folder / name
    if file_hash(path) != metadata["sha256"]:
        raise EncodingError("Corrupt V7 projection cache")
    frame = pl.read_parquet(path)
    required = {f.column for f in dependencies if f.source == "v7"} | {
        f.valid_column for f in dependencies if f.source == "v7" and f.valid_column
    }
    if (
        frame.height != metadata["rows"]
        or not required <= set(frame.columns)
        or frame.select("listing_id", "time_us").is_duplicated().any()
    ):
        raise EncodingError("Malformed retained V7 projection")
    # Cached frame includes the exact pinned market lane used to build it.
    # Its key binds that lane, period, membership and dependency projection.
    if frame.estimated_size() > prepared.config.max_prepared_gib * 1024**3:
        raise MemoryError("Retained V7 projection exceeds the host memory guard")
    return replace(
        prepared,
        features={**prepared.features, 1000: frame},
        dependencies=tuple(dict.fromkeys((*prepared.dependencies, *dependencies))),
        source_key=metadata["source_key"],
        metrics={
            **prepared.metrics,
            "v7": metadata["proof"],
            "v7_cache": {
                "reused": True,
                "key": key,
                "bytes_verified": path.stat().st_size,
            },
        },
    )


def save_projection(folder, key, prepared, market_key, seals):
    """Publish data before an atomic complete seal; partial output is not read."""
    folder.mkdir(parents=True, exist_ok=True)
    name = f"features-{uuid4().hex}.parquet"
    path = folder / name
    frame = prepared.features[1000]
    frame.write_parquet(path)
    metadata = {
        "version": VERSION,
        "key": key,
        "market_source_key": market_key,
        "producer_seals": seals,
        "file": name,
        "sha256": file_hash(path),
        "rows": frame.height,
        "source_key": prepared.source_key,
        "proof": prepared.metrics["v7"],
    }
    temporary = folder / f"complete-{uuid4().hex}.tmp"
    temporary.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    temporary.replace(folder / "complete.json")
    prepared.metrics["v7_cache"] = {
        "reused": False,
        "key": key,
        "bytes_verified": path.stat().st_size,
    }
