"""Shared cold-path verification of held broker quantity and committed OMS ownership.

Callers must independently verify the committed prefix, broker image and full
OMS inventory. This check grants neither checkpoint attestation nor order admission.
"""
from .strategy_one_contract import STRATEGY_ID


def verify_native_held_oms_position(prefix, sequence, image, lineage, *, accounts,
        strategy_number, financial, source_entry_intent_id, expected_exit_quantity):
    """Return the original entry instrument and exact current held quantity.

    Inspect all recovered groups, including pending exits, before accepting the
    original entry. This bounded worker operation performs no database queries.
    """
    from .arte_oms_projection import RecoveredStrategyOneOmsLineage, RecoveredOmsGroupState
    from .arte_intent_projection import RecoveredIntent
    from .ibkr_schema import OrderRequest
    from .signals import StrategyIntent
    from .order_management import OrderManagementState, TERMINAL_MANAGEMENT_STATES
    from .strategy_one_broker_match_snapshot import float64_from_bits
    if type(lineage) is not tuple or len(lineage) > 2_000:
        raise ValueError('Liquidity financial OMS inventory exceeds its native bound')
    seen, entries, pending_exit = set(), [], False
    for item in lineage:
        if (type(item) is not RecoveredStrategyOneOmsLineage or type(item.state) is not RecoveredOmsGroupState
                or type(item.source_intent) is not RecoveredIntent or type(item.approved_intent) is not StrategyIntent
                or type(item.state.group) is not dict or type(item.source_intent.intent) is not StrategyIntent
                or item.through_sequence != sequence or type(item.admission_reservation) is not dict):
            raise ValueError('Liquidity financial checkpoint lacks exact native OMS lineage')
        group, source, approved = item.state.group, item.source_intent, item.approved_intent
        assignment = item.admission_reservation.get('assignment_id')
        identity = group['account_id'], group['group_id']
        if (identity in seen or group['account_id'] not in accounts
                or group['run_id'] != prefix.run_id or str(group['batch_id']) not in prefix.batch_ids
                or group['strategy_id'] != STRATEGY_ID
                or type(group['strategy_revision']) is not int or group['strategy_revision'] != strategy_number
                or not 0 < source.sequence < item.state.sequence <= sequence
                or source.account_id != group['account_id'] or str(source.batch_id) not in prefix.batch_ids
                or item.state.intent_record_id != source.record_id
                or group['strategy_intent_id'] != source.intent.intent_id
                or type(assignment) is not str or not assignment
                or approved.metadata.get('assignment_id') != assignment
                or approved.intent_id != source.intent.intent_id
                or approved.ticker != source.intent.ticker or approved.action != source.intent.action):
            raise ValueError('Liquidity financial OMS lineage differs from its pinned identity')
        seen.add(identity)
        state = OrderManagementState(group['state'])
        if (source.account_id, assignment, source.intent.ticker) != (
                financial.account_id, financial.assignment_id, financial.ticker):
            continue
        if source.intent.action in {'exit', 'exit_long', 'reduce_long'} and state not in TERMINAL_MANAGEMENT_STATES:
            pending_exit = True
        if source.intent.intent_id == source_entry_intent_id:
            if source.intent.action != 'enter_long':
                raise ValueError('Liquidity financial source is not its original entry')
            entries.append(item)
    if pending_exit:
        raise ValueError('Liquidity native OMS checkpoint has a pending exit')
    if len(entries) != 1 or type(entries[0].orders) is not tuple or not entries[0].orders:
        raise ValueError('Liquidity financial checkpoint lacks its unique original entry instrument')
    orders = entries[0].orders
    if any(type(order) is not OrderRequest or order.acctId != financial.account_id
           or order.ticker != financial.ticker or type(order.conid) is not int or order.conid <= 0 for order in orders):
        raise ValueError('Liquidity original entry orders differ from its instrument')
    conids = {order.conid for order in orders}
    if len(conids) != 1:
        raise ValueError('Liquidity original entry has ambiguous instrument identity')
    conid = next(iter(conids))
    positions = [item for item in image.positions
                 if (item['account_id'], item['conid']) == (financial.account_id, conid)]
    if len(positions) != 1 or positions[0]['ticker'] != financial.ticker:
        raise ValueError('Liquidity broker checkpoint lacks its exact held instrument')
    quantity = float64_from_bits(positions[0]['quantity_f64_bits'], 'held quantity')
    if quantity <= 0 or quantity != financial.position_quantity or quantity != expected_exit_quantity:
        raise ValueError('Liquidity held quantity differs from its native broker checkpoint')
    return conid, quantity
