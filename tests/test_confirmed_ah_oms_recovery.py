"""Prepared AH exit reconstruction; not connected recovery or installed admission."""
from dataclasses import replace

import pytest

from src.trading_runtime.arte_oms_projection import (
    _approved_strategy_one_oms_intent, reconstruct_strategy_one_oms_lineage,
)
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw
from test_arte_confirmed_ah_failure_v4 import prepared_case
from test_profit_giveback_oms_recovery import prepared as profit_prepared


def prepared():
    group, source, history, reservation, decision, _ = profit_prepared(34)
    _, held, _, intent, row = prepared_case()
    source = replace(source, intent=intent)
    order = replace(group.orders[0], ticker=held.ticker, quantity=held.position_quantity,
                    price=intent.reference_price)
    group = replace(group, group={**group.group, 'strategy_intent_id': intent.intent_id},
                    orders=(order,))
    reservation = {**reservation, 'intent_id': intent.intent_id, 'quantity': held.position_quantity}
    decision = {**decision, 'requested_quantity': held.position_quantity}
    row = {**row, 'run_id': history.run_id, 'batch_id': source.batch_id,
           'parent_record_id': source.record_id}
    return group, source, history, reservation, decision, row


def test_complete_two_timeframe_exit_restores_exact_order_and_assignment():
    group, source, history, reservation, decision, row = prepared()
    approved, _ = _approved_strategy_one_oms_intent(group, source, history,
        reservation, decision, confirmed_ah_row=row)
    assert approved.metadata['assignment_id'] == reservation['assignment_id']
    assert replace(approved, metadata={}) == source.intent
    orders = reconstruct_strategy_one_oms_lineage(group, source, history,
        admission_reservation=reservation, admission_decision=decision, confirmed_ah_row=row)
    assert orders[0].raw == canonical_runtime_order_raw(group.orders[0], approved,
        run_id=history.run_id, strategy_id='early-squeeze-strategy', strategy_revision=34)


@pytest.mark.parametrize('field,value', [
    ('strategy_number', 33), ('run_id', 'wrong'), ('assignment_id', 'wrong'),
    ('parent_record_id', 'wrong'), ('batch_id', 'wrong'),
    ('completed_ten_second_boundary_ms', 43710000), ('ten_second_macd_line', .1),
    ('bid', 2.02), ('source_entry_intent_id', 'wrong'),
])
def test_changed_scalar_or_parent_authority_cannot_restore(field, value):
    group, source, history, reservation, decision, row = prepared()
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            confirmed_ah_row={**row, field: value})


@pytest.mark.parametrize('case', ['missing_witness', 'missing_admission', 'resized', 'mixed', 'old_revision'])
def test_missing_mixed_or_changed_financial_authority_rejects(case):
    group, source, history, reservation, decision, row = prepared()
    if case == 'missing_witness':
        row = None
    elif case == 'missing_admission':
        reservation = decision = None
    elif case == 'resized':
        reservation = {**reservation, 'quantity': 1.}
    elif case == 'old_revision':
        group = replace(group, group={**group.group, 'strategy_revision': 33})
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            confirmed_ah_row=row, profit_giveback_row={} if case == 'mixed' else None)
