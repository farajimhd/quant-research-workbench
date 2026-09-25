from __future__ import annotations

from copy import deepcopy

import pytest

from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, DISPATCH_TABLES, INTENT, INTENT_COMMIT,
    project_dispatch_ack, project_dispatch_intents, read_committed_dispatch_prefix,
    verify_dispatch_cursor,
)


EVENT_ID = "a" * 64
SOURCE_HASH = "b" * 64
ACTIVATION_HASH = "c" * 64


class Authority:
    def __init__(self, occurrence):
        self.occurrence = occurrence

    def read_exact(self, event_id):
        return deepcopy(self.occurrence) if event_id == EVENT_ID else None


def _input():
    occurrence = {
        "event_id": EVENT_ID, "ticker": "ABC", "signal_stream_id": "stream-1",
        "event_time": "2026-09-24T14:00:00+00:00",
        "effective_at": "2026-09-24T14:00:00+00:00",
    }
    delivery = {
        "delivery_id": f"plan-1:{EVENT_ID}", "run_plan_id": "plan-1",
        "profile_id": "profile-1", "book_id": "default", "ticker": "ABC",
        "signal_stream_id": "stream-1", "event_id": EVENT_ID,
        "event_time": "2026-09-24T14:00:00+00:00", "occurrence": occurrence,
    }
    return occurrence, delivery


def _intents(deliveries):
    occurrence, _ = _input()
    return project_dispatch_intents(
        deliveries, session_key="2026-09-24", source_batch_sequence=1,
        source_cursor_commit_hash=SOURCE_HASH,
        configuration_revision_id="approved-revision-1",
        occurrence_authority=Authority(occurrence),
    )


def test_normalized_intent_ack_roundtrip_and_storage_policy() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(
        intents, [{"delivery_id": delivery["delivery_id"],
                   "ack_kind": "activation_durable",
                   "activation_receipt_hash": ACTIVATION_HASH}],
        acknowledged_at="2026-09-24T14:00:01+00:00")
    verify_dispatch_cursor(intents, acks)
    assert intents["intents"][0]["event_time"] == "2026-09-24T14:00:00.000000+00:00"
    assert all("storage_policy = 'live_market_ssd'" in table.ddl()
               for table in DISPATCH_TABLES)
    assert all("JSON" not in table.ddl() and "Object" not in table.ddl()
               for table in DISPATCH_TABLES)


def test_zero_delivery_cursor_is_committed() -> None:
    intents = _intents([])
    acks = project_dispatch_ack(intents, [],
                                acknowledged_at="2026-09-24T14:00:01+00:00")
    verify_dispatch_cursor(intents, acks)
    assert intents["commit"]["intent_count"] == acks["commit"]["ack_count"] == 0


def test_unmodeled_or_unbacked_delivery_rejected() -> None:
    occurrence, delivery = _input()
    with pytest.raises(ValueError, match="unmodeled"):
        _intents([{**delivery, "extra": 1}])
    with pytest.raises(ValueError, match="exact typed source"):
        _intents([{**delivery, "occurrence": {**occurrence, "ticker": "XYZ"}}])
    with pytest.raises(ValueError, match="identity"):
        _intents([{**delivery, "delivery_id": "wrong"}])


def test_local_queue_count_cannot_be_used_as_durable_ack() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    with pytest.raises(ValueError, match="ACK"):
        project_dispatch_ack(intents, [{"delivery_id": delivery["delivery_id"],
                                        "ack_kind": "queue_accepted",
                                        "activation_receipt_hash": ACTIVATION_HASH}],
                             acknowledged_at="2026-09-24T14:00:01+00:00")
    with pytest.raises(ValueError, match="ACK"):
        project_dispatch_ack(intents, [],
                             acknowledged_at="2026-09-24T14:00:01+00:00")


def test_cold_verifier_rejects_duplicate_and_corrupt_rows() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(
        intents, [{"delivery_id": delivery["delivery_id"],
                   "ack_kind": "activation_durable",
                   "activation_receipt_hash": ACTIVATION_HASH}],
        acknowledged_at="2026-09-24T14:00:01+00:00")
    duplicate = deepcopy(intents)
    duplicate["intents"].append(deepcopy(duplicate["intents"][0]))
    with pytest.raises(ValueError, match="fence"):
        verify_dispatch_cursor(duplicate, acks)
    corrupt = deepcopy(acks)
    corrupt["acks"][0]["activation_receipt_hash"] = "d" * 64
    with pytest.raises(ValueError, match="fence"):
        verify_dispatch_cursor(intents, corrupt)


class ColdStorage:
    def __init__(self, intents, acks):
        self.rows = {
            INTENT.name: deepcopy(intents["intents"]),
            INTENT_COMMIT.name: [deepcopy(intents["commit"])],
            ACK.name: deepcopy(acks["acks"]),
            ACK_COMMIT.name: [deepcopy(acks["commit"])],
        }

    def read_dispatch_rows(self, table_name, *, session_key, source_batch_sequence):
        return [deepcopy(row) for row in self.rows[table_name]
                if row["session_key"] == session_key
                and row["source_batch_sequence"] == source_batch_sequence]

    def list_dispatch_commits(self, table_name, *, session_key):
        return [deepcopy(row) for row in self.rows[table_name]
                if row["session_key"] == session_key]


def test_cold_dispatch_prefix_requires_exact_commits_and_source_hash() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [{"delivery_id": delivery["delivery_id"],
                                           "ack_kind": "activation_durable",
                                           "activation_receipt_hash": ACTIVATION_HASH}],
                                acknowledged_at="2026-09-24T14:00:01+00:00")
    storage = ColdStorage(intents, acks)
    with pytest.raises(ValueError, match="nonempty source bound"):
        read_committed_dispatch_prefix(
            storage, session_key="2026-09-24", source_commit_hashes=(),
            configuration_revision_id="approved-revision-1")
    def recover():
        return read_committed_dispatch_prefix(
            storage, session_key="2026-09-24", source_commit_hashes=(SOURCE_HASH,),
            configuration_revision_id="approved-revision-1")
    assert recover() == ((intents, acks),)
    storage.rows[ACK_COMMIT.name].clear()
    with pytest.raises(ValueError, match="missing"):
        recover()
    storage.rows[ACK_COMMIT.name].append(acks["commit"])
    storage.rows[INTENT.name].append(intents["intents"][0])
    with pytest.raises(ValueError, match="fence"):
        recover()
    storage.rows[INTENT.name].pop()
    storage.rows[INTENT_COMMIT.name].append(intents["commit"])
    with pytest.raises(ValueError, match="duplicate"):
        recover()
