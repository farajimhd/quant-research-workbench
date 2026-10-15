"""Transient sequencing and native receipt handling; no live database calls."""
import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.backend.program_execution_journal import ProgramExecutionJournal
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.program_v4_principal import ProgramV4WriterClient
from src.trading_runtime.arte_journal_writer import ArteJournalWriter, TypedJournalBatch
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_projection import project_journal_record, project_program_journal_record
from tests.test_program_v4_principal import lease
from tests.test_live_strategy_one_v4_principal import Client


AT = datetime(2026, 10, 15, tzinfo=timezone.utc)
ZERO = str(UUID(int=0))


def assembly(monkeypatch):
    selected = lease(monkeypatch)
    native_client = ProgramV4WriterClient(Client(user='program_v4_runner'), selected)
    # Isolate the adapter's receipt protocol from the independently tested
    # writer transport. Concrete owner/lease checks still execute normally.
    writer = object.__new__(ArteJournalWriter)
    writer._journal_profile, writer._run_mode = 'live_v4', 'paper'
    writer._client, writer._live_v4_lease, writer._run_id = native_client, selected, selected.run_id
    writer._accepted_writes, writer._closed, writer._error = False, False, None
    prefixes = [None]
    monkeypatch.setattr('src.trading_runtime.arte_journal_commit_v4.load_writer_v4_snapshot_prefix',
                        lambda client, run_id: prefixes[-1])
    return writer, selected, prefixes


def append(journal):
    return journal.append(run_id=journal.run_id, category='risk', entity_type='continuous_risk_state',
        entity_id='risk-1', account_id='account', event_time=AT, payload={'state': 'open'})


def batch(journal, record, *, prior=ZERO):
    identity = str(uuid4())
    return TypedJournalBatch(journal.run_id, AT.date().replace(day=1), str(uuid4()), identity,
        prior, record.sequence, record.sequence, 'source', 'running',
        ({'record_id': record.record_id, 'run_id': journal.run_id, 'batch_id': identity,
          'category': record.category, 'entity_type': record.entity_type, 'sequence': record.sequence,
          'entity_id': record.entity_id, 'account_id': record.account_id,
          'event_time': record.event_time.isoformat(), 'recorded_at': record.recorded_at.isoformat()},))


def test_native_journal_has_no_backtest_identity_and_fences_only_exact_receipt(monkeypatch):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        assert not isinstance(journal, BacktestMemoryJournal)
        assert journal.writer is writer and journal.mode == 'paper'
        record = append(journal)
        unit = batch(journal, record)
        future = Future()
        writer.submit_base_v4 = lambda actual: future
        pending = asyncio.create_task(journal.commit_native(unit))
        await asyncio.sleep(0)
        assert journal.fenced_sequence == 0 and journal.pending_record_count == 1
        future.set_result(unit.batch_id)
        receipt = await pending
        assert receipt.batch_id == unit.batch_id and journal.fenced_sequence == 1
        assert journal.pending_record_count == 0
        with pytest.raises(AttributeError):
            journal.mark_fenced(2)
        with pytest.raises(AttributeError):
            journal.acquire_campaign_session_ownership('unverified')
        journal.close()
    try:
        asyncio.run(run())
    finally:
        selected.release()


@pytest.mark.parametrize('failure', ['exception', 'wrong_receipt', 'cancelled_receipt'])
def test_uncertain_or_foreign_receipt_stops_admission_without_discarding_records(monkeypatch, failure):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        unit = batch(journal, append(journal))
        future = Future()
        if failure == 'exception':
            future.set_exception(RuntimeError('uncertain commit'))
        elif failure == 'wrong_receipt':
            future.set_result(str(uuid4()))
        else:
            future.cancel()
        writer.submit_base_v4 = lambda actual: future
        with pytest.raises(RuntimeError):
            await journal.commit_native(unit)
        assert journal.fenced_sequence == 0 and journal.pending_record_count == 1
        with pytest.raises(RuntimeError, match='cold recovery'):
            append(journal)
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_cancellation_drains_actual_receipt_and_then_requires_recovery(monkeypatch):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        unit = batch(journal, append(journal))
        future = Future()
        writer.submit_base_v4 = lambda actual: future
        pending = asyncio.create_task(journal.commit_native(unit))
        await asyncio.sleep(0)
        pending.cancel()
        asyncio.get_running_loop().call_soon(future.set_result, unit.batch_id)
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert journal.fenced_sequence == 1 and journal.pending_record_count == 0
        with pytest.raises(RuntimeError, match='cold recovery'):
            journal.require_current()
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_foreign_pending_record_identity_rejected_before_native_submission(monkeypatch):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        record = append(journal)
        unit = batch(journal, replace(record, record_id=str(uuid4())))
        writer.submit_base_v4 = lambda actual: pytest.fail('Foreign record was submitted')
        with pytest.raises(ValueError, match='pending journal prefix'):
            await journal.commit_native(unit)
        assert journal.pending_record_count == 1
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_external_typed_prefix_refresh_rebases_only_empty_buffer(monkeypatch):
    writer, selected, prefixes = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        external = str(uuid4())
        prefixes.append(V4CommittedPrefix(journal.run_id, 3, external, 'portfolio', 'running', (external,)))
        await journal.refresh_native_prefix()
        record = append(journal)
        assert record.sequence == 4 and journal.fenced_sequence == 3
        with pytest.raises(RuntimeError, match='empty idle'):
            await journal.refresh_native_prefix()
        assert journal.pending_record_count == 1
        with pytest.raises(RuntimeError, match='cold recovery'):
            journal.require_current()
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_old_exact_projection_mode_guard_remains_closed(monkeypatch):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        record = append(journal)
        scope = dict(run_month=AT.date().replace(day=1), attempt_id=str(uuid4()),
            batch_id=str(uuid4()), prior_batch_id=ZERO, source_cursor='source',
            expected_mode='paper', exact_signal_prices=True)
        with pytest.raises(ValueError, match='Backtest mode'):
            project_journal_record(record, **scope)
        with pytest.raises(TypeError, match='native owned'):
            project_program_journal_record(record, journal=object(), **scope)
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_native_program_signal_codec_preserves_actual_binary64_price(monkeypatch):
    from src.trading_runtime.signals import StrategySignal
    from src.trading_runtime.arte_journal_writer import _sealed_families
    from src.trading_runtime.trading_price_rows import restore_exact_price_row
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        price = 10.001000000000001
        signal = StrategySignal('signal-1', 'breakout', 'ABC', AT, 'enter_long', 'bullish',
                                .75, .9, 'entry_ready', ('source-a',), '1s', price)
        record = journal.append(run_id=journal.run_id, category='strategy_decision', entity_type='signal',
            entity_id=signal.signal_id, account_id='account', event_time=AT,
            payload={**signal.payload(), 'strategy_id': 'generic-program', 'strategy_revision': 62})
        unit = project_program_journal_record(record, journal=journal,
            run_month=AT.date().replace(day=1), attempt_id=str(uuid4()), batch_id=str(uuid4()),
            prior_batch_id=ZERO, source_cursor='source', expected_mode='paper', exact_signal_prices=True)
        sealed, = dict(_sealed_families(unit))['trading_strategy_signal_exact_price_v1']
        restored = restore_exact_price_row('trading_strategy_signal_v1', sealed)
        assert restored['invalidation_price'].hex() == price.hex()
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_completion_cannot_use_plain_base_commit_to_skip_native_qualification(monkeypatch):
    from src.trading_runtime.signals import StrategySignal
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        signal = StrategySignal('decision', 'program_boundary_completed', 'ABC', AT, 'wait', 'neutral',
                                0., 1., 'program:wait', (), '1s')
        record = journal.append(run_id=journal.run_id, category='strategy_decision', entity_type='signal',
            entity_id=signal.signal_id, account_id='account', event_time=AT,
            payload={**signal.payload(), 'strategy_id': 'generic-program', 'strategy_revision': 62})
        unit = project_program_journal_record(record, journal=journal,
            run_month=AT.date().replace(day=1), attempt_id=str(uuid4()), batch_id=str(uuid4()),
            prior_batch_id=ZERO, source_cursor='source', expected_mode='paper', exact_signal_prices=True)
        writer.submit_base_v4 = lambda actual: pytest.fail('Unqualified completion reached writer')
        with pytest.raises(ValueError, match='native decision qualification'):
            await journal.commit_native(unit)
        assert journal.pending_record_count == 1 and journal.fenced_sequence == 0
    try:
        asyncio.run(run())
    finally:
        selected.release()


def test_writer_client_or_owner_replacement_stops_buffer_admission(monkeypatch):
    writer, selected, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        writer._client = object()
        with pytest.raises(RuntimeError, match='native writer authority'):
            append(journal)
        assert journal.pending_record_count == 0
    try:
        asyncio.run(run())
    finally:
        selected.release()
