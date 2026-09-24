"""Causal in-memory V7 over persisted completed one-second bars.

This consumes a typed prior-session seed and never queries or writes market
products. Structural projections are reused until the engine revision changes.
"""
from __future__ import annotations

from datetime import date, datetime
from math import prod
from typing import Any, Mapping, Sequence

from src.backend.swing_book_source import session_bounds
from src.market_engine.streaming_level_book import StreamingLevelBook, VERSION
from src.market_engine.v7_qmd import projection


class FixedV7Stream:
    def __init__(self, seed: Mapping[str, Any], *, ticker: str, session: date,
                 splits: Sequence[dict[str, Any]] = ()) -> None:
        start, end = session_bounds(session.isoformat())
        if float(seed["available_at"]) > start.timestamp():
            raise ValueError("V7 seed was not available at the session opening")
        if str(seed["session"]) >= session.isoformat():
            raise ValueError("V7 seed must precede the simulated session")
        factor = prod(float(item["split_from"]) / float(item["split_to"])
                      for item in splits)
        self.engine = StreamingLevelBook(dict(seed), ticker=ticker,
            session=session.isoformat(), start=start.timestamp(), end=end.timestamp(),
            split_factor=factor, split_evidence=splits)
        self._last_projection_revision = -1
        self._levels: list[dict[str, Any]] = []
        self._latest_completed_second = start.timestamp()

    def update_second(self, row: Mapping[str, Any], *, at: datetime) -> None:
        """Advance exactly one completed, valid 1s bar at its close boundary."""
        if at.tzinfo is None or int(row["resolution_ms"]) != 1_000:
            raise ValueError("V7 requires a timezone-aware completed 1s bar")
        stamp = at.timestamp()
        if stamp <= self._latest_completed_second:
            raise ValueError("V7 completed seconds must be strictly increasing")
        if int(row.get("price_valid") or 0) != 1 or int(row.get("extremes_valid") or 0) != 1:
            raise ValueError("V7 cannot fit an incomplete persisted second")
        scale = 10_000.0
        bar = {"t": stamp, "open": float(row["open_int"]) / scale,
               "high": float(row["high_int"]) / scale,
               "low": float(row["low_int"]) / scale,
               "close": float(row["close_int"]) / scale,
               "volume": float(row["volume"])}
        self.engine.update(bar, observed_at=stamp)
        self._latest_completed_second = stamp

    def context(self, *, as_of: datetime) -> dict[str, Any]:
        """Return the latest causal structure, never advancing from future bars."""
        if as_of.tzinfo is None or not self.engine.start <= as_of.timestamp() <= self.engine.end:
            raise ValueError("V7 projection time is outside the session")
        if as_of.timestamp() < self._latest_completed_second:
            raise ValueError("V7 projection cannot rewind before its consumed bars")
        revision = getattr(self.engine, "_projection_revision", 0)
        if revision != self._last_projection_revision:
            self._levels = projection(self.engine, as_of.timestamp(), {}, False)["unified_levels"]
            self._last_projection_revision = revision
        return {
            "qmd_structure_unified_levels": self._levels,
            "qmd_structure_session_high": self.engine.hod,
            "qmd_level_book_version": VERSION,
            "v7_seed_input_policy": self.engine.seed_input_policy,
        }
