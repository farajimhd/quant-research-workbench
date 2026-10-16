from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio

import pytest

from src.backend.completed_program_execution_evidence import _project_attribution
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.ibkr_schema import Execution, OrderRequest
from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState


AT = datetime(2026, 7, 31, 14, tzinfo=timezone.utc)


def evidence():
    intent = StrategyIntent('intent', 'TEST', AT, 'enter_long', 10, 5., reason='program_entry')
    request = OrderRequest('SIM-01', 123, 'MKT', 'BUY', quantity=10., cOID='client', ticker='TEST')
    group = RecoveredOmsGroupState(8, 'record', dict(group_id='group', account_id='SIM-01',
        strategy_id='program', strategy_revision=1, strategy_intent_id='intent'),
        (request,), (0,), ('',), (dict(broker_order_id='42', request_index=0,
        has_role=True, role='entry'),), (), ())
    source = RecoveredIntent(4, 'SIM-01', 'record', 'batch', intent)
    assignment = SimpleNamespace(assignment_id='assignment', account_id='SIM-01',
        ticker='TEST', strategy_id='program', strategy_revision=1)
    execution = Execution('SIM-1', 'TEST', 'B', 'client', AT, int(AT.timestamp()*1000),
        10., 5., '42', 'SIM-01', 123, commission=1.)
    return (group,), {8: dict(assignment_id='assignment')}, {'record': source}, (assignment,), execution


def test_verified_admission_identity_uses_existing_native_oms_validator():
    groups, admissions, sources, assignments, execution = evidence()
    view, requests, broker_coids = _project_attribution(groups, admissions, sources, assignments)
    result = view.execution_attribution(execution)
    assert (result.account_id, result.assignment_id, result.intent_id, result.action, result.role) == (
        'SIM-01', 'assignment', 'intent', 'enter_long', 'entry')
    assert requests['client'] == groups[0].orders[0]
    assert broker_coids == {'42': 'client'}
    assert sources['record'].intent.metadata == {}
    assert not hasattr(view, 'submit') and not hasattr(view, 'broker')


@pytest.mark.parametrize('changes', (
    dict(account='OTHER'), dict(order_ref='other'), dict(conid=124),
    dict(symbol='OTHER'), dict(side='S'), dict(order_id='43'),
))
def test_conflicting_actual_execution_cannot_borrow_request_attribution(changes):
    groups, admissions, sources, assignments, execution = evidence()
    view, _, _ = _project_attribution(groups, admissions, sources, assignments)
    with pytest.raises(ValueError):
        view.execution_attribution(replace(execution, **changes))


@pytest.mark.parametrize('binding', (
    dict(broker_order_id='42', request_index=1, has_role=True, role='entry'),
    dict(broker_order_id='', request_index=0, has_role=True, role='entry'),
))
def test_incomplete_committed_broker_bindings_fail_closed(binding):
    groups, admissions, sources, assignments, _ = evidence()
    with pytest.raises(ValueError):
        _project_attribution((replace(groups[0], broker_bindings=(binding,)),), admissions, sources, assignments)


@pytest.mark.parametrize('binding', (
    dict(broker_order_id='42', request_index=None, has_role=True, role='entry'),
    dict(broker_order_id='42', request_index=0, has_role=False, role=''),
))
def test_unattributed_oms_binding_is_retained_but_cannot_attribute_a_fill(binding):
    groups, admissions, sources, assignments, execution = evidence()
    view, _, _ = _project_attribution((replace(groups[0], broker_bindings=(binding,)),), admissions, sources, assignments)
    assert view._group_by_broker_id['42'] == 'group'
    with pytest.raises(ValueError):
        view.execution_attribution(execution)


def test_foreign_assignment_or_repeated_group_fails_closed():
    groups, admissions, sources, assignments, _ = evidence()
    with pytest.raises(ValueError):
        _project_attribution(groups, {8: dict(assignment_id='OTHER')}, sources, assignments)
    with pytest.raises(ValueError):
        _project_attribution(groups+groups, admissions, sources, assignments)


@pytest.mark.parametrize('fault', (None, 'writable', 'moved', 'running'))
def test_completed_reader_fences_readonly_scope_and_closes_owned_client(monkeypatch, fault):
    from src.backend import completed_program_execution_evidence as module
    from tests.test_recorded_program_comparison import inputs
    prefix, packet, *_ = inputs(monkeypatch)
    prefix = replace(prefix, status='completed')
    state = dict(closed=False, calls=0)
    class Client:
        def execute(self, query):
            assert query == "SELECT getSetting('readonly')"
            return '0' if fault == 'writable' else '1'
        def close(self):
            state['closed'] = True
    def read_prefix(*_):
        state['calls'] += 1
        return replace(prefix, last_sequence=prefix.last_sequence+1) if fault == 'moved' and state['calls'] == 2 else prefix
    monkeypatch.setattr(module, 'load_verified_v4_prefix', read_prefix)
    monkeypatch.setattr(module, 'load_typed_backtest_financial_evidence', lambda *_a, **_k: dict(fills=(), commissions=()))
    monkeypatch.setattr(module, 'load_latest_committed_oms_groups', lambda *_a, **_k: ())
    async def run():
        return await module.read_completed_program_execution_evidence(lambda: Client(),
            replace(prefix, status='running') if fault == 'running' else prefix, actor=packet.actor)
    if fault:
        with pytest.raises(ValueError):
            asyncio.run(run())
    else:
        executions, attribution = asyncio.run(run())
        assert executions == () and not attribution._groups
        assert state['calls'] == 2
    assert state['closed'] is (fault != 'running')
