"""Bounded in-memory compaction of normalized V4 Backtest event units.

STRATEGY CREATION RULES: this changes only journal transport grouping. Every
source record and typed child remains a scalar row in its existing ClickHouse
family; no market product is built and no JSON/blob is persisted. Publication
must still verify the complete mixed family graph before advancing Keeper.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID

from .arte_journal_writer import (
    TypedJournalBatch, V4BrokerAcknowledgementBatch, V4OrderCancelBatch,
    V4OrderRepriceBatch, V4ProtectionChangeBatch, V4StrategyOneEntryBatch,
    _can_coalesce, _coalesce_unpublished,
)
from .arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from .arte_protection_reconciliation_v4 import V4ProtectionReconciliationBatch
from .arte_reservation_reason_v4 import V4ReservationReasonBatch
from .arte_risk_action_v4 import V4RiskActionBatch


_CHILD_KEYS = (
    "entry_evidence", "allocations", "reservation_reasons",
    "acknowledgements", "cancellations", "repricings", "risk_actions",
    "risk_replies", "protection_changes", "protection_entry_orders",
    "protection_reconciliations", "reconciliation_actions",
    "reconciliation_replies",
)
_EVENT_PARENT_KEYS = frozenset({
    "entry_evidence", "allocations", "reservation_reasons",
    "acknowledgements", "cancellations", "repricings", "risk_actions",
    "protection_changes", "protection_reconciliations",
})


@dataclass(frozen=True, slots=True)
class V4CompoundBatch:
    """One future commit with its original units retained for source fencing."""

    base: TypedJournalBatch
    units: tuple[Any, ...]
    children: Mapping[str, tuple[Mapping[str, Any], ...]]

    def __post_init__(self) -> None:
        if (self.base.status != "running" or len(self.units) < 2
                or set(self.children) != set(_CHILD_KEYS)
                or len(self.base.events) > 512
                or len(self.base.events)
                   != self.base.last_sequence - self.base.first_sequence + 1):
            raise ValueError("V4 compound lacks a bounded contiguous event prefix")
        if any(len(self.children[key]) > 65_536 for key in _CHILD_KEYS):
            raise ValueError("V4 compound exceeds a normalized family readback bound")
        object.__setattr__(self, "children", MappingProxyType({
            key: tuple(MappingProxyType(dict(row)) for row in self.children[key])
            for key in _CHILD_KEYS
        }))


def _unit_children(unit: Any) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    if type(unit) is TypedJournalBatch:
        return ()
    if type(unit) is V4StrategyOneEntryBatch:
        return tuple(("entry_evidence", row) for row in unit.entry_evidence)
    if type(unit) is V4PortfolioAllocationBatch:
        return (("allocations", unit.allocation),)
    if type(unit) is V4ReservationReasonBatch:
        return tuple(("reservation_reasons", row) for row in unit.reasons)
    if type(unit) is V4BrokerAcknowledgementBatch:
        return (("acknowledgements", unit.acknowledgement),)
    if type(unit) is V4OrderCancelBatch:
        return (("cancellations", unit.cancellation),)
    if type(unit) is V4OrderRepriceBatch:
        return (("repricings", unit.repricing),)
    if type(unit) is V4RiskActionBatch:
        return (("risk_actions", unit.action),) + tuple(
            ("risk_replies", row) for row in unit.replies)
    if type(unit) is V4ProtectionChangeBatch:
        return (("protection_changes", unit.change),) + tuple(
            ("protection_entry_orders", row) for row in unit.entry_orders)
    if type(unit) is V4ProtectionReconciliationBatch:
        return (("protection_reconciliations", unit.reconciliation),) + tuple(
            ("reconciliation_actions", row) for row in unit.actions) + tuple(
            ("reconciliation_replies", row) for row in unit.replies)
    raise TypeError("V4 compound contains an unsupported journal envelope")


def coalesce_v4_units(
    units: Sequence[Any], *, max_events: int = 512,
) -> V4CompoundBatch:
    """Rekey adjacent unpublished micro-units without mutating source rows."""
    if (len(units) < 2 or type(max_events) is not int
            or not 2 <= max_events <= 512):
        raise ValueError("V4 compound needs two or more bounded units")
    bases = []
    for unit in units:
        _unit_children(unit)  # Reject foreign or legacy envelopes before access.
        base = unit if type(unit) is TypedJournalBatch else unit.base
        if base.status != "running":
            raise ValueError("V4 terminal events cannot enter a running compound")
        if bases and not _can_coalesce(bases[-1], base, max_events):
            raise ValueError("V4 compound units are not an adjacent run prefix")
        bases.append(base)
    if bases[-1].last_sequence - bases[0].first_sequence + 1 > max_events:
        raise ValueError("V4 compound exceeds its event bound")
    combined = _coalesce_unpublished(tuple(bases))
    event_ids = {str(UUID(str(row["record_id"]))) for row in combined.events}
    if len(event_ids) != len(combined.events):
        raise ValueError("V4 compound repeats a source event identity")
    children: dict[str, list[Mapping[str, Any]]] = {
        key: [] for key in _CHILD_KEYS
    }
    child_ids: dict[str, set[str]] = {key: set() for key in _CHILD_KEYS}
    nested_parents: list[tuple[str, str]] = []
    for unit, base in zip(units, bases, strict=True):
        for key, source in _unit_children(unit):
            if (not isinstance(source, Mapping)
                    or source.get("run_id") != combined.run_id
                    or str(UUID(str(source.get("batch_id")))) != base.batch_id):
                raise ValueError("V4 scalar child changed its source batch")
            parent_id = str(UUID(str(source.get(
                "parent_record_id", source.get("record_id")))))
            identity = str(UUID(str(source["record_id"])))
            if key in _EVENT_PARENT_KEYS and parent_id not in event_ids:
                raise ValueError("V4 scalar child has no event parent")
            if identity in child_ids[key]:
                raise ValueError("V4 compound repeats a scalar child identity")
            child_ids[key].add(identity)
            if key not in _EVENT_PARENT_KEYS:
                nested_parents.append((parent_id, identity))
            children[key].append({
                **{name: value for name, value in source.items()
                   if name != "content_hash"},
                "batch_id": combined.batch_id,
            })
    all_ids = event_ids | set().union(*child_ids.values())
    if any(parent == identity or parent not in all_ids
           for parent, identity in nested_parents):
        raise ValueError("V4 scalar child has no normalized compound parent")
    return V4CompoundBatch(combined, tuple(units), children)
