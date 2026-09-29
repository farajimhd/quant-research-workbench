"""Pure Strategy 1 OMS actor image from verified normalized V4 facts.

This module reconstructs mutable in-memory state but does not install it in an
actor, reconcile orders, acquire a lease, or send a broker command. Historical
protection changes are a separate normalized family and must be complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any

from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime.arte_oms_projection import RecoveredStrategyOneOmsLineage
from src.trading_runtime.order_management import (
    _ManagedOrderGroup, OrderManagementEngine, OrderManagementState,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    BrokerMatchSnapshotRows, verify_broker_match_snapshot,
)
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.signals import StrategyIntent


@dataclass(frozen=True, slots=True)
class TypedOmsActorImage:
    run_id: str
    strategy_id: str
    strategy_revision: int
    groups: dict[str, _ManagedOrderGroup]
    group_by_client_id: dict[str, str]
    group_by_broker_id: dict[str, str]
    protection_versions: dict[tuple[str, str, str, str, str], tuple[float, bool]]
    body_entry_group_ids: frozenset[str]


def install_typed_oms_actor_image(
    actor: OrderManagementEngine, image: TypedOmsActorImage,
) -> None:
    """Install a verified image into a fresh, inert OMS actor without I/O.

    This does not reconcile or command a broker and does not grant resume.
    The caller must separately fence the run and verify the broker image.
    """
    if not isinstance(actor, OrderManagementEngine) or not isinstance(image, TypedOmsActorImage):
        raise TypeError("Typed OMS installation requires an actor and image")
    if (actor.run_id != image.run_id or actor.strategy_id != image.strategy_id
            or actor.strategy_revision != image.strategy_revision):
        raise RuntimeError("Typed OMS actor identity differs from its image")
    if (actor._groups or actor._group_by_client_id or actor._group_by_broker_id
            or actor._body_entry_group_ids or actor._entry_trade_prices
            or getattr(actor, "_protection_versions", {})):
        raise RuntimeError("Typed OMS actor must be fresh before installation")
    if any(group.reprice_task is not None or group.protection_task is not None
           for group in image.groups.values()):
        raise RuntimeError("Typed OMS image contains an active task")
    expected_client = {request.cOID: group.group_id
                       for group in image.groups.values() for request in group.orders
                       if request.cOID}
    expected_broker = {broker_id: group.group_id
                       for group in image.groups.values()
                       for broker_id in group.broker_order_ids}
    if (len(expected_client) != sum(bool(request.cOID) for group in image.groups.values()
                                   for request in group.orders)
            or len(expected_broker) != sum(len(group.broker_order_ids)
                                          for group in image.groups.values())
            or expected_client != image.group_by_client_id
            or expected_broker != image.group_by_broker_id
            or not image.body_entry_group_ids.issubset(image.groups)):
        raise RuntimeError("Typed OMS image indexes differ from groups")
    # Detach from the audit image: subsequent broker replies mutate actor state.
    actor._groups = deepcopy(image.groups)
    actor._group_by_client_id = dict(image.group_by_client_id)
    actor._group_by_broker_id = dict(image.group_by_broker_id)
    actor._body_entry_group_ids = set(image.body_entry_group_ids)
    actor._protection_versions = dict(image.protection_versions)


def verify_typed_oms_broker_open_orders(
    image: TypedOmsActorImage, broker: BrokerMatchSnapshotRows,
) -> int:
    """Prove one exact open-order set without mutating OMS or broker actors."""
    if not isinstance(image, TypedOmsActorImage):
        raise TypeError("Typed OMS broker audit requires a reconstructed image")
    rows = verify_broker_match_snapshot(broker).open_orders
    by_id = {row["broker_order_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise RuntimeError("Typed OMS broker snapshot repeats an open order")
    expected = {
        broker_id
        for group in image.groups.values()
        for broker_id in group.broker_order_ids
        if broker_id not in group.terminal_broker_order_ids
    }
    if expected != set(by_id):
        raise RuntimeError("Typed OMS and broker disagree on open order identities")
    for broker_id, row in by_id.items():
        group_id = image.group_by_broker_id.get(broker_id)
        group = image.groups.get(group_id or "")
        if group is None:
            raise RuntimeError("Typed OMS open broker order has no group")
        index = group.broker_order_request_indexes.get(broker_id)
        if index is None or not 0 <= index < len(group.orders):
            raise RuntimeError("Typed OMS open broker order lacks a request index")
        request = group.orders[index]
        if (request.cOID != row["client_order_id"]
                or request.acctId != row["account_id"]
                or request.conid != row["conid"]
                or request.ticker != row["ticker"]
                or group.account_id != row["account_id"]):
            raise RuntimeError("Typed OMS open request differs from broker")
        tracked = group.filled_by_broker_order.get(broker_id, 0.0)
        if abs(tracked - float(row["filled"])) > 1e-8:
            raise RuntimeError("Typed OMS fill quantity differs from broker")
    return len(rows)


def _time(value: Any, *, cutoff_at: datetime) -> datetime:
    source = str(value)
    parsed = datetime.fromisoformat(source.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # The verified ClickHouse DateTime64 wire format has no offset even
        # though the column contract is UTC. Only accept that exact format;
        # never reinterpret arbitrary naive application timestamps.
        if re.fullmatch(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d\.\d{6}(?:000)?", source) is None:
            raise RuntimeError("Typed OMS recovery timestamp lacks UTC authority")
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > cutoff_at:
        raise RuntimeError(
            f"Typed OMS recovery boundary {cutoff_at.isoformat()} precedes "
            f"timestamp {parsed.isoformat()}")
    return parsed


def reconstruct_typed_oms_actor_image(
    lineages: tuple[RecoveredStrategyOneOmsLineage, ...],
    protection: CompleteProtectionHistory, *,
    run_id: str, strategy_id: str, strategy_revision: int,
    through_sequence: int, cutoff_at: datetime,
) -> TypedOmsActorImage:
    """Recover exact group and protection caches without touching a journal."""
    if (not run_id or not strategy_id or type(strategy_revision) is not int
            or strategy_revision < 1 or type(through_sequence) is not int
            or through_sequence < 1 or cutoff_at.tzinfo is None
            or protection.run_id != run_id
            or protection.through_sequence != through_sequence):
        raise ValueError("Typed OMS recovery needs one verified causal authority")
    groups: dict[str, _ManagedOrderGroup] = {}
    by_client: dict[str, str] = {}
    by_broker: dict[str, str] = {}
    body_ids: set[str] = set()
    for lineage in lineages:
        state = lineage.state
        row = state.group
        # The source intent predates Portfolio approval and has no assignment
        # or approved quantity. Active OMS recovery must install the verified
        # admitted intent reconstructed from normalized reservation/decision.
        if not isinstance(lineage.approved_intent, StrategyIntent):
            raise RuntimeError("Typed OMS group lacks its verified approved intent")
        intent = lineage.approved_intent
        orders = lineage.orders
        group_id = str(row["group_id"])
        if (lineage.through_sequence != through_sequence
                or not 1 <= state.sequence <= through_sequence
                or not state.tactic_recorded
                or row["run_id"] != run_id
                or row["strategy_id"] != strategy_id
                or int(row["strategy_revision"]) != strategy_revision
                or row["strategy_intent_id"] != intent.intent_id
                or lineage.source_intent.account_id != row["account_id"]
                or not group_id or group_id in groups
                or len(orders) != int(row["order_count"])
                or len(state.broker_bindings) != int(row["broker_binding_count"])
                or len(state.warning_message_ids) != int(row["warning_count"])
                or len(state.cancel_oca_groups) != int(row["cancel_oca_count"])):
            raise RuntimeError("Typed OMS group differs from its committed lineage")
        ordinals = state.order_batch_ordinals
        if (len(ordinals) != len(orders) or not ordinals
                or ordinals[0] != 0 or tuple(sorted(ordinals)) != ordinals
                or set(ordinals) != set(range(ordinals[-1] + 1))
                or len(state.order_slice_ids) != len(orders)):
            raise RuntimeError("Typed OMS plan has incomplete batch identities")
        batches = tuple(tuple(order for order, ordinal in zip(orders, ordinals)
                              if ordinal == batch)
                        for batch in range(ordinals[-1] + 1))
        plan = StrategyOrderPlan(
            orders=orders, batches=batches,
            order_slice_ids=state.order_slice_ids,
            cancel_oca_groups=state.cancel_oca_groups,
            cancel_strategy_protection=bool(row["cancel_strategy_protection"]),
            protection_reconciliation_required=bool(
                row["protection_reconciliation_required"]),
        )
        bindings = state.broker_bindings
        broker_ids = [str(binding["broker_order_id"]) for binding in bindings]
        if len(set(broker_ids)) != len(broker_ids) or not all(broker_ids):
            raise RuntimeError("Typed OMS broker identities repeat")
        request_indexes = {}
        roles = {}
        slices = {}
        filled = {}
        terminal = set()
        for binding in bindings:
            broker_id = str(binding["broker_order_id"])
            index = binding["request_index"]
            if index is not None:
                if type(index) is not int or not 0 <= index < len(orders):
                    raise RuntimeError("Typed OMS broker request index is invalid")
                request_indexes[broker_id] = index
            if binding["has_role"]:
                roles[broker_id] = str(binding["role"])
            if binding["has_slice"]:
                slices[broker_id] = str(binding["slice_id"])
            if binding["has_filled_quantity"]:
                filled[broker_id] = float(binding["filled_quantity"])
            if binding["terminal"]:
                terminal.add(broker_id)
        deferred_from, deferred_to = (row["deferred_reprice_from"],
                                      row["deferred_reprice_to"])
        if (deferred_from is None) != (deferred_to is None):
            raise RuntimeError("Typed OMS deferred reprice is incomplete")
        optional_time = lambda key: (_time(row[key], cutoff_at=cutoff_at)
                                     if row[key] is not None else None)
        created = _time(row["created_at"], cutoff_at=cutoff_at)
        updated = _time(row["updated_at"], cutoff_at=cutoff_at)
        if updated < created or any(request.acctId != row["account_id"]
                                   for request in orders):
            raise RuntimeError("Typed OMS group time or account changed")
        group = _ManagedOrderGroup(
            group_id=group_id, intent=intent, account_id=row["account_id"],
            plan=plan, state=OrderManagementState(row["state"]),
            created_at=created, updated_at=updated, orders=list(orders),
            tactic=state.tactic, broker_order_ids=broker_ids,
            broker_order_roles=roles, broker_order_slices=slices,
            broker_order_request_indexes=request_indexes,
            filled_by_broker_order=filled, terminal_broker_order_ids=terminal,
            warning_message_ids=list(state.warning_message_ids),
            rejection_reason=str(row["rejection_reason"]),
            submitted_at=optional_time("submitted_at"),
            filled_quantity=float(row["filled_quantity"]),
            remaining_quantity=float(row["remaining_quantity"]),
            decision_to_submit_ms=(float(row["decision_to_submit_ms"])
                                   if row["decision_to_submit_ms"] is not None else None),
            reprice_count=int(row["reprice_count"]),
            last_reprice_at=optional_time("last_reprice_at"),
            failed_reprice_at=optional_time("failed_reprice_at"),
            internal_reaction_ms=(float(row["internal_reaction_ms"])
                                  if row["internal_reaction_ms"] is not None else None),
            current_limit_price=(float(row["current_limit_price"])
                                 if row["current_limit_price"] is not None else None),
            deferred_reprice=((float(deferred_from), float(deferred_to))
                              if deferred_from is not None else None),
            high_water_price=float(row["high_water_price"]),
            low_water_price=float(row["low_water_price"]),
            protection_required_quantity=float(row["protection_required_quantity"]),
            protection_coverage_quantity=float(row["protection_coverage_quantity"]),
            protection_delegated=bool(row["protection_delegated"]),
        )
        groups[group_id] = group
        if intent.metadata.get("entry_body_trigger"):
            body_ids.add(group_id)
        for request in orders:
            if request.cOID:
                if request.cOID in by_client:
                    raise RuntimeError("Typed OMS client order identity repeats")
                by_client[request.cOID] = group_id
        for broker_id in broker_ids:
            if broker_id in by_broker:
                raise RuntimeError("Typed OMS broker order identity repeats")
            by_broker[broker_id] = group_id
    versions = {}
    for record in protection.records:
        payload = record.payload
        key = (payload["order_group_id"], payload["client_order_id"],
               payload["order_id"], payload["kind"], payload["phase"])
        if (record.run_id != run_id or not 1 <= record.sequence <= through_sequence
                or record.event_time > cutoff_at or key[0] not in groups):
            raise RuntimeError("Typed OMS protection history differs from groups")
        versions[key] = (float(payload["price"]), bool(payload["active"]))
    return TypedOmsActorImage(run_id, strategy_id, strategy_revision,
                              groups, by_client, by_broker, versions,
                              frozenset(body_ids))
