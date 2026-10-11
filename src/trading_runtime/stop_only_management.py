"""Shared stop-only sell-capacity allocation from processed OMS facts.

The scope is explicitly admitted by a runtime. Existing target/stop OCA modes
retain their guard. Rights are derived from normalized group fills and pending
managed orders; no inferred position or separate persistence authority exists.
"""
from dataclasses import replace
from decimal import Decimal

from .ibkr_schema import OPEN_ORDER_STATUSES
from .filled_position_state import _exact_math


def managed_order_quantity(group):
    quantities = [float(request.quantity) for request in group.orders
        if request.raw.get("canonical_metadata", {}).get("execution_role") == "managed_exit"]
    if not quantities:
        raise ValueError("Stop-only reduction lacks its typed executable order budget")
    return sum(quantities)


@_exact_math
def residual_rights(manager, account_id, assignment_id):
    from .order_management import TERMINAL_MANAGEMENT_STATES
    groups = manager._groups_by_assignment.get((account_id, assignment_id), ())
    sources, consumed = [], Decimal(0)
    for group in groups:
        action = str(group.intent.action)
        if action in {"enter_long", "add_long"}:
            own_exits = sum((Decimal(str(value)) for order, value in group.filled_by_broker_order.items()
                if group.broker_order_roles.get(order) in {"profit_target", "protective_stop", "trailing_stop", "protective_exit"}), Decimal(0))
            sources.append((group, max(Decimal(0), Decimal(str(group.filled_quantity)) - own_exits)))
        elif action in {"reduce_long", "take_profit", "exit"}:
            consumed += Decimal(str(group.filled_quantity))
            if group.state not in TERMINAL_MANAGEMENT_STATES:
                if group.broker_order_ids:
                    consumed += sum((max(Decimal(0), Decimal(str(group.orders[index].quantity))
                        - Decimal(str(group.filled_by_broker_order.get(order, 0))))
                        for order, index in group.broker_order_request_indexes.items()
                        if group.broker_order_roles.get(order) == "managed_exit"
                        and order not in group.terminal_broker_order_ids), Decimal(0))
                else:
                    consumed += Decimal(str(group.remaining_quantity))
    result = {}
    for group, quantity in sorted(sources, key=lambda item: (item[0].created_at, item[0].group_id)):
        used = min(quantity, consumed)
        result[group.group_id] = float(quantity - used)
        consumed -= used
    if consumed > Decimal(".000001"):
        raise ValueError("Stop-only managed allocation exceeds processed acquisition rights")
    return result


async def allocate_stop_only_protection(manager, managed, *, affected_sources):
    """Resize/cancel acknowledged standalone stops before a new partial sell."""
    from .order_management import _require_modify_acknowledgement
    assignment = managed.intent.metadata.get("assignment_id")
    scope = (managed.account_id, assignment)
    if scope not in manager._stop_only_management_scopes or managed.intent.action != "reduce_long":
        raise ValueError("Partial reduction lacks its admitted stop-only management scope")
    groups = manager._groups_by_assignment.get(scope, ())
    live = {str(order.orderId): order for order in await manager.broker.live_orders()
        if order.order_status in OPEN_ORDER_STATUSES}
    rights = residual_rights(manager, *scope)
    sources = [group for group in groups if group.intent.action in {"enter_long", "add_long"}
        and (rights[group.group_id] > 1e-6 or any(order in live for order in group.broker_order_ids))]
    changes = []
    # Validate every protection leg before the first broker command.
    for source in sources:
        if source.protection_delegated:
            raise ValueError("Stop-only reduction cannot take an unresolved protection delegation")
        budget = rights[source.group_id]
        for broker_id in source.broker_order_ids:
            order = live.get(broker_id)
            if order is None:
                continue
            role = source.broker_order_roles.get(broker_id)
            if role == "entry":
                raise ValueError("Stop-only reduction cannot race a pending acquisition")
            if role != "protective_stop" or order.orderType != "STP":
                raise ValueError("Stop-only reduction cannot resize a target or trailing OCA leg")
            index = source.broker_order_request_indexes.get(broker_id)
            if index is None or order.account != managed.account_id or order.ticker != managed.intent.ticker:
                raise ValueError("Stop-only protection lacks exact source request lineage")
            amount = min(float(order.remainingQuantity), budget)
            budget -= amount
            if abs(amount - float(order.remainingQuantity)) > 1e-6:
                changes.append((source, broker_id, index, order, amount))
        if budget > 1e-6:
            raise ValueError("Stop-only survivors lack acknowledged protective capacity")
    affected_sources.extend(sources)
    for source in sources:
        source.protection_delegated = True
        manager._transition(source, source.state, {"event": "stop_only_allocation_started"})
    for source, broker_id, index, order, amount in changes:
        async with manager._command_lane(managed.account_id):
            if amount <= 0:
                await manager.broker.cancel_order(managed.account_id, broker_id)
            else:
                request = replace(source.orders[index], quantity=float(order.filledQuantity) + amount)
                response = await manager.broker.modify_order(managed.account_id, broker_id, request)
                _require_modify_acknowledgement(response)
                source.orders[index] = request
        manager._transition(source, source.state, {"event": "stop_only_allocation_command"})
    after = {str(order.orderId): order for order in await manager.broker.live_orders()
        if order.order_status in OPEN_ORDER_STATUSES}
    for source in sources:
        capacity = sum(float(after[broker_id].remainingQuantity) for broker_id in source.broker_order_ids
            if broker_id in after and source.broker_order_roles.get(broker_id) == "protective_stop")
        if abs(capacity - rights[source.group_id]) > 1e-6:
            raise RuntimeError("Stop-only allocation lacks exact broker acknowledgement; do not submit reduction")
        for broker_id in source.broker_order_ids:
            if broker_id in after:
                await manager.on_order_update(after[broker_id])
    return sources


async def refresh_stop_only_protection(manager, group):
    scope = (group.account_id, group.intent.metadata.get("assignment_id"))
    if scope not in manager._stop_only_management_scopes:
        return
    for source in manager._groups_by_assignment.get(scope, ()):
        if source.intent.action in {"enter_long", "add_long"} and not source.protection_delegated:
            await manager.reconcile_protection(source)
    for order in await manager.broker.live_orders():
        source = manager._group_for_order(order)
        if (source is not None and source.intent.action in {"enter_long", "add_long"}
                and (source.account_id, source.intent.metadata.get("assignment_id")) == scope):
            await manager.on_order_update(order)
