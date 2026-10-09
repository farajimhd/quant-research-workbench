"""Declared repair metadata at creation; grants no protection or admission."""
from datetime import datetime
from math import isfinite

from .journal_contract import JournalRecord

RULE = 'independent-lot-repair-creation-lineage@1'


def metadata_at_creation(group, order, metadata, proofs, *, source, run_id,
                         strategy_id, strategy_revision, sequence, boundary):
    """Opt in only through an exact installed independent-lot declaration."""
    if order.side != 'SELL' or order.parentId or order.orderType not in {'LMT', 'STP', 'STOP_LIMIT'}:
        return None
    from .independent_lot_initial_stop_lineage import selected_source
    if not selected_source(source, run_id=run_id, strategy_id=strategy_id,
                           strategy_revision=strategy_revision):
        return None
    rules = source.installed_payload['strategy']['numbered_release']['contract']['rule_set_contracts']
    if RULE not in rules:
        return None
    if rules.count(RULE) != 1:
        raise ValueError('Repair creation lineage requires one declared rule')
    return _metadata_at_creation(group, order, metadata, proofs, run_id=run_id,
        strategy_id=strategy_id, strategy_revision=strategy_revision,
        sequence=sequence, boundary=boundary)


def _metadata_at_creation(group, order, metadata, proofs, *, run_id,
                          strategy_id, strategy_revision, sequence, boundary):
    """Pure typed-history reconstruction; caller retains full source authority."""
    if order.side != 'SELL' or order.parentId or order.orderType not in {'LMT', 'STP', 'STOP_LIMIT'}:
        return None
    if (type(sequence) is not int or sequence <= 0
            or not isinstance(boundary, datetime) or boundary.utcoffset() is None):
        raise ValueError('Repair creation lineage lacks a causal fence')
    orders = group.orders
    slices = group.plan.order_slice_ids if hasattr(group, 'plan') else group.order_slice_ids
    indexes = [i for i, value in enumerate(orders) if value.cOID == order.cOID]
    if len(slices) != len(orders) or len(indexes) != 1:
        raise ValueError('Repair creation lineage has ambiguous order ownership')
    index = indexes[0]
    stop_index = index if order.orderType in {'STP', 'STOP_LIMIT'} else index + 1
    if (stop_index >= len(orders) or slices[index] != slices[stop_index]
            or orders[stop_index].side != 'SELL' or orders[stop_index].parentId
            or orders[stop_index].orderType not in {'STP', 'STOP_LIMIT'}):
        raise ValueError('Repair creation lineage lacks its owned stop pair')
    if (order.acctId != group.account_id or order.ticker != group.intent.ticker
            or orders[stop_index].acctId != group.account_id
            or orders[stop_index].ticker != group.intent.ticker):
        raise ValueError('Repair creation lineage has foreign order identity')
    bindings = group.broker_order_request_indexes
    if any(type(i) is not int or not 0 <= i < len(orders) for i in bindings.values()):
        raise ValueError('Repair creation lineage has malformed broker bindings')
    by_client = {value.cOID: i for i, value in enumerate(orders)}
    if len(by_client) != len(orders):
        raise ValueError('Repair creation lineage repeats client identities')
    unique = {}
    for record in proofs.values():
        if type(record) is not JournalRecord or type(record.sequence) is not int:
            raise ValueError('Repair creation lineage requires typed journal records')
        if record.sequence in unique and unique[record.sequence] != record:
            raise ValueError('Repair creation lineage has ambiguous source sequence')
        unique[record.sequence] = record
    creations, amendments, initials = [], [], []
    for record in sorted(unique.values(), key=lambda value: value.sequence):
        payload = record.payload
        if payload.get('kind') != 'stop':
            continue
        client = payload.get('client_order_id')
        if client not in by_client:
            raise ValueError('Repair creation lineage has a foreign protection order')
        owned_index = by_client[client]
        owned = orders[owned_index]
        price = payload.get('price')
        if (record.run_id != run_id or record.account_id != group.account_id
                or record.category != 'protection' or record.entity_type != 'protection_change'
                or not 0 < record.sequence < sequence
                or not isinstance(record.event_time, datetime) or record.event_time.utcoffset() is None
                or record.event_time > boundary or payload.get('phase') != 'effective'
                or payload.get('order_group_id') != group.group_id
                or payload.get('source_intent_id') != group.intent.intent_id
                or payload.get('strategy_id') != strategy_id
                or type(payload.get('strategy_revision')) is not int
                or payload.get('strategy_revision') != strategy_revision
                or payload.get('ticker') != group.intent.ticker
                or bindings.get(payload.get('order_id')) != owned_index
                or owned.side != 'SELL' or owned.orderType not in {'STP', 'STOP_LIMIT'}
                or type(price) not in {int, float} or not isfinite(price) or price <= 0
                or not payload.get('intent_id')):
            raise ValueError('Repair creation lineage has foreign or future acknowledged history')
        action = payload.get('action')
        if action == 'replace_protective_stop':
            amendments.append(record)
        elif action in {None, 'enter_long'}:
            initials.append(record)
            if client == orders[stop_index].cOID:
                creations.append(record)
        else:
            raise ValueError('Repair creation lineage has an unsupported stop action')
    marker = group.intent.metadata.get('confirmed_support_stop')
    latest = amendments[-1].payload['price'] if amendments else None
    if marker != latest:
        raise ValueError('Repair creation lineage group marker lacks its acknowledged source')
    if stop_index in set(bindings.values()):
        if not creations:
            raise ValueError('Registered repair lacks its actual creation acknowledgement')
        # Later retirement/readback observations do not redefine creation.
        creation = creations[0]
        fence, at = creation.sequence, creation.event_time
    else:
        if creations:
            raise ValueError('Pending repair has an invented creation acknowledgement')
        lot = slices[stop_index]
        original = [record for record in initials
                    if slices[by_client[record.payload['client_order_id']]] == lot
                    and orders[by_client[record.payload['client_order_id']]].parentId]
        if not original:
            raise ValueError('Pending repair lacks its original lot acknowledgement')
        earned = original + [record for record in amendments
                             if slices[by_client[record.payload['client_order_id']]] == lot]
        if orders[stop_index].auxPrice != max(record.payload['price'] for record in earned):
            raise ValueError('Pending repair differs from its acknowledged lot price')
        fence, at = sequence, boundary
    prior = [record for record in amendments if record.sequence < fence and record.event_time <= at]
    result = dict(metadata)
    result.pop('confirmed_support_stop', None)
    if prior:
        result['confirmed_support_stop'] = prior[-1].payload['price']
    return result
