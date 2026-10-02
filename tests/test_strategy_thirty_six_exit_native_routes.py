"""Actual native buffering/projection/preparation without connected publication.

Cold source and financial verification remain required at publication. The
prepared successor remains unregistered while these routes are assembled.
"""
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4
from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from test_liquidity_fade_runtime_emission import values
from test_liquidity_fade_native_publication import client, native_unit
from test_strategy_thirty_six_exit_evidence import successor


def buffer_case():
    data = values()
    data['strategy_revision'] = 36
    data['intent'] = liquidity_fade_exit_intent(data['witness'], data['financial'],
        session_date=data['session_date'], source_entry_intent_id=data['source_entry_intent_id'],
        strategy_number=36)
    journal = BacktestMemoryJournal(run_id='run', initial_sequence=64)
    record = journal.append_liquidity_fade_exit(**data)
    entry = replace(data['intent'], intent_id=data['source_entry_intent_id'], action='enter_long',
        reason='strategy_one_entry', reference_price=data['witness'].reference_ask,
        invalidation_price=data['witness'].initial_stop,
        event_time=data['intent'].event_time-timedelta(seconds=30))
    batch = strategy_intent_batch(entry, run_id='run', run_month=date(2026, 8, 1),
        account_id=data['financial'].account_id, attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id=str(uuid4()), sequence=12, source_cursor='entry-fixture',
        run_status='running', recorded_at=entry.event_time)
    context = dict(attempt_id=batch.attempt_id, run_month=date(2026, 8, 1),
        prior_sequence=64, prior_batch_id=str(uuid4()), through_sequence=65,
        expected_config={'strategy_id':'early-squeeze-strategy', 'strategy_revision':36},
        published_sources={entry.intent_id:(batch, entry)})
    return journal, record, context, data


def test_successor_buffer_projects_complete_numbered_exit_and_releases_sidecar():
    journal, record, context, data = buffer_case()
    try:
        assert journal.append_liquidity_fade_exit(**data) is record
        unit, = project_pending_backtest_v4_prefix(journal, **context)
        assert type(unit) is V4LiquidityFadeFailureBatch
        assert unit.failure['strategy_number'] == 36
        assert unit.base.intents[0]['reason'] == 'strategy_thirty_six_liquidity_fade_failure'
        journal.mark_fenced(record.sequence)
        assert journal.liquidity_fade_exit_for_record(record.record_id) is None
        assert journal.pending_record_count == 0
    finally:
        journal.close()


@pytest.mark.parametrize('change', ['missing_witness', 'missing_original_entry', 'wrong_number'])
def test_successor_projector_rejects_missing_or_cross_numbered_authority(change):
    journal, record, context, _ = buffer_case()
    try:
        if change == 'missing_witness':
            journal._liquidity_fade_exits.pop(record.record_id)
        elif change == 'missing_original_entry':
            context['published_sources'] = {}
        else:
            record.payload['strategy_revision'] = 34
        with pytest.raises((ValueError, RuntimeError)):
            project_pending_backtest_v4_prefix(journal, **context)
    finally:
        journal.close()


def successor_native_unit():
    old = native_unit()
    row, parent, event = dict(old.failure), dict(old.base.intents[0]), dict(old.base.events[0])
    event['entity_id'] = successor(row, parent).intent_id
    return V4LiquidityFadeFailureBatch(replace(old.base, intents=(parent,), events=(event,)), row)


def test_successor_native_micro_preparation_preserves_complete_family_without_sql():
    unit = successor_native_unit()
    _, families = _publish_typed_batch_v4(client(), unit.base,
        liquidity_fade_rows=(unit.failure,), _prepare_only=True)
    row, = dict(families)[LIQUIDITY_FADE_FAILURE.name]
    assert row['strategy_number'] == 36
    assert row['source_manager_snapshot_hash'] == unit.failure['source_manager_snapshot_hash']


def test_successor_native_publication_requires_certified_source_before_any_sql():
    unit = successor_native_unit()
    with pytest.raises(ValueError, match='certified run market authority'):
        _publish_typed_batch_v4(client(), unit.base, liquidity_fade_rows=(unit.failure,))
