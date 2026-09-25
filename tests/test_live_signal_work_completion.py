from __future__ import annotations

from concurrent.futures import Future
from copy import deepcopy
from datetime import datetime

import pytest

from src.backend.live_signal_work_completion import (
    COMPLETION, CompletionProof, CompletionPublicationQueue, prepare_completion_proof,
    project_completion, read_completed_dispatch_prefix, read_exact_completion,
)
from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT,
    project_dispatch_ack, project_dispatch_intents,
)


class Authority:
    def __init__(self, occurrence):
        self.occurrence = occurrence

    def read_exact(self, event_id):
        return self.occurrence if event_id == self.occurrence["event_id"] else None


class Storage:
    def __init__(self):
        self.rows = []
        self.fail_after_insert = False

    def insert_completion_row(self, row):
        self.rows.append(deepcopy(row))
        if self.fail_after_insert:
            raise RuntimeError("ambiguous completion write")

    def read_completion_rows(self, *, session_key, source_batch_sequence, ordinal):
        rows = deepcopy([row for row in self.rows if row["session_key"] == session_key
                         and row["source_batch_sequence"] == source_batch_sequence
                         and row["ordinal"] == ordinal])
        for row in rows:
            row["processed_at"] = datetime.fromisoformat(row["processed_at"]).replace(tzinfo=None)
            for key in ("intent_content_hash", "dispatch_ack_commit_hash",
                        "activation_receipt_hash", "content_hash"):
                row[key] = row[key].encode("ascii")
        return rows


class Keeper:
    def __init__(self):
        self.epoch = 0
        self.current = None
        self.proof = None
        self.lost_before_attest = False

    def acquire_completion_claim(self, resource, *, owner_id):
        self.epoch += 1
        self.current = (resource, owner_id, self.epoch)
        return {"resource_id": resource, "owner_id": owner_id, "epoch": self.epoch}

    def completion_claim_is_current(self, resource, *, owner_id, epoch):
        return self.current == (resource, owner_id, epoch)

    def attest_completion(self, resource, *, owner_id, epoch, content_hash):
        if self.lost_before_attest or not self.completion_claim_is_current(
                resource, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("stale Keeper owner")
        self.proof = (resource, owner_id, epoch, content_hash)

    def completion_proof_matches(self, resource, *, owner_id, epoch, content_hash):
        return self.proof == (resource, owner_id, epoch, content_hash)

    def completion_proof_exists(self, resource):
        return self.proof is not None and self.proof[0] == resource

    def release_completion_claim(self, resource, *, owner_id, epoch):
        if not self.completion_claim_is_current(resource, owner_id=owner_id, epoch=epoch):
            return False
        self.current = None
        return True


def _proof_inputs():
    occurrence = {"event_id": "a" * 64, "ticker": "ABC",
                  "signal_stream_id": "stream-1",
                  "event_time": "2026-09-24T14:00:00+00:00",
                  "effective_at": "2026-09-24T14:00:00+00:00"}
    delivery = {"delivery_id": f"plan-1:{occurrence['event_id']}",
                "run_plan_id": "plan-1", "profile_id": "profile-1",
                "book_id": "default", "ticker": "ABC",
                "signal_stream_id": "stream-1", "event_id": occurrence["event_id"],
                "event_time": occurrence["effective_at"], "occurrence": occurrence}
    intents = project_dispatch_intents(
        [delivery], session_key="2026-09-24", source_batch_sequence=1,
        source_cursor_commit_hash="b" * 64,
        configuration_revision_id="approved-1",
        occurrence_authority=Authority(occurrence))
    acks = project_dispatch_ack(
        intents, [{"delivery_id": delivery["delivery_id"],
                   "ack_kind": "activation_durable",
                   "activation_receipt_hash": "c" * 64}],
        acknowledged_at="2026-09-24T14:00:01+00:00")
    return delivery, intents, acks


def test_cold_prefix_requires_attested_completion_for_every_dispatch_ack() -> None:
    from src.backend.live_signal_completion_keeper import completion_resource

    _, intents, acks = _proof_inputs()
    class DispatchStorage:
        rows = {INTENT.name: intents["intents"],
                INTENT_COMMIT.name: [intents["commit"]],
                ACK.name: acks["acks"], ACK_COMMIT.name: [acks["commit"]]}

        def read_dispatch_rows(self, table_name, *, session_key, source_batch_sequence):
            return deepcopy(self.rows[table_name])

        def list_dispatch_commits(self, table_name, *, session_key):
            return deepcopy(self.rows[table_name])

    storage, keeper = Storage(), Keeper()
    def recover():
        return read_completed_dispatch_prefix(
            DispatchStorage(), storage, keeper, session_key="2026-09-24",
            source_commit_hashes=("b" * 64,),
            configuration_revision_id="approved-1")
    with pytest.raises(ValueError, match="absent or uncertain"):
        recover()
    projected = project_completion(intents, acks, ordinal=0,
                                   processed_at="2026-09-24T14:00:02+00:00",
                                   keeper_owner_id="owner-1", keeper_epoch=1)
    storage.insert_completion_row(projected.row)
    with pytest.raises(ValueError, match="Keeper attestation"):
        recover()
    row = projected.row
    keeper.proof = (completion_resource(row["session_key"], row["source_batch_sequence"],
                                        row["ordinal"], row["delivery_id"]),
                    row["keeper_owner_id"], row["keeper_epoch"], row["content_hash"])
    proofs = recover()
    assert len(proofs) == 1 and proofs[0].delivery_id == row["delivery_id"]
    storage.insert_completion_row(projected.row)
    with pytest.raises(ValueError, match="duplicate"):
        recover()


def test_immutable_packet_survives_caller_mutation_and_cold_roundtrip() -> None:
    _, intents, acks = _proof_inputs()
    proof = prepare_completion_proof(intents, acks, ordinal=0)
    with pytest.raises(TypeError):
        CompletionProof(intents, acks, 0, "2026-09-24", 1, "delivery", "a" * 64)
    intents["intents"][0]["ticker"] = "CORRUPTED"
    acks["acks"][0]["activation_receipt_hash"] = "d" * 64
    with pytest.raises(TypeError):
        proof.intents["commit"]["intent_count"] = 99
    storage = Storage()
    keeper = Keeper()
    queue = CompletionPublicationQueue(storage, keeper, owner_id="owner-1")
    try:
        receipt = queue.submit(proof, processed_at="2026-09-24T14:00:02+00:00")
        assert not receipt.cancel()
        projected = receipt.result(timeout=3)
        original_intents, original_acks = proof.materialize()
        assert read_exact_completion(storage, original_intents, original_acks,
                                     ordinal=0, keeper=keeper) == projected
        assert projected.row["outcome"] == "completed"
        assert "storage_policy = 'live_market_ssd'" in COMPLETION.ddl()
    finally:
        queue.close()
    restarted = CompletionPublicationQueue(storage, keeper, owner_id="owner-2")
    try:
        replayed = restarted.submit(
            proof, processed_at="2026-09-24T14:00:05+00:00").result(timeout=3)
        assert replayed == projected
        assert len(storage.rows) == 1
    finally:
        restarted.close()


def test_missing_duplicate_or_ambiguous_completion_fails_closed() -> None:
    _, intents, acks = _proof_inputs()
    keeper = Keeper()
    assert read_exact_completion(Storage(), intents, acks, ordinal=0, keeper=keeper) is None
    storage = Storage()
    proof = prepare_completion_proof(intents, acks, ordinal=0)
    projected = project_completion(intents, acks, ordinal=0,
                                   processed_at="2026-09-24T14:00:02+00:00",
                                   keeper_owner_id="owner-1", keeper_epoch=1)
    storage.rows = [dict(projected.row), dict(projected.row)]
    with pytest.raises(ValueError, match="duplicate"):
        read_exact_completion(storage, intents, acks, ordinal=0, keeper=keeper)
    storage = Storage()
    storage.fail_after_insert = True
    queue = CompletionPublicationQueue(storage, keeper, owner_id="owner-1")
    try:
        with pytest.raises(RuntimeError, match="ambiguous"):
            queue.submit(proof, processed_at="2026-09-24T14:00:02+00:00").result(timeout=3)
        with pytest.raises(RuntimeError, match="unavailable"):
            queue.submit(proof, processed_at="2026-09-24T14:00:03+00:00")
    finally:
        queue.close()


def test_stale_keeper_owner_and_unattested_row_fail_closed() -> None:
    _, intents, acks = _proof_inputs()
    keeper = Keeper()
    storage = Storage()
    projected = project_completion(
        intents, acks, ordinal=0, processed_at="2026-09-24T14:00:02+00:00",
        keeper_owner_id="owner-1", keeper_epoch=1)
    storage.rows.append(dict(projected.row))
    with pytest.raises(ValueError, match="Keeper attestation"):
        read_exact_completion(storage, intents, acks, ordinal=0, keeper=keeper)
    storage.rows.clear()
    keeper.lost_before_attest = True
    proof = prepare_completion_proof(intents, acks, ordinal=0)
    queue = CompletionPublicationQueue(storage, keeper, owner_id="owner-1")
    try:
        with pytest.raises(RuntimeError, match="stale Keeper owner"):
            queue.submit(proof, processed_at="2026-09-24T14:00:02+00:00").result(timeout=3)
        assert len(storage.rows) == 1
        with pytest.raises(ValueError, match="Keeper attestation"):
            read_exact_completion(storage, intents, acks, ordinal=0, keeper=keeper)
    finally:
        queue.close()
