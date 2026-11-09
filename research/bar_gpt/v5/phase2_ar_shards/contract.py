"""CPU-only shared context compilation; every published block has 4,096 origins."""

from dataclasses import dataclass
import hashlib

import numpy as np

from ..shards.phase1_contract import CHANNELS
from ..shards.contract import digest
from ..shards.sql import clock

VERSION = "bargpt-phase2-ar-sampled-v2"
BLOCK = 4096
FRAMES = (1_000_000, 10_000_000, 60_000_000, 3_600_000_000, 86_400_000_000)
# Trading-session years are independent of the phase-one cutoff.
CUTOFF = clock("2026-01-01", 4)
BAR = np.dtype(
    [
        ("values", "<f4", (len(CHANNELS),)),
        ("valid", "u1", (len(CHANNELS),)),
        ("end_us", "<i8"),
    ]
)
ORIGIN = np.dtype([("end_us", "<i8"), ("latest", "<i8", (5,))])


@dataclass(frozen=True)
class Geometry:
    """Cached history budget, not a commitment to a tokenizer overlap/target."""

    history: tuple = (64, 64, 64, 64, 64)
    cutoff: int = CUTOFF

    def __post_init__(self):
        if len(self.history) != 5 or any(int(n) != n or n < 64 for n in self.history):
            raise ValueError("Five integer history lengths >=64 are required")
        if self.cutoff <= 0:
            raise ValueError("Positive chronological cutoff is required")

    def manifest(self):
        return dict(
            version=VERSION,
            block_origins=BLOCK,
            frames=list(FRAMES),
            channels=list(CHANNELS),
            history=list(self.history),
            cutoff=self.cutoff,
            bar_dtype=BAR.descr,
            origin_dtype=ORIGIN.descr,
            unavailable_history="deferred, never invented/padded",
            minimum_completed_bars=64,
            origin_stride="one observed 1s bar; no saved tokenizer stride",
            validation_context="completed earlier-year bars permitted",
            origin_split="New York trading-session year; timestamps are UTC",
            block_scope="one NY04:00-20:00 session only",
            price_basis="one session basis, applied once before publication",
            future_targets="not stored; reader must additionally purge target support",
        )


def validate_bars(rows):
    """Reject noncanonical geometry, unordered clocks, or missing trade evidence."""
    if rows.dtype != BAR or rows.ndim != 1:
        raise ValueError("Expected a one-dimensional canonical BAR array")
    if (
        not np.isfinite(rows["values"]).all()
        or not np.isin(rows["valid"], (0, 1)).all()
        or not rows["valid"][:, :6].all()
        or np.any(rows["values"][:, :6] <= 0)
        or np.any(rows["end_us"] <= 0)
        or np.any(np.diff(rows["end_us"]) <= 0)
    ):
        raise ValueError("Malformed canonical bars")


class Compiler:
    """Ticker-owned rolling state across months and years; no repeated queries.

    Buffers retain the first pending origin's complete context and the newest
    history needed for the next origin. All newly fetched bars are published in
    shared monthly arrays, including warmup/future-coarse evidence not selected
    into a training block. Origin indices are absolute until publication.
    """

    def __init__(self, geometry=Geometry()):
        self.geometry = geometry
        self.views = [np.empty(0, dtype=BAR) for _ in FRAMES]
        self.starts = np.zeros(5, dtype=np.int64)
        self.pending = np.empty(0, dtype=ORIGIN)
        self.pending_key = None

    def bootstrap(self, arrays):
        """Install explicit prior bars, without turning warmup into origins."""
        if len(arrays) != 5:
            raise ValueError("Five explicit historical views required")
        if any(len(a) for a in self.views) or len(self.pending):
            raise ValueError("Bootstrap is only permitted on a fresh compiler")
        for i, a in enumerate(arrays):
            validate_bars(a)
            self.starts[i] = max(0, len(a) - self.geometry.history[i])
            self.views[i] = a[-self.geometry.history[i] :].copy()

    def compile(self, arrays, liquid):
        """Append one chronological source month, cache every origin's context."""
        if len(arrays) != 5 or liquid not in (0, 1):
            raise ValueError("Five frames and a frozen liquidity class are required")
        for old, new in zip(self.views, arrays):
            validate_bars(new)
            if len(old) and len(new) and new["end_us"][0] <= old["end_us"][-1]:
                raise ValueError("Month overlaps previously consumed canonical bars")
        pools = [np.concatenate((old, new)) for old, new in zip(self.views, arrays)]
        origins = np.zeros(len(arrays[0]), dtype=ORIGIN)
        origins["end_us"] = arrays[0]["end_us"]
        complete = np.ones(len(origins), dtype=bool)
        for i, (pool, history, frame) in enumerate(
            zip(pools, self.geometry.history, FRAMES)
        ):
            local = np.searchsorted(pool["end_us"], origins["end_us"], side="right") - 1
            origins["latest"][:, i] = self.starts[i] + local
            enough = local >= history - 1
            complete &= enough
        # A validation origin may use past-year context. Only the origin/target
        # year determines the split; the target reader must purge future targets.
        splits = (origins["end_us"] >= self.geometry.cutoff).astype(np.uint8)
        stats = dict(
            source_origins=len(origins),
            warmup_deferred=int((~complete).sum()),
            boundary_tail_deferred=0,
            new_bar_rows=[len(a) for a in arrays],
        )
        origins, splits = origins[complete], splits[complete]
        blocks, block_splits = [], []
        # Split changes and liquidity reclassification cannot mix inside a block.
        changes = (
            np.r_[0, np.flatnonzero(np.diff(splits)) + 1, len(splits)]
            if len(splits)
            else [0]
        )
        for left, right in zip(changes[:-1], changes[1:]):
            key = (int(liquid), int(splits[left]))
            if self.pending_key is not None and self.pending_key != key:
                stats["boundary_tail_deferred"] += len(self.pending)
                self.pending = np.empty(0, dtype=ORIGIN)
            self.pending_key = key
            combined = np.concatenate((self.pending, origins[left:right]))
            count = len(combined) // BLOCK
            if count:
                blocks.append(combined[: count * BLOCK].reshape(count, BLOCK))
                block_splits.extend([key[1]] * count)
            self.pending = combined[count * BLOCK :].copy()
        result_blocks = (
            np.concatenate(blocks) if blocks else np.empty((0, BLOCK), dtype=ORIGIN)
        )
        # The stored block references use local pool indices for direct mmap reads.
        result_blocks["latest"] -= self.starts
        keep = []
        for i, (pool, history) in enumerate(zip(pools, self.geometry.history)):
            begin = max(0, len(pool) - history)
            if len(self.pending):
                begin = min(
                    begin,
                    int(self.pending["latest"][0, i] - self.starts[i]) - history + 1,
                )
            if begin < 0:
                raise ValueError("Pending origin lost its cached context")
            keep.append(begin)
        result = dict(
            views=pools,
            blocks=result_blocks,
            splits=block_splits,
            pool_starts=self.starts.tolist(),
            cache_slices=keep,
            pending=self.pending.copy(),
            pending_key=self.pending_key,
            stats=stats,
        )
        for i, begin in enumerate(keep):
            self.views[i] = pools[i][begin:].copy()
            self.starts[i] += begin
        stats["pending_origins"] = len(self.pending)
        return result

    def fingerprint(self):
        return digest(
            dict(
                starts=self.starts.tolist(),
                pending_key=self.pending_key,
                pending=hashlib.sha256(self.pending.tobytes()).hexdigest(),
                views=[hashlib.sha256(a.tobytes()).hexdigest() for a in self.views],
            )
        )
