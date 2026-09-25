"""Pure content seals shared by eligible-price producer and Backtest reader.

ClickHouse Float64 sum order can drift in the last bits after part merges. The
volume is checked separately with a numeric tolerance; the v2 seal hashes
stable row identities and their per-price persisted values instead.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping


def summary_digest(row: Mapping[str, Any]) -> str:
    values = ["eligible-price-summary-v2"] + [str(row[key]) for key in (
        "row_count", "unique_keys", "eligible_bucket_count", "row_hash")]
    return sha256("\n".join(values).encode()).hexdigest()


def legacy_summary_digest(row: Mapping[str, Any], *,
                          published_volume: Any) -> str:
    """Verify immutable early coverage rows without rewriting or duplicating them."""
    values = [str(row[key]) for key in (
        "row_count", "unique_keys", "eligible_bucket_count")]
    values.extend((str(published_volume), str(row["row_hash"])))
    return sha256("\n".join(values).encode()).hexdigest()


def matches_summary_digest(row: Mapping[str, Any], *, content_hash: str,
                           published_volume: Any) -> bool:
    return content_hash in (summary_digest(row),
                            legacy_summary_digest(row,
                                                  published_volume=published_volume))
