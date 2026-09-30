"""Typed OMS group-state projection; no runtime cutover is implied.

Exact canonical Strategy 1 order lineage is checked against the typed intent
and protection proof instead of being stored as raw metadata. Arbitrary raw
metadata and broker algo parameters fail closed. Cold OMS rows still contain
flat orders only. A separate complete-prefix helper can reconstruct supported
entry-group lineage, but broker-state fingerprints, external reconciliation,
and executable runtime restoration remain outside this stage.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime.arte_journal_projection import _exact_decimal
from src.trading_runtime.arte_journal_writer import (
    VerifiedPrefix, TypedJournalBatch, _CONTRACTS, _canonical_typed_content,
    _committed_batch_filter, _literal, _rows, _sealed_families, _valid_prefix, typed_row,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import ExecutionTactic
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.signals import StrategyIntent


@dataclass(frozen=True, slots=True)
class FrozenOmsGroup:
    """Immutable-to-the-actor snapshot for asynchronous typed projection."""

    group_id: str
    intent: Any
    account_id: str
    plan: Any
    state: Any
    created_at: datetime
    updated_at: datetime
    orders: tuple[OrderRequest, ...]
    broker_order_ids: tuple[str, ...]
    broker_order_roles: dict[str, str]
    broker_order_slices: dict[str, str]
    broker_order_request_indexes: dict[str, int]
    filled_by_broker_order: dict[str, float]
    terminal_broker_order_ids: frozenset[str]
    warning_message_ids: tuple[str, ...]
    rejection_reason: str
    submitted_at: datetime | None
    filled_quantity: float
    remaining_quantity: float
    decision_to_submit_ms: float | None
    reprice_count: int
    last_reprice_at: datetime | None
    current_limit_price: float | None
    deferred_reprice: tuple[float, float] | None
    failed_reprice_at: datetime | None
    internal_reaction_ms: float | None
    high_water_price: float
    low_water_price: float
    protection_required_quantity: float
    protection_coverage_quantity: float
    protection_delegated: bool
    tactic: ExecutionTactic | None = None


_APPROVED_ADMISSION_KEYS = frozenset({
    "assignment_id", "portfolio_account_key", "portfolio_decision_id",
    "unprotected_backtest_authorized", "portfolio_policy",
    "portfolio_reservation_id", "requested_quantity", "portfolio_fx_to_base",
    "correlation_id", "causation_id",
})


def approved_oms_lineage_intent(
    group: FrozenOmsGroup | _ColdLineageView,
) -> StrategyIntent:
    """Use original admission metadata to verify orders after protection trails."""
    meta = group.intent.metadata
    if (_APPROVED_ADMISSION_KEYS <= set(meta)
            and set(meta) - _APPROVED_ADMISSION_KEYS <= {"confirmed_support_stop"}):
        return replace(group.intent, metadata={key: meta[key]
                                               for key in _APPROVED_ADMISSION_KEYS})
    return group.intent


def _target_proof_failures(
    group: FrozenOmsGroup | _ColdLineageView, order: OrderRequest,
    proof: JournalRecord | None,
) -> tuple[str, ...]:
    if proof is None:
        return ("missing_proof",)
    matching_indexes = [index for index, candidate in enumerate(group.orders)
                        if candidate.cOID == order.cOID]
    terminal_target = False
    if len(matching_indexes) == 1:
        broker_ids = [broker_id for broker_id, index in
                      group.broker_order_request_indexes.items()
                      if index == matching_indexes[0]]
        terminal_target = bool(broker_ids) and all(
            broker_id in group.terminal_broker_order_ids for broker_id in broker_ids)
    checks = {
        "category": proof.category == "protection",
        "entity_type": proof.entity_type == "protection_change",
        "account_id": proof.account_id == group.account_id,
        "group_id": proof.payload.get("order_group_id") == group.group_id,
        "source_intent_id": proof.payload.get("source_intent_id") == group.intent.intent_id,
        "phase": proof.payload.get("phase") == "effective",
        "kind": proof.payload.get("kind") == "target",
        "action": proof.payload.get("action") == "replace_profit_target",
        "client_order_id": proof.payload.get("client_order_id") == order.cOID,
        "order_price": proof.payload.get("price") == order.price,
        # A terminal target remains in immutable OMS order history after a
        # later target amendment. Its exact per-order proof must still match
        # that order, but only a live target must equal the current intent.
        "intent_target": (proof.payload.get("price") == group.intent.profit_target_price
                          or terminal_target),
        "amendment_intent_id": isinstance(proof.payload.get("intent_id"), str)
                               and bool(proof.payload.get("intent_id")),
    }
    return tuple(name for name, passed in checks.items() if not passed)


def canonical_oms_order_metadata(
    group: FrozenOmsGroup | _ColdLineageView, order: OrderRequest,
    authorized_protection: Mapping[str, JournalRecord] | None = None,
) -> dict[str, Any]:
    """Rebuild initial lineage plus only the target amendment's typed delta."""
    from src.trading_runtime.strategy_orders import canonical_runtime_metadata

    metadata = canonical_runtime_metadata(order, approved_oms_lineage_intent(group))
    proofs = authorized_protection or {}
    proof = proofs.get(f"target:{order.cOID}") or proofs.get("target")
    if proof is not None and not _target_proof_failures(group, order, proof):
        metadata = {**metadata, "reason": "structural_profit_target_advanced",
                    "replacement_intent_id": proof.payload["intent_id"],
                    "target_price": proof.payload["price"]}
    return metadata


def freeze_oms_group(group: Any) -> FrozenOmsGroup:
    """Copy only financial/order facts; never copy asyncio tasks or events."""
    return FrozenOmsGroup(
        group.group_id, deepcopy(group.intent), group.account_id,
        deepcopy(group.plan), group.state, group.created_at, group.updated_at,
        tuple(deepcopy(order) for order in group.orders),
        tuple(group.broker_order_ids), dict(group.broker_order_roles),
        dict(group.broker_order_slices), dict(group.broker_order_request_indexes),
        dict(group.filled_by_broker_order), frozenset(group.terminal_broker_order_ids),
        tuple(group.warning_message_ids), group.rejection_reason,
        group.submitted_at, group.filled_quantity, group.remaining_quantity,
        group.decision_to_submit_ms, group.reprice_count, group.last_reprice_at,
        group.current_limit_price, group.deferred_reprice,
        group.failed_reprice_at, group.internal_reaction_ms,
        group.high_water_price, group.low_water_price,
        group.protection_required_quantity, group.protection_coverage_quantity,
        group.protection_delegated,
        deepcopy(group.tactic),
    )


def _duration_ms(value: float | None) -> str | None:
    """Bound non-financial clock telemetry to its declared Decimal(38,10)."""
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError("OMS duration must be finite and nonnegative")
        return str(number.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_EVEN))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("OMS duration cannot fit its typed column") from exc


def oms_group_state_batch(
    group: Any, *, run_id: str, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, sequence: int, source_cursor: str,
    run_status: str, strategy_id: str, strategy_revision: int,
    recorded_at: datetime, published_intent_batch: TypedJournalBatch,
    committed_intent_batch_id: str,
    admission_source_intent: StrategyIntent | None = None,
    admission_reservation: Mapping[str, Any] | None = None,
    authorized_protection: Mapping[str, JournalRecord] | None = None,
    journal_record_id: str | None = None,
    correlation_id: str = "",
    causation_id: str = "",
) -> TypedJournalBatch:
    """Project the represented group revision and its keyed recovery components."""
    if not run_id or not group.group_id or not group.account_id or not group.intent.intent_id:
        raise ValueError("OMS state identity is incomplete")
    if (len(published_intent_batch.events) != 1
            or len(published_intent_batch.intents) != 1
            or published_intent_batch.run_id != run_id):
        raise ValueError("OMS requires one pinned typed intent revision")
    original = published_intent_batch.events[0]
    original_detail = published_intent_batch.intents[0]
    if (admission_source_intent is None) != (admission_reservation is None):
        raise ValueError("OMS admission source and reservation must be paired")
    source_intent = admission_source_intent or group.intent
    if admission_reservation is not None:
        meta = group.intent.metadata
        required = _APPROVED_ADMISSION_KEYS
        mismatch = []
        extra = set(meta) - required
        if (not required <= set(meta)
                or extra - {"confirmed_support_stop"}):
            mismatch.append("metadata_keys")
        if source_intent.metadata:
            mismatch.append("source_metadata")
        amended_stop = group.intent.invalidation_price != source_intent.invalidation_price
        amended_target = group.intent.profit_target_price != source_intent.profit_target_price
        proofs = authorized_protection or {}
        for changed, kind, action, price in (
                (amended_stop, "stop", "replace_protective_stop",
                 group.intent.invalidation_price),
                (amended_target, "target", "replace_profit_target",
                 group.intent.profit_target_price)):
            if not changed:
                continue
            proof = proofs.get(kind)
            if (proof is None or proof.sequence >= sequence
                    or proof.run_id != run_id or proof.account_id != group.account_id
                    or proof.category != "protection"
                    or proof.entity_type != "protection_change"
                    or proof.payload.get("order_group_id") != group.group_id
                    or proof.payload.get("source_intent_id") != group.intent.intent_id
                    or proof.payload.get("phase") != "effective"
                    or proof.payload.get("kind") != kind
                    or proof.payload.get("action") != action
                    or proof.event_time > group.updated_at
                    or proof.payload.get("price") != price):
                mismatch.append(f"{kind}_amendment")
        if extra == {"confirmed_support_stop"} and (
                not amended_stop
                or meta["confirmed_support_stop"] != group.intent.invalidation_price):
            mismatch.append("confirmed_support_stop")
        original_profile = source_intent.protection_profile
        expected_profile = original_profile
        if original_profile is not None and (amended_stop or amended_target):
            expected_profile = replace(original_profile, slices=tuple(
                replace(item,
                        stop=(replace(item.stop, price=group.intent.invalidation_price)
                              if amended_stop else item.stop),
                        profit_target_price=(group.intent.profit_target_price
                                             if amended_target and item.profit_target_price is not None
                                             else item.profit_target_price))
                for item in original_profile.slices))
        if (group.intent.protection_profile != expected_profile
                or replace(group.intent, quantity=source_intent.quantity,
                           metadata={}, invalidation_price=source_intent.invalidation_price,
                           profit_target_price=source_intent.profit_target_price,
                           protection_profile=source_intent.protection_profile) != source_intent):
            mismatch.append("source_intent")
        for field, expected in (
                ("intent_id", group.intent.intent_id),
                ("account_id", group.account_id),
                ("reservation_id", meta.get("portfolio_reservation_id")),
                ("decision_id", meta.get("portfolio_decision_id")),
                ("assignment_id", meta.get("assignment_id")),
                ("status", "reserved")):
            if admission_reservation.get(field) != expected:
                mismatch.append("reservation_" + field)
        if float(admission_reservation.get("quantity") or 0) != group.intent.quantity:
            mismatch.append("reservation_quantity")
        if not meta.get("assignment_id"):
            mismatch.append("assignment_identity")
        if mismatch:
            # Names only: never place financial values or mutable metadata in
            # an error, disk log, or untyped journal payload.
            raise ValueError("OMS approved intent differs from its normalized admission: "
                             + ",".join(mismatch))
    rebuilt = strategy_intent_batch(
        source_intent, run_id=published_intent_batch.run_id,
        run_month=published_intent_batch.run_month,
        account_id=str(original_detail["account_id"]),
        attempt_id=published_intent_batch.attempt_id,
        batch_id=published_intent_batch.batch_id,
        prior_batch_id=published_intent_batch.prior_batch_id,
        sequence=published_intent_batch.first_sequence,
        source_cursor=published_intent_batch.source_cursor,
        run_status=published_intent_batch.status,
        recorded_at=datetime.fromisoformat(str(original["recorded_at"])),
        record_id=str(original["record_id"]),
        correlation_id=str(original["correlation_id"]),
        causation_id=str(original["causation_id"]),
    )
    if (rebuilt.events != published_intent_batch.events
            or rebuilt.intents != published_intent_batch.intents
            or rebuilt.intent_slices != published_intent_batch.intent_slices):
        raise ValueError("OMS group intent differs from its published typed revision")
    intent_record_id = str(UUID(str(original_detail["record_id"])))
    committed_source_id = str(UUID(committed_intent_batch_id))
    sealed_intent = dict(_sealed_families(published_intent_batch))[
        "trading_strategy_intent_v1"][0]
    intent_content_hash = typed_row("trading_strategy_intent_v1", {
        **{key: value for key, value in sealed_intent.items() if key != "content_hash"},
        "batch_id": committed_source_id,
    })["content_hash"]
    if (recorded_at.tzinfo is None or group.created_at.tzinfo is None
            or group.updated_at.tzinfo is None
            or not isinstance(correlation_id, str)
            or not isinstance(causation_id, str)):
        raise ValueError("OMS state timestamps must be timezone-aware")
    if len(group.orders) > 65535 or len(group.broker_order_ids) > 65535:
        raise ValueError("OMS state exceeds typed child bounds")
    if any(not isinstance(value, str) or not value
           for value in (*group.warning_message_ids, *group.plan.cancel_oca_groups)):
        raise ValueError("OMS warning and OCA identities must be nonempty strings")
    for order in group.orders:
        if order.strategyParameters:
            raise ValueError("OMS order has unmodeled broker algo evidence")
        if order.raw:
            expected_raw = {
                "canonical_run_id": run_id,
                "canonical_strategy_id": strategy_id,
                "canonical_strategy_revision": strategy_revision,
                "canonical_metadata": canonical_oms_order_metadata(
                    group, order, authorized_protection),
            }
            if order.raw != expected_raw:
                changed = sorted(key for key in set(order.raw) | set(expected_raw)
                                 if order.raw.get(key) != expected_raw.get(key))
                actual_meta = order.raw.get("canonical_metadata")
                expected_meta = expected_raw["canonical_metadata"]
                meta_changed = (sorted(key for key in set(actual_meta) | set(expected_meta)
                                if actual_meta.get(key) != expected_meta.get(key))
                                if isinstance(actual_meta, Mapping) else [])
                raise ValueError(
                    "OMS order has unmodeled raw lineage differing from its typed intent: "
                    + ",".join(changed)
                    + (" [metadata: " + ",".join(meta_changed) + "]"
                       if meta_changed else "")
                    + (" [target proof: " + ",".join(_target_proof_failures(
                        group, order, (authorized_protection or {}).get(
                            f"target:{order.cOID}") or
                        (authorized_protection or {}).get("target")))
                       + "]" if "replacement_intent_id" in meta_changed else ""))
    lengths = tuple(len(batch) for batch in group.plan.broker_batches)
    if not lengths or any(length < 1 for length in lengths) or sum(lengths) != len(group.orders):
        raise ValueError("OMS plan batches do not cover every order")
    if group.plan.order_slice_ids and len(group.plan.order_slice_ids) != len(group.orders):
        raise ValueError("OMS order slice identities are not aligned")
    broker_ids = tuple(str(value) for value in group.broker_order_ids)
    if len(set(broker_ids)) != len(broker_ids) or any(not value for value in broker_ids):
        raise ValueError("OMS broker order identities are missing or duplicated")
    for mapping in (group.broker_order_roles, group.broker_order_slices,
                    group.broker_order_request_indexes, group.filled_by_broker_order):
        if set(mapping) - set(broker_ids):
            raise ValueError("OMS broker association has an unlisted order identity")
    if set(group.terminal_broker_order_ids) - set(broker_ids):
        raise ValueError("OMS terminal state has an unlisted broker order")
    if group.deferred_reprice is not None and len(group.deferred_reprice) != 2:
        raise ValueError("OMS deferred reprice must have two prices")
    at = group.updated_at.astimezone(timezone.utc).isoformat()
    month = group.updated_at.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = (str(UUID(journal_record_id)) if journal_record_id is not None
                 else str(uuid5(NAMESPACE_URL,
                                f"{run_id}:{batch_id}:{sequence}:oms-group-state")))
    common = {"run_id": run_id, "event_month": month, "batch_id": batch_id,
              "account_id": group.account_id}
    event = {**common, "record_id": record_id, "attempt_id": attempt_id,
             "sequence": sequence, "event_time": at,
             "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
             "category": "order_management", "entity_type": "order_group_state",
             "entity_id": group.group_id, "correlation_id": correlation_id,
             "causation_id": causation_id}
    optional_number = lambda value: _exact_decimal(value) if value is not None else None
    optional_time = lambda value: value.astimezone(timezone.utc).isoformat() if value is not None else None
    state = {
        **common, "record_id": record_id, "group_id": group.group_id,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "strategy_intent_id": group.intent.intent_id,
        "state": group.state.value, "created_at": group.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": at, "submitted_at": optional_time(group.submitted_at),
        "rejection_reason": group.rejection_reason,
        "decision_to_submit_ms": _duration_ms(group.decision_to_submit_ms),
        "reprice_count": group.reprice_count,
        "last_reprice_at": optional_time(group.last_reprice_at),
        "failed_reprice_at": optional_time(group.failed_reprice_at),
        "internal_reaction_ms": _duration_ms(group.internal_reaction_ms),
        "deferred_reprice_from": optional_number(group.deferred_reprice[0]) if group.deferred_reprice else None,
        "deferred_reprice_to": optional_number(group.deferred_reprice[1]) if group.deferred_reprice else None,
        "high_water_price": _exact_decimal(group.high_water_price),
        "low_water_price": _exact_decimal(group.low_water_price),
        "cancel_strategy_protection": int(group.plan.cancel_strategy_protection),
        "protection_reconciliation_required": int(group.plan.protection_reconciliation_required),
        "filled_quantity": _exact_decimal(group.filled_quantity),
        "remaining_quantity": _exact_decimal(group.remaining_quantity),
        "current_limit_price": optional_number(group.current_limit_price),
        "protection_required_quantity": _exact_decimal(group.protection_required_quantity),
        "protection_coverage_quantity": _exact_decimal(group.protection_coverage_quantity),
        "protection_delegated": int(group.protection_delegated),
        "order_count": len(group.orders), "broker_binding_count": len(broker_ids),
        "warning_count": len(group.warning_message_ids),
        "cancel_oca_count": len(group.plan.cancel_oca_groups),
    }
    intent_use = {
        **common, "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:intent-use")),
        "parent_record_id": record_id, "intent_record_id": intent_record_id,
        "intent_content_hash": intent_content_hash,
    }
    batch_ordinals = tuple(index for index, length in enumerate(lengths) for _ in range(length))
    orders = []
    for ordinal, order in enumerate(group.orders):
        if order.acctId != group.account_id:
            raise ValueError("OMS order account differs from its group")
        orders.append({
            **common, "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:order:{ordinal}")),
            "parent_record_id": record_id, "ordinal": ordinal,
            "batch_ordinal": batch_ordinals[ordinal],
            "slice_id": group.plan.order_slice_ids[ordinal] if group.plan.order_slice_ids else "",
            "client_order_id": order.cOID, "parent_broker_order_id": order.parentId or "",
            "conid": order.conid, "ticker": order.ticker,
            "security_type": order.secType, "listing_exchange": order.listingExchange,
            "side": order.side, "order_type": order.orderType, "time_in_force": order.tif,
            "quantity": optional_number(order.quantity),
            "cash_quantity": optional_number(order.cashQty),
            "limit_price": optional_number(order.price),
            "aux_price": optional_number(order.auxPrice),
            "trailing_amount": optional_number(order.trailingAmt),
            "trailing_type": order.trailingType or "", "outside_rth": int(order.outsideRTH),
            "single_group": int(order.isSingleGroup),
            "manual_indicator": int(order.manualIndicator),
            "external_operator": order.extOperator or "", "referrer": order.referrer or "",
            "broker_strategy": order.strategy or "",
        })
    bindings = []
    for ordinal, broker_id in enumerate(broker_ids):
        request_index = group.broker_order_request_indexes.get(broker_id)
        if request_index is not None and not 0 <= int(request_index) < len(orders):
            raise ValueError("OMS broker binding has an invalid request index")
        bindings.append({
            **common, "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:broker:{ordinal}")),
            "parent_record_id": record_id, "ordinal": ordinal,
            "broker_order_id": broker_id,
            "has_role": int(broker_id in group.broker_order_roles),
            "role": group.broker_order_roles.get(broker_id, ""),
            "has_slice": int(broker_id in group.broker_order_slices),
            "slice_id": group.broker_order_slices.get(broker_id, ""),
            "request_index": request_index,
            "has_filled_quantity": int(broker_id in group.filled_by_broker_order),
            "filled_quantity": _exact_decimal(group.filled_by_broker_order.get(broker_id, 0)),
            "terminal": int(broker_id in group.terminal_broker_order_ids),
        })
    def strings(values: Any, label: str, field: str) -> tuple[dict[str, Any], ...]:
        return tuple({**common,
                      "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:{label}:{ordinal}")),
                      "parent_record_id": record_id, "ordinal": ordinal,
                      field: str(value)}
                     for ordinal, value in enumerate(values))
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        oms_group_states=(state,), oms_order_states=tuple(orders),
        oms_broker_bindings=tuple(bindings),
        oms_warnings=strings(group.warning_message_ids, "warning", "message_id"),
        oms_cancel_ocas=strings(group.plan.cancel_oca_groups, "cancel-oca", "oca_group"),
        intent_uses=(intent_use,),
    )


@dataclass(frozen=True, slots=True)
class RecoveredOmsGroupState:
    sequence: int
    intent_record_id: str | None
    group: dict[str, Any]
    orders: tuple[OrderRequest, ...]
    order_batch_ordinals: tuple[int, ...]
    order_slice_ids: tuple[str, ...]
    broker_bindings: tuple[dict[str, Any], ...]
    warning_message_ids: tuple[str, ...]
    cancel_oca_groups: tuple[str, ...]
    tactic: ExecutionTactic | None = None
    tactic_recorded: bool = False
    first_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class _ColdLineageView:
    group_id: str
    account_id: str
    intent: StrategyIntent
    orders: tuple[OrderRequest, ...]
    broker_order_request_indexes: dict[str, int]
    terminal_broker_order_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneOmsLineage:
    state: RecoveredOmsGroupState
    source_intent: Any
    orders: tuple[OrderRequest, ...]
    through_sequence: int
    approved_intent: StrategyIntent | None = None
    admission_reservation: Mapping[str, Any] | None = None


def _approved_strategy_one_oms_intent(
    state: RecoveredOmsGroupState, source_intent: Any,
    protection_history: Any,
    admission_reservation: Mapping[str, Any] | None,
    admission_decision: Mapping[str, Any] | None,
    followthrough_row: Mapping[str, Any] | None = None,
) -> tuple[StrategyIntent, tuple[Any, ...]]:
    """Restore the approved, amended group intent from normalized facts."""
    if (admission_reservation is None) != (admission_decision is None):
        raise ValueError("Strategy 1 OMS admission needs its decision and reservation")
    approved_intent = source_intent.intent
    account = state.group["account_id"]
    if admission_reservation is not None:
        from src.trading_runtime.portfolio import _intent_correlation

        reservation, decision = admission_reservation, admission_decision
        if (approved_intent.metadata
                or reservation.get("account_id") != account
                or reservation.get("intent_id") != approved_intent.intent_id
                or reservation.get("decision_id") != decision.get("decision_id")
                or reservation.get("reservation_id") != decision.get("reservation_id")
                or reservation.get("account_key") != decision.get("account_key")
                or not reservation.get("assignment_id")
                or decision.get("status") not in {"approved", "resized"}
                or not decision.get("policy_id")
                or int(decision.get("policy_revision") or 0) < 1):
            raise ValueError("Strategy 1 OMS admission differs from typed source")
        if approved_intent.reason == "strategy_nine_followthrough_failure":
            from .arte_followthrough_failure_v4 import restore_failure
            if (followthrough_row is None or state.group["strategy_revision"] not in (9, 10, 11, 12)
                    or followthrough_row["strategy_number"] != state.group["strategy_revision"]
                    or followthrough_row["assignment_id"] != reservation["assignment_id"]
                    or str(followthrough_row["parent_record_id"]) != source_intent.record_id
                    or str(followthrough_row["batch_id"]) != source_intent.batch_id
                    or float(followthrough_row["bid"]) != approved_intent.reference_price):
                raise ValueError("Failure recovery lacks its exact committed scalar witness")
            from .strategy_followthrough_exit import followthrough_exit_intent
            from .strategy_one_stateful import StrategyOneFinancialView
            from .strategy_engine import AssignmentStatus, StrategyPermissions
            from zoneinfo import ZoneInfo
            financial = StrategyOneFinancialView(reservation["assignment_id"], account,
                approved_intent.ticker, AssignmentStatus.WATCHING, StrategyPermissions(),
                approved_intent.quantity, False, False, False, 1)
            expected = followthrough_exit_intent(restore_failure(followthrough_row), financial,
                session_date=approved_intent.event_time.astimezone(ZoneInfo("America/New_York")).date(),
                source_entry_intent_id=str(followthrough_row["source_entry_intent_id"]))
            if expected != approved_intent:
                raise ValueError("Failure recovery differs from the exact scalar exit intent")
        elif approved_intent.action == "exit":
            from zoneinfo import ZoneInfo
            from datetime import datetime, time
            from .numbered_session_exit import numbered_session_exit_intent
            local = approved_intent.event_time.astimezone(ZoneInfo("America/New_York"))
            delta = local - datetime.combine(local.date(), time(4), local.tzinfo)
            boundary_ms = (delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000)
            expected_exit = numbered_session_exit_intent(
                session_date=local.date(), account_id=account,
                assignment_id=reservation["assignment_id"], ticker=approved_intent.ticker,
                boundary_ms=boundary_ms, quantity=approved_intent.quantity,
                bid=approved_intent.reference_price, strategy_number=state.group["strategy_revision"])
            if state.group.get("strategy_revision") not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12) or expected_exit != approved_intent:
                raise ValueError("Session exit recovery differs from sealed scalar source")
        metadata = {
            "assignment_id": reservation["assignment_id"],
            "portfolio_account_key": reservation["account_key"],
            "portfolio_decision_id": decision["decision_id"],
            "unprotected_backtest_authorized": False,
            "portfolio_policy": f"{decision['policy_id']}@{decision['policy_revision']}",
            "portfolio_reservation_id": reservation["reservation_id"],
            "requested_quantity": float(decision["requested_quantity"]),
            "portfolio_fx_to_base": 1.0,
            "correlation_id": _intent_correlation(protection_history.run_id,
                                                   approved_intent),
            "causation_id": decision["decision_id"],
        }
        approved_intent = replace(
            approved_intent, quantity=float(reservation["quantity"]),
            metadata=metadata)
    history = tuple(row for row in protection_history.records
                    if row.account_id == account
                    and row.payload.get("order_group_id") == state.group["group_id"]
                    and row.sequence <= state.sequence)
    if any(row.run_id != protection_history.run_id
           or not 0 < row.sequence <= protection_history.through_sequence
           or (row.category, row.entity_type) !=
           ("protection", "protection_change") for row in history):
        raise ValueError("Strategy 1 OMS protection history differs from its prefix")
    for kind, field in (("stop", "invalidation_price"),
                        ("target", "profit_target_price")):
        effective = [row for row in history
                     if row.payload.get("kind") == kind
                     and row.payload.get("phase") == "effective"
                     and row.payload.get("action") == (
                         "replace_protective_stop" if kind == "stop"
                         else "replace_profit_target")]
        if effective:
            latest = max(effective, key=lambda row: row.sequence)
            price = latest.payload["price"]
            # Live OMS amends the scalar and its protection slice together.
            # A cold actor must restore that same pair before reconciliation.
            profile = approved_intent.protection_profile
            if profile is not None:
                profile = replace(profile, slices=tuple(
                    replace(item, stop=replace(item.stop, price=price))
                    if kind == "stop" else
                    replace(item, profit_target_price=price)
                    if item.profit_target_price is not None else item
                    for item in profile.slices))
            approved_intent = replace(
                approved_intent, **{field: price}, protection_profile=profile,
                metadata=({**approved_intent.metadata,
                           "confirmed_support_stop": price}
                          if kind == "stop" else approved_intent.metadata))
    return approved_intent, history


def reconstruct_strategy_one_oms_lineage(
    state: RecoveredOmsGroupState, source_intent: Any,
    protection_history: Any,
    *, admission_reservation: Mapping[str, Any] | None = None,
    admission_decision: Mapping[str, Any] | None = None,
    followthrough_row: Mapping[str, Any] | None = None,
) -> tuple[OrderRequest, ...]:
    """Rebuild exact entry-group raw lineage from completed typed evidence.

    The supplied history must be a complete cold scan through this OMS state.
    This returns recovery evidence only; it does not reconcile broker orders,
    restore OMS tasks, or grant live order admission.
    """
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER

    if (not isinstance(state, RecoveredOmsGroupState)
            or not isinstance(source_intent, RecoveredIntent)
            or not isinstance(protection_history, CompleteProtectionHistory)):
        raise ValueError("Strategy 1 OMS lineage needs typed cold evidence")
    group = state.group
    if (
            not isinstance(group, dict)
            or group.get("strategy_id") != STRATEGY_ID
            or group.get("strategy_revision") not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
            or group.get("run_id") != protection_history.run_id
            or group.get("batch_id") not in protection_history.committed_batch_ids
            or source_intent.batch_id not in protection_history.committed_batch_ids
            or not 0 < source_intent.sequence < state.sequence
            <= protection_history.through_sequence
            or state.intent_record_id != source_intent.record_id
            or group.get("account_id") != source_intent.account_id
            or group.get("strategy_intent_id") != source_intent.intent.intent_id
            # A resistance add is a new, independently admitted OMS group.
            # Its immutable source intent is add_long, not the first entry's
            # enter_long. Both require the same exact typed lineage proof.
            or source_intent.intent.action not in (
                {"enter_long", "exit"} if group.get("strategy_revision") in (4, 5, 6, 7, 8, 9, 10, 11, 12) else
                {"enter_long", "add_long", "exit"} if group.get("strategy_revision") in (2, 3)
                else {"enter_long", "add_long"})
            or not state.orders or len(state.orders) > 65_535
            or len({order.cOID for order in state.orders}) != len(state.orders)):
        raise ValueError("Strategy 1 OMS lineage lacks one complete typed authority")
    account = group["account_id"]
    identity = group["group_id"]
    if any(order.acctId != account
           or order.ticker.upper() != source_intent.intent.ticker.upper()
           or not order.cOID or order.raw or order.strategyParameters
           for order in state.orders):
        raise ValueError("Strategy 1 OMS order differs from its flat typed state")
    bindings = {str(row["broker_order_id"]): int(row["request_index"])
                for row in state.broker_bindings
                if row["request_index"] is not None}
    terminal = frozenset(str(row["broker_order_id"])
                         for row in state.broker_bindings if row["terminal"])
    approved_intent, history = _approved_strategy_one_oms_intent(
        state, source_intent, protection_history,
        admission_reservation, admission_decision, followthrough_row)
    view = _ColdLineageView(
        identity, account, approved_intent, state.orders, bindings, terminal)
    rebuilt = []
    for order in state.orders:
        amendments = [row for row in history
                      if row.payload.get("client_order_id") == order.cOID
                      and row.payload.get("kind") == "target"
                      and row.payload.get("phase") == "effective"
                      and row.payload.get("action") == "replace_profit_target"]
        matching = [row for row in amendments
                    if row.payload.get("price") == order.price]
        if len(matching) > 1:
            raise ValueError("Strategy 1 target amendment lineage is ambiguous")
        proofs = {}
        if matching:
            proof = matching[0]
            if (proof.sequence <= source_intent.sequence
                    or proof.payload.get("ticker") != source_intent.intent.ticker.upper()
                    or proof.payload.get("strategy_id") != STRATEGY_ID
                    or proof.payload.get("strategy_revision") != group["strategy_revision"]
                    or _target_proof_failures(view, order, proof)):
                raise ValueError("Strategy 1 target amendment proof differs")
            proofs[f"target:{order.cOID}"] = proof
        metadata = canonical_oms_order_metadata(view, order, proofs)
        rebuilt.append(replace(order, raw={
            "canonical_run_id": protection_history.run_id,
            "canonical_strategy_id": STRATEGY_ID,
            "canonical_strategy_revision": group["strategy_revision"],
            "canonical_metadata": metadata,
        }))
    return tuple(rebuilt)


def load_recovered_strategy_one_oms_lineage(
    client: Any, prefix: VerifiedPrefix, *,
    allowed_accounts: frozenset[str], page_size: int = 500,
    max_transitions: int = 20_000, max_groups: int = 2_000,
    max_events: int = 100_000,
    protection_history: CompleteProtectionHistory | None = None,
    strategy_number: int = 1,
) -> tuple[RecoveredStrategyOneOmsLineage, ...]:
    """Cold-join latest OMS groups to exact intents and complete protection.

    This returns diagnostic, fully typed lineage only. It does not reconstruct
    broker state or grant permission to resume an OMS actor or send an order.
    """
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    numbered_fixed_strategy(strategy_number)
    from src.trading_runtime.arte_intent_projection import (
        load_committed_strategy_intent_page,
    )
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_reader import (
        load_complete_typed_protection_history,
    )
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER

    if (not isinstance(prefix, V4CommittedPrefix)
            or type(allowed_accounts) is not frozenset or not allowed_accounts
            or any(type(value) is not str or not value for value in allowed_accounts)
            or type(page_size) is not int or not 1 <= page_size <= 500
            or type(max_transitions) is not int or max_transitions < page_size
            or type(max_groups) is not int or not 1 <= max_groups <= max_transitions
            or type(max_events) is not int or max_events < 1):
        raise ValueError("Strategy 1 OMS cold join needs bounded V4 authority")
    # Protection history scans the entire committed event prefix. Its reader
    # independently bounds each page at 1,000 events and verifies every typed
    # child; using that full bound avoids five small network round trips for
    # each one that the OMS group join actually needs. Keep the OMS join's
    # tighter page size because its per-group child budget is separate.
    if protection_history is not None and not isinstance(
            protection_history, CompleteProtectionHistory):
        raise TypeError("Strategy 1 OMS needs verified protection history")
    history = (protection_history if protection_history is not None
               else load_complete_typed_protection_history(
                   client, prefix, page_size=1_000, max_events=max_events))
    if (history.run_id != prefix.run_id
            or history.through_sequence != prefix.last_sequence
            or history.committed_batch_ids != prefix.batch_ids):
        raise RuntimeError("Strategy 1 OMS protection history head differs")
    groups = load_latest_committed_oms_groups(
        client, prefix, page_size=page_size,
        max_transitions=max_transitions, allowed_accounts=allowed_accounts,
        strategy_identity=(STRATEGY_ID, strategy_number), require_tactic=True)
    if len(groups) > max_groups:
        raise RuntimeError("Strategy 1 OMS cold group inventory exceeds bound")
    if not groups:
        return ()
    if len(groups) > 4096:
        raise RuntimeError("Strategy 1 OMS admissions exceed one cold join bound")
    wanted = {group.intent_record_id for group in groups}
    if None in wanted:
        raise RuntimeError("Strategy 1 OMS group lacks an intent revision")
    by_id = {}
    identifiers = sorted(wanted)
    for start in range(0, len(identifiers), page_size):
        chunk = tuple(identifiers[start:start + page_size])
        page = load_committed_strategy_intent_page(
            client, prefix, limit=len(chunk), record_ids=chunk,
            include_source_batch=True)
        if len(page) != len(chunk):
            raise RuntimeError("Strategy 1 OMS intent join is incomplete")
        for row in page:
            if row.record_id in by_id:
                raise RuntimeError("Strategy 1 OMS intent revision was duplicated")
            by_id[row.record_id] = row
    if set(by_id) != wanted:
        raise RuntimeError("Strategy 1 OMS intent identities differ")
    admissions = load_committed_oms_admission_page(
        client, prefix, groups, max_rows=4096)
    decisions = load_committed_oms_decision_page(
        client, prefix, groups, admissions, max_rows=4096)
    failure_rows = {}
    from .arte_followthrough_failure_v4 import REASON, load_followthrough_failure
    for record_id, source in by_id.items():
        if source.intent.reason == REASON:
            failure_rows[record_id] = load_followthrough_failure(client, prefix, record_id)[0]
    return tuple(RecoveredStrategyOneOmsLineage(
        group, by_id[group.intent_record_id],
        reconstruct_strategy_one_oms_lineage(
            group, by_id[group.intent_record_id], history,
            admission_reservation=admissions[group.sequence],
            admission_decision=decisions[group.sequence],
            followthrough_row=failure_rows.get(group.intent_record_id)),
        history.through_sequence,
        _approved_strategy_one_oms_intent(
            group, by_id[group.intent_record_id], history,
            admissions[group.sequence], decisions[group.sequence],
            failure_rows.get(group.intent_record_id))[0],
        dict(admissions[group.sequence]),
    ) for group in groups)


def load_latest_committed_oms_groups(
    client: Any, prefix: VerifiedPrefix, *, page_size: int = 500,
    max_transitions: int = 20_000,
    allowed_accounts: frozenset[str] | None = None,
    strategy_identity: tuple[str, int] | None = None,
    require_tactic: bool = False,
) -> tuple[RecoveredOmsGroupState, ...]:
    """Cold-read one latest normalized state per OMS group, with a hard bound.

    This is not broker reconciliation or permission to resubmit an order. Every
    page is checked by the typed family reader against the committed prefix.
    """
    if (not _valid_prefix(prefix) or type(page_size) is not int
            or not 1 <= page_size <= 500
            or type(max_transitions) is not int or max_transitions < page_size
            or allowed_accounts is not None and (
                type(allowed_accounts) is not frozenset or not allowed_accounts
                or any(type(account) is not str or not account
                       for account in allowed_accounts))
            or strategy_identity is not None and (
                type(strategy_identity) is not tuple
                or len(strategy_identity) != 2
                or type(strategy_identity[0]) is not str
                or not strategy_identity[0]
                or type(strategy_identity[1]) is not int
                or strategy_identity[1] < 1)):
        raise ValueError("OMS cold inventory needs a verified bounded prefix")
    latest: dict[tuple[str, str], RecoveredOmsGroupState] = {}
    first_sequences: dict[tuple[str, str], int] = {}
    after = 0
    transitions = 0
    current_page_size = page_size
    while True:
        try:
            page = load_committed_oms_group_state_page(
                client, prefix, after_sequence=after, limit=current_page_size,
                require_tactic=require_tactic)
        except RuntimeError as exc:
            # A dense group can exceed the independently bounded child count.
            # Retry the *same* cursor with fewer parents; never skip or relax
            # verification. Other integrity errors must still fail closed.
            if (current_page_size == 1 or not any(message in str(exc) for message in (
                    "exceeds its total child budget",
                    "exceeds its child budget",
                    "exceeds its row budget"))):
                raise
            current_page_size = max(1, current_page_size // 2)
            continue
        if not page:
            break
        for item in page:
            account_id = item.group["account_id"]
            group_id = item.group["group_id"]
            if (item.sequence <= after or item.sequence > prefix.last_sequence
                    or not account_id or not group_id):
                raise RuntimeError("OMS cold inventory has invalid transition order")
            if (allowed_accounts is not None
                    and account_id not in allowed_accounts
                    or strategy_identity is not None
                    and (item.group["strategy_id"], item.group["strategy_revision"])
                    != strategy_identity):
                raise RuntimeError("OMS cold transition differs from pinned run authority")
            after = item.sequence
            key = (account_id, group_id)
            first = first_sequences.setdefault(key, item.sequence)
            latest[key] = replace(item, first_sequence=first)
            transitions += 1
            if transitions > max_transitions:
                raise RuntimeError("OMS cold inventory exceeds its transition bound")
        if len(page) < current_page_size:
            break
    return tuple(sorted(latest.values(), key=lambda item: item.sequence))


def _verified_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(
            name, content, stored_utc=True,
        )).encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError(f"Committed {name} row differs from its hash")
    return rows


def load_committed_oms_admission_page(
    client: Any, prefix: VerifiedPrefix,
    groups: tuple[RecoveredOmsGroupState, ...], *, max_rows: int = 500,
) -> dict[int, dict[str, Any]]:
    """Join OMS revisions to one earlier normalized reservation per intent.

    This is a cold read only. The reservation is the sole authority for the
    approved quantity and assignment; OMS does not store a redundant blob.
    """
    if not _valid_prefix(prefix) or not groups or not 1 <= len(groups) <= max_rows <= 4096:
        raise ValueError("OMS admission recovery needs a bounded verified page")
    intent_ids = {str(row.group["strategy_intent_id"]) for row in groups}
    if not all(intent_ids):
        raise ValueError("OMS admission page has an empty intent identity")
    ids = ",".join(_literal(value) for value in sorted(intent_ids))
    name = "trading_portfolio_reservation_event_v1"
    columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
    rows = _verified_rows(name, _rows(client,
        f"SELECT {columns} FROM arte.{name} "
        f"WHERE run_id={_literal(prefix.run_id)} AND intent_id IN ({ids}) "
        "AND event='reservation_created' "
        f"{_committed_batch_filter(prefix)}"
        f"LIMIT {max_rows + 1} FORMAT JSONEachRow"))
    if len(rows) > max_rows:
        raise RuntimeError("Committed OMS admission exceeds its row budget")
    created = [row for row in rows if row["event"] == "reservation_created"]
    record_ids = {str(UUID(str(row["record_id"]))) for row in created}
    events: dict[str, dict[str, Any]] = {}
    if record_ids:
        event_ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(record_ids))
        source = _rows(client,
            "SELECT record_id,batch_id,sequence,account_id,entity_id,category,entity_type "
            "FROM arte.trading_event_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({event_ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {max_rows + 1} FORMAT JSONEachRow")
        events = {str(UUID(str(row["record_id"]))): row for row in source}
        if len(source) != len(record_ids) or len(events) != len(record_ids):
            raise RuntimeError("Committed OMS admission event is missing or duplicated")
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in created:
        key = (str(row["account_id"]), str(row["intent_id"]))
        by_key.setdefault(key, []).append(row)
    result = {}
    for group in groups:
        state = group.group
        key = (str(state["account_id"]), str(state["strategy_intent_id"]))
        matches = by_key.get(key, [])
        if len(matches) != 1:
            raise RuntimeError("Committed OMS state lacks one unique admission")
        reservation = matches[0]
        event = events[str(UUID(str(reservation["record_id"])))]
        if (str(UUID(str(reservation["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or event["account_id"] != key[0]
                or event["entity_id"] != reservation["reservation_id"]
                or (event["category"], event["entity_type"]) !=
                   ("portfolio_management", "portfolio_reservation")
                or int(event["sequence"]) >= group.sequence
                or reservation["status"] != "reserved"
                or not reservation["assignment_id"]
                or float(reservation["quantity"]) <= 0):
            raise RuntimeError("Committed OMS admission differs from its event or order")
        if group.sequence in result:
            raise RuntimeError("Committed OMS admission page repeats a transition")
        result[group.sequence] = reservation
    return result


def load_committed_oms_decision_page(
    client: Any, prefix: VerifiedPrefix,
    groups: tuple[RecoveredOmsGroupState, ...],
    admissions: Mapping[int, Mapping[str, Any]], *, max_rows: int = 500,
) -> dict[int, dict[str, Any]]:
    """Join each recovered OMS head to its exact committed Portfolio approval."""
    if (not _valid_prefix(prefix) or not groups
            or not 1 <= len(groups) <= max_rows <= 4096
            or set(admissions) != {group.sequence for group in groups}):
        raise ValueError("OMS decision recovery needs exact bounded admissions")
    ids = {str(admissions[group.sequence]["decision_id"]) for group in groups}
    if not all(ids):
        raise RuntimeError("OMS admission lacks its Portfolio decision identity")
    sql_ids = ",".join(_literal(value) for value in sorted(ids))
    name = "trading_portfolio_decision_v1"
    columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
    rows = _verified_rows(name, _rows(client,
        f"SELECT {columns} FROM arte.{name} "
        f"WHERE run_id={_literal(prefix.run_id)} AND decision_id IN ({sql_ids}) "
        f"{_committed_batch_filter(prefix)}"
        f"LIMIT {max_rows + 1} FORMAT JSONEachRow"))
    if len(rows) > max_rows:
        raise RuntimeError("Committed OMS decisions exceed their row budget")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        decision_id = str(row["decision_id"])
        if decision_id in by_id:
            raise RuntimeError("Committed OMS Portfolio decision is duplicated")
        by_id[decision_id] = row
    if set(by_id) != ids:
        raise RuntimeError("Committed OMS Portfolio decision is missing")
    event_columns = ",".join(column for column, _ in _CONTRACTS["trading_event_v1"].columns)
    record_ids = ",".join(f"toUUID({_literal(str(UUID(str(row['record_id']))))})"
                          for row in rows)
    events = _verified_rows("trading_event_v1", _rows(client,
        f"SELECT {event_columns} FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({record_ids}) "
        f"{_committed_batch_filter(prefix)}"
        f"LIMIT {len(rows) + 1} FORMAT JSONEachRow"))
    by_record = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(events) != len(rows) or len(by_record) != len(rows):
        raise RuntimeError("Committed OMS Portfolio decision event is missing or duplicated")
    result = {}
    for group in groups:
        admission = admissions[group.sequence]
        decision = by_id[str(admission["decision_id"])]
        event = by_record[str(UUID(str(decision["record_id"])))]
        if (decision["account_id"] != group.group["account_id"]
                or decision["decision_id"] != admission["decision_id"]
                or decision["reservation_id"] != admission["reservation_id"]
                or decision["account_key"] != admission["account_key"]
                or decision["ticker"].upper() != admission["ticker"].upper()
                or decision["action"] != admission["action"]
                or decision["status"] not in {"approved", "resized"}
                or not decision["policy_id"] or int(decision["policy_revision"]) < 1
                or Decimal(str(decision["requested_quantity"]))
                < Decimal(str(decision["approved_quantity"]))
                or Decimal(str(decision["approved_quantity"])) <= 0
                or Decimal(str(decision["approved_quantity"]))
                != Decimal(str(admission["quantity"]))
                or str(UUID(str(decision["batch_id"])))
                != str(UUID(str(event["batch_id"])))
                or event["account_id"] != decision["account_id"]
                or event["entity_id"] != decision["decision_id"]
                or (event["category"], event["entity_type"])
                != ("portfolio_management", "portfolio_decision")
                or int(event["sequence"]) >= group.sequence):
            raise RuntimeError("Committed OMS Portfolio decision differs from admission")
        result[group.sequence] = decision
    return result


def load_committed_oms_group_state_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 200, max_children: int = 4096,
    require_intent_revision: bool = True,
    require_tactic: bool = False,
) -> tuple[RecoveredOmsGroupState, ...]:
    """Cold-read a bounded, fence-certified OMS group page without disk state."""
    if not _valid_prefix(prefix):
        raise ValueError("OMS recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 500 or max_children < 1:
        raise ValueError("OMS recovery page bounds are invalid")
    events = _rows(client,
        "SELECT record_id,batch_id,sequence,account_id,entity_id,event_month "
        "FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{int(after_sequence)} AND sequence<={int(prefix.last_sequence)} "
        "AND category='order_management' AND entity_type='order_group_state' "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {int(limit)} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = tuple(str(UUID(str(row["record_id"]))) for row in events)
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed OMS page repeated an event identity")
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    def family(name: str, *, children: bool) -> list[dict[str, Any]]:
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        key = "parent_record_id" if children else "record_id"
        rows = _rows(client,
            f"SELECT {columns} FROM arte.{name} "
            f"WHERE run_id={_literal(prefix.run_id)} AND {key} IN ({ids_sql}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {max_children + 1 if children else len(events) + 1} FORMAT JSONEachRow")
        if len(rows) > (max_children if children else len(events)):
            raise RuntimeError(f"Committed OMS {name} page exceeds its row budget")
        return _verified_rows(name, rows)
    groups = family("trading_oms_group_state_v1", children=False)
    if len(groups) != len(events):
        raise RuntimeError("Committed OMS page has missing or duplicate group states")
    by_id = {str(UUID(str(row["record_id"]))): row for row in groups}
    if set(by_id) != set(ids):
        raise RuntimeError("Committed OMS group states differ from events")
    declared_children = sum(sum(int(row[field]) for field in (
        "order_count", "broker_binding_count", "warning_count", "cancel_oca_count",
    )) for row in groups)
    if declared_children + len(groups) > max_children:
        raise RuntimeError("Committed OMS page exceeds its total child budget")
    children = {name: family(name, children=True) for name in (
        "trading_oms_order_state_v1", "trading_oms_broker_binding_v1",
        "trading_oms_warning_v1", "trading_oms_cancel_oca_v1",
        "trading_strategy_intent_use_v1",
    )}
    links = children["trading_strategy_intent_use_v1"]
    if (sum(len(rows) for rows in children.values()) != declared_children + len(links)
            or declared_children + len(links) > max_children):
        raise RuntimeError("Committed OMS page has missing or excess child rows")
    # A group page can contain thousands of normalized child rows. Index each
    # verified row once by its exact parent; scanning every family for every
    # group made cold recovery quadratic without adding any verification.
    children_by_parent: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name, rows in children.items():
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            parent_id = str(UUID(str(row["parent_record_id"])))
            if parent_id not in by_id:
                raise RuntimeError("Committed OMS child has no page parent")
            grouped.setdefault(parent_id, []).append(row)
        children_by_parent[name] = grouped
    tactic_by_group: dict[str, ExecutionTactic | None] = {}
    if require_tactic:
        from .arte_oms_tactic_projection import (
            PARENT_TABLE, STEP_TABLE, seal_oms_tactic_rows, tactic_from_rows,
        )

        tactic_states = family(PARENT_TABLE, children=True)
        if len(tactic_states) != len(groups):
            raise RuntimeError("Committed OMS page lacks one tactic state per group")
        tactic_ids = tuple(str(UUID(str(row["record_id"])))
                           for row in tactic_states)
        tactic_sql = ",".join(f"toUUID({_literal(value)})" for value in tactic_ids)
        columns = ",".join(column for column, _ in _CONTRACTS[STEP_TABLE].columns)
        tactic_steps = _verified_rows(STEP_TABLE, _rows(client,
            f"SELECT {columns} FROM arte.{STEP_TABLE} "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND parent_record_id IN ({tactic_sql}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {max_children + 1} FORMAT JSONEachRow"))
        if len(tactic_steps) + declared_children + len(links) + len(groups) > max_children:
            raise RuntimeError("Committed OMS tactic page exceeds its child budget")
        seal_oms_tactic_rows(
            tuple(tactic_states), tuple(tactic_steps), tuple(groups),
            tuple({**row, "category": "order_management",
                   "entity_type": "order_group_state"} for row in events),
            run_id=prefix.run_id, batch_id=None,
            stored_utc=True)
        steps_by_parent: dict[str, list[dict[str, Any]]] = {}
        tactic_id_set = set(tactic_ids)
        for row in tactic_steps:
            parent_id = str(UUID(str(row["parent_record_id"])))
            if parent_id not in tactic_id_set:
                raise RuntimeError("Committed OMS tactic step has no state parent")
            steps_by_parent.setdefault(parent_id, []).append(row)
        for parent in tactic_states:
            parent_id = str(UUID(str(parent["record_id"])))
            selected = tuple(sorted(steps_by_parent.get(parent_id, ()),
                                    key=lambda row: int(row["ordinal"])))
            tactic_by_group[str(UUID(str(parent["parent_record_id"])))] = (
                tactic_from_rows(parent, selected, stored_utc=True))
    source_by_id: dict[str, dict[str, Any]] = {}
    source_events: dict[str, dict[str, Any]] = {}
    if links:
        source_ids = {str(UUID(str(row["intent_record_id"]))) for row in links}
        source_sql = ",".join(f"toUUID({_literal(value)})" for value in sorted(source_ids))
        columns = ",".join(column for column, _ in _CONTRACTS["trading_strategy_intent_v1"].columns)
        sources = _verified_rows("trading_strategy_intent_v1", _rows(client,
            f"SELECT {columns} FROM arte.trading_strategy_intent_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({source_sql}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {len(source_ids) + 1} FORMAT JSONEachRow"))
        source_rows = _rows(client,
            "SELECT record_id,batch_id,sequence,account_id,category,entity_type "
            "FROM arte.trading_event_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({source_sql}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {len(source_ids) + 1} FORMAT JSONEachRow")
        source_by_id = {str(UUID(str(row["record_id"]))): row for row in sources}
        source_events = {str(UUID(str(row["record_id"]))): row for row in source_rows}
        if (len(sources) != len(source_ids) or len(source_rows) != len(source_ids)
                or set(source_by_id) != source_ids or set(source_events) != source_ids):
            raise RuntimeError("Committed OMS intent revision source is missing or duplicated")
    result = []
    prior = after_sequence
    for event in events:
        parent_id = str(UUID(str(event["record_id"])))
        group = by_id[parent_id]
        sequence = int(event["sequence"])
        if (sequence <= prior or str(UUID(str(group["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or group["account_id"] != event["account_id"]
                or group["event_month"] != event["event_month"]
                or group["group_id"] != event["entity_id"]):
            raise RuntimeError("Committed OMS group differs from its event envelope")
        prior = sequence
        ordered: dict[str, list[dict[str, Any]]] = {}
        for name in children:
            if name == "trading_strategy_intent_use_v1":
                continue
            selected = list(children_by_parent[name].get(parent_id, ()))
            selected.sort(key=lambda row: int(row["ordinal"]))
            if ([int(row["ordinal"]) for row in selected] != list(range(len(selected)))
                    or any(str(UUID(str(row["batch_id"]))) != str(UUID(str(group["batch_id"])))
                           or row["account_id"] != group["account_id"]
                           or row["event_month"] != group["event_month"] for row in selected)):
                raise RuntimeError("Committed OMS child rows differ from their group")
            ordered[name] = selected
        order_rows = ordered["trading_oms_order_state_v1"]
        binding_rows = ordered["trading_oms_broker_binding_v1"]
        warning_rows = ordered["trading_oms_warning_v1"]
        cancel_rows = ordered["trading_oms_cancel_oca_v1"]
        use_rows = children_by_parent["trading_strategy_intent_use_v1"].get(
            parent_id, ())
        if any(str(UUID(str(row["batch_id"]))) != str(UUID(str(group["batch_id"])))
               or row["account_id"] != group["account_id"]
               or row["event_month"] != group["event_month"] for row in use_rows):
            raise RuntimeError("Committed OMS intent link differs from its group")
        if len(use_rows) > 1 or (require_intent_revision and len(use_rows) != 1):
            raise RuntimeError("Committed OMS state lacks one exact intent revision")
        source_id = str(UUID(str(use_rows[0]["intent_record_id"]))) if use_rows else None
        if source_id is not None:
            source = source_by_id[source_id]
            source_event = source_events[source_id]
            if (str(source["intent_id"]) != str(group["strategy_intent_id"])
                    or source["account_id"] != group["account_id"]
                    or source_event["account_id"] != group["account_id"]
                    or str(UUID(str(source["batch_id"]))) != str(UUID(str(source_event["batch_id"])))
                    or source_event["category"] != "strategy"
                    or source_event["entity_type"] != "strategy_intent"
                    or str(source["content_hash"]) != str(use_rows[0]["intent_content_hash"])
                    or int(source_event["sequence"]) >= sequence):
                raise RuntimeError("Committed OMS intent revision differs from its source")
        if (len(order_rows) != int(group["order_count"])
                or len(binding_rows) != int(group["broker_binding_count"])
                or len(warning_rows) != int(group["warning_count"])
                or len(cancel_rows) != int(group["cancel_oca_count"])):
            raise RuntimeError("Committed OMS children differ from their declared counts")
        batch_ordinals = tuple(int(row["batch_ordinal"]) for row in order_rows)
        if batch_ordinals != tuple(sorted(batch_ordinals)) or (
            batch_ordinals and sorted(set(batch_ordinals)) != list(range(batch_ordinals[-1] + 1))
        ):
            raise RuntimeError("Committed OMS order batches are not contiguous")
        orders = tuple(OrderRequest(
            acctId=group["account_id"], conid=int(row["conid"]),
            cOID=row["client_order_id"], parentId=row["parent_broker_order_id"] or None,
            ticker=row["ticker"], secType=row["security_type"],
            listingExchange=row["listing_exchange"], side=row["side"],
            orderType=row["order_type"], tif=row["time_in_force"],
            quantity=float(row["quantity"]) if row["quantity"] is not None else None,
            cashQty=float(row["cash_quantity"]) if row["cash_quantity"] is not None else None,
            price=float(row["limit_price"]) if row["limit_price"] is not None else None,
            auxPrice=float(row["aux_price"]) if row["aux_price"] is not None else None,
            trailingAmt=float(row["trailing_amount"]) if row["trailing_amount"] is not None else None,
            trailingType=row["trailing_type"] or None,
            outsideRTH=bool(row["outside_rth"]),
            isSingleGroup=bool(row["single_group"]),
            manualIndicator=bool(row["manual_indicator"]),
            extOperator=row["external_operator"] or None,
            referrer=row["referrer"] or None,
            strategy=row["broker_strategy"] or None,
        ) for row in order_rows)
        if len({str(row["broker_order_id"]) for row in binding_rows}) != len(binding_rows):
            raise RuntimeError("Committed OMS broker identities are duplicated")
        if any(row["request_index"] is not None and
               int(row["request_index"]) >= len(orders) for row in binding_rows):
            raise RuntimeError("Committed OMS broker request index is out of bounds")
        result.append(RecoveredOmsGroupState(
            sequence, source_id, group, orders, batch_ordinals,
            tuple(row["slice_id"] for row in order_rows),
            tuple(binding_rows),
            tuple(row["message_id"] for row in warning_rows),
            tuple(row["oca_group"] for row in cancel_rows),
            tactic_by_group.get(parent_id), require_tactic,
        ))
    return tuple(result)
