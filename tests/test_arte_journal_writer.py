from __future__ import annotations

from datetime import date
import json
from threading import Event
from uuid import UUID

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, JournalQueueFull, TypedJournalBatch, publish_typed_batch,
    typed_row,
)


RUN = "live:account:session"
ATTEMPT = "00000000-0000-0000-0000-000000000011"
BATCH = "00000000-0000-0000-0000-000000000012"
RECORD = "00000000-0000-0000-0000-000000000013"
ZERO = "00000000-0000-0000-0000-000000000000"


def batch() -> TypedJournalBatch:
    event = typed_row({
        "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
        "batch_id": BATCH, "record_id": RECORD, "sequence": 1,
        "event_time": "2026-08-18T08:05:00+00:00",
        "recorded_at": "2026-08-18T08:05:01+00:00",
        "category": "execution", "entity_type": "fill", "entity_id": "fill-1",
        "account_id": "DU1", "correlation_id": "c1", "causation_id": "k1",
    })
    return TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, (event,))


class MemoryClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.tables.setdefault(name, []).extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert sql.startswith("SELECT ")
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
        if "WHERE run_id=" in sql:
            run_id = sql.split("WHERE run_id='", 1)[1].split("'", 1)[0]
            matching = [row for row in self.tables.get(name, []) if row["run_id"] == run_id]
            matching.sort(key=lambda row: row["last_sequence"], reverse=True)
            return "\n".join(json.dumps({column: row[column] for column in columns})
                             for row in matching[:1])
        batch_id = sql.split("batch_id=toUUID('", 1)[1].split("')", 1)[0]
        return "\n".join(json.dumps({column: row[column] for column in columns})
                         for row in self.tables.get(name, []) if row["batch_id"] == batch_id)


def test_typed_publication_commits_last_and_retry_is_idempotent() -> None:
    client = MemoryClient()
    item = batch()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert len(client.tables["trading_commit_v1"]) == 1
    assert not any("payload_json" in row for rows in client.tables.values() for row in rows)


def test_typed_publication_detects_conflicting_readback() -> None:
    client = MemoryClient()
    item = batch()
    publish_typed_batch(client, item)
    client.tables["trading_event_v1"][0]["content_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="conflicting"):
        publish_typed_batch(client, item)


def test_typed_publication_rejects_out_of_order_prefix() -> None:
    client = MemoryClient()
    item = batch()
    altered = TypedJournalBatch(item.run_id, item.run_month, item.attempt_id,
                                item.batch_id, item.prior_batch_id, 2, 2,
                                (typed_row({**{key: value for key, value in item.events[0].items()
                                               if key != "content_hash"}, "sequence": 2}),))
    with pytest.raises(RuntimeError, match="committed prefix"):
        publish_typed_batch(client, altered)
    assert not client.inserts


def test_submission_never_waits_for_network_or_queue_space(monkeypatch) -> None:
    entered, release = Event(), Event()

    def stalled(_client, item):
        entered.set()
        assert release.wait(5)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", stalled)
    journal = ArteJournalWriter(object(), capacity=1)
    try:
        first = journal.submit(batch())
        assert entered.wait(5)
        second = journal.submit(batch())
        with pytest.raises(JournalQueueFull):
            journal.submit(batch())
        assert not first.done() and not second.done()
        release.set()
        assert UUID(first.result(timeout=5)) == UUID(BATCH)
        assert UUID(second.result(timeout=5)) == UUID(BATCH)
    finally:
        release.set()
        journal.close()


def test_invalid_family_row_is_rejected_before_enqueue() -> None:
    item = batch()
    altered = dict(item.events[0])
    altered["payload_json"] = "{}"
    with pytest.raises(ValueError, match="typed columns"):
        TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                          1, 1, (altered,))
