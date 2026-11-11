"""Bounded FIFO source verification/read-ahead, independent of batch prefetch.

One I/O thread per existing loader lane overlaps immutable source reads with
CPU preparation. It never reorders entries, drops tails, changes masks or skips
the existing full-payload SHA and complete-market checks. Mmaps remain read-only.
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import numpy as np

from .shards.contract import file_hash
from .shards.phase1_contract import SAMPLE_DTYPE


class VerifiedShardPrefetch:
    def __init__(
        self, items, *, depth=32, byte_budget=128 * 2**20, validator=None, on_wait=None
    ):
        if not 0 <= depth <= 64 or byte_budget <= 0:
            raise ValueError(
                "Source prefetch requires depth0..64 and positive byte budget"
            )
        self.items, self.depth, self.byte_budget = iter(items), depth, byte_budget
        self.validator, self.on_wait = validator, on_wait
        self.verified = {}
        self.wait_seconds = self.read_seconds = 0.0
        self.read_units = self.pending_bytes = self.peak_pending_bytes = 0
        self.pending = deque()

    def read(self, item):
        began = time.perf_counter()
        path = Path(item["path"])
        binding = (item["bytes"], item["sha256"])
        if path.stat().st_size != binding[0]:
            raise ValueError(f"Changed sealed payload size: {path}")
        previous = self.verified.get(str(path))
        if previous is not None and previous != binding:
            raise ValueError(f"Inconsistent repeated payload binding: {path}")
        if previous is None:
            if file_hash(path) != binding[1]:
                raise ValueError(f"Changed sealed payload SHA256: {path}")
            self.verified[str(path)] = binding
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if array.dtype != SAMPLE_DTYPE or array.ndim != 1:
            raise ValueError(f"Invalid sealed payload header: {path}")
        if not 0 <= item["start"] <= item["stop"] <= len(array):
            raise ValueError(f"Invalid source range: {path}")
        selected = array[item["start"] : item["stop"]]
        if self.validator is not None:
            self.validator(selected)
        self.read_seconds += time.perf_counter() - began
        self.read_units += 1
        return selected

    def telemetry(self):
        return dict(
            source_wait_seconds=self.wait_seconds,
            source_read_seconds=self.read_seconds,
            source_verified_units=self.read_units,
            source_pending_units=len(self.pending),
            source_ready_units=sum(f.done() for _, f in self.pending),
            source_pending_bytes=self.pending_bytes,
            source_peak_pending_bytes=self.peak_pending_bytes,
        )

    def __iter__(self):
        if not self.depth:
            for item in self.items:
                if self.on_wait:
                    self.on_wait()
                began = time.perf_counter()
                value = self.read(item)
                self.wait_seconds += time.perf_counter() - began
                yield item, value
            return
        # A single producer preserves per-lane verification/cache ordering.
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="verified-shard"
        ) as pool:
            lookahead = None
            exhausted = False

            def fill():
                nonlocal lookahead, exhausted
                while len(self.pending) < self.depth and not exhausted:
                    if lookahead is None:
                        lookahead = next(self.items, None)
                        if lookahead is None:
                            exhausted = True
                            break
                    size = int(lookahead["bytes"])
                    # An oversized file is admitted alone, via readonly mmap;
                    # it is never skipped or copied into an unbounded RAM buffer.
                    if self.pending and self.pending_bytes + size > self.byte_budget:
                        break
                    item, lookahead = lookahead, None
                    self.pending.append((item, pool.submit(self.read, item)))
                    self.pending_bytes += size
                    self.peak_pending_bytes = max(
                        self.peak_pending_bytes, self.pending_bytes
                    )

            try:
                fill()
                while self.pending:
                    item, future = self.pending.popleft()
                    self.pending_bytes -= int(item["bytes"])
                    if self.on_wait:
                        self.on_wait()
                    began = time.perf_counter()
                    value = future.result()
                    self.wait_seconds += time.perf_counter() - began
                    fill()
                    yield item, value
            finally:
                for _, future in self.pending:
                    future.cancel()
                self.pending.clear()
                self.pending_bytes = 0
