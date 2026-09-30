"""Pure scalar and columnar eligibility for Strategy 12's recent BOS entry.

The clocks come from certified completed 1s BOS and 100ms candidate facts.
This rule neither derives structure nor authorizes orders. Elapsed time can
reject a new entry; it never exits an existing holding.
"""
from __future__ import annotations

import numpy as np

MAX_BOS_ENTRY_AGE_MS = 30_000


def recent_bos_entry(*, boundary_ms: int,
                     bos_break_boundary_ms: int | None) -> bool:
    """Reject missing BOS; malformed or future evidence is an integrity error."""
    if (type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000
            or boundary_ms % 100):
        raise ValueError("Recent BOS entry needs a completed 100ms boundary")
    if bos_break_boundary_ms is None:
        return False
    if (type(bos_break_boundary_ms) is not int
            or not 0 < bos_break_boundary_ms <= boundary_ms
            or bos_break_boundary_ms % 1_000):
        raise ValueError("Recent BOS entry needs a causal completed 1s break")
    return boundary_ms - bos_break_boundary_ms <= MAX_BOS_ENTRY_AGE_MS


def recent_bos_entry_mask(boundaries_ms: np.ndarray,
                          bos_break_boundaries_ms: np.ndarray) -> np.ndarray:
    """Return shape (N,) eligibility from aligned integer clocks; zero is missing.

    Native comparisons operate independently at each candidate. A future tail
    cannot alter earlier decisions. No per-candidate Python rule invocation.
    """
    boundaries = np.asarray(boundaries_ms)
    breaks = np.asarray(bos_break_boundaries_ms)
    if (boundaries.ndim != 1 or breaks.shape != boundaries.shape
            or boundaries.dtype.kind not in "iu" or breaks.dtype.kind not in "iu"
            or np.any(boundaries <= 0) or np.any(boundaries > 57_600_000)
            or np.any(boundaries % 100) or np.any(breaks < 0)
            or np.any(breaks > boundaries) or np.any(breaks % 1_000)):
        raise ValueError("Recent BOS entry needs aligned causal completed clocks")
    # Bounds above make conversion/subtraction safe even for unsigned inputs.
    age = boundaries.astype(np.int64, copy=False) - breaks.astype(np.int64, copy=False)
    return (breaks > 0) & (age <= MAX_BOS_ENTRY_AGE_MS)
