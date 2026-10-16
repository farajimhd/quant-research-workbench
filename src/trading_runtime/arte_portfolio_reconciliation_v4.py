"""Shared normalized V4 Portfolio reconciliation differences and envelope.

The existing reconciliation event remains the parent. Its children use the
same closed scalar projection and seal as the established journal contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from src.backend.backtest_reconciliation_v3 import (
    CHILD, project_reconciliation_v3, seal_reconciliation_difference_family_v3,
)

DIFFERENCE = replace(CHILD, name="trading_portfolio_reconciliation_difference_v4")


def seal_portfolio_reconciliation_v4(differences, events, parents, **scope):
    """Avoid serializing empty seals on unrelated V4 event batches."""
    if (not differences and not parents and not any(
            (row["category"], row["entity_type"])
            == ("portfolio_management", "portfolio_reconciliation") for row in events)):
        return
    seal_reconciliation_difference_family_v3(differences, events, parents, **scope)


@dataclass(frozen=True, slots=True)
class V4PortfolioReconciliationBatch:
    base: Any
    differences: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch

        if (type(self.base) is not TypedJournalBatch or self.base.status != "running"
                or len(self.base.events) != 1
                or (self.base.events[0]["category"], self.base.events[0]["entity_type"])
                != ("portfolio_management", "portfolio_reconciliation")):
            raise ValueError("V4 Portfolio reconciliation requires one normalized event")
        if (type(self.differences) is not tuple
                or any(not isinstance(row, Mapping) for row in self.differences)):
            raise ValueError("V4 reconciliation differences require typed scalar rows")
        rows = tuple(MappingProxyType(dict(row)) for row in self.differences)
        seal_portfolio_reconciliation_v4(rows, self.base.events,
            self.base.portfolio_reconciliation_events,
            run_id=self.base.run_id, batch_id=self.base.batch_id)
        object.__setattr__(self, "differences", rows)
