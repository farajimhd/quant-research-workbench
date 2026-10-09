"""Rule-selected fixed-bracket reconciliation for independent acquisition lots.

Only the shared OMS invokes this command path. Older profiles retain their
existing reconciliation. No numbered strategy is registered by this module.
"""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from .ibkr_schema import OPEN_ORDER_STATUSES, OrderRequest, OrderStatus
from .execution_policies import AddProtectionPolicy, StopRuleType, TrailingRuleType
from .squeeze_ladder_lots import LadderFillFact, ladder_lot_exposure
from .strategy_orders import canonical_runtime_order_raw


async def recover_ladder_repair_outcome(manager, group) -> bool:
    """Resolve persisted repair commands by exact broker identity, never retry."""
    from .order_management import OrderManagementState, _apply_cumulative_fill

    pending = [(index, order) for index, order in enumerate(group.orders)
               if "repair-" in order.cOID]
    if not pending:
        return False
    live = await manager.broker.live_orders()
    matched = []
    for index, request in pending:
        candidates = [order for order in live if order.account == group.account_id and order.cOID == request.cOID]
        if not candidates:
            return False
        if len(candidates) != 1:
            raise RuntimeError("Ladder repair client identity is ambiguous")
        order = candidates[0]
        if any(actual != planned for actual, planned in (
            (order.conid, request.conid), (order.ticker, request.ticker),
            (order.side, request.side), (order.orderType, request.orderType),
            (order.tif, request.tif), (order.totalSize, request.quantity),
            (order.price, request.price), (order.auxPrice, request.auxPrice),
            (order.parentId or "", request.parentId or ""), (order.outsideRTH, request.outsideRTH))):
            raise RuntimeError("Ladder repair broker order differs from persisted command")
        owner = manager._group_by_broker_id.get(str(order.orderId))
        if owner is not None and owner != group.group_id:
            raise RuntimeError("Ladder repair broker identity belongs to another group")
        matched.append((index, request, order))
    if len({str(order.orderId) for _, _, order in matched}) != len(matched):
        raise RuntimeError("Ladder repair broker identities are duplicated")
    for index, request, order in matched:
        order_id = str(order.orderId)
        role = "profit_target" if request.orderType == "LMT" else "protective_stop"
        if order_id not in group.broker_order_ids:
            group.broker_order_ids.append(order_id)
        manager._group_by_broker_id[order_id] = group.group_id
        group.broker_order_roles[order_id] = role
        group.broker_order_slices[order_id] = group.plan.order_slice_ids[index]
        group.broker_order_request_indexes[order_id] = index
        _apply_cumulative_fill(group, order_id, float(order.filledQuantity), role)
        manager._record_protection(group, request, phase="effective", broker_order_id=order_id,
                                   active=order.order_status in OPEN_ORDER_STATUSES)
    state = OrderManagementState.PARTIALLY_FILLED if group.remaining_quantity > 0 else OrderManagementState.FILLED
    manager._transition(group, state, {"event": "ladder_repair_outcome_recovered"})
    return True


async def reconcile_independent_lot_protection(manager, group):
    from .order_management import OrderManagementState, _protection_group_key, _require_modify_acknowledgement

    profile = group.intent.resolved_protection_profile()
    if profile is None or profile.add_policy != AddProtectionPolicy.INDEPENDENT_FIXED_LOTS:
        raise ValueError("Independent lot reconciliation requires an explicit profile rule")
    if (not 2 <= len(profile.slices) <= 32
            or any(item.stop.rule_type != StopRuleType.FIXED_PRICE
                   or item.stop.price is None or item.profit_target_price is None
                   or item.inherit_profit_target
                   or item.trailing.rule_type != TrailingRuleType.NONE
                   for item in profile.slices)):
        raise ValueError("Prepared ladder repair requires fixed independent brackets")
    if group.intent.action != "enter_long" or group.protection_delegated:
        raise ValueError("Prepared ladder repair requires its own long acquisition")
    bound_indexes = set(group.broker_order_request_indexes.values())
    has_unbound_repair = any(index not in bound_indexes and "repair-" in order.cOID
                             for index, order in enumerate(group.orders))
    if group.state == OrderManagementState.OUTCOME_UNKNOWN or has_unbound_repair:
        if not await recover_ladder_repair_outcome(manager, group):
            if group.state != OrderManagementState.OUTCOME_UNKNOWN:
                manager._transition(group, OrderManagementState.OUTCOME_UNKNOWN,
                                    {"event": "ladder_repair_outcome_unknown"})
            return {"status": "ladder_submission_outcome_unknown"}
    elif group.state == OrderManagementState.WORKING and group.filled_quantity > 0:
        # Recovery may observe an unfilled child last. Acquisition state is
        # determined by processed entry fills, not the last protective update.
        state = (OrderManagementState.PARTIALLY_FILLED if group.remaining_quantity > 0
                 else OrderManagementState.FILLED)
        manager._transition(group, state, {"event": "ladder_acquisition_state_recovered"})
    live = {str(order.orderId): order for order in await manager.broker.live_orders()}
    # Defer retirement while broker snapshots are ahead of processed entry
    # callbacks; future shares must not be assigned to another lot or cancelled.
    for order_id, role in group.broker_order_roles.items():
        order = live.get(order_id)
        if role == "entry" and order is not None and float(order.filledQuantity) > group.filled_by_broker_order.get(order_id, 0):
            return {"status": "awaiting_processed_ladder_fill_state"}
    facts = []
    for order_id, role in group.broker_order_roles.items():
        quantity = group.filled_by_broker_order.get(order_id, 0.)
        if role != "entry" and order_id in live:
            quantity = max(quantity, float(live[order_id].filledQuantity))
        facts.append(LadderFillFact(order_id, group.broker_order_slices.get(order_id, ""),
                                    role, Decimal(str(quantity))))
    exposures = ladder_lot_exposure(tuple(item.slice_id for item in profile.slices), tuple(facts))
    held = sum(Decimal(str(row.position)) for row in await manager.broker.positions(group.account_id)
               if int(row.conid) == int(group.orders[0].conid))
    if held < sum(item.remaining for item in exposures):
        return {"status": "awaiting_ladder_position_reconciliation"}
    actions = []
    for exposure, rule in zip(exposures, profile.slices, strict=True):
        pairs = {}
        for order_id, lot_id in group.broker_order_slices.items():
            order = live.get(order_id)
            if (lot_id != exposure.lot_id or order is None
                    or order.order_status not in OPEN_ORDER_STATUSES
                    or order.order_status == OrderStatus.INACTIVE
                    or group.broker_order_roles.get(order_id) not in {"profit_target", "protective_stop"}):
                continue
            pairs.setdefault(_protection_group_key(order), {})[group.broker_order_roles[order_id]] = order
        # Never duplicate an orphaned target or stop. Its repair needs a
        # separately qualified capacity transfer, not an invented pairing.
        if any(set(pair) != {"profit_target", "protective_stop"} for pair in pairs.values()):
            return {"status": "ladder_orphan_capacity_requires_reconciliation"}
        original_coverage = sum(Decimal(str(min(float(pair['profit_target'].remainingQuantity),
            float(pair['protective_stop'].remainingQuantity)))) for pair in pairs.values()
            if not any("repair-" in order.cOID for order in pair.values()))
        if original_coverage >= exposure.remaining:
            for pair in pairs.values():
                if not any("repair-" in order.cOID for order in pair.values()):
                    continue
                async with manager._command_lane(group.account_id):
                    for order in pair.values():
                        response = await manager.broker.cancel_order(group.account_id, str(order.orderId))
                        if (not isinstance(response, dict) or response.get("error")
                                or str(response.get("order_id") or response.get("orderId")) != str(order.orderId)):
                            raise RuntimeError("Ladder repair cancellation is not acknowledged")
                        index = group.broker_order_request_indexes[str(order.orderId)]
                        from .independent_lot_repair_retirement import record_terminal_repair_readback
                        await record_terminal_repair_readback(manager, group, index, order)
                        manager._record_protection(group, group.orders[index], phase="effective",
                            broker_order_id=str(order.orderId), active=False)
                actions.append({"action": "retire_ladder_repair_pair", "lot_id": exposure.lot_id})
            continue
        coverage = sum(Decimal(str(min(float(pair['profit_target'].remainingQuantity),
            float(pair['protective_stop'].remainingQuantity)))) for pair in pairs.values())
        missing = exposure.remaining - coverage
        if missing <= 0:
            continue
        if any(abs(float(pair['profit_target'].remainingQuantity) - float(pair['protective_stop'].remainingQuantity)) > 1e-9
               for pair in pairs.values()):
            return {"status": "ladder_pair_capacity_changed"}
        root = next(order for order, identity in zip(group.plan.orders, group.plan.order_slice_ids, strict=True)
                    if identity == exposure.lot_id and order.side == "BUY")
        quantity = float(missing)
        if Decimal(str(quantity)) != missing:
            raise ValueError("Ladder repair quantity cannot round across the broker domain")
        tag = uuid4().hex[:12]
        common = dict(acctId=group.account_id, conid=root.conid, secType=root.secType,
            ticker=root.ticker, side="SELL", quantity=quantity, tif=root.tif,
            outsideRTH=root.outsideRTH, listingExchange=root.listingExchange, isSingleGroup=True)
        target = OrderRequest(**common, cOID=f"{manager._protective_order_prefix()}repair-target-{tag}",
                              orderType="LMT", price=rule.profit_target_price)
        stop = OrderRequest(**common, cOID=f"{manager._protective_order_prefix()}repair-{tag}",
                            orderType="STP", auxPrice=rule.stop.price)
        pair = tuple(replace(order, raw=canonical_runtime_order_raw(order, group.intent,
            run_id=manager.run_id, strategy_id=manager.strategy_id,
            strategy_revision=manager.strategy_revision)) for order in (target, stop))
        extended = group.plan.with_lot_repair_pair(lot_id=exposure.lot_id, pair=pair)
        start = len(group.orders)
        group.orders.extend(pair)
        group.plan = extended
        for order in pair:
            manager._group_by_client_id[order.cOID] = group.group_id
        manager._transition(group, group.state, {"event": "ladder_repair_planned"})
        try:
            async with manager._command_lane(group.account_id):
                for order in pair:
                    manager._record_protection(group, order, phase="requested")
                response = await manager.broker.place_orders(group.account_id, list(pair))
            _require_modify_acknowledgement(response)
            if (len(response) != 2
                    or any(not (row.get("order_id") or row.get("orderId")) for row in response)
                    or len({str(row.get("order_id") or row.get("orderId")) for row in response}) != 2
                    or any(str(row.get("order_id") or row.get("orderId")) in group.broker_order_ids for row in response)):
                raise RuntimeError("Ladder repair requires both unique broker acknowledgements")
        except Exception:
            # The planned pair and client IDs were persisted before dispatch.
            # Never issue a replacement while its broker outcome is unresolved.
            manager._transition(group, OrderManagementState.OUTCOME_UNKNOWN,
                                {"event": "ladder_repair_outcome_unknown"})
            raise
        for offset, (row, order, role) in enumerate(zip(response, pair, ("profit_target", "protective_stop"), strict=True)):
            order_id = str(row.get("order_id") or row.get("orderId"))
            if order_id in group.broker_order_ids:
                raise RuntimeError("Ladder repair acknowledgement repeats an owned order")
            group.broker_order_ids.append(order_id)
            manager._group_by_broker_id[order_id] = group.group_id
            group.broker_order_roles[order_id] = role
            group.broker_order_slices[order_id] = exposure.lot_id
            group.broker_order_request_indexes[order_id] = start + offset
            manager._record_protection(group, order, phase="effective", broker_order_id=order_id)
        manager._transition(group, group.state, {"event": "protection_repair_registered"})
        actions.append({"action": "place_ladder_repair_pair", "lot_id": exposure.lot_id,
                        "quantity": quantity, "target_price": rule.profit_target_price})
    observed = await manager.broker.live_orders()
    capacity = {}
    for order in observed:
        order_id = str(order.orderId)
        if (order_id in group.broker_order_slices
                and group.broker_order_roles.get(order_id) == "protective_stop"
                and order.order_status in OPEN_ORDER_STATUSES
                and order.order_status != OrderStatus.INACTIVE):
            key = (group.broker_order_slices[order_id], _protection_group_key(order))
            capacity[key] = max(capacity.get(key, 0.), float(order.remainingQuantity))
    required = sum(float(item.remaining) for item in exposures)
    protected = sum(min(float(item.remaining), sum(value for (lot_id, _), value in capacity.items()
                     if lot_id == item.lot_id)) for item in exposures)
    group.protection_required_quantity = required
    group.protection_coverage_quantity = protected
    if actions:
        manager._transition(group, group.state, {"event": "protection_repair_registered"})
    return {"status": "repaired" if actions else "reconciled", "actions": actions,
            "required_quantity": required, "protected_quantity": protected}
