"""Lossless bar pools and exact, ordered 64-bar window locators."""
from pathlib import Path
import sqlite3

import numpy as np

from ..v5.shards.phase1_contract import MARKET_CHANNELS, SAMPLE_DTYPE, WIDTH, file_hash

BAR = np.dtype([
    ("end_us", "<i8"),
    ("values", "<f4", (MARKET_CHANNELS,)),
    ("valid", "u1", (MARKET_CHANNELS,)),
])
BAR_BYTES = np.dtype(f"V{BAR.itemsize}")
STORED_BAR = np.dtype([
    ("end_us", "<i8"), ("values", "<f4", (MARKET_CHANNELS,)),
    ("mask", "u1", ((MARKET_CHANNELS + 7) // 8,)),
])
OFFSETS = np.arange(WIDTH, dtype=np.uint32)


def pack_windows(windows):
    """Use clocks as a fast key, then check all bits before sharing a bar.

    Causal split adjustment can give the same clock different values in two
    windows. Those records are distinct pool entries, even with equal clocks.
    """
    if windows.ndim != 1 or windows.dtype != SAMPLE_DTYPE or not len(windows):
        raise ValueError("Expected nonempty certified Phase 1 records")
    if (windows["timeframe_us"] != windows["timeframe_us"][0]).any():
        raise ValueError("Phase 1 source shard mixes timeframes")
    bars = np.empty((len(windows), WIDTH), dtype=BAR)
    for name in BAR.names:
        bars[name] = windows[name]
    flat = bars.reshape(-1)
    _, first, indices = np.unique(flat["end_us"], return_index=True, return_inverse=True)
    pool = flat[first]
    if not np.array_equal(pool[indices].view(BAR_BYTES), flat.view(BAR_BYTES)):
        # Work only on timestamp groups containing bitwise variants.
        different = pool[indices].view(BAR_BYTES) != flat.view(BAR_BYTES)
        affected = np.zeros(len(pool), dtype=bool)
        affected[indices[different]] = True
        selected = np.flatnonzero(affected[indices])
        variants, inverse = np.unique(flat[selected].view(BAR_BYTES), return_inverse=True)
        indices[selected] = len(pool) + inverse
        pool = np.concatenate((pool, variants.view(BAR)))
        # Drop now-unreferenced clock representatives and preserve chronological
        # pool order to keep most original locators contiguous.
        used = np.flatnonzero(np.bincount(indices, minlength=len(pool)))
        order = used[np.argsort(pool["end_us"][used], kind="stable")]
        remap = np.empty(len(pool), dtype=np.uint32)
        remap[order] = np.arange(len(order), dtype=np.uint32)
        pool, indices = pool[order], remap[indices]
    index_dtype = np.uint16 if len(pool) <= 65536 else np.uint32
    rows = indices.reshape(-1, WIDTH)
    starts = rows[:, 0].astype(index_dtype)
    exceptional = np.flatnonzero(np.any(rows != starts[:, None].astype(np.uint32) + OFFSETS, axis=1))
    if (pool["valid"] > 1).any():
        raise ValueError("Bar validity must be binary")
    stored_pool = np.empty(len(pool), dtype=STORED_BAR)
    stored_pool["end_us"] = pool["end_us"]
    stored_pool["values"] = pool["values"]
    stored_pool["mask"] = np.packbits(pool["valid"], axis=1, bitorder="little")
    return {
        "pool": stored_pool, "starts": starts, "exceptions": exceptional.astype(np.uint32),
        "indices": rows[exceptional].astype(index_dtype),
        "frame": windows["timeframe_us"][0], "split": windows["split"].copy(),
    }


def unpack(part, start=0, stop=None):
    """Materialize a contiguous slice in the original mmap record format."""
    stop = len(part["starts"]) if stop is None else stop
    if not 0 <= start <= stop <= len(part["starts"]):
        raise IndexError("Source window slice outside compact shard")
    starts = part["starts"][start:stop]
    indices = starts[:, None].astype(np.uint32) + OFFSETS
    exceptions = part["exceptions"]
    lo, hi = np.searchsorted(exceptions, (start, stop))
    if hi > lo:
        indices[exceptions[lo:hi] - start] = part["indices"][lo:hi]
    bars = part["pool"][indices]
    output = np.empty(len(starts), dtype=SAMPLE_DTYPE)
    for name in ("end_us", "values"):
        output[name] = bars[name]
    output["valid"] = np.unpackbits(bars["mask"], axis=-1, count=MARKET_CHANNELS, bitorder="little")
    output["timeframe_us"] = part["frame"]
    output["split"] = part["split"][start:stop]
    return output


def part_path(root, number):
    return Path(root) / "parts" / f"{number // 1024:04x}" / f"{number:08x}.npz"


class CompactRelease:
    """Read a sealed release from its new root without an original shard mount."""

    def __init__(self, root):
        import json
        from ..v5.shards.phase1_contract import digest

        self.root = Path(root).resolve()
        seal = json.loads((self.root / "certificate.json").read_text())
        if seal.pop("hash") != digest(seal) or seal["version"] != "phase1-compact-v1":
            raise ValueError("Invalid compact Phase 1 certificate")
        index = self.root / "catalog.sqlite"
        if file_hash(index) != seal["catalog_sha256"]:
            raise ValueError("Compact catalog hash differs")
        self.db = sqlite3.connect(index.as_uri() + "?mode=ro&immutable=1", uri=True)
        self.seal = seal

    def read(self, split, ordinal):
        """Resolve an original split-loader entry by ordinal."""
        item = self.db.execute(
            """SELECT s.number, e.start, e.stop, s.output_sha FROM entries e
               JOIN sources s ON e.number=s.number WHERE e.split=? AND e.ordinal=?""",
            (split, ordinal),
        ).fetchone()
        if item is None:
            raise IndexError((split, ordinal))
        number, start, stop, expected = item
        path = part_path(self.root, number)
        import hashlib
        import io

        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Compact shard hash differs: {number}")
        with np.load(io.BytesIO(data), allow_pickle=False) as part:
            return unpack(part, start, stop)

    def close(self):
        self.db.close()
