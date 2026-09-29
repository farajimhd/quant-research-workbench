"""Bounded in-memory compaction of normalized V4 Backtest event units.

STRATEGY CREATION RULES: this changes only journal transport grouping. Every
source record and typed child remains a scalar row in its existing ClickHouse
family; no market product is built and no JSON/blob is persisted. Publication
must still verify the complete mixed family graph before advancing Keeper.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID

from .arte_journal_writer import (
    TypedJournalBatch, V4BrokerAcknowledgementBatch, V4OrderCancelBatch,
    V4OrderRepriceBatch, V4ProtectionChangeBatch, V4StrategyOneEntryBatch,
    _can_coalesce, _coalesce_unpublished,
)
from .arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS
from .arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from .arte_protection_reconciliation_v4 import V4ProtectionReconciliationBatch
from .arte_reservation_reason_v4 import V4ReservationReasonBatch
from .arte_risk_action_v4 import V4RiskActionBatch
from .arte_oms_tactic_projection import (
    V4OmsTacticBatch, PARENT_TABLE, STEP_TABLE, seal_oms_tactic_rows,
)


_CHILD_KEYS = (
    "command_lineages",
    "entry_evidence", "allocations", "reservation_reasons",
    "acknowledgements", "cancellations", "repricings", "risk_actions",
    "risk_replies", "protection_changes", "protection_entry_orders",
    "protection_reconciliations", "reconciliation_actions",
    "reconciliation_replies", "oms_tactics", "oms_tactic_steps",
)
_EVENT_PARENT_KEYS = frozenset({
    "command_lineages",
    "entry_evidence", "allocations", "reservation_reasons",
    "acknowledgements", "cancellations", "repricings", "risk_actions",
    "protection_changes", "protection_entry_orders",
    "protection_reconciliations", "oms_tactics",
})
_MULTIROW_CHILD_KEYS = frozenset({"reservation_reasons", "protection_entry_orders"})


@dataclass(frozen=True, slots=True)
class V4CompoundBatch:
    """One future commit with its original units retained for source fencing."""

    base: TypedJournalBatch
    units: tuple[Any, ...]
    children: Mapping[str, tuple[Mapping[str, Any], ...]]

    def __post_init__(self) -> None:
        if (self.base.status != "running" or len(self.units) < 2
                or set(self.children) != set(_CHILD_KEYS)
                or len(self.base.events) > MAX_V4_COMMIT_EVENTS
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
        return tuple(("command_lineages", row)
                     for row in unit.v4_command_lineages)
    if type(unit) is V4OmsTacticBatch:
        return (("oms_tactics", unit.tactic_state),) + tuple(
            ("oms_tactic_steps", row) for row in unit.tactic_steps)
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
            or not 2 <= max_events <= MAX_V4_COMMIT_EVENTS):
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
            if identity in child_ids[key] and key not in _MULTIROW_CHILD_KEYS:
                raise ValueError("V4 compound repeats a scalar child identity")
            child_ids[key].add(identity)
            if key not in _EVENT_PARENT_KEYS:
                nested_parents.append((parent_id, identity))
            children[key].append({
                **{name: value for name, value in source.items()
                   if name != "content_hash"},
                "batch_id": combined.batch_id,
            })
        if type(unit) is V4ProtectionChangeBatch:
            # This legacy normalized parent seals child hashes. Rekeying the
            # children changes those hashes even though their order IDs do not.
            from hashlib import sha256

            from src.backend.backtest_protection_change_v3 import ENTRY_ORDER
            from .arte_journal_writer import typed_row
            from .journal_contract import canonical_json

            count = len(unit.entry_orders)
            ordered = children["protection_entry_orders"][-count:] if count else ()
            child_hashes = tuple(
                (row["ordinal"], typed_row(ENTRY_ORDER.name, row)["content_hash"])
                for row in ordered)
            children["protection_changes"][-1]["entry_order_hash"] = sha256(
                canonical_json(list(child_hashes)).encode()).hexdigest()
    all_ids = event_ids | set().union(*child_ids.values())
    if any(parent == identity or parent not in all_ids
           for parent, identity in nested_parents):
        raise ValueError("V4 scalar child has no normalized compound parent")
    return V4CompoundBatch(combined, tuple(units), children)


def _publication_kwargs(unit: Any) -> dict[str, Any]:
    if type(unit) is V4OmsTacticBatch:
        return {"oms_tactic_rows": (unit.tactic_state, unit.tactic_steps)}
    if type(unit) is V4StrategyOneEntryBatch:
        return {"strategy_one_entry_rows": unit.entry_evidence}
    if type(unit) is V4PortfolioAllocationBatch:
        return {"portfolio_allocation_row": unit.allocation}
    if type(unit) is V4ReservationReasonBatch:
        return {"reservation_reason_rows": unit.reasons}
    if type(unit) is V4BrokerAcknowledgementBatch:
        return {"broker_acknowledgement_row": unit.acknowledgement}
    if type(unit) is V4OrderCancelBatch:
        return {"order_cancel_row": unit.cancellation}
    if type(unit) is V4OrderRepriceBatch:
        return {"order_reprice_row": unit.repricing}
    if type(unit) is V4RiskActionBatch:
        return {"risk_action_row": unit.action, "risk_reply_rows": unit.replies}
    if type(unit) is V4ProtectionChangeBatch:
        return {"protection_change_row": unit.change,
                "protection_entry_order_rows": unit.entry_orders}
    if type(unit) is V4ProtectionReconciliationBatch:
        return {"protection_reconciliation_row": unit.reconciliation,
                "protection_reconciliation_actions": unit.actions,
                "protection_reconciliation_replies": unit.replies}
    if type(unit) is TypedJournalBatch:
        return {}
    raise TypeError("V4 compound contains an unsupported journal envelope")


def prepare_compound_v4_families(
    client: Any, compound: V4CompoundBatch,
) -> tuple[tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
           tuple[tuple[str, tuple[dict[str, Any], ...]], ...]]:
    """Seal each original unit, then rekey its normalized rows to one batch.

    This is still pure preparation. The caller must independently read back
    every merged family before publishing a commit or advancing Keeper.
    """
    from .arte_journal_commit_v4 import (
        ACKNOWLEDGEMENT, CANCEL, ENTRY_EVIDENCE, PROTECTION_CHANGE,
        PROTECTION_ENTRY_ORDER, PROTECTION_RECONCILIATION,
        RECONCILIATION_ACTION, RECONCILIATION_REPLY, REPRICE,
        RESERVATION_REASON, RISK_ACTION, RISK_REPLY, V4_ALLOCATION,
        _publish_typed_batch_v4, _sealed_strategy_one_entry_rows,
        seal_portfolio_allocation_v3, seal_protection_changes_v3,
        seal_protection_reconciliation_v4, seal_reservation_reason_family_v3,
        seal_risk_action_v4,
    )
    from .arte_journal_writer import (
        _FAMILIES, _sealed_families, _v4_family_table, typed_row,
    )
    from .arte_journal_schema import V4_ORDER_COMMAND_LINEAGE

    if type(compound) is not V4CompoundBatch:
        raise TypeError("V4 mixed preparation requires a compound batch")
    if getattr(client, "live_v4_lease", None) is not None and any(
            getattr(compound.base, name) for name in (
                "backtest_cursors", "backtest_market_authorities",
                "backtest_progress", "prepared_v7_leases")):
        raise ValueError("Live V4 compound cannot publish Backtest-only families")
    table_for_key = {
        "command_lineages": V4_ORDER_COMMAND_LINEAGE.name,
        "entry_evidence": ENTRY_EVIDENCE.name,
        "allocations": V4_ALLOCATION.name,
        "reservation_reasons": RESERVATION_REASON.name,
        "acknowledgements": ACKNOWLEDGEMENT.name,
        "cancellations": CANCEL.name,
        "repricings": REPRICE.name,
        "risk_actions": RISK_ACTION.name,
        "risk_replies": RISK_REPLY.name,
        "protection_changes": PROTECTION_CHANGE.name,
        "protection_entry_orders": PROTECTION_ENTRY_ORDER.name,
        "protection_reconciliations": PROTECTION_RECONCILIATION.name,
        "reconciliation_actions": RECONCILIATION_ACTION.name,
        "reconciliation_replies": RECONCILIATION_REPLY.name,
        "oms_tactics": PARENT_TABLE,
        "oms_tactic_steps": STEP_TABLE,
    }
    base_names = {_v4_family_table(name) for name, _, _, _ in _FAMILIES}
    extra: dict[str, list[dict[str, Any]]] = {
        name: [] for name in table_for_key.values()
    }
    for unit in compound.units:
        base = unit if type(unit) is TypedJournalBatch else unit.base
        _, micro_families = _publish_typed_batch_v4(
            client, base, _prepare_only=True, **_publication_kwargs(unit))
        for name, rows in micro_families:
            if name in base_names:
                continue
            if name not in extra:
                raise ValueError("V4 micro-unit prepared an unknown scalar family")
            extra[name].extend(typed_row(name, {
                **{key: value for key, value in row.items()
                   if key != "content_hash"},
                "batch_id": compound.base.batch_id,
            }) for row in rows)
    # The protection parent includes a digest of its child rows. All other
    # scalar columns must match micro-preparation exactly; only that digest
    # changes when the child's batch ID is rekeyed.
    protection_name = PROTECTION_CHANGE.name
    for index, source in enumerate(compound.children["protection_changes"]):
        prepared = extra[protection_name][index]
        if any(prepared[key] != value for key, value in source.items()
               if key != "entry_order_hash"):
            raise ValueError("V4 protection parent changed outside its child seal")
        extra[protection_name][index] = typed_row(protection_name, {
            **{key: value for key, value in prepared.items()
               if key != "content_hash"},
            "entry_order_hash": source["entry_order_hash"],
        })
    for key, name in table_for_key.items():
        # Each prepared row above was already schema-checked and hashed by
        # typed_row. Exact scalar equality to the rekeyed source also proves
        # its expected hash; sealing the same children twice is redundant.
        prepared_content = tuple(
            {column: value for column, value in row.items()
             if column != "content_hash"} for row in extra[name])
        expected_content = tuple(dict(row) for row in compound.children[key])
        if prepared_content != expected_content:
            raise ValueError(f"V4 compound lost normalized {name} children")

    ids = lambda name: tuple(row["record_id"] for row in extra[name])
    base_families = _sealed_families(
        compound.base,
        v4_broker_ack_ids=ids(ACKNOWLEDGEMENT.name),
        v4_allocation_ids=ids(V4_ALLOCATION.name),
        v4_order_cancel_ids=ids(CANCEL.name),
        v4_order_reprice_ids=ids(REPRICE.name),
        v4_risk_action_ids=ids(RISK_ACTION.name),
        v4_protection_ids=ids(PROTECTION_CHANGE.name),
        v4_reconciliation_ids=ids(PROTECTION_RECONCILIATION.name),
    )
    if tuple(extra[ENTRY_EVIDENCE.name]) != _sealed_strategy_one_entry_rows(
            compound.base, base_families, tuple(
                {key: value for key, value in row.items() if key != "content_hash"}
                for row in extra[ENTRY_EVIDENCE.name])):
        raise ValueError("V4 compound entry evidence differs from its parent")
    seal_portfolio_allocation_v3(
        extra[V4_ALLOCATION.name], compound.base.events,
        run_id=compound.base.run_id, batch_id=compound.base.batch_id)
    seal_reservation_reason_family_v3(
        extra[RESERVATION_REASON.name], compound.base.events,
        compound.base.portfolio_reservation_events,
        run_id=compound.base.run_id, batch_id=compound.base.batch_id)
    seal_risk_action_v4(
        extra[RISK_ACTION.name], extra[RISK_REPLY.name], compound.base.events,
        run_id=compound.base.run_id, batch_id=compound.base.batch_id)
    seal_protection_changes_v3(
        extra[PROTECTION_CHANGE.name], extra[PROTECTION_ENTRY_ORDER.name],
        compound.base.events, run_id=compound.base.run_id,
        batch_id=compound.base.batch_id)
    seal_protection_reconciliation_v4(
        extra[PROTECTION_RECONCILIATION.name],
        extra[RECONCILIATION_ACTION.name], extra[RECONCILIATION_REPLY.name],
        compound.base.events, run_id=compound.base.run_id,
        batch_id=compound.base.batch_id)
    if extra[PARENT_TABLE] or extra[STEP_TABLE]:
        seal_oms_tactic_rows(
            tuple(extra[PARENT_TABLE]), tuple(extra[STEP_TABLE]),
            dict(base_families)["trading_oms_group_state_v1"],
            dict(base_families)["trading_event_v1"],
            run_id=compound.base.run_id, batch_id=compound.base.batch_id)
    families = tuple((_v4_family_table(name), rows)
                     for name, rows in base_families) + tuple(
        (table_for_key[key], tuple(extra[table_for_key[key]]))
        for key in _CHILD_KEYS if extra[table_for_key[key]])
    return base_families, families


def publish_compound_v4(
    client: Any, compound: V4CompoundBatch, *,
    timings_ns: dict[str, int] | None = None,
) -> str:
    """Commit a mixed normalized prefix only after its full family graph seals."""
    from .arte_journal_commit_v4 import _publish_sealed_batch_v4

    started_ns = perf_counter_ns()
    base_families, families = prepare_compound_v4_families(client, compound)
    prepared_ns = perf_counter_ns()
    committed_id = _publish_sealed_batch_v4(
        client, compound.base, base_families, families,
        timings_ns=timings_ns)
    if timings_ns is not None:
        timings_ns["prepare"] = prepared_ns - started_ns
        timings_ns["publish"] = perf_counter_ns() - prepared_ns
    return committed_id
