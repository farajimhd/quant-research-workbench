"""Phase-one v6 contract: 18 market channels, with no halt fields.

The archived v4/v5 contract remains readable for audited migration. New shards
contain only trade/bid/ask OHLC, volume or closing size, and observed event count.
Each prepared record is [64,18] Float32 values plus masks, UTC clocks, timeframe
and split. Nine UTC features are appended later by the Torch input transform.
"""

import numpy as np
from research.bar_gpt.v5.quote_quality_v1 import sanitize
from .contract import FRAMES, WIDTH, digest, file_hash, seed_for, write_json

VERSION = "bargpt-ordinal-balanced-64-v6-no-halts"
CHANNELS = tuple(
    f"{side}_{field}"
    for side, fields in (
        ("trade", ("open", "high", "low", "close", "volume", "count")),
        ("bid", ("open", "high", "low", "close", "size", "count")),
        ("ask", ("open", "high", "low", "close", "size", "count")),
    )
    for field in fields
)
MARKET_CHANNELS = len(CHANNELS)
SAMPLE_DTYPE = np.dtype(
    [
        ("values", "<f4", (WIDTH, MARKET_CHANNELS)),
        ("valid", "u1", (WIDTH, MARKET_CHANNELS)),
        ("end_us", "<i8", (WIDTH,)),
        ("timeframe_us", "<i8"),
        ("split", "u1"),
    ]
)


def pack(values, valid, ends, frame, split):
    """Copy complete final arrays [N,64,18] into the mmap-ready v6 record dtype."""
    values, valid, _ = sanitize(values, valid)
    if (
        values.shape[1:] != (WIDTH, MARKET_CHANNELS)
        or valid.shape != values.shape
        or ends.shape != values.shape[:2]
    ):
        raise ValueError("Expected values/masks [N,64,18] and UTC clocks [N,64]")
    if (
        not np.isfinite(values).all()
        or not np.isin(valid, (0, 1)).all()
        or np.any(ends <= 0)
        or np.any(np.diff(ends, axis=1) <= 0)
    ):
        raise ValueError("Phase-one samples require complete finite, ordered real bars")
    result = np.zeros(len(values), dtype=SAMPLE_DTYPE)
    result["values"], result["valid"], result["end_us"] = values, valid, ends
    result["timeframe_us"], result["split"] = frame, split
    return result
