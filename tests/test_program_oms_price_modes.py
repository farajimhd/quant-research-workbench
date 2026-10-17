"""OMS representation selection stays independent of protection-history mode."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.backend import program_oms_recovery as recovery
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from tests.test_columnar_program_coordinator import inputs


def arguments(client, prefix, release):
    return dict(reader_factory=lambda: client, prefix=prefix, release=release,
        account_ids=('DU1',), reselect=lambda *args: None, execution_policy=None,
        protection_for=lambda proposal: None, outside_rth=False)


@pytest.mark.parametrize('prices,oms_override,expected_oms', [
    (False, None, False), (True, None, True),
    (False, True, True), (True, False, False),
    (False, False, False), (True, True, True),
])
def test_independent_modes_preserve_inherited_defaults(monkeypatch, prices, oms_override, expected_oms):
    release = inputs(entry=True)[0]
    prefix = V4CommittedPrefix('run', 2, 'batch', 'cursor', 'running', ('batch',))
    closed, calls = [], []
    client = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(recovery, 'load_verified_v4_prefix', lambda *args: prefix)
    def groups(actual, actual_prefix, **scope):
        assert actual is client and actual_prefix is prefix
        calls.append(('oms', scope.get('exact_prices', False)))
        return ()
    def history(actual, actual_prefix, **scope):
        assert actual is client and actual_prefix is prefix
        calls.append(('protection', scope.get('exact_prices', False)))
        return CompleteProtectionHistory(prefix.run_id, prefix.last_sequence, prefix.batch_ids, ())
    monkeypatch.setattr(recovery, 'load_latest_committed_oms_groups', groups)
    monkeypatch.setattr(recovery, 'load_complete_typed_protection_history', history)
    assert asyncio.run(recovery.recover_program_oms_lineages_async(
        **arguments(client, prefix, release), exact_prices=prices, exact_oms_prices=oms_override)) == ()
    assert calls == [('oms', expected_oms), ('protection', prices)]
    assert closed == [True]


@pytest.mark.parametrize('mode', [0, 1, '', 'true', (), object()])
def test_invalid_oms_override_rejected_before_opening_reader(mode):
    opened = []
    scope = arguments(None, V4CommittedPrefix('run', 2, 'batch', 'cursor', 'running', ('batch',)),
        inputs(entry=True)[0])
    scope['reader_factory'] = lambda: opened.append(True)
    with pytest.raises(ValueError, match='bounded declared authority'):
        asyncio.run(recovery.recover_program_oms_lineages_async(**scope, exact_oms_prices=mode))
    assert opened == []


def test_exact_oms_storage_reaches_reselection_with_auto_protection_and_keeps_legacy_rejection(monkeypatch):
    from src.trading_runtime.arte_oms_projection import oms_group_state_batch
    from src.trading_runtime.arte_oms_exact_prices import publish_exact_oms_state_batch_v4
    from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4, load_verified_v4_prefix
    from src.trading_runtime.order_management import ExecutionQuote, ExecutionTactic, ExecutionUrgency, PriceStep
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from tests.test_arte_oms_exact_prices import prepared
    from tests.test_arte_journal_commit_v4 import attached_v4_client

    release = inputs(entry=True)[0]
    group, scope, price = prepared()
    scope.update(strategy_id=release.header['strategy_id'], strategy_revision=release.header['strategy_number'])
    group.tactic = ExecutionTactic(ExecutionUrgency.PATIENT, 'BUY',
        (PriceStep(0, 12.5),), ExecutionQuote(12.5, 12.6, group.updated_at, .01), 200)
    exact = oms_group_state_batch(group, **scope, exact_prices=True)
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    publish_base_typed_batch_v4(client, scope['published_intent_batch'])
    publish_exact_oms_state_batch_v4(client, exact)
    from src.trading_runtime.arte_journal_projection import order_command_batch
    from tests.test_arte_order_exact_prices import request_and_scope
    request, command_scope = request_and_scope()
    command_scope.update(run_id=exact.base.run_id, attempt_id=exact.base.attempt_id,
        batch_id=str(uuid4()), prior_batch_id=exact.base.batch_id, sequence=3,
        strategy_id=release.header['strategy_id'], strategy_revision=release.header['strategy_number'])
    publish_base_typed_batch_v4(client,
        order_command_batch(replace(request, auxPrice=10.), **command_scope))
    prefix = load_verified_v4_prefix(client, exact.base.run_id)
    closed, protection_modes = [], []
    client.close = lambda: closed.append(True)
    real_protection = recovery.load_complete_typed_protection_history
    history = real_protection(client, prefix)
    assert client.tables['trading_order_command_v1']
    with pytest.raises(RuntimeError, match='committed mode'):
        real_protection(client, prefix, exact_prices=True)
    def protection(actual, actual_prefix, **options):
        protection_modes.append(options.get('exact_prices', False))
        return real_protection(actual, actual_prefix, **options)
    monkeypatch.setattr(recovery, 'load_complete_typed_protection_history', protection)
    source = SimpleNamespace(record_id=scope['published_intent_batch'].intents[0]['record_id'])
    monkeypatch.setattr(recovery, 'load_committed_strategy_intent_page', lambda *args, **kwargs: (source,))
    reservation = {'assignment_id': 'assignment'}
    monkeypatch.setattr(recovery, 'load_committed_oms_admission_page', lambda *args, **kwargs: {2: reservation})
    monkeypatch.setattr(recovery, 'load_committed_oms_decision_page', lambda *args, **kwargs: {2: {}})
    class ReselectionReached(Exception):
        pass
    def reselect(state, actual_source, actual_reservation, actual_history):
        assert state.orders == tuple(group.orders)
        assert state.group['current_limit_price'] == price
        assert (actual_source, actual_reservation, actual_history) == (source, reservation, history)
        raise ReselectionReached
    options = {**arguments(client, prefix, release), 'reselect': reselect}
    # Actual exact writer rows and committed child hashes are read by the real
    # OMS loader. Reaching reselection grants no executable lineage by itself.
    with pytest.raises(ReselectionReached):
        asyncio.run(recovery.recover_program_oms_lineages_async(**options, exact_oms_prices=True))
    assert protection_modes == [False] and closed == [True]
    assert not client.tables.get('trading_oms_group_state_v1')
    with pytest.raises(RuntimeError, match='missing or duplicate group states'):
        asyncio.run(recovery.recover_program_oms_lineages_async(**options))
    assert protection_modes == [False] and closed == [True, True]
    # Strict exact OMS recovery keeps the exact-price row hash check.
    client.tables['trading_oms_order_state_exact_price_v1'][0]['limit_price_bits'] += 1
    with pytest.raises(RuntimeError, match='row hash|exact price contract'):
        asyncio.run(recovery.recover_program_oms_lineages_async(**options, exact_oms_prices=True))
    assert closed == [True, True, True]


def test_exact_oms_override_rejects_legacy_writer_rows_without_fallback(monkeypatch):
    from src.trading_runtime.arte_oms_projection import oms_group_state_batch
    from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4, load_verified_v4_prefix
    from src.trading_runtime.order_management import ExecutionQuote, ExecutionTactic, ExecutionUrgency, PriceStep
    from src.trading_runtime.strategy_orders import StrategyOrderPlan
    from tests.test_arte_oms_exact_prices import prepared
    from tests.test_arte_journal_commit_v4 import attached_v4_client

    release = inputs(entry=True)[0]
    group, scope, _ = prepared()
    scope.update(strategy_id=release.header['strategy_id'], strategy_revision=release.header['strategy_number'])
    group.orders = [replace(group.orders[0], price=10.)]
    group.plan = StrategyOrderPlan(tuple(group.orders), batches=(tuple(group.orders),))
    group.current_limit_price = group.high_water_price = group.low_water_price = 10.
    group.deferred_reprice = (10., 10.)
    group.tactic = ExecutionTactic(ExecutionUrgency.PATIENT, 'BUY',
        (PriceStep(0, 12.5),), ExecutionQuote(12.5, 12.6, group.updated_at, .01), 200)
    client = attached_v4_client()
    publish_base_typed_batch_v4(client, scope['published_intent_batch'])
    publish_base_typed_batch_v4(client, oms_group_state_batch(group, **scope))
    prefix = load_verified_v4_prefix(client, scope['run_id'])
    closed = []
    client.close = lambda: closed.append(True)
    def no_protection(*args, **kwargs):
        pytest.fail('A strict OMS rejection must not retry or proceed to protection')
    monkeypatch.setattr(recovery, 'load_complete_typed_protection_history', no_protection)
    assert client.tables['trading_oms_group_state_v1']
    for mode in ({'exact_oms_prices': True}, {'exact_prices': True}):
        with pytest.raises(RuntimeError, match='missing or duplicate group states'):
            asyncio.run(recovery.recover_program_oms_lineages_async(**arguments(client, prefix, release), **mode))
    assert closed == [True, True]
