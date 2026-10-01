"""Prepared Strategy 31 journal route; database checkpoint attestation mocked."""
import asyncio
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from src.trading_runtime.strategy_profit_giveback_arm import ProfitArmCandidate
from src.trading_runtime.strategy_profit_giveback_arm_reference import ProfitArmReference
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import intent, financial, ENTRY
from test_profit_giveback_publication import context


CONFIG = {'strategy_id': 'early-squeeze-strategy', 'strategy_revision': 31, 'mode': 'backtest'}
RUN = str(UUID(int=1))
ATTEMPT = str(UUID(int=2))
PRIOR = str(UUID(int=4))


def values(strategy_number=31):
    witness = profit_giveback(sample())
    held = financial()
    arm = ProfitArmReference(ProfitArmCandidate(held.account_id, held.assignment_id,
        held.ticker, 9900, 1000, 10., 9., 110000), str(UUID(int=5)), 7, PRIOR, 'f' * 64)
    return dict(intent=intent(strategy_number=strategy_number), witness=witness, source_entry_intent_id=ENTRY,
        arm_reference=arm, account_id=held.account_id, assignment_id=held.assignment_id,
        strategy_id=CONFIG['strategy_id'], strategy_revision=strategy_number)


def original_source(strategy_number=31):
    # A synthetic retained original source models the publisher's committed
    # cache. The transport tests do not claim native entry/checkpoint proof.
    x = replace(intent(strategy_number=strategy_number), intent_id=ENTRY, action='enter_long', reason='strategy_one_entry',
        event_time=market_day_boundary(date(2026, 8, 4), 900),
        reference_price=10., invalidation_price=9.)
    batch = strategy_intent_batch(x, run_id=RUN, run_month=date(2026, 8, 1),
        account_id=financial().account_id, attempt_id=ATTEMPT, batch_id=str(UUID(int=8)),
        prior_batch_id=str(UUID(int=9)), sequence=6, source_cursor='cursor',
        run_status='running', recorded_at=x.event_time)
    return batch, x


def prepared(strategy_number=31):
    journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=9)
    record = journal.append_profit_giveback_exit(**values(strategy_number=strategy_number))
    return journal, record


def project(journal, sources, strategy_number=31):
    return project_pending_backtest_v4_prefix(journal, attempt_id=ATTEMPT,
        run_month=date(2026, 8, 1), prior_sequence=9, prior_batch_id=PRIOR,
        expected_config={**CONFIG, 'strategy_revision': strategy_number}, published_sources=sources, through_sequence=10)


def test_journal_retry_retains_frozen_source_and_releases_pending_sidecar():
    journal, record = prepared()
    assert journal.append_profit_giveback_exit(**values()) is record
    assert journal.latest_sequence(RUN) == 10
    assert journal.profit_giveback_exit_for_record(record.record_id) == (
        values()['intent'], values()['witness'], ENTRY, values()['arm_reference'])
    changed = values()
    changed['arm_reference'] = replace(changed['arm_reference'], snapshot_hash='e' * 64)
    with pytest.raises(ValueError, match='immutable witness'):
        journal.append_profit_giveback_exit(**changed)
    assert journal.latest_sequence(RUN) == 10
    journal.mark_fenced(10)
    assert journal.profit_giveback_exit_for_record(record.record_id) is None
    assert journal.append_profit_giveback_exit(**values()) is record
    assert journal.pending_record_count == 0
    journal.close()
    with pytest.raises(RuntimeError, match='closed'):
        journal.append_profit_giveback_exit(**values())


@pytest.mark.parametrize('change', ['unfenced', 'candidate', 'factory', 'revision', 'assignment'])
def test_journal_rejects_unconfirmed_or_changed_authority(change):
    kwargs = values()
    journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=6 if change == 'unfenced' else 9)
    if change == 'candidate':
        kwargs['arm_reference'] = replace(kwargs['arm_reference'],
            candidate=replace(kwargs['arm_reference'].candidate, high_int=110001))
    if change == 'factory':
        kwargs['intent'] = replace(kwargs['intent'], reference_price=10.49)
    if change == 'revision':
        kwargs['strategy_revision'] = 30
    if change == 'assignment':
        kwargs['assignment_id'] = 'other'
    before = journal.latest_sequence(RUN)
    with pytest.raises(ValueError):
        journal.append_profit_giveback_exit(**kwargs)
    assert journal.latest_sequence(RUN) == before
    assert journal.pending_record_count == 0


@pytest.mark.parametrize('change', [None, 'missing', 'ask', 'stop', 'ticker', 'future'])
@pytest.mark.parametrize('number', [31, 32])
def test_projection_requires_exact_precheckpoint_original_source(change, number):
    journal, record = prepared(strategy_number=number)
    batch, entry = original_source(strategy_number=number)
    if change == 'ask':
        entry = replace(entry, reference_price=10.01)
    if change == 'stop':
        entry = replace(entry, invalidation_price=9.01)
    if change == 'ticker':
        entry = replace(entry, ticker='OTHER')
    if change == 'future':
        batch = replace(batch, first_sequence=7, last_sequence=7)
    sources = {} if change == 'missing' else {ENTRY: (batch, entry)}
    if change:
        with pytest.raises(RuntimeError, match='original typed entry'):
            project(journal, sources, strategy_number=number)
    else:
        result, = project(journal, sources, strategy_number=number)
        assert type(result) is V4ProfitGivebackBatch
        assert result.base.events[0]['record_id'] == record.record_id
        assert result.profit['source_manager_checkpoint_sequence'] == 7
        assert result.profit['source_manager_snapshot_id'] == str(UUID(int=5))


@pytest.mark.parametrize('number', [31, 32])
def test_missing_sidecar_cannot_be_published_as_an_ordinary_exit(number):
    journal, record = prepared(strategy_number=number)
    journal._profit_giveback_exits.pop(record.record_id)
    with pytest.raises(RuntimeError, match='normalized witness'):
        project(journal, {ENTRY: original_source(strategy_number=number)}, strategy_number=number)


@pytest.mark.parametrize('field,value', [('quantity', 124.), ('reference_price', 10.49), ('time_in_force', 'GTC')])
def test_changed_record_payload_cannot_replace_immutable_exit_source(field, value):
    journal, record = prepared()
    record.payload[field] = value
    with pytest.raises(RuntimeError, match='immutable exit source'):
        project(journal, {ENTRY: original_source()})


@pytest.mark.parametrize('compound', [False, True])
@pytest.mark.parametrize('number', [31, 32])
def test_async_publisher_projects_writes_retains_source_and_fences(monkeypatch, compound, number):
    from src.trading_runtime import arte_journal_writer as writer
    client, _, _, _, _ = context(monkeypatch)
    monkeypatch.setattr(writer, 'storage_preflight', lambda *a, **k: None)
    monkeypatch.setattr(writer, 'journal_permission_preflight', lambda *a, **k: None)
    monkeypatch.setattr(writer, '_verify_run_identity',
                        lambda *a: {'mode': 'backtest', 'account_ids': ('account',)})
    journal, record = prepared(strategy_number=number)
    if compound:
        journal.append(run_id=RUN, category='lifecycle', entity_type='run',
            entity_id=RUN, event_time=record.event_time,
            payload={'status': 'running', 'config': {**CONFIG, 'strategy_revision': number}})
    retained = original_source(strategy_number=number)
    worker = writer.ArteJournalWriter(client, run_id=RUN,
        journal_profile='backtest_v4', coalesce_batches=False)
    try:
        publisher = BacktestTypedJournalPublisher(journal, worker, attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), initial_sequence=9, prior_batch_id=PRIOR,
            expected_config={**CONFIG, 'strategy_revision': number})
        publisher._committed_strategy_intents[ENTRY] = retained
        async def publish():
            return await publisher.enqueue_pending()
        receipt = asyncio.run(publish())
        assert receipt.last_sequence == (11 if compound else 10)
        assert journal.pending_record_count == 0
        assert journal.profit_giveback_exit_for_record(record.record_id) is None
        source, restored = publisher._committed_strategy_intents[record.entity_id]
        assert restored == values(strategy_number=number)['intent']
        assert source.events[0]['record_id'] == record.record_id
        assert source.batch_id == receipt.last_batch_id
        assert load_verified_v4_prefix(client, RUN).last_sequence == receipt.last_sequence
    finally:
        worker.close()
