"""Normalized Strategy 1 portfolio-allocation fill family.

The source payload is already a closed scalar contract in V3. V4 gives it a
separate physical table and commits its parent event and detail together.
No JSON, blob, or legacy V3 table is an execution authority here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from src.backend.backtest_portfolio_allocation_v3 import (
    ALLOCATION as V3_ALLOCATION,
    project_portfolio_allocation_v3,
    seal_portfolio_allocation_v3,
)


ALLOCATION = replace(V3_ALLOCATION, name="trading_portfolio_allocation_fill_v4")


@dataclass(frozen=True, slots=True)
class V4PortfolioAllocationBatch:
    base: Any
    allocation: Mapping[str, Any]

    def __post_init__(self) -> None:
        from src.trading_runtime.arte_journal_writer import TypedJournalBatch

        if (not isinstance(self.base, TypedJournalBatch)
                or self.base.status != "running"
                or len(self.base.events) != 1
                or (self.base.events[0]["category"],
                    self.base.events[0]["entity_type"])
                != ("portfolio_management", "portfolio_allocation")
                or not isinstance(self.allocation, Mapping)):
            raise ValueError("V4 allocation requires one typed parent event")
        object.__setattr__(self, "allocation",
                           MappingProxyType(dict(self.allocation)))


__all__ = ("ALLOCATION", "V4PortfolioAllocationBatch",
           "project_portfolio_allocation_v3", "seal_portfolio_allocation_v3")
