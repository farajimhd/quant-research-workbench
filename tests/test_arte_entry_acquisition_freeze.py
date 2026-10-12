from datetime import date, datetime, timezone
from dataclasses import replace
from uuid import uuid4

import pytest

from src.trading_runtime.arte_entry_acquisition_freeze import (
    FREEZE, GROUP, entry_acquisition_freeze_batch, seal_entry_freeze_rows,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4, load_verified_v4_prefix
from src.trading_runtime import rule_decision_writer_preflight as audit
from tests.test_arte_journal_commit_v4 import attached_v4_client


def unit():
    at = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)
    record = JournalRecord(str(uuid4()), 'backtest:freeze', 1, at, at,
        'order_management', 'entry_acquisition_frozen_before_exit', 'exit-1', 'DU1',
        dict(ticker='AAA', exit_action='exit', source_group_ids=['group-1', 'group-2'],
            command_order='cancel_entries_then_submit_exit', strategy_id='program', strategy_revision=62,
            correlation_id='correlation', causation_id='causation'))
    scope = dict(run_month=date(2026, 8, 1), attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id='00000000-0000-0000-0000-000000000000', source_cursor='freeze')
    return record, scope, entry_acquisition_freeze_batch(record, **scope)


def test_freeze_persists_named_parent_and_ordered_groups_and_verifies_commit(monkeypatch):
    record, scope, batch = unit()
    assert [row['source_group_id'] for row in batch.groups] == ['group-1', 'group-2']
    assert batch.base.events[0]['correlation_id'] == 'correlation'
    assert 'live_market_ssd' in FREEZE.ddl() and 'live_market_ssd' in GROUP.ddl()
    client = attached_v4_client()
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    assert _publish_typed_batch_v4(client, batch.base, entry_freeze=batch) == batch.base.batch_id
    assert load_verified_v4_prefix(client, record.run_id).last_sequence == 1
    assert _publish_typed_batch_v4(client, batch.base, entry_freeze=batch) == batch.base.batch_id
    client.tables[GROUP.name][0]['source_group_id'] = 'corrupt'
    with pytest.raises(RuntimeError, match='row hash'):
        load_verified_v4_prefix(client, record.run_id)


def test_freeze_rejects_unknown_fields_missing_duplicate_and_foreign_groups():
    record, scope, batch = unit()
    for payload in ({**record.payload, 'unknown': 'data'},
                    {**record.payload, 'source_group_ids': []},
                    {**record.payload, 'source_group_ids': ['same', 'same']}):
        with pytest.raises(ValueError):
            entry_acquisition_freeze_batch(replace(record, payload=payload), **scope)
    with pytest.raises(ValueError):
        seal_entry_freeze_rows((batch.freeze,), batch.groups[:1], batch.base.events,
            run_id=batch.base.run_id, batch_id=batch.base.batch_id)
