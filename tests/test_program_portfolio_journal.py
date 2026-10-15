from dataclasses import replace
from datetime import datetime, UTC

import pytest

from src.trading_runtime.program_portfolio_journal import project_program_portfolio_records
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_portfolio_control_v4 import CONTROL
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import RUN, ATTEMPT


def units():
    records = (
        ('portfolio_control', 'primary', 'DU1',
         {'event': 'control_changed', 'control_mode': 'entries_paused', 'reason': 'operator review'}),
        ('portfolio_control', 'primary:generic-program', 'DU1',
         {'event': 'strategy_allocation_control_changed', 'strategy_id': 'generic-program',
          'enabled': False, 'reason': 'allocation pause'}),
    )
    return project_program_portfolio_records(records, run_id=RUN, account_key='primary',
        at=datetime(2026, 8, 18, 12, tzinfo=UTC), attempt_id=ATTEMPT,
        prior_batch_id='00000000-0000-0000-0000-000000000000', first_sequence=1,
        transition_hash='a' * 64, expected_mode='live')


def test_control_source_facts_commit_in_v4_compound_and_cold_read_preserves_reasons():
    client = attached_v4_client()
    original = units()
    compound = coalesce_v4_units(original)
    result = publish_compound_v4(client, compound)
    prefix = load_verified_v4_prefix(client, RUN)
    assert result == compound.base.batch_id and prefix.last_sequence == 2
    assert [row['reason'] for row in client.tables[CONTROL.name]] == ['operator review', 'allocation pause']
    assert original[0].control['batch_id'] != client.tables[CONTROL.name][0]['batch_id']
    client.tables[CONTROL.name][0]['reason'] = 'tampered'
    with pytest.raises(RuntimeError):
        load_verified_v4_prefix(client, RUN)


def test_source_recipe_rejects_unknown_fields_and_families():
    with pytest.raises(ValueError, match='no exact normalized source family'):
        project_program_portfolio_records((('invented', 'entity', 'DU1', {}),),
            run_id=RUN, account_key='primary', at=datetime(2026, 8, 18, 12, tzinfo=UTC),
            attempt_id=ATTEMPT, prior_batch_id='00000000-0000-0000-0000-000000000000',
            first_sequence=1, transition_hash='a' * 64, expected_mode='paper')


def test_native_v4_writer_queues_control_and_cold_verifies_its_actual_receipt(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    client = attached_v4_client()
    monkeypatch.setattr(writer_module, 'storage_preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(writer_module, 'journal_permission_preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(writer_module, '_verify_run_identity',
        lambda *args: {'mode': 'backtest', 'account_ids': ('DU1',)})
    writer = ArteJournalWriter(client, run_id=RUN, journal_profile='backtest_v4', coalesce_batches=False)
    try:
        for unit in units():
            assert writer.submit_portfolio_control_v4(unit).result(timeout=5) == unit.base.batch_id
        prefix = load_verified_v4_prefix(client, RUN)
        assert prefix.last_sequence == 2
        assert len(client.tables[CONTROL.name]) == 2
    finally:
        writer.close()
