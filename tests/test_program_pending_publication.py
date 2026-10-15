"""Native source retention and ordered receipt publication without database I/O."""
import asyncio
from concurrent.futures import Future
from uuid import uuid4

import pytest

from src.backend.program_execution_journal import ProgramExecutionJournal
from src.backend.program_pending_publication import commit_program_pending_records
from src.trading_runtime.signals import StrategyIntent
from tests.test_program_execution_journal import assembly, AT


def test_semantic_sources_promote_only_after_ordered_native_receipts(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        originals = [StrategyIntent(str(uuid4()), 'ABC', AT, 'enter_long', 1., 12.25),
                     StrategyIntent(str(uuid4()), 'ABC', AT, 'exit', 1., 12.25)]
        records = [journal.append_program_intent(intent, account_id='account', assignment_id='assignment')
                   for intent in originals]
        submitted = []
        def submit(unit):
            with pytest.raises(ValueError, match='no native committed'):
                journal.program_intent_source(originals[len(submitted)].intent_id, 'account')
            submitted.append(unit)
            future = Future()
            future.set_result(unit.batch_id)
            return future
        writer.submit_base_v4 = submit
        result = await commit_program_pending_records(journal, attempt_id=str(uuid4()),
            expected_config={'strategy_id': lease.registration.strategy_id,
                             'strategy_revision': lease.registration.revision},
            run_month=AT.date().replace(day=1), source_cursor='owned-source:1')
        assert type(result) is tuple and len(result) == 2
        assert submitted[1].prior_batch_id == submitted[0].batch_id
        assert [receipt.last_sequence for _, receipt in result] == [1, 2]
        for intent, record, (unit, receipt) in zip(originals, records, result):
            source = journal.program_intent_source(intent.intent_id, 'account')
            assert source.intent == intent and source.record_id == record.record_id
            assert source.batch_id == receipt.batch_id == unit.batch_id
            assert source.assignment_id == 'assignment'
            assert journal.program_intent_for_record(record.record_id) is None
        assert journal.pending_record_count == 0
    try:
        asyncio.run(run())
    finally:
        lease.release()


def test_missing_original_intent_fails_before_native_submit(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        intent = StrategyIntent(str(uuid4()), 'ABC', AT, 'enter_long', 1., 12.25)
        journal.append(run_id=journal.run_id, category='strategy', entity_type='strategy_intent',
            entity_id=intent.intent_id, account_id='account', event_time=AT, payload=intent.payload())
        writer.submit_base_v4 = lambda _: pytest.fail('Unretained source must not reach writer')
        with pytest.raises(ValueError, match='original semantic'):
            await commit_program_pending_records(journal, attempt_id=str(uuid4()),
                expected_config={'strategy_id': lease.registration.strategy_id,
                                 'strategy_revision': lease.registration.revision},
                run_month=AT.date().replace(day=1), source_cursor='owned-source:1')
        assert journal.pending_record_count == 1 and journal.fenced_sequence == 0
    try:
        asyncio.run(run())
    finally:
        lease.release()


def test_missing_completion_qualification_prevents_preceding_publication(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        intent = StrategyIntent(str(uuid4()), 'ABC', AT, 'enter_long', 1., 12.25)
        journal.append_program_intent(intent, account_id='account', assignment_id='assignment')
        journal.append(run_id=journal.run_id, category='strategy_decision', entity_type='signal',
            entity_id=str(uuid4()), account_id='account', event_time=AT,
            payload={'signal_type': 'program_boundary_completed'})
        writer.submit_base_v4 = lambda _: pytest.fail('Missing completion proof must precede all writes')
        with pytest.raises(ValueError, match='decision qualification'):
            await commit_program_pending_records(journal, attempt_id=str(uuid4()),
                expected_config={'strategy_id': lease.registration.strategy_id,
                                 'strategy_revision': lease.registration.revision},
                run_month=AT.date().replace(day=1), source_cursor='owned-source:1')
        assert journal.pending_record_count == 2 and journal.fenced_sequence == 0
    try:
        asyncio.run(run())
    finally:
        lease.release()

