from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import oms_group_state_batch
from src.trading_runtime.arte_oms_exact_prices import (
    ExactPriceOmsStateBatch, seal_exact_oms_state_batch, decode_exact_oms_order_rows,
)
from src.trading_runtime.arte_journal_writer import _wire_row
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.trading_price_rows import restore_exact_price_row
from tests.test_arte_intent_projection import intent


def prepared():
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    run, attempt, first_id, second_id = 'backtest:exact-oms', str(uuid4()), str(uuid4()), str(uuid4())
    source = intent(ticker='AAA')
    first = strategy_intent_batch(source, run_id=run, run_month=date(2026, 8, 1),
        account_id='DU1', attempt_id=attempt, batch_id=first_id,
        prior_batch_id='00000000-0000-0000-0000-000000000000', sequence=1,
        source_cursor='intent', run_status='running', recorded_at=at)
    price = 9.989999771118164
    order = OrderRequest(acctId='DU1', conid=123, cOID='entry', ticker='AAA',
        orderType='LMT', side='BUY', quantity=5, price=price)
    group = _ManagedOrderGroup(group_id='group-1', intent=source, account_id='DU1',
        plan=StrategyOrderPlan((order,), batches=((order,),)),
        state=OrderManagementState.CREATED, created_at=at, updated_at=at,
        orders=[order], current_limit_price=price, high_water_price=price,
        low_water_price=price, deferred_reprice=(price, price))
    kwargs = dict(run_id=run, run_month=date(2026, 8, 1), attempt_id=attempt,
        batch_id=second_id, prior_batch_id=first_id, sequence=2, source_cursor='oms',
        run_status='running', strategy_id='program', strategy_revision=1,
        recorded_at=at, published_intent_batch=first, committed_intent_batch_id=first_id)
    return group, kwargs, price


def test_native_oms_projection_preserves_exact_prices_and_keeps_legacy_guard():
    group, kwargs, price = prepared()
    with pytest.raises(ValueError, match='losslessly'):
        oms_group_state_batch(group, **kwargs)
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    assert type(exact) is ExactPriceOmsStateBatch
    assert not exact.base.oms_group_states and not exact.base.oms_order_states
    state = restore_exact_price_row('trading_oms_group_state_v1', exact.group)
    for field in ('current_limit_price', 'high_water_price', 'low_water_price',
                  'deferred_reprice_from', 'deferred_reprice_to'):
        assert state[field] == price
    order = restore_exact_price_row('trading_oms_order_state_v1', exact.orders[0])
    assert order['limit_price'] == price and order['quantity'] == '5.0000000000'
    wire = _wire_row('trading_oms_order_state_exact_price_v1', exact.orders[0])
    assert restore_exact_price_row('trading_oms_order_state_v1', wire, stored_utc=True)['limit_price'] == price
    sealed = dict(seal_exact_oms_state_batch(exact))
    assert 'trading_oms_group_state_v1' not in sealed
    assert 'trading_oms_order_state_v1' not in sealed
    assert sealed['trading_oms_group_state_exact_price_v1'][0]['content_hash'] == exact.group['content_hash']
    assert sealed['trading_oms_order_state_exact_price_v1'][0]['content_hash'] == exact.orders[0]['content_hash']
    group_wire = _wire_row('trading_oms_group_state_exact_price_v1', exact.group)
    restored_orders = decode_exact_oms_order_rows(group_wire, (wire,), stored_utc=True)
    assert restored_orders == tuple(group.orders)
    with pytest.raises(ValueError, match='count'):
        decode_exact_oms_order_rows(group_wire, (), stored_utc=True)


def test_exact_oms_keeps_intent_lineage_and_rejects_tampering():
    group, kwargs, _ = prepared()
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    with pytest.raises(ValueError):
        replace(exact, orders=())
    with pytest.raises(ValueError, match='hash mismatch'):
        replace(exact, orders=({**exact.orders[0], 'limit_price_bits': 1},))
    with pytest.raises(ValueError, match='pinned typed intent'):
        oms_group_state_batch(group, **{**kwargs, 'published_intent_batch': replace(
            kwargs['published_intent_batch'], run_id='other')}, exact_prices=True)
    with pytest.raises(ValueError, match='explicit boolean'):
        oms_group_state_batch(group, **kwargs, exact_prices=1)


def test_exact_seal_keeps_component_counts_and_parent_scope_checks():
    group, kwargs, _ = prepared()
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    base = exact.base
    broken = replace(exact, base=replace(base, oms_warnings=({
        'run_id': base.run_id, 'batch_id': base.batch_id, 'event_month': '2026-08-01',
        'account_id': 'DU1', 'record_id': str(uuid4()),
        'parent_record_id': exact.group['record_id'], 'ordinal': 0, 'message_id': 'unexpected',
    },)))
    with pytest.raises(ValueError, match='counts or identity'):
        seal_exact_oms_state_batch(broken)
    broken = replace(exact, base=replace(base, events=({**base.events[0], 'event_month': '2026-09-01'},)))
    with pytest.raises(ValueError, match='partition'):
        seal_exact_oms_state_batch(broken)


def test_exact_oms_uses_v4_commit_readback_and_exact_intent_fence(monkeypatch):
    from src.trading_runtime.arte_oms_exact_prices import publish_exact_oms_state_batch_v4
    from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4, load_verified_commit_v4
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    group, kwargs, _ = prepared()
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    client = attached_v4_client()
    calls = []
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: calls.append(actual))
    with pytest.raises(RuntimeError, match='predecessor|prior|earlier|committed'):
        publish_exact_oms_state_batch_v4(client, exact)
    assert not client.inserts
    publish_base_typed_batch_v4(client, kwargs['published_intent_batch'])
    assert publish_exact_oms_state_batch_v4(client, exact) == exact.base.batch_id
    commit, families = load_verified_commit_v4(client, run_id=exact.base.run_id, batch_id=exact.base.batch_id)
    assert commit['last_sequence'] == 2
    assert {row['family_name'] for row in families} >= {
        'trading_oms_group_state_exact_price_v1', 'trading_oms_order_state_exact_price_v1'}
    assert 'trading_oms_group_state_v1' not in client.tables
    assert 'trading_oms_order_state_v1' not in client.tables
    assert publish_exact_oms_state_batch_v4(client, exact) == exact.base.batch_id
    assert calls and all(actual is client for actual in calls)
    client.tables['trading_oms_order_state_exact_price_v1'][0]['limit_price_bits'] += 1
    with pytest.raises(RuntimeError, match='row hash'):
        load_verified_commit_v4(client, run_id=exact.base.run_id, batch_id=exact.base.batch_id)


def test_exact_oms_publication_requires_storage_audit_before_any_write(monkeypatch):
    from src.trading_runtime.arte_oms_exact_prices import publish_exact_oms_state_batch_v4
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    group, kwargs, _ = prepared()
    exact = oms_group_state_batch(group, **kwargs, exact_prices=True)
    client = attached_v4_client()
    def reject(actual):
        assert actual is client
        raise RuntimeError('missing exact-price storage approval')
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', reject)
    with pytest.raises(RuntimeError, match='storage approval'):
        publish_exact_oms_state_batch_v4(client, exact)
    assert not client.inserts and not client.typed_insert_dispatch.reserved
