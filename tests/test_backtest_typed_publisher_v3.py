"""Inactive fixed publisher routes closed squeeze facts through V3 only."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_journal_writer import V3SqueezeBatch
from src.trading_runtime import arte_journal_writer as writer_module
from src.backend.backtest_squeeze_episode_v3 import _canonical_row
from src.backend.backtest_squeeze_episode_v3 import (
    coalesce_squeeze_units_v3, project_squeeze_batch_v3,
)
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_squeeze_v3_publisher import FakeV3Client
from tests.test_backtest_squeeze_episode_projection import ATTEMPT, PLAN, QUERY, _record


class FakeV3Writer:
    run_id = "backtest:squeeze"
    run_mode = "backtest"
    journal_profile = "backtest_v3"
    coalesce_batches = False
    max_events_per_commit = 512

    def __init__(self):
        self.units = []
    def submit(self, batch):
        raise AssertionError("V3 must not use V2 batch submission")
    def submit_squeeze_v3(self, unit):
        self.units.append(unit)
        result = Future()
        result.set_result(unit.base.batch_id)
        return result


def test_v3_publisher_projects_closed_occurrence_and_fences_receipt():
    async def run():
        record = _record()
        journal = BacktestMemoryJournal(run_id=record.run_id)
        journal.append(
            run_id=record.run_id, category=record.category,
            entity_type=record.entity_type, entity_id=record.entity_id,
            account_id=record.account_id, event_time=record.event_time,
            payload=dict(record.payload))
        writer = FakeV3Writer()
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), batch_size=1,
            expected_market_plan_token=PLAN, expected_query_sha256=QUERY)
        receipt = await publisher.enqueue_pending()
        assert receipt.last_sequence == 1
        assert len(writer.units) == 1
        unit = writer.units[0]
        assert isinstance(unit, V3SqueezeBatch)
        assert unit.episodes[0]["episode_id"] == record.entity_id
        assert publisher.fenced_sequence == 1
        journal.close()
    asyncio.run(run())


def test_v3_publisher_fails_closed_without_pinned_query_hash():
    record = _record()
    journal = BacktestMemoryJournal(run_id=record.run_id)
    with pytest.raises(ValueError, match="pinned squeeze authority"):
        BacktestTypedJournalPublisher(
            journal, FakeV3Writer(), attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), batch_size=1,
            expected_market_plan_token=PLAN)
    journal.close()


def test_v3_publisher_batches_two_occurrences_under_one_exact_commit():
    async def run():
        first = _record()
        second_payload = dict(first.payload)
        second_payload["event_id"] = "d" * 64
        second_payload["squeeze_episode_id"] = "d" * 64
        journal = BacktestMemoryJournal(run_id=first.run_id)
        for entity_id, payload in ((first.entity_id, first.payload),
                                   ("d" * 64, second_payload)):
            journal.append(
                run_id=first.run_id, category=first.category,
                entity_type=first.entity_type, entity_id=entity_id,
                account_id=first.account_id, event_time=first.event_time,
                payload=dict(payload))
        writer = FakeV3Writer()
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), batch_size=2,
            expected_market_plan_token=PLAN, expected_query_sha256=QUERY)
        receipt = await publisher.enqueue_pending()
        assert receipt.last_sequence == 2
        assert len(writer.units) == 1
        unit = writer.units[0]
        assert (unit.base.first_sequence, unit.base.last_sequence) == (1, 2)
        assert len(unit.base.events) == len(unit.episodes) == 2
        assert {row["record_id"] for row in unit.base.events} == {
            row["record_id"] for row in unit.episodes}
        for child in unit.episodes:
            assert child["batch_id"] == unit.base.batch_id
            canonical = _canonical_row({key: value for key, value in child.items()
                                        if key != "content_hash"})
            assert child["content_hash"] == sha256(
                canonical_json(canonical).encode()).hexdigest()
        journal.close()
    asyncio.run(run())


def test_v3_publisher_to_real_worker_seals_two_events_once(monkeypatch):
    async def run():
        first = _record()
        journal = BacktestMemoryJournal(run_id=first.run_id)
        for identity in (first.entity_id, "d" * 64):
            payload = dict(first.payload)
            payload["event_id"] = identity
            payload["squeeze_episode_id"] = identity
            journal.append(
                run_id=first.run_id, category=first.category,
                entity_type=first.entity_type, entity_id=identity,
                account_id=first.account_id, event_time=first.event_time,
                payload=payload)
        client = FakeV3Client()
        monkeypatch.setattr(writer_module, "_v3_preflight", lambda _: None)
        monkeypatch.setattr(writer_module, "_verify_run_identity",
                            lambda *_: {"mode": "backtest", "account_ids": ("DU1",)})
        lane = writer_module.ArteJournalWriter(
            client, run_id=first.run_id, journal_profile="backtest_v3",
            coalesce_batches=False, max_events_per_commit=2)
        try:
            publisher = BacktestTypedJournalPublisher(
                journal, lane, attempt_id=ATTEMPT,
                run_month=date(2026, 8, 1), batch_size=2,
                expected_market_plan_token=PLAN,
                expected_query_sha256=QUERY)
            receipt = await publisher.enqueue_pending()
            assert receipt.last_sequence == 2
            assert client.inserts.count("trading_commit_v3") == 1
            assert len(client.tables["trading_backtest_squeeze_episode_v1"]) == 2
            assert len(client.tables["trading_event_v1"]) == 2
        finally:
            lane.close()
            journal.close()
    asyncio.run(run())


def test_v3_delayed_batch_receipt_does_not_block_engine_ticks():
    async def run():
        record = _record()
        journal = BacktestMemoryJournal(run_id=record.run_id)
        journal.append(
            run_id=record.run_id, category=record.category,
            entity_type=record.entity_type, entity_id=record.entity_id,
            account_id=record.account_id, event_time=record.event_time,
            payload=dict(record.payload))

        class DelayedWriter(FakeV3Writer):
            def __init__(self):
                super().__init__()
                self.receipt = Future()
            def submit_squeeze_v3(self, unit):
                self.units.append(unit)
                return self.receipt

        writer = DelayedWriter()
        publisher = BacktestTypedJournalPublisher(
            journal, writer, attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), batch_size=16,
            expected_market_plan_token=PLAN, expected_query_sha256=QUERY)
        task = publisher.enqueue_pending()
        ticks = 0
        for _ in range(100):
            ticks += 1
            await asyncio.sleep(0)
            if writer.units:
                break
        assert ticks > 0 and writer.units and not task.done()
        assert publisher.fenced_sequence == 0
        writer.receipt.set_result(writer.units[0].base.batch_id)
        assert (await task).last_sequence == 1
        journal.close()
    asyncio.run(run())


def test_v3_coalescer_rejects_mixed_pinned_market_authority():
    record = _record()
    first = project_squeeze_batch_v3(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id="00000000-0000-0000-0000-000000000f01",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="start", expected_market_plan_token=PLAN,
        expected_query_sha256=QUERY)
    second = replace(first, base=replace(
        first.base, batch_id="00000000-0000-0000-0000-000000000f02",
        prior_batch_id=first.base.batch_id, first_sequence=2,
        last_sequence=2),
        episodes=({**first.episodes[0], "query_sha256": "e" * 64},))
    with pytest.raises(ValueError, match="market plan/query"):
        coalesce_squeeze_units_v3((first, second))
