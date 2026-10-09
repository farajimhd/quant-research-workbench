"""Exact broker evidence for retiring an owned independent repair leg.

This verifier issues no commands and changes no order or cash state. A cancel
submission response alone must never authorize terminal ownership.
"""
from math import isfinite

from .ibkr_schema import OrderStatus


def require_cancelled_repair_readback(group, request_index, before, after):
    """Return the exact owned broker ID only after terminal cancellation readback."""
    if type(request_index) is not int or not 0 <= request_index < len(group.orders):
        raise ValueError('Repair retirement has an invalid request index')
    request = group.orders[request_index]
    broker_id = str(before.orderId)
    lot = group.broker_order_slices.get(broker_id)
    if (not broker_id or group.broker_order_request_indexes.get(broker_id) != request_index
            or broker_id not in group.broker_order_ids
            or group.broker_order_roles.get(broker_id) not in {'profit_target', 'protective_stop'}
            or not lot or group.plan.order_slice_ids[request_index] != lot
            or request.parentId or request.side != 'SELL' or 'repair-' not in request.cOID):
        raise ValueError('Repair retirement lacks exact independent leg ownership')
    if after is None or str(after.orderId) != broker_id:
        raise ValueError('Repair retirement lacks exact broker readback')
    for observed in (before, after):
        if any(actual != expected for actual, expected in (
                (observed.account, group.account_id), (observed.cOID, request.cOID),
                (observed.conid, request.conid), (observed.ticker, request.ticker),
                (observed.side, request.side), (observed.orderType, request.orderType),
                (observed.price, request.price), (observed.auxPrice, request.auxPrice),
                (observed.parentId or '', request.parentId or ''))):
            raise ValueError('Repair retirement readback has foreign order identity')
        quantities = (observed.filledQuantity, observed.remainingQuantity, observed.totalSize)
        if any(type(value) not in {int, float} or not isfinite(value) or value < 0
                for value in quantities):
            raise ValueError('Repair retirement has invalid broker quantities')
        if observed.totalSize != request.quantity:
            raise ValueError('Repair retirement broker size differs from its request')
    if after.order_status != OrderStatus.CANCELLED:
        raise ValueError('Repair cancellation is not terminally observed')
    if after.filledQuantity != before.filledQuantity:
        raise ValueError('Repair retirement awaits newly observed fill reconciliation')
    return broker_id
