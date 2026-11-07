"""Atomic small mmap shards, vectorized packing, and a shared byte reservation."""

import json
import os
import threading
from pathlib import Path

import numpy as np
import polars as pl

from .contract import FRAMES, SAMPLE_DTYPE, WIDTH, digest, file_hash, pack, write_json

# These intermediate rows are already final market aggregates, not raw events.
# They remain transient in memory while forming cross-session sample contexts.
BAR_DTYPE = np.dtype([
    ("values", "<f4", (21,)), ("valid", "u1", (21,)),
    ("end_us", "<i8"), ("timeframe_us", "<i8"), ("split", "u1"),
])


class Budget:
    """Thread-safe reservation; queued writers cannot collectively exceed cap."""

    def __init__(self, limit, used=0):
        self.limit, self.used, self.reserved = limit, used, 0
        self.lock = threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if self.used + self.reserved + amount > self.limit:
                return False
            self.reserved += amount
            return True

    def finish(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved
            self.used += actual


def unpack_bars(frame, development):
    """Arrow lists → aligned NumPy arrays, without per-bar Python loops.

    Frame zero is a metadata-only SQL row for sessions with no eligible bars;
    it carries source/halt continuity but is never packed into a sample.
    """
    values = frame["values"].list.to_array(21).to_numpy().astype(np.float32, copy=False)
    valid = frame["valid"].list.to_array(21).to_numpy().astype(np.uint8, copy=False)
    ends = frame["end_us"].to_numpy().astype(np.int64, copy=False)
    frames = frame["g.frame"].to_numpy()
    positions = frame["observed_index"].to_numpy()
    if not np.isfinite(values).all() or not np.isin(valid, (0, 1)).all():
        raise ValueError("Malformed SQL result.")
    samples = []
    for resolution in FRAMES[:4]:
        take = frames == resolution
        count = int(take.sum())
        if count % WIDTH:
            raise ValueError("Selected SQL windows are not complete 64-bar blocks.")
        if count:
            if np.any(np.diff(positions[take].reshape(-1, WIDTH), axis=1) != 1):
                raise ValueError("Missing or duplicated observed bar inside a window.")
            samples.append(pack(values[take].reshape(-1, WIDTH, 21),
                                valid[take].reshape(-1, WIDTH, 21),
                                ends[take].reshape(-1, WIDTH), resolution, development))
    coarse = np.zeros(int((frames >= FRAMES[4]).sum()), dtype=BAR_DTYPE)
    take = frames >= FRAMES[4]
    coarse["values"], coarse["valid"], coarse["end_us"] = values[take], valid[take], ends[take]
    coarse["timeframe_us"], coarse["split"] = frames[take], development
    if len(coarse) > 17:
        raise ValueError("More than 16 observed session hours and one daily bar.")
    result = np.concatenate(samples) if samples else np.empty(0, dtype=SAMPLE_DTYPE)
    return result, coarse


def coarse_samples(day_rows):
    """Pack full chronological segments; caller separates missing days/splits.

    Every nonoverlapping 64-bar coarse window is retained. Fine windows use 10%
    retention; coarse windows are scarce and are never duplicated to fill space.
    """
    result = []
    if not day_rows:
        return np.empty(0, dtype=SAMPLE_DTYPE)
    rows = np.concatenate(day_rows)
    for frame in FRAMES[4:]:
        selected = rows[rows["timeframe_us"] == frame]
        length = len(selected) // WIDTH * WIDTH
        selected = selected[:length]
        if length:
            if len(np.unique(selected["split"])) != 1:
                raise ValueError("Cross-split coarse window requested.")
            result.append(pack(selected["values"].reshape(-1, WIDTH, 21),
                               selected["valid"].reshape(-1, WIDTH, 21),
                               selected["end_us"].reshape(-1, WIDTH), frame,
                               int(selected["split"][0])))
    return np.concatenate(result) if result else np.empty(0, dtype=SAMPLE_DTYPE)


def verify_receipt(directory, expected_identity=None, hashes=True):
    """Only a receipt publishes shards. Reject mutations instead of overwriting."""
    path = Path(directory) / "receipt.json"
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    if digest({k: v for k, v in receipt.items() if k != "receipt_hash"}) != receipt["receipt_hash"]:
        raise ValueError(f"Corrupt receipt: {path}")
    if expected_identity is not None and receipt["identity"] != expected_identity:
        raise ValueError(f"Changed extraction identity: {path}")
    for item in receipt["files"]:
        file = path.parent / item["name"]
        if file.stat().st_size != item["bytes"] or (hashes and file_hash(file) != item["sha256"]):
            raise ValueError(f"Changed sealed file: {file}")
    return receipt


def publish(directory, samples, coarse, metadata, budget, shard_bytes):
    """Write <=shard_bytes files, fsync, then atomically publish a hashed receipt.

    Each call owns its directory. A shared I/O executor may run several calls;
    there is no concurrent mutation of a file or a receipt. Unsealed files can
    be deterministically regenerated after interruption.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    existing = verify_receipt(directory, metadata["identity"])
    if existing:
        return existing
    # A rare all-status/all-volume block can still lack a price reference. Record
    # its exclusion explicitly; never manufacture prices or change its 64-bar span.
    candidate_samples = len(samples)
    observed_price = samples["valid"][..., [3, 9, 15]].any(axis=(1, 2))
    samples = samples[observed_price]
    per_shard = max(1, (shard_bytes - 4096) // SAMPLE_DTYPE.itemsize)
    # Include conservative file-header and receipt overhead in the hard budget.
    reserve = samples.nbytes + 65536 + (len(samples) // per_shard + 1) * 4096
    if not budget.reserve(reserve):
        return None
    files = []
    try:
        arrays = [(f"samples-{i//per_shard:04d}.npy", samples[i:i+per_shard])
                  for i in range(0, len(samples), per_shard)]
        # Intermediate coarse bars remain in controller memory. A partial
        # ticker-year resume replays canonical days rather than persisting bars.
        for name, array in arrays:
            target = directory / name
            temporary = directory / (name + ".partial")
            with temporary.open("wb") as handle:
                np.save(handle, array, allow_pickle=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
            item = dict(name=name, bytes=target.stat().st_size, sha256=file_hash(target))
            if name.startswith("samples-") and len(array):
                # Full context spans, including gaps, permit safe cross-timeframe split assignment.
                item.update(first_start_us=int((array["end_us"][:, 0]-array["timeframe_us"]).min()),
                            last_end_us=int(array["end_us"][:, -1].max()))
            files.append(item)
        receipt = dict(metadata, files=files, samples=len(samples),
                       candidate_samples=candidate_samples,
                       excluded_no_price=candidate_samples-len(samples),
                       frames={str(f): int((samples["timeframe_us"] == f).sum()) for f in FRAMES},
                       bytes=sum(item["bytes"] for item in files))
        receipt["receipt_hash"] = digest(receipt)
        write_json(directory / "receipt.json", receipt)
        actual = receipt["bytes"] + (directory / "receipt.json").stat().st_size
        if actual > reserve:
            raise ValueError("Storage reservation underestimated actual size.")
        budget.finish(reserve, actual)
        return receipt
    except Exception:
        # Account for unsealed files too; they occupy real space until resumed.
        actual = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        budget.finish(reserve, actual)
        raise
