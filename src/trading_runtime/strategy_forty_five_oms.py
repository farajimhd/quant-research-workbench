"""Group-scoped native OMS protection changes for Strategy 45.

No broker, order book, cash ledger or fill matcher is introduced here. The
existing OMS command lane, acknowledgement and protection proof machinery
remain the authorities. Entry sources and amendment sources must already be
bound to the normalized journal before calling this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .ibkr_schema import OPEN_ORDER_STATUSES
from .order_management import (
    OrderGroupSnapshot, OrderManagementEngine,
    _require_modify_acknowledgement, _warning_response,
)
from .signals import StrategyIntent
from .strategy_forty_five_rules import STRATEGY_ID, STRATEGY_NUMBER


@dataclass(frozen=True, slots=True)
class LegStopAmendment:
    group_id: str
    source_entry_intent_id: str
    assignment_id: str
    account_id: str
    intent: StrategyIntent


async def replace_leg_stop(manager: OrderManagementEngine,
                           source: LegStopAmendment) -> OrderGroupSnapshot:
    """Ratchet exactly one broker-held stop, retaining its OCA-reduced size.

    The typed source names the native group rather than using intent metadata
    to select it. All identities are checked before the first broker mutation.
    No already-effective higher stop can be lowered by an acknowledgement retry.
    """
    if (not isinstance(manager, OrderManagementEngine)
            or type(source) is not LegStopAmendment
            or (manager.strategy_id, manager.strategy_revision) != (STRATEGY_ID, STRATEGY_NUMBER)
            or not source.group_id or not source.source_entry_intent_id
            or not source.assignment_id or not source.account_id
            or type(source.intent) is not StrategyIntent):
        raise ValueError("Strategy 45 amendment needs its exact typed native OMS source")
    intent = source.intent
    group = manager._groups.get(source.group_id)
    if (group is None or group.account_id != source.account_id
            or group.intent.intent_id != source.source_entry_intent_id
            or group.intent.ticker != intent.ticker
            or group.intent.metadata.get("assignment_id") != source.assignment_id
            or intent.action != "replace_protective_stop" or intent.metadata
            or intent.reason != "strategy_forty_five_adaptive_stop"
            or intent.event_time.tzinfo is None
            or group.submitted_at is None or intent.event_time <= group.submitted_at):
        raise ValueError("Strategy 45 stop amendment differs from its native entry group")
    desired = float(intent.invalidation_price or 0)
    if not 0 < desired < intent.reference_price:
        raise ValueError("Long support stop must be positive and below the market")
    profile = group.intent.resolved_protection_profile()
    if profile is None or len(profile.slices) != 1:
        raise RuntimeError("Strategy 45 entry lost its single-slice protection contract")
    live = [order for order in await manager.broker.live_orders()
            if manager._group_for_order(order) is group
            and order.order_status in OPEN_ORDER_STATUSES
            and order.orderType.upper() in {"STP", "STOP_LIMIT"}]
    if not live:
        raise ValueError("Strategy 45 leg needs broker-held stop protection")
    confirmed_prices = []
    # Keep attached future protection and active partial-fill repair aligned.
    for order in sorted(live,key=lambda row:int(row.orderId)):
        index = group.broker_order_request_indexes.get(str(order.orderId))
        if index is None:
            raise RuntimeError("Strategy 45 held stop lacks native request lineage")
        request = replace(group.orders[index],
            quantity=float(order.filledQuantity) + float(order.remainingQuantity))
        previous = max(float(request.auxPrice or 0), float(order.auxPrice or 0))
        if desired <= previous:
            # The broker may hold an amendment whose caller lost its receipt.
            # Reconcile through the normal native path; do not mutate siblings.
            if float(order.auxPrice or 0) < desired:
                raise RuntimeError("Strategy 45 stop acknowledgement requires native reconciliation")
            confirmed = float(order.auxPrice)
        else:
            replacement = replace(request, auxPrice=desired,
                price=(float(request.price) + desired - float(request.auxPrice or 0)
                       if request.orderType == "STOP_LIMIT" and request.price is not None else request.price))
            async with manager._command_lane(source.account_id):
                manager._record_protection(group, replacement, phase="requested",
                    broker_order_id=str(order.orderId), event_time=intent.event_time,
                    amendment_intent=intent)
                response = await manager.broker.modify_order(
                    source.account_id, str(order.orderId), replacement)
            if _warning_response(response):
                async with manager._warning_lane:
                    response = await manager._resolve_warning_chain_locked(group, response)
            _require_modify_acknowledgement(response)
            manager._record_strategy_one_modify_acknowledgement(
                group, intent, str(order.orderId), response)
            manager._record_protection(group, replacement, phase="effective",
                broker_order_id=str(order.orderId), event_time=intent.event_time,
                amendment_intent=intent)
            request = replacement
            confirmed = desired
        group.orders[index] = replace(request, auxPrice=confirmed, price=order.price
            if desired <= previous else request.price)
        confirmed_prices.append(confirmed)
    confirmed = min(confirmed_prices)
    profile = replace(profile, slices=(replace(profile.slices[0],
        stop=replace(profile.slices[0].stop, price=confirmed)),))
    group.intent = replace(group.intent, invalidation_price=confirmed,
        protection_profile=profile,
        metadata={**group.intent.metadata, "confirmed_support_stop": confirmed})
    group.updated_at = intent.event_time
    manager._transition(group, group.state,
        {"event": "support_stop_replaced", "stop": confirmed})
    return group.snapshot(manager.policy.version)
