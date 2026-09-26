"""Pure typed projection and exact seal for Strategy 1 candidate boundaries.

The producer calls this after read-only columnar preparation and publishes the
result to app-owned arte tables. Backtest independently verifies the same seal
on SELECTed rows; it never calls a producer or writes market data.
"""
from __future__ import annotations

from hashlib import sha256
from struct import pack
from typing import Any, Mapping
from uuid import UUID

import numpy as np

from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


VALUE_FIELDS = (
    "boundary_ms", "source_row_index", "episode_start_ms",
    "macd_1s_boundary_ms", "macd_5s_boundary_ms",
    "macd_10s_boundary_ms", "macd_30s_boundary_ms",
    "stop_30s_boundary_ms", "stop_low_int",
)
_MACD_RESOLUTIONS = (1_000, 5_000, 10_000, 30_000)
_SEAL_PREFIX = b"strategy-one-candidates-v1\0"


def candidate_content_hash(rows: tuple[Mapping[str, Any], ...]) -> str:
    """Hash ordered scalar values, including an exact empty-set identity."""
    digest = sha256(_SEAL_PREFIX)
    previous = 0
    for row in rows:
        values = tuple(row[field] for field in VALUE_FIELDS)
        if (any(type(value) is not int or not 0 <= value < 2**64
                for value in values)
                or values[0] <= previous):
            raise ValueError("Strategy 1 candidate rows are not ordered typed scalars")
        digest.update(pack(">9Q", *values))
        previous = values[0]
    digest.update(pack(">Q", len(rows)))
    return digest.hexdigest()


def project_candidate_rows(prepared: PreparedStrategyOneTicker, *,
                           build_id: str, session_date: str,
                           derivation_attempt_id: str) -> tuple[dict[str, Any], ...]:
    """Flatten one causal candidate schedule into normalized integer columns."""
    if (not isinstance(prepared, PreparedStrategyOneTicker)
            or not build_id or not session_date or not prepared.ticker
            or type(prepared.source_rows) is not int
            or not 0 < prepared.source_rows <= 1_000_000):
        raise ValueError("Strategy 1 candidate projection lacks pinned scope")
    attempt = str(UUID(derivation_attempt_id))
    arrays = (
        np.asarray(prepared.row_index), np.asarray(prepared.boundary_ms),
        np.asarray(prepared.episode_start_ms),
        np.asarray(prepared.stop_bar_boundary_ms),
        np.asarray(prepared.stop_low_int),
    )
    macd = np.asarray(prepared.macd_boundary_ms)
    count = len(arrays[0])
    if (any(array.shape != (count,) or array.dtype.kind not in "iu"
            for array in arrays)
            or macd.shape != (count, 4) or macd.dtype.kind not in "iu"):
        raise ValueError("Strategy 1 candidate scalar arrays are not aligned")
    rows = []
    prior_index = -1
    prior_boundary = 0
    for index in range(count):
        source_index, boundary, episode, stop_bar, low = (
            int(array[index]) for array in arrays)
        macd_clocks = tuple(int(value) for value in macd[index])
        if (not prior_index < source_index < prepared.source_rows
                or not prior_boundary < boundary <= 57_600_000
                or boundary % 100 or not 0 < episode <= boundary
                or episode % 100 or boundary - episode > 300_000
                or not 0 < stop_bar <= boundary or stop_bar % 30_000
                or boundary - stop_bar >= 30_000 or low <= 0
                or any(not 0 < clock <= boundary or clock % resolution
                       or boundary - clock >= resolution
                       for clock, resolution in zip(macd_clocks, _MACD_RESOLUTIONS))):
            raise ValueError("Strategy 1 candidate violates causal completed boundaries")
        row = dict(zip(VALUE_FIELDS, (
            boundary, source_index, episode, *macd_clocks, stop_bar, low)))
        rows.append({
            "source_build_id": build_id, "session_date": session_date,
            "ticker": prepared.ticker, "derivation_attempt_id": attempt,
            **row,
        })
        prior_index, prior_boundary = source_index, boundary
    result = tuple(rows)
    candidate_content_hash(result)
    return result
