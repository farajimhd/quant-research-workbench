"""Immutable phase-two input geometry and mmap storage types.

Market values come exclusively from the phase-one canonical bar producer.
Raw bars remain on their original share basis: each reconstructed 64-bar window
uses phase one's split adjustment at its own endpoint, before the same Torch
transform adds nine UTC inputs. No labels, decoder outputs or GPU embeddings
are materialized here; the encoder checkpoint is still changing in phase one.
"""

from datetime import datetime, timezone
import numpy as np

from ..shards.phase1_contract import CHANNELS, pack
from ..shards.storage import adjust_splits
from ..shards.contract import digest, file_hash, seed_for, write_json

VERSION = "bargpt-phase2-stream-v1"
# Each tuple is (microseconds, observed context bars, historical window stride).
SPECS = (
    (1_000_000, 544, 32),
    (10_000_000, 288, 32),
    (60_000_000, 288, 32),
    (3_600_000_000, 160, 32),
    (86_400_000_000, 80, 16),
)
FRAMES = tuple(s[0] for s in SPECS)
BLOCK = 256  # Consecutive observed 1s decisions; gaps retain their actual clocks.
CAPACITY = 4_000_000_000_000  # Stable inventory; execution cap can grow within it.
# Match the already-training encoder's cutoff; never expose its training era as
# a supposedly unseen phase-two validation era.
CUTOFF = int(datetime(2024, 4, 16, 4, tzinfo=timezone.utc).timestamp() * 1e6)
BAR = np.dtype([("values", "<f4", (18,)), ("valid", "u1", (18,)), ("end_us", "<i8")])
DECISION = np.dtype(
    [("latest", "<i8", (5,)), ("end_us", "<i8"), ("context_start_us", "<i8")]
)


def windows(latest, frame_index):
    """Return [decisions,windows,64] indices and [decisions,windows] presence.

    ``latest`` is an Int64 vector of the last completed observed bar index.
    Older windows have fixed observed-bar offsets, never fabricated clock bars.
    """
    _, count, stride = SPECS[frame_index]
    offsets = np.arange(count - 64, -1, -stride, dtype=np.int64)
    last = np.asarray(latest)[..., None] - offsets
    return last[..., None] - np.arange(63, -1, -1), last >= 63


def encoder_window(bars, frame, actions):
    """Prepare one [64,18] raw window using phase one's exact split semantics.

    ``bars`` is BAR[64] from the stream's immutable segments; ``actions`` is the
    frozen ticker-specific executed-action catalog. Return phase-one records
    [1], ready for its existing scaling and nine-channel UTC transform.
    """
    if len(bars) != 64:
        raise ValueError("An encoder window requires 64 actual observations")
    sample = pack(
        bars["values"][None], bars["valid"][None], bars["end_us"][None], frame, 0
    )
    adjust_splits(sample, actions)
    return sample


class Context:
    """Bounded timestamp cache; raw market arrays leave RAM after publication.

    Each frame retains only its configured history plus pending decision rows.
    ``counts`` are absolute indices in the stream, including bootstrap history.
    """

    def __init__(self, counts=None, tails=None, pending=None, initialized=False):
        self.initialized = initialized  # Older receipts restore this as True.
        self.counts = list(counts or [0] * 5)
        self.tails = tails or [np.empty(0, dtype=np.int64) for _ in SPECS]
        self.pending = np.empty(0, dtype=DECISION) if pending is None else pending

    def append(self, arrays, decisions=True):
        """Consume five BAR[N] arrays; return full causal blocks and split IDs.

        Coarser bars may finish after some decisions in the same fetched range.
        searchsorted(side='right') makes those future observations inaccessible.
        """
        clocks = [
            np.concatenate((old, new["end_us"])) for old, new in zip(self.tails, arrays)
        ]
        for old, new in zip(self.tails, arrays):
            if len(new) and len(old) and new["end_us"][0] <= old[-1]:
                raise ValueError("Overlapping stream publication")
        anchor = arrays[0]["end_us"] if decisions else np.empty(0, dtype=np.int64)
        rows = np.empty(len(anchor), dtype=DECISION)
        rows["end_us"] = anchor
        rows["context_start_us"] = anchor
        for i, ((frame, width, _), times) in enumerate(zip(SPECS, clocks)):
            offset = self.counts[i] - len(self.tails[i])
            local = np.searchsorted(times, anchor, side="right") - 1
            rows["latest"][:, i] = np.where(local >= 0, offset + local, -1)
            # Purge conservatively using every available history bar, including
            # partial contexts. No validation window can cross the cutoff.
            if len(times) and len(anchor):
                first = np.maximum(0, local - width + 1)
                starts = times[first] - frame
                rows["context_start_us"] = np.minimum(
                    rows["context_start_us"], np.where(local >= 0, starts, anchor)
                )
            self.counts[i] += len(arrays[i])
            self.tails[i] = times[-width:].copy()
        joined = np.concatenate((self.pending, rows))
        full = len(joined) // BLOCK
        blocks = joined[: full * BLOCK].reshape(full, BLOCK)
        self.pending = joined[full * BLOCK :].copy()
        train = blocks["end_us"][:, -1] < CUTOFF
        val = blocks["context_start_us"].min(axis=1) >= CUTOFF
        return blocks, np.where(train, 0, np.where(val, 1, 2)).astype(np.uint8)
