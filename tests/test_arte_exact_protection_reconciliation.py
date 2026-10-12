from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_protection_reconciliation_v4 import protection_reconciliation_batch_v4
from src.trading_runtime.arte_journal_commit_v4 import publish_protection_reconciliation_batch_v4, load_verified_v4_prefix
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.trading_price_rows import restore_exact_price_row
from src.trading_runtime.arte_journal_writer import _wire_row
from src.trading_runtime import rule_decision_writer_preflight as audit
from tests.test_arte_journal_commit_v4 import attached_v4_client


def record_and_scope():
    at = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)
    price = 9.989999771118164
    record = JournalRecord(str(uuid4()), 'backtest:repair', 1, at, at,
        'order_management', 'protection_reconciliation', 'group-1', 'DU1', {
            'order_group_id': 'group-1', 'required_quantity': 1., 'protected_quantity': 1.,
            'status': 'repaired', 'ticker': 'AAA', 'action': 'exit', 'intent_id': 'exit-1',
            'correlation_id': 'correlation', 'causation_id': 'causation',
            'strategy_id': 'program', 'strategy_revision': 62,
            'actions': [{'action': 'replace_stop', 'quantity': 1., 'stop_price': price,
                'target_price': price + 1., 'from_stop': price, 'to_stop': price + .25,
                'response': [{'order_id': 'broker-1', 'order_status': 'Submitted', 'local_order_id': 'stop-1'}]}],
        })
    scope = dict(run_month=date(2026, 8, 1), attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id='00000000-0000-0000-0000-000000000000', source_cursor='repair')
    return record, scope


def test_exact_reconciliation_preserves_all_four_prices_and_shared_graph(monkeypatch):
    record, scope = record_and_scope()
    with pytest.raises(ValueError, match='decimal precision'):
        protection_reconciliation_batch_v4(record, **scope)
    unit = protection_reconciliation_batch_v4(record, **scope, exact_prices=True)
    name = 'trading_protection_reconciliation_action_exact_price_v4'
    restored = restore_exact_price_row('trading_protection_reconciliation_action_v4',
        _wire_row(name, unit.actions[0]), stored_utc=True)
    for field in ('stop_price', 'target_price', 'from_stop', 'to_stop'):
        assert restored[field] == record.payload['actions'][0][field]
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    kwargs = dict(reconciliation=unit.reconciliation, actions=unit.actions,
        replies=unit.replies, exact_prices=True)
    assert publish_protection_reconciliation_batch_v4(client, unit.base, **kwargs) == unit.base.batch_id
    assert load_verified_v4_prefix(client, record.run_id).last_sequence == 1
    assert publish_protection_reconciliation_batch_v4(client, unit.base, **kwargs) == unit.base.batch_id
    client.tables[name][0]['to_stop_bits'] += 1
    with pytest.raises(RuntimeError, match='row hash'):
        load_verified_v4_prefix(client, record.run_id)
