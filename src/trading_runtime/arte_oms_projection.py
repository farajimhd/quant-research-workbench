"""Typed OMS group-state projection; no runtime cutover is implied.

The live strategy still carries open-ended order raw metadata and broker algo
parameters. Those inputs fail closed until named typed child contracts exist.
Tactic and broker-state-fingerprint recovery also remain outside this stage.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_projection import _exact_decimal
from src.trading_runtime.arte_journal_writer import (
    VerifiedPrefix, TypedJournalBatch, _CONTRACTS, _canonical_typed_content,
    _committed_batch_filter, _literal, _rows, _sealed_families, _valid_prefix, typed_row,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal_contract import canonical_json
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
    )


def oms_group_state_batch(
    group: Any, *, run_id: str, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, sequence: int, source_cursor: str,
    run_status: str, strategy_id: str, strategy_revision: int,
    recorded_at: datetime, published_intent_batch: TypedJournalBatch,
    committed_intent_batch_id: str,
    admission_source_intent: StrategyIntent | None = None,
    admission_reservation: Mapping[str, Any] | None = None,
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
        required = {
            "assignment_id", "portfolio_account_key", "portfolio_decision_id",
            "unprotected_backtest_authorized", "portfolio_policy",
            "portfolio_reservation_id", "requested_quantity",
            "portfolio_fx_to_base", "correlation_id", "causation_id",
        }
        if (set(meta) != required or source_intent.metadata
                or replace(group.intent, quantity=source_intent.quantity,
                           metadata={}) != source_intent
                or admission_reservation.get("intent_id") != group.intent.intent_id
                or admission_reservation.get("account_id") != group.account_id
                or admission_reservation.get("reservation_id") != meta["portfolio_reservation_id"]
                or admission_reservation.get("decision_id") != meta["portfolio_decision_id"]
                or admission_reservation.get("assignment_id") != meta["assignment_id"]
                or admission_reservation.get("status") != "reserved"
                or float(admission_reservation.get("quantity") or 0) != group.intent.quantity
                or not meta["assignment_id"]):
            raise ValueError("OMS approved intent differs from its normalized admission")
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
    if recorded_at.tzinfo is None or group.created_at.tzinfo is None or group.updated_at.tzinfo is None:
        raise ValueError("OMS state timestamps must be timezone-aware")
    if len(group.orders) > 65535 or len(group.broker_order_ids) > 65535:
        raise ValueError("OMS state exceeds typed child bounds")
    if any(not isinstance(value, str) or not value
           for value in (*group.warning_message_ids, *group.plan.cancel_oca_groups)):
        raise ValueError("OMS warning and OCA identities must be nonempty strings")
    if any(order.raw or order.strategyParameters for order in group.orders):
        raise ValueError("OMS order has unmodeled raw or broker algo evidence")
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
    record_id = str(uuid5(NAMESPACE_URL, f"{run_id}:{batch_id}:{sequence}:oms-group-state"))
    common = {"run_id": run_id, "event_month": month, "batch_id": batch_id,
              "account_id": group.account_id}
    event = {**common, "record_id": record_id, "attempt_id": attempt_id,
             "sequence": sequence, "event_time": at,
             "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
             "category": "order_management", "entity_type": "order_group_state",
             "entity_id": group.group_id, "correlation_id": "", "causation_id": ""}
    optional_number = lambda value: _exact_decimal(value) if value is not None else None
    optional_time = lambda value: value.astimezone(timezone.utc).isoformat() if value is not None else None
    state = {
        **common, "record_id": record_id, "group_id": group.group_id,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "strategy_intent_id": group.intent.intent_id,
        "state": group.state.value, "created_at": group.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": at, "submitted_at": optional_time(group.submitted_at),
        "rejection_reason": group.rejection_reason,
        "decision_to_submit_ms": optional_number(group.decision_to_submit_ms),
        "reprice_count": group.reprice_count,
        "last_reprice_at": optional_time(group.last_reprice_at),
        "failed_reprice_at": optional_time(group.failed_reprice_at),
        "internal_reaction_ms": optional_number(group.internal_reaction_ms),
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


def _verified_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(
            name, content, stored_utc=True,
        )).encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError(f"Committed {name} row differs from its hash")
    return rows


def load_committed_oms_group_state_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 200, max_children: int = 4096,
    require_intent_revision: bool = True,
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
        for name, rows in children.items():
            if name == "trading_strategy_intent_use_v1":
                continue
            selected = [row for row in rows if str(UUID(str(row["parent_record_id"]))) == parent_id]
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
        use_rows = [row for row in links
                    if str(UUID(str(row["parent_record_id"]))) == parent_id]
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
        ))
    return tuple(result)
