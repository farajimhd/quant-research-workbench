"""Prepared 64-bar sample contract and deterministic sampling primitives."""

import hashlib
import json
from pathlib import Path
import time

import numpy as np

VERSION = "bargpt-session-padded-64-v3"
WIDTH = 64
FRAMES = (100_000, 1_000_000, 10_000_000, 60_000_000, 3_600_000_000, 86_400_000_000)
CHANNELS = tuple(
    f"{side}_{field}"
    for side, fields in (
        ("trade", ("open", "high", "low", "close", "volume", "count")),
        ("bid", ("open", "high", "low", "close", "size", "count")),
        ("ask", ("open", "high", "low", "close", "size", "count")),
    )
    for field in fields
) + ("halted_open", "halted_close", "halt_start_count")

# One mmap-compatible record is one complete sample; no Arrow decoding or window
# assembly is necessary in training. Values are unscaled Float32 model inputs.
SAMPLE_DTYPE = np.dtype([
    ("values", "<f4", (WIDTH, len(CHANNELS))),
    ("valid", "u1", (WIDTH, len(CHANNELS))),
    ("end_us", "<i8", (WIDTH,)),  # Zero denotes right padding, never an observed bar.
    ("timeframe_us", "<i8"),
    ("split", "u1"),  # 0=train; 1=chronological development (2025 Q4).
])


def digest(value):
    """SHA-256 of canonical JSON, used to pin contracts and source inventories."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def seed_for(*parts):
    """Stable unsigned 64-bit seed; independent of Python's randomized hash."""
    return int(digest(parts)[:16], 16)


def file_hash(path):
    """Bounded-memory file hashing for durable shard receipts."""
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    """Atomically publish JSON, tolerating brief Windows/SMB reader locks.

    Never delete the destination to force publication: readers must see either
    the old complete document or the new one. Permanent permission failures
    still propagate after a bounded retry; callers decide whether the document
    is required data evidence or merely best-effort progress telemetry.
    """
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
    deadline = time.monotonic() + 10
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def pack(values, valid, ends, frame, development):
    """Pack aligned [N,64,21] arrays into N ready-to-read records, vectorized."""
    if values.shape[1:] != (WIDTH, len(CHANNELS)) or valid.shape != values.shape:
        raise ValueError("Expected aligned [N,64,21] values and validity.")
    if ends.shape != values.shape[:2]:
        raise ValueError("Sample clocks must have shape [N,64].")
    present = ends > 0  # [N,64]; actual timestamps followed only by zero padding.
    if (np.any(ends < 0) or np.any(~present[:, 0])
            or np.any(present[:, 1:] & ~present[:, :-1])
            or np.any((np.diff(ends, axis=1) <= 0) & present[:, 1:])):
        raise ValueError("Observed clocks must increase strictly before right padding.")
    if np.any(values[~present]) or np.any(valid[~present]):
        raise ValueError("Padding must have zero values and zero evidence masks.")
    if not np.isfinite(values).all() or not np.isin(valid, (0, 1)).all():
        raise ValueError("Nonfinite values or malformed masks in final samples.")
    result = np.zeros(len(values), dtype=SAMPLE_DTYPE)
    result["values"] = values
    result["valid"] = valid
    result["end_us"] = ends
    result["timeframe_us"] = frame
    result["split"] = development
    return result
