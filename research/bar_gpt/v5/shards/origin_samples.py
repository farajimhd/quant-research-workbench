"""Vectorized final 64-bar packing; no loader-side aggregation or window assembly."""

from datetime import datetime, timezone
import numpy as np

from .contract import FRAMES, SAMPLE_DTYPE, pack, seed_for
from .storage import adjust_splits

CUTOFF = int(datetime(2025, 10, 1, 4, tzinfo=timezone.utc).timestamp() * 1_000_000)


def arrays(frame):
    """Convert final Arrow list columns to contiguous [bars,21] CPU arrays."""
    values = frame["values"].list.to_array(21).to_numpy().astype(np.float32, copy=False)
    valid = frame["valid"].list.to_array(21).to_numpy().astype(np.uint8, copy=False)
    ends = frame["end_us"].to_numpy().astype(np.int64, copy=False)
    if (
        not np.isfinite(values).all()
        or not valid[:, :6].all()
        or np.any(values[:, :6] <= 0)
        or np.any(np.diff(ends) <= 0)
    ):
        raise ValueError("Malformed or incomplete final trade bars")
    return values, valid, ends


def select_origins(count, seed, fraction):
    """Select origins without replacement; randomized rounding is unbiased at 20%."""
    rng = np.random.default_rng(seed)
    expected = count * fraction
    selected = int(expected) + int(rng.random() < expected - int(expected))
    return (
        np.sort(rng.choice(count, selected, replace=False))
        if selected
        else np.empty(0, dtype=np.int64)
    )


def samples(
    values, valid, ends, resolution, seed, actions, *, fraction=0.2, previous=()
):
    """Pack sampled complete sliding contexts and return the final 63-bar tail.

    Inputs are final bars [bars,21], masks [bars,21], and UTC clocks [bars].
    previous contains up to 63 unadjusted bars from the preceding coarse span.
    Fine chunks pass no previous state: incomplete chunk contexts are dropped.
    Split adjustment is applied only to copied final samples, never to the tail.
    """
    if previous:
        values = np.concatenate((previous[0], values))
        valid = np.concatenate((previous[1], valid))
        ends = np.concatenate((previous[2], ends))
    if np.any(np.diff(ends) <= 0):
        raise ValueError("Overlapping or unordered coarse timestamp spans")
    tail = (values[-63:].copy(), valid[-63:].copy(), ends[-63:].copy())
    origins = select_origins(max(0, len(ends) - 63), seed, fraction)
    if not len(origins):
        return np.empty(0, dtype=SAMPLE_DTYPE), tail
    # Gather selected origins only; unselected overlapping contexts allocate no
    # sample tensors. The source bars themselves remain a small transient array.
    indices = origins[:, None] + np.arange(64)[None, :]
    result = pack(values[indices], valid[indices], ends[indices], resolution, 0)
    train = result["end_us"][:, -1] < CUTOFF
    validation = result["end_us"][:, 0] - resolution >= CUTOFF
    result = result[train | validation]
    result["split"] = (result["end_us"][:, 0] - resolution >= CUTOFF).astype(np.uint8)
    adjust_splits(result, actions)
    return result, tail
