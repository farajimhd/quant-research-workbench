"""A stateless cosine schedule derived from durable training progress."""

import math


def learning_rate(completed_windows, total_windows, peak=1e-4, floor=1e-6):
    """Return the rate at a window boundary, including both exact endpoints.

    Using consumed examples instead of elapsed time makes pauses harmless and avoids
    changing the decay when the final batch is short. The trainer samples this curve
    at the END of each proposed batch: the final optimizer update uses `floor`.
    The full curve starts at `peak`; the first finite batch is slightly below it.
    """
    if total_windows <= 0 or not 0 <= completed_windows <= total_windows:
        raise ValueError("Invalid scheduler progress")
    if not 0 < floor <= peak:
        raise ValueError("Require 0 < floor <= peak")
    fraction = completed_windows / total_windows
    return floor + 0.5 * (peak - floor) * (1 + math.cos(math.pi * fraction))
