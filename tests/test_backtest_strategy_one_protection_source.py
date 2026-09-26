"""V4 keeps each normalized Strategy 1 amendment as an OMS source revision."""
import asyncio
from concurrent.futures import Future
from datetime import date
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_v4_prefix, publish_base_typed_batch_v4,
)
from test_strategy_one_protection_intent import (
    financial, previous, transition,
)
from src.trading_runtime.strategy_one_protection_intent import (
    strategy_one_protection_intents,
)


def test_protection_source_is_isolated_and_fenced_without_blob():
    run_id = str(UUID(int=901))
    attempt_id = str(UUID(int=902))
    journal = BacktestMemoryJournal(run_id=run_id)
    intents = strategy_one_protection_intents(
        previous(), transition(), financial(),
        session_date=date(2026, 8, 18), bid=10., ask=10.01)
    records = [journal.append_strategy_one_protection_intent(
        intent=intent, account_id="DU1", strategy_id="early-squeeze-strategy",
        strategy_revision=1) for intent in intents]
    units = project_pending_backtest_v4_prefix(
        journal, attempt_id=attempt_id, run_month=date(2026, 8, 1),
        prior_sequence=0, source_cursor="2026-08-18:31000",
        expected_config={"strategy_id": "early-squeeze-strategy",
                         "strategy_revision": 1}, through_sequence=2)
    assert len(units) == 2
    assert [unit.events[0]["record_id"] for unit in units] == [
        record.record_id for record in records]
    assert all(unit.first_sequence == unit.last_sequence for unit in units)

    class Writer:
        run_mode = "backtest"
        journal_profile = "backtest_v4"
        coalesce_batches = False
        max_events_per_commit = 2

        def __init__(self, source_run_id):
            self.run_id = source_run_id
            from tests.test_arte_journal_commit_v4 import attached_v4_client
            self.client = attached_v4_client()

        def submit_base_v4(self, batch):
            result = Future()
            result.set_result(publish_base_typed_batch_v4(self.client, batch))
            return result

    async def publish():
        writer = Writer(run_id)
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=attempt_id,
            run_month=date(2026, 8, 1), batch_size=2,
            expected_config={"strategy_id": "early-squeeze-strategy",
                             "strategy_revision": 1})
        receipt = await publisher.enqueue_pending()
        assert receipt.last_sequence == 2
        assert {intent.intent_id for intent in intents} == set(
            publisher._committed_strategy_intents)
        assert load_verified_v4_prefix(writer.client, run_id).last_sequence == 2

    asyncio.run(publish())
    assert journal.pending_record_count == 0
    assert all(journal.strategy_one_protection_for_record(record.record_id) is None
               for record in records)


def test_numbered_protection_without_source_fails_projection():
    run_id = str(UUID(int=903))
    journal = BacktestMemoryJournal(run_id=run_id)
    intent = strategy_one_protection_intents(
        previous(), transition(), financial(),
        session_date=date(2026, 8, 18), bid=10., ask=10.01)[0]
    journal.append(
        run_id=run_id, category="strategy", entity_type="strategy_intent",
        entity_id=intent.intent_id, account_id="DU1",
        event_time=intent.event_time,
        payload={**intent.payload(), "strategy_id": "early-squeeze-strategy",
                 "strategy_revision": 1})
    with pytest.raises(RuntimeError, match="lacks typed source"):
        project_pending_backtest_v4_prefix(
            journal, attempt_id=str(UUID(int=904)),
            run_month=date(2026, 8, 1), prior_sequence=0,
            source_cursor="2026-08-18:31000",
            expected_config={"strategy_id": "early-squeeze-strategy",
                             "strategy_revision": 1}, through_sequence=1)
