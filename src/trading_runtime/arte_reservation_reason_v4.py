"""Scalar reservation-reason child for the V4 Strategy 1 journal.

The existing normalized V1 reason table is shared by run identity; no V3
commit, SQLite artifact, JSON payload, or legacy strategy is an authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from src.backend.backtest_squeeze_episode_schema import RESERVATION_REASON
from src.backend.backtest_reservation_reason_v3 import (
    project_reservation_reasons_v3, seal_reservation_reason_family_v3,
)


@dataclass(frozen=True, slots=True)
class V4ReservationReasonBatch:
    base: Any
    reasons: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        from src.trading_runtime.arte_journal_writer import TypedJournalBatch

        if (not isinstance(self.base, TypedJournalBatch)
                or self.base.status != "running"
                or len(self.base.events) != 1
                or len(self.base.portfolio_reservation_events) != 1
                or (self.base.events[0]["category"],
                    self.base.events[0]["entity_type"])
                != ("portfolio_management", "portfolio_reservation")
                or not self.reasons):
            raise ValueError("V4 reservation reasons require one typed parent")
        object.__setattr__(self, "reasons", tuple(
            MappingProxyType(dict(row)) for row in self.reasons))


__all__ = ("RESERVATION_REASON", "V4ReservationReasonBatch",
           "project_reservation_reasons_v3", "seal_reservation_reason_family_v3")
