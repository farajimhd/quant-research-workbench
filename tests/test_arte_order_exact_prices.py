from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.arte_order_exact_prices import ExactPriceOrderCommandBatch, seal_exact_order_command_batch
from src.trading_runtime.arte_journal_writer import _wire_row
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.trading_price_rows import restore_exact_price_row


def request_and_scope():
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    price = 9.989999771118164
    request = OrderRequest(acctId='DU1', conid=123, cOID='stop-1', ticker='AAA',
        orderType='STP', side='SELL', quantity=5, auxPrice=price)
    scope = dict(run_id='backtest:exact-order', run_month=date(2026, 8, 1),
        attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id='00000000-0000-0000-0000-000000000000', sequence=1,
        source_cursor='command', run_status='running', command_id='command-1',
        created_at=at, recorded_at=at, strategy_id='program', strategy_revision=1)
    return request, scope


def test_exact_order_projection_keeps_prices_and_nonprice_instructions():
    request, scope = request_and_scope()
    with pytest.raises(ValueError, match='losslessly for order.aux_price'):
        order_command_batch(request, **scope)
    unit = order_command_batch(request, **scope, exact_prices=True)
    assert type(unit) is ExactPriceOrderCommandBatch
    assert not unit.base.order_commands
    row = _wire_row('trading_order_command_exact_price_v1', unit.command)
    restored = restore_exact_price_row('trading_order_command_v1', row, stored_utc=True)
    assert restored['aux_price'] == request.auxPrice
    assert restored['limit_price'] is None
    assert restored['quantity'] == '5.0000000000'
    assert restored['order_type'] == 'STP' and restored['side'] == 'SELL'
    assert restored['client_order_id'] == request.cOID
    families = dict(seal_exact_order_command_batch(unit))
    assert 'trading_order_command_v1' not in families
    assert families['trading_order_command_exact_price_v1'][0]['content_hash'] == unit.command['content_hash']


def test_exact_order_preserves_unknown_field_and_content_guards():
    request, scope = request_and_scope()
    for unsupported in (
        replace(request, raw={'unknown': 'evidence'}),
        replace(request, strategyParameters=({'tag': 'unknown'},)),
    ):
        with pytest.raises(ValueError, match='unmodeled'):
            order_command_batch(unsupported, **scope, exact_prices=True)
    unit = order_command_batch(request, **scope, exact_prices=True)
    with pytest.raises(ValueError, match='hash mismatch'):
        replace(unit, command={**unit.command, 'aux_price_bits': 1})
    with pytest.raises(ValueError, match='explicit boolean'):
        order_command_batch(request, **scope, exact_prices=1)


def test_exact_command_v4_publication_readback_retry_and_corruption(monkeypatch):
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from src.trading_runtime.arte_order_exact_prices import publish_exact_order_command_batch_v4
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix, load_verified_commit_v4
    from src.trading_runtime.arte_journal_writer import load_committed_order_command_page
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    request, scope = request_and_scope()
    unit = order_command_batch(request, **scope, exact_prices=True)
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    assert publish_exact_order_command_batch_v4(client, unit) == unit.base.batch_id
    prefix = load_verified_v4_prefix(client, unit.base.run_id)
    row, = load_committed_order_command_page(client, prefix, exact_prices=True)
    assert row['aux_price'] == request.auxPrice and row['limit_price'] is None
    assert row['quantity'] == '5.0000000000' and row['sequence'] == 1
    assert 'trading_order_command_v1' not in client.tables
    assert publish_exact_order_command_batch_v4(client, unit) == unit.base.batch_id
    with pytest.raises(RuntimeError, match='missing or duplicate'):
        load_committed_order_command_page(client, prefix)
    client.tables['trading_order_command_exact_price_v1'][0]['aux_price_bits'] += 1
    with pytest.raises(RuntimeError, match='row hash'):
        load_verified_commit_v4(client, run_id=unit.base.run_id, batch_id=unit.base.batch_id)
    with pytest.raises(RuntimeError, match='row hash'):
        load_committed_order_command_page(client, prefix, exact_prices=True)


def test_exact_command_batches_with_its_source_intent_and_keeps_context(monkeypatch):
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_writer import load_committed_order_command_page, load_committed_order_context_page, typed_row
    from tests.test_arte_oms_exact_prices import prepared
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    _, original_scope, _ = prepared()
    source = original_scope['published_intent_batch']
    request, scope = request_and_scope()
    scope.update(run_id=source.run_id, attempt_id=source.attempt_id,
        prior_batch_id=source.batch_id, sequence=2)
    source_row = source.intents[0]
    source_hash = typed_row('trading_strategy_intent_v1', source_row)['content_hash']
    unit = order_command_batch(request, **scope, exact_prices=True,
        strategy_intent_id=source_row['intent_id'], order_group_id='group-1', policy_version='policy-1',
        strategy_intent_record_id=source_row['record_id'], strategy_intent_content_hash=source_hash)
    compound = coalesce_v4_units((source, unit), max_events=2)
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    assert publish_compound_v4(client, compound) == unit.base.batch_id
    prefix = load_verified_v4_prefix(client, unit.base.run_id)
    assert prefix.batch_ids == (unit.base.batch_id,)
    commands = load_committed_order_command_page(client, prefix, exact_prices=True)
    contexts = load_committed_order_context_page(client, prefix, commands)
    assert commands[0]['aux_price'] == request.auxPrice
    assert contexts[commands[0]['record_id']]['strategy_intent_id'] == source_row['intent_id']
    assert publish_compound_v4(client, compound) == unit.base.batch_id
