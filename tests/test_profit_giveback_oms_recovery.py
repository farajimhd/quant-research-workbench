"""Exact profit factory and Portfolio proof through cold OMS reconstruction."""
from datetime import date
from dataclasses import replace
from uuid import uuid4

import pytest

from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, _approved_strategy_one_oms_intent,
    reconstruct_strategy_one_oms_lineage,
)
from src.trading_runtime.arte_profit_giveback_v4 import project_profit_giveback
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import financial, intent, ENTRY


def prepared(strategy_number=31):
    held, witness = financial(), profit_giveback(sample())
    exit_intent = intent(witness, held, strategy_number=strategy_number)
    record, batch, group_batch = (str(uuid4()) for _ in range(3))
    run = 'backtest:profit-oms'
    source = RecoveredIntent(10, held.account_id, record, batch, exit_intent)
    order = OrderRequest(acctId=held.account_id, conid=123, cOID='exit-1',
        ticker=held.ticker, orderType='LMT', side='SELL', quantity=held.position_quantity,
        price=exit_intent.reference_price)
    group = RecoveredOmsGroupState(12, record, dict(run_id=run, batch_id=group_batch,
        account_id=held.account_id, group_id='group', strategy_id='early-squeeze-strategy',
        strategy_revision=strategy_number, strategy_intent_id=exit_intent.intent_id),
        (order,), (0,), ('',), (), (), ())
    history = CompleteProtectionHistory(run, 12, (batch, group_batch), ())
    reservation = dict(account_id=held.account_id, intent_id=exit_intent.intent_id,
        decision_id='decision', reservation_id='reservation', account_key='cash',
        assignment_id=held.assignment_id, quantity=held.position_quantity)
    decision = dict(decision_id='decision', reservation_id='reservation', account_key='cash',
        status='approved', policy_id='cash', policy_revision=1,
        requested_quantity=held.position_quantity)
    row = project_profit_giveback(witness, exit_intent, held, session_date=date(2026, 8, 4),
        source_entry_intent_id=ENTRY, run_id=run, batch_id=batch, parent_record_id=record,
        source_manager_snapshot_id=str(uuid4()), source_manager_checkpoint_sequence=7,
        strategy_number=strategy_number)
    return group, source, history, reservation, decision, row


@pytest.mark.parametrize('number', [31, 32, 33, 34])
def test_full_profit_exit_reconstructs_exact_order_and_portfolio_assignment(number):
    group, source, history, reservation, decision, row = prepared(strategy_number=number)
    approved, _ = _approved_strategy_one_oms_intent(
        group, source, history, reservation, decision, profit_giveback_row=row)
    assert approved.metadata['assignment_id'] == reservation['assignment_id']
    orders = reconstruct_strategy_one_oms_lineage(group, source, history,
        admission_reservation=reservation, admission_decision=decision, profit_giveback_row=row)
    assert orders[0].raw == canonical_runtime_order_raw(group.orders[0], approved,
        run_id=history.run_id, strategy_id='early-squeeze-strategy', strategy_revision=number)


@pytest.mark.parametrize('number', [31, 32, 33, 34])
def test_profit_row_cannot_cross_numbered_oms_group(number):
    group, source, history, reservation, decision, row = prepared(strategy_number=number)
    with pytest.raises(ValueError, match='exact committed scalar witness'):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            profit_giveback_row={**row, 'strategy_number': (31 if number != 31 else 32)})


@pytest.mark.parametrize('field,value', [
    ('strategy_number', 30), ('run_id', 'wrong'), ('assignment_id', 'wrong'),
    ('parent_record_id', str(uuid4())), ('batch_id', str(uuid4())),
    ('source_manager_checkpoint_sequence', 10), ('source_entry_intent_id', str(uuid4())),
    ('bid', 10.49),
])
def test_changed_profit_source_cannot_restore_approved_order(field, value):
    group, source, history, reservation, decision, row = prepared()
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group, source, history, reservation, decision,
            profit_giveback_row={**row, field: value})


def test_profit_cannot_restore_without_witness_admission_or_with_resized_quantity():
    group, source, history, reservation, decision, row = prepared()
    for admitted, selected in ((None, row), (reservation, None),
                              ({**reservation, 'quantity': 1.}, row)):
        with pytest.raises(ValueError):
            _approved_strategy_one_oms_intent(group, source, history, admitted,
                decision if admitted is not None else None, profit_giveback_row=selected)
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(replace(group,
            group={**group.group, 'strategy_revision': 30}), source, history,
            reservation, decision, profit_giveback_row=row)


@pytest.mark.parametrize('number', [31, 32, 33, 34])
def test_cold_join_routes_exact_native_source_to_profit_reader(monkeypatch, number):
    from src.trading_runtime import arte_oms_projection as oms
    from src.trading_runtime import arte_intent_projection as intents
    from src.trading_runtime import arte_profit_giveback_reader_v4 as reader
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    group, source, history, reservation, decision, row = prepared(strategy_number=number)
    prefix = V4CommittedPrefix(history.run_id, 12, group.group['batch_id'],
        '2026-08-04:10000', 'running', history.committed_batch_ids)
    monkeypatch.setattr(oms, 'load_latest_committed_oms_groups', lambda *a, **k: (group,))
    monkeypatch.setattr(intents, 'load_committed_strategy_intent_page', lambda *a, **k: (source,))
    monkeypatch.setattr(oms, 'load_committed_oms_admission_page', lambda *a, **k: {12: reservation})
    monkeypatch.setattr(oms, 'load_committed_oms_decision_page', lambda *a, **k: {12: decision})
    authority = object()
    calls = []
    def load(client, proof, record_id, *, first_price_source):
        calls.append((client, proof, record_id, first_price_source))
        return row
    monkeypatch.setattr(reader, 'load_committed_profit_giveback', load)
    client = object()
    result = oms.load_recovered_strategy_one_oms_lineage(client, prefix,
        allowed_accounts=frozenset({source.account_id}), protection_history=history,
        strategy_number=number, first_price_source=authority)
    assert calls == [(client, prefix, source.record_id, authority)]
    assert len(result) == 1 and result[0].approved_intent.reason == source.intent.reason
    assert result[0].approved_intent.metadata['assignment_id'] == reservation['assignment_id']


def test_future35_cold_join_remains_closed_until_installed_contract():
    from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    group, source, history, _, _, _ = prepared(strategy_number=34)
    prefix = V4CommittedPrefix(history.run_id, 12, group.group['batch_id'],
        '2026-08-04:10000', 'running', history.committed_batch_ids)
    with pytest.raises(ValueError, match='No installed numbered'):
        load_recovered_strategy_one_oms_lineage(object(), prefix,
            allowed_accounts=frozenset({source.account_id}), protection_history=history,
            strategy_number=35)
