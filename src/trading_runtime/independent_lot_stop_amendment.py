"""Owned independent-lot amendments; a scalar command is never a lot receipt."""
from dataclasses import replace
from decimal import Decimal
import math

from .execution_policies import AddProtectionPolicy
from .ibkr_schema import OPEN_ORDER_STATUSES, OrderStatus
from .squeeze_ladder_lots import LadderFillFact, ladder_lot_exposure


def independent_profile(intent):
    profile = intent.resolved_protection_profile()
    return profile if profile is not None and profile.add_policy == AddProtectionPolicy.INDEPENDENT_FIXED_LOTS else None


def amended_lot_profile(profile, lot_id, price):
    if (type(price) is not float or not math.isfinite(price) or price <= 0
            or sum(item.slice_id == lot_id for item in profile.slices) != 1):
        raise ValueError("Independent stop amendment has invalid lot identity or price")
    return replace(profile, slices=tuple(
        replace(item, stop=replace(item.stop, price=price)) if item.slice_id == lot_id else item
        for item in profile.slices))


def rebuild_independent_profile(profile, orders, slice_ids, bindings, history, *,
                                run_id, account_id, group_id, source_intent_id,
                                sequence, boundary):
    """Exact per-order effective history, independent of the scalar last stop."""
    if len(orders) != len(slice_ids) or len({v.cOID for v in orders}) != len(orders):
        raise ValueError("Independent cold order inventory is invalid")
    by_client = {order.cOID: (index, order) for index, order in enumerate(orders)}
    bound = {}
    bound_indexes = set()
    for broker_id, index, lot, role in bindings:
        if (type(index) is not int or not 0 <= index < len(orders)
                or broker_id in bound or index in bound_indexes or slice_ids[index] != lot
                or role not in {'entry', 'profit_target', 'protective_stop'}):
            raise ValueError("Independent cold binding is invalid")
        bound[broker_id] = (index, lot, role)
        bound_indexes.add(index)
    latest, initial_proofs, seen = {}, {}, set()
    earned = {item.slice_id: item.stop.price for item in profile.slices}
    earned_source = {}
    for record in sorted(history, key=lambda value: value.sequence):
        payload = record.payload
        if payload.get('phase') != 'effective':
            continue
        if payload.get('kind') not in {'stop', 'target'}:
            continue
        creation = payload.get('kind') == 'stop' and payload.get('action') in {None, 'enter_long'}
        if not creation and payload.get('action') not in {'replace_protective_stop', 'replace_profit_target'}:
            continue
        if payload.get('kind') == 'target':
            raise ValueError("Independent fixed targets cannot be amended")
        broker_id, client_id = payload.get('order_id'), payload.get('client_order_id')
        if (record.sequence in seen or record.run_id != run_id or record.account_id != account_id
                or record.category != 'protection' or record.entity_type != 'protection_change'
                or not 0 < record.sequence < sequence or record.event_time > boundary
                or payload.get('order_group_id') != group_id
                or payload.get('source_intent_id') != source_intent_id
                or payload.get('kind') != 'stop' or broker_id not in bound
                or client_id not in by_client or not payload.get('intent_id')):
            raise ValueError("Independent effective stop proof differs from its source prefix")
        index, lot, role = bound[broker_id]
        if role != 'protective_stop' or by_client[client_id][0] != index:
            raise ValueError("Independent effective stop proof differs from its order binding")
        price = payload.get('price')
        prior = latest.get(client_id)
        initial = next(item.stop.price for item in profile.slices if item.slice_id == lot)
        if type(price) not in {int, float} or not math.isfinite(price) or price < (prior.payload['price'] if prior else initial):
            raise ValueError("Independent effective stop proof is not upward and finite")
        amended_lot_profile(profile, lot, float(price))
        if creation:
            request = orders[index]
            expected_creation = prior.payload['price'] if prior else (earned[lot] if not request.parentId else initial)
            if price != expected_creation:
                raise ValueError(f"Independent initial stop differs from its prior earned source: sequence={record.sequence}, client={client_id}, actual={price}, expected={expected_creation}")
            initial_proofs[client_id] = record
        else:
            latest[client_id] = record
            earned[lot] = max(earned[lot], price)
        earned_source[lot] = record
        seen.add(record.sequence)
    rebuilt = profile
    for index, order in enumerate(orders):
        lot = slice_ids[index]
        rule = next((item for item in profile.slices if item.slice_id == lot), None)
        if rule is None:
            raise ValueError("Independent cold order has a foreign lot")
        if order.side == 'SELL' and order.orderType == 'LMT' and order.price != rule.profit_target_price:
            raise ValueError("Independent cold target differs from its original source")
        if order.side == 'SELL' and order.orderType in {'STP', 'STOP_LIMIT'}:
            proof = latest.get(order.cOID)
            creation = initial_proofs.get(order.cOID)
            if not order.parentId and creation is None:
                if index in bound_indexes:
                    raise ValueError("Independent repair stop lacks its initial effective creation proof")
                # A journaled, unbound repair is a pending request only. Exact
                # earlier ACK source proves its requested price, not protection.
                prior_lot = earned_source.get(lot)
                if prior_lot is None or order.auxPrice != earned[lot]:
                    raise ValueError("Independent pending repair lacks its exact earlier earned stop")
            expected = proof.payload['price'] if proof else (creation.payload['price'] if creation else
                earned[lot] if not order.parentId and index not in bound_indexes else rule.stop.price)
            if order.auxPrice != expected:
                raise ValueError("Independent cold stop lacks its exact effective order proof")
            if proof or creation:
                current = next(item.stop.price for item in rebuilt.slices if item.slice_id == lot)
                rebuilt = amended_lot_profile(rebuilt, lot, max(float(current), float(expected)))
    return rebuilt


def _prepare(group, live, desired):
    profile = independent_profile(group.intent)
    if profile is None or group.protection_delegated or group.intent.action != 'enter_long':
        raise ValueError("Independent amendment requires its own fixed long acquisition")
    ids = tuple(item.slice_id for item in profile.slices)
    if (len(set(group.broker_order_ids)) != len(group.broker_order_ids)
            or len(group.plan.order_slice_ids) != len(group.orders)
            or len({order.cOID for order in group.orders}) != len(group.orders)
            or set(group.broker_order_request_indexes) != set(group.broker_order_ids)
            or set(group.broker_order_roles) != set(group.broker_order_ids)
            or set(group.broker_order_slices) != set(group.broker_order_ids)):
        raise ValueError("Independent stop ownership is incomplete")
    facts, acquiring, stops, indexes, capacity = [], set(), [], set(), dict.fromkeys(ids, Decimal(0))
    for broker_id in group.broker_order_ids:
        index = group.broker_order_request_indexes[broker_id]
        if type(index) is not int or not 0 <= index < len(group.orders) or index in indexes:
            raise ValueError("Independent stop request binding is invalid")
        indexes.add(index)
        request, lot = group.orders[index], group.broker_order_slices[broker_id]
        role = group.broker_order_roles[broker_id]
        if lot not in ids or group.plan.order_slice_ids[index] != lot:
            raise ValueError("Independent stop slice binding differs from its plan")
        order = live.get(broker_id)
        if order is None:
            raise ValueError("Independent stop broker roster is incomplete")
        if any(a != b for a, b in ((order.account, group.account_id), (order.cOID, request.cOID),
                (order.ticker, request.ticker), (order.conid, request.conid),
                (order.side, request.side), (order.orderType, request.orderType))):
            raise ValueError("Independent stop broker identity differs from its request")
        observed = float(order.filledQuantity)
        known = float(group.filled_by_broker_order.get(broker_id, 0.))
        if not all(math.isfinite(v) and v >= 0 for v in (observed, known, float(order.remainingQuantity))):
            raise ValueError("Independent stop broker quantities are invalid")
        if observed + float(order.remainingQuantity) > float(request.quantity) + 1e-9:
            raise ValueError("Independent stop broker quantity exceeds its request")
        if role == 'entry' and observed > known:
            raise ValueError("Independent stop awaits processed acquisition fills")
        facts.append(LadderFillFact(broker_id, lot, role, Decimal(str(known if role == 'entry' else max(known, observed)))))
        active = order.order_status in OPEN_ORDER_STATUSES
        if role == 'entry' and active and float(order.remainingQuantity) > 0:
            acquiring.add(lot)
        rule = next(item for item in profile.slices if item.slice_id == lot)
        if role == 'profit_target' and (request.price != rule.profit_target_price or order.price != rule.profit_target_price):
            raise ValueError("Independent fixed target was amended")
        if role == 'protective_stop' and active:
            if request.side != 'SELL' or request.orderType not in {'STP', 'STOP_LIMIT'}:
                raise ValueError("Independent stop role differs from its request")
            current = float(order.auxPrice or 0)
            if not math.isfinite(current) or current <= 0 or current >= rule.profit_target_price:
                raise ValueError("Independent broker stop differs from its fixed bracket")
            amended_lot_profile(profile, lot, max(desired, current))
            stops.append((order, index, lot))
            if order.order_status != OrderStatus.INACTIVE:
                capacity[lot] += Decimal(str(float(order.remainingQuantity)))
    exposures = ladder_lot_exposure(ids, tuple(facts))
    if any(capacity[row.lot_id] < row.remaining for row in exposures):
        raise ValueError("Independent stop coverage is incomplete before amendment")
    active_ids = {row.lot_id for row in exposures if row.remaining > 0} | acquiring
    if any(desired >= item.profit_target_price for item in profile.slices if item.slice_id in active_ids):
        raise ValueError("Independent stop exceeds an active or acquiring fixed target")
    if any(lot not in active_ids for _, _, lot in stops):
        raise ValueError("Independent stop remains live on a retired lot")
    return tuple(stops)


async def replace_independent_stops(manager, intent, account_id):
    from .order_management import OrderManagementState, _require_modify_acknowledgement, _warning_response

    desired = float(intent.invalidation_price or 0)
    if not math.isfinite(desired) or not math.isfinite(intent.reference_price) or desired <= 0 or desired >= intent.reference_price:
        raise ValueError("Long support stop must be positive and below the market")
    assignment = str(intent.metadata.get('assignment_id') or '')
    groups = tuple(group for group in manager._groups.values()
        if group.account_id == account_id and group.intent.ticker == intent.ticker
        and str(group.intent.metadata.get('assignment_id') or '') == assignment
        and group.intent.action == 'enter_long')
    observed = tuple(await manager.broker.live_orders())
    if len({str(order.orderId) for order in observed}) != len(observed):
        raise ValueError("Independent broker roster repeats order identities")
    live = {str(order.orderId): order for order in observed}
    recovery=getattr(manager,'_fixed_lot_recovery_context',None)
    if recovery is not None:
        from src.backend.backtest_fixed_structural_lot_management import FixedStructuralLotRecoveryContext
        if type(recovery) is not FixedStructuralLotRecoveryContext:
            raise ValueError('Independent recovery has no exact selected source')
        recovery.owner.require_recovery(recovery)
        recovery.owner.verify_recovery_execution(recovery,intent)
        if recovery.request.financial.account_id!=account_id:
            raise ValueError('Independent recovery has foreign command/assignment')
    # Validate every affected group and every active/acquiring lot BEFORE the first command.
    prepared = tuple((group, _prepare(group, live, desired)) for group in groups)
    if recovery is not None:
        recovery.owner.verify_recovery_roster(recovery,manager,intent,prepared)
    else:
        lookup=getattr(manager.journal,'fixed_lot_management_request',None)
        selected=lookup(intent.intent_id) if lookup is not None else None
        if selected is not None:
            selected.owner._verify_issued_request(selected)
            if selected.financial.account_id!=account_id:
                raise ValueError('Selected stop command has foreign owner account')
            if any(float(order.auxPrice)>=desired and float(order.auxPrice)!=group.orders[index].auxPrice
                    for group,stops in prepared for order,index,lot in stops):
                raise ValueError('Selected lost stop outcome requires explicit issued recovery')
    changed = []
    for group, stops in prepared:
        for order, index, lot in stops:
            request = replace(group.orders[index], quantity=float(order.filledQuantity) + float(order.remainingQuantity))
            actual = float(order.auxPrice)
            confirmed = max(desired, actual)
            replacement = replace(request, auxPrice=confirmed,
                price=(float(request.price) + confirmed - float(request.auxPrice or 0)
                       if request.orderType == 'STOP_LIMIT' and request.price is not None else request.price))
            if actual >= desired and group.orders[index].auxPrice == actual and recovery is None:
                continue
            try:
                if actual >= desired:
                    history=(recovery.requested_records if recovery is not None
                        else manager.journal.protection_records(manager.run_id))
                    source_command=(recovery.original_command.intent_id if recovery is not None else intent.intent_id)
                    matching = [record for record in history
                        if record.account_id == account_id and record.payload.get('order_group_id') == group.group_id
                        and record.payload.get('source_intent_id') == group.intent.intent_id
                        and record.payload.get('client_order_id') == request.cOID
                        and record.payload.get('order_id') == str(order.orderId)
                        and record.payload.get('phase') == 'requested'
                        and record.payload.get('action') == 'replace_protective_stop'
                        and record.payload.get('price') == actual
                        and record.payload.get('intent_id') == source_command]
                    if not matching:
                        raise ValueError("Independent lost acknowledgement lacks its exact requested command")
                    # Independent broker readback is the outcome evidence, not a retry.
                    if recovery is not None:
                        recovery.owner.record_recovery_readback(recovery,manager,intent,
                            group,request,order,lot)
                else:
                    async with manager._command_lane(account_id):
                        manager._record_protection(group, replacement, phase='requested',
                            broker_order_id=str(order.orderId), event_time=intent.event_time, amendment_intent=intent)
                        response = await manager.broker.modify_order(account_id, str(order.orderId), replacement)
                    if _warning_response(response):
                        async with manager._warning_lane:
                            response = await manager._resolve_warning_chain_locked(group, response)
                    _require_modify_acknowledgement(response)
                    manager._record_strategy_one_modify_acknowledgement(group, intent, str(order.orderId), response)
                manager._record_protection(group, replacement, phase='effective',
                    broker_order_id=str(order.orderId), event_time=intent.event_time, amendment_intent=intent)
                group.orders[index] = replacement
                profile = amended_lot_profile(independent_profile(group.intent), lot, confirmed)
                group.intent = replace(group.intent, invalidation_price=confirmed, protection_profile=profile,
                    metadata={**group.intent.metadata, 'confirmed_support_stop': confirmed})
                group.updated_at = intent.event_time
                manager._transition(group, group.state, {'event': 'support_stop_replaced', 'stop': confirmed})
                changed.append(group)
            except Exception:
                manager._transition(group, OrderManagementState.OUTCOME_UNKNOWN,
                    {'event': 'independent_stop_outcome_unknown'})
                raise
    if not changed:
        protected = tuple(group for group, stops in prepared if stops)
        if protected:
            return protected[-1].snapshot(manager.policy.version)
        raise ValueError("No broker-held support stop is available for replacement")
    return changed[-1].snapshot(manager.policy.version)
