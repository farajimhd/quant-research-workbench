"""Source-bound, unadjusted session contexts; bounded RAM with persistent misses."""

from collections import OrderedDict
import json
import os
from pathlib import Path

import numpy as np
import psutil

from .coarse_cache import owned_lock
from .contract import BAR, FRAMES
from ..shards.contract import digest, file_hash, write_json
from ..shards.sql import clock
from ..quote_repair_v1 import verify_seal


class BarCache:
    """Persist all coarse bars and last64 fine bars per complete source session.

    Fine tails are ONLY usable for history, never a substitute for full current
    session output. Each session retains enough observed rows to reconstruct
    exactly the final64 history rows across any number of sparse sessions.
    """

    def __init__(self, root, binding, memory_bytes=64 * 1024**2, fine_tail_rows=64):
        self.root, self.binding = Path(root), binding
        if fine_tail_rows < 64:
            raise ValueError("Context tails must retain at least64 bars")
        self.fine_tail_rows = fine_tail_rows
        self.memory_bytes = memory_bytes
        self.memory = OrderedDict()
        self.used = 0
        self.ticker = None

    def identity(self, ticker, row, coarse):
        return dict(version="unadjusted-session-context-v1", binding=self.binding,
                    ticker=ticker, source_row=row, coarse=bool(coarse),
                    fine_tail_rows=self.fine_tail_rows, price_basis="unadjusted")

    def _paths(self, identity):
        key = digest(identity)
        directory = self.root / digest(identity["ticker"])[:24] / identity["source_row"]["source_date"][:7]
        return key, directory / (key + ".json"), directory / (key + ".npz")

    def _remember(self, key, arrays):
        size = sum(a.nbytes for a in arrays)
        if size > self.memory_bytes:
            return
        prior = self.memory.pop(key, None)
        if prior is not None:
            self.used -= sum(a.nbytes for a in prior)
        self.memory[key] = arrays
        for array in arrays:
            array.flags.writeable = False
        self.used += size
        while self.used > self.memory_bytes:
            _, old = self.memory.popitem(last=False)
            self.used -= sum(a.nbytes for a in old)

    def _select_ticker(self, ticker):
        if ticker != self.ticker:
            self.memory.clear()
            self.used = 0
            self.ticker = ticker

    def get(self, ticker, row, coarse):
        identity = self.identity(ticker, row, coarse)
        key, marker, payload = self._paths(identity)
        self._select_ticker(ticker)
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key], "memory"
        if not marker.exists():
            return None, None
        receipt = json.loads(marker.read_text())
        verify_seal(receipt, "hash")
        if (receipt["identity"] != identity or payload.stat().st_size != receipt["bytes"]
                or file_hash(payload) != receipt["sha256"]):
            raise ValueError("Persistent session context changed")
        with np.load(payload, allow_pickle=False) as archive:
            arrays = [archive[f"f{i}"].copy() for i in range(5)]
        for i, a in enumerate(arrays):
            if (a.dtype != BAR or a.ndim != 1 or len(a) != receipt["rows"][i]
                    or np.any(np.diff(a["end_us"]) <= 0)
                    or (not coarse and len(a) > self.fine_tail_rows)):
                raise ValueError("Persistent session context geometry changed")
        self._remember(key, arrays)
        return arrays, "disk"

    def put(self, ticker, row, coarse, arrays):
        self._select_ticker(ticker)
        identity = self.identity(ticker, row, coarse)
        key, marker, payload = self._paths(identity)
        opened, closed = clock(row["source_date"], 4), clock(row["source_date"], 20)
        frames = range(3, 5) if coarse else range(3)
        selected = [np.empty(0, dtype=BAR) for _ in FRAMES]
        for i in frames:
            a = arrays[i]
            a = a[(a["end_us"] > opened) & (a["end_us"] <= closed)]
            selected[i] = (a if coarse else a[-self.fine_tail_rows:]).copy()
        with owned_lock(payload.with_suffix(".lock")):
            if marker.exists():
                prior, _ = self.get(ticker, row, coarse)
                if any(a.tobytes() != b.tobytes() for a, b in zip(prior, selected)):
                    raise ValueError("Recomputed session context differs")
                return
            payload.parent.mkdir(parents=True, exist_ok=True)
            needed = sum(a.nbytes for a in selected)
            if psutil.disk_usage(str(payload.parent)).free < 150 * 1024**3 + needed * 2 + 1024**2:
                raise OSError("Persistent context would consume disk reserve")
            temporary = payload.with_suffix(f".{os.getpid()}.partial")
            with temporary.open("wb") as stream:
                np.savez_compressed(stream, **{f"f{i}": a for i, a in enumerate(selected)})
                stream.flush()
                os.fsync(stream.fileno())
            sha = file_hash(temporary)
            if payload.exists():
                if file_hash(payload) != sha:
                    with np.load(payload, allow_pickle=False) as archive:
                        if any(archive[f"f{i}"].tobytes() != a.tobytes() for i, a in enumerate(selected)):
                            raise ValueError("Unsealed context payload differs")
                    sha = file_hash(payload)
            else:
                temporary.replace(payload)
            if file_hash(payload) != sha:
                raise ValueError("Persistent context independent hash mismatch")
            # Independently reload before sealing. A missing marker is a miss,
            # never a partial cache hit; no cache evidence is deleted.
            with np.load(payload, allow_pickle=False) as archive:
                if any(archive[f"f{i}"].tobytes() != a.tobytes() for i, a in enumerate(selected)):
                    raise ValueError("Persistent context readback differs")
            receipt = dict(identity=identity, sha256=sha, bytes=payload.stat().st_size,
                           rows=[len(a) for a in selected])
            receipt["hash"] = digest(receipt)
            write_json(marker, receipt)
            self._remember(key, selected)
