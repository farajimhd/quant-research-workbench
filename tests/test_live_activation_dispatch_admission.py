from __future__ import annotations

from concurrent.futures import Future
from threading import Event

import pytest

from src.backend.live_activation_dispatch_admission import (
    AdmissionBatch, TypedActivationDispatchAdmission,
)
from src.backend.signal_dispatch_typed_cursor import project_dispatch_intents
from tests.test_signal_stream_typed_publication import _batch


class Authority:
    def __init__(self, occurrence):
        self.occurrence = occurrence

    def read_exact(self, event_id):
        return self.occurrence if event_id == self.occurrence["event_id"] else None


class FakeActivationWriter:
    def __init__(self):
        self.receipt = Future()
        self.submissions = []

    def submit(self, projected, *, owner_id):
        self.submissions.append((projected, owner_id))
        return self.receipt


class FakeAckWriter:
    def __init__(self):
        self.receipt = Future()
        self.submissions = []
        self.submitted = Event()

    def submit_ack(self, projected):
        self.submissions.append(projected)
        self.submitted.set()
        return self.receipt


def _admission_batch():
    occurrence = _batch().occurrences[0]
    delivery = {
        "delivery_id": f"plan-1:{occurrence['event_id']}",
        "run_plan_id": "plan-1", "profile_id": "profile-1", "book_id": "default",
        "ticker": occurrence["ticker"], "signal_stream_id": occurrence["signal_stream_id"],
        "event_id": occurrence["event_id"], "event_time": occurrence["effective_at"],
        "occurrence": occurrence,
    }
    intents = project_dispatch_intents(
        [delivery], session_key="2026-09-24", source_batch_sequence=1,
        source_cursor_commit_hash="b" * 64,
        configuration_revision_id="approved-1",
        occurrence_authority=Authority(occurrence))
    return AdmissionBatch(intents, (delivery,), "owner-1",
                          "2026-09-24T14:00:01+00:00")


def test_nonblocking_submit_promotes_only_after_two_durable_receipts() -> None:
    activation = FakeActivationWriter()
    ack = FakeAckWriter()
    admitted = []
    lane = TypedActivationDispatchAdmission(activation, ack, admitted.extend)
    try:
        receipt = lane.submit(_admission_batch())
        assert not receipt.done()
        assert admitted == []
        activation.receipt.set_result("c" * 64)
        # ACK publisher receives a projected normalized fence, but promotion
        # remains withheld until its durable receipt is confirmed.
        assert ack.submitted.wait(2)
        assert len(ack.submissions) == 1
        assert admitted == []
        ack.receipt.set_result(ack.submissions[0]["commit"]["content_hash"])
        assert len(receipt.result(timeout=3)) == 64
        assert len(admitted) == 1
        assert activation.submissions[0][1] == "owner-1"
    finally:
        lane.close()


def test_activation_failure_never_publishes_ack_or_promotes() -> None:
    activation = FakeActivationWriter()
    ack = FakeAckWriter()
    admitted = []
    lane = TypedActivationDispatchAdmission(activation, ack, admitted.extend)
    try:
        receipt = lane.submit(_admission_batch())
        activation.receipt.set_exception(RuntimeError("activation uncertain"))
        with pytest.raises(RuntimeError, match="activation uncertain"):
            receipt.result(timeout=3)
        assert not ack.submissions and not admitted
        with pytest.raises(RuntimeError, match="unavailable"):
            lane.submit(_admission_batch())
    finally:
        lane.close()


def test_ack_conflict_never_promotes() -> None:
    activation = FakeActivationWriter()
    ack = FakeAckWriter()
    admitted = []
    lane = TypedActivationDispatchAdmission(activation, ack, admitted.extend)
    try:
        receipt = lane.submit(_admission_batch())
        activation.receipt.set_result("c" * 64)
        ack.receipt.set_result("d" * 64)
        with pytest.raises(ValueError, match="ACK receipt differs"):
            receipt.result(timeout=3)
        assert admitted == []
    finally:
        lane.close()
