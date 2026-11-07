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
    real = frames > 0  # Metadata frame zero is not a market observation.
    if (not valid[real, :6].all() or np.any(values[real, :6] <= 0)):
        raise ValueError("Every real bar must contain complete positive trade OHLC, volume and count.")
    samples = []
    for resolution in FRAMES[:4]:
        take = frames == resolution
        count = int(take.sum())
        if count:
            # SQL returns complete selected blocks followed by at most one
            # partial session tail. Padding never creates another clock bucket.
            padded_count = ((count + WIDTH - 1) // WIDTH) * WIDTH
            v = np.zeros((padded_count, 21), dtype=np.float32)
            m = np.zeros((padded_count, 21), dtype=np.uint8)
            t = np.zeros(padded_count, dtype=np.int64)
            indices = np.zeros(padded_count, dtype=np.int64)
            v[:count], m[:count], t[:count] = values[take], valid[take], ends[take]
            indices[:count] = positions[take]
            # Validate only real neighbors within each selected 64-bar block.
            present = t.reshape(-1, WIDTH) > 0
            if np.any((np.diff(indices.reshape(-1, WIDTH), axis=1) != 1) & present[:, 1:]):
                raise ValueError("Missing or duplicated observed bar inside a window.")
            samples.append(pack(v.reshape(-1, WIDTH, 21), m.reshape(-1, WIDTH, 21),
                                t.reshape(-1, WIDTH), resolution, development))
    coarse = np.zeros(int((frames >= FRAMES[4]).sum()), dtype=BAR_DTYPE)
    take = frames >= FRAMES[4]
    coarse["values"], coarse["valid"], coarse["end_us"] = values[take], valid[take], ends[take]
    coarse["timeframe_us"], coarse["split"] = frames[take], development
    if len(coarse) > 17:
        raise ValueError("More than 16 observed session hours and one daily bar.")
    result = np.concatenate(samples) if samples else np.empty(0, dtype=SAMPLE_DTYPE)
    return result, coarse


def coarse_samples(day_rows, actions=()):
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
    samples = np.concatenate(result) if result else np.empty(0, dtype=SAMPLE_DTYPE)
    adjust_splits(samples, actions)
    return samples


def adjust_splits(samples, actions):
    """Adjust [N,64,21] coarse samples in place to each sample's final share basis.

    actions are frozen q_live corporate-action rows for this ticker. As in
    ARTE market_day_sql.split_factor, prices before execution multiply by
    split_from/split_to; volume and quote sizes multiply by the reciprocal.
    Counts, timestamps and masks stay unchanged. Future executions do not affect
    samples ending before the action. Canonical event prices are never modified.
    """
    from .sql import clock

    price_columns = [0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15]
    size_columns = [4, 10, 16]
    ends = samples["end_us"]  # [N,64], full observed coarse contexts.
    factor = np.ones(ends.shape, dtype=np.float64)
    for action in sorted(actions, key=lambda row: row["execution_date"]):
        effective = clock(action["execution_date"], 4)
        before, after = float(action["split_from"]), float(action["split_to"])
        if not (np.isfinite(before) and np.isfinite(after) and before > 0 and after > 0):
            raise ValueError("Invalid split ratio; never publish unadjusted fallback.")
        affected = (ends > 0) & (ends < effective) & (ends.max(axis=1)[:, None] >= effective)
        factor *= np.where(affected, before / after, 1.)  # Compound executions within each context.
    samples["values"][..., price_columns] *= factor[..., None]
    samples["values"][..., size_columns] /= factor[..., None]
    if not np.isfinite(samples["values"]).all():
        raise ValueError("Corporate-action adjustment overflowed final Float32 values.")


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
    # Trade completeness is an invariant, not a sample filter. A failure must
    # stop publication rather than silently turn incomplete bars into training data.
    candidate_samples = len(samples)
    real = samples["end_us"] > 0
    if (not samples["valid"][..., :6][real].all()
            or np.any(samples["values"][..., :6][real] <= 0)):
        raise ValueError("Cannot publish a sample with incomplete trade attributes.")
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
                            last_end_us=int(array["end_us"].max()))
            files.append(item)
        receipt = dict(metadata, files=files, samples=len(samples),
                       candidate_samples=candidate_samples,
                       excluded_no_price=candidate_samples-len(samples),
                       observed_bars=int((samples["end_us"] > 0).sum()),
                       padded_bars=int((samples["end_us"] == 0).sum()),
                       valid_bars_by_channel=samples["valid"].sum(axis=(0, 1)).tolist(),
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
