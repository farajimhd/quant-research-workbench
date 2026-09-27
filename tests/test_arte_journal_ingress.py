from concurrent.futures import Future
from datetime import date, datetime, timezone
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import pytest

from src.trading_runtime.arte_journal_ingress import (
    JournalIngressFull, TypedJournalIngress,
)
from src.trading_runtime.arte_journal_writer import JournalQueueFull, TypedJournalBatch
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
ZERO = "00000000-0000-0000-0000-000000000000"


def record(sequence, payload=None):
    return JournalRecord(str(uuid4()), "run-1", sequence, AT, AT,
                         "execution", "fill", f"exec-{sequence}", "DU1",
                         payload if payload is not None else {"nested": {"value": 1}})


def batch(source, identity):
    return TypedJournalBatch(
        source.run_id, date(2026, 8, 1), identity["attempt_id"],
        identity["batch_id"], identity["prior_batch_id"],
        source.sequence, source.sequence, identity["source_cursor"],
        "running", (),
    )


class Writer:
    run_id = "run-1"

    def __init__(self):
        self.receipts = []
        self.batches = []

    def submit(self, batch):
        receipt = Future()
        self.batches.append(batch)
        self.receipts.append(receipt)
        return receipt


def test_ingress_snapshots_payload_and_confirms_only_after_writer_receipt():
    writer = Writer()
    entered = Event()
    release = Event()
    observed = []

    def projector(source, **identity):
        entered.set()
        release.wait(2)
        observed.append((source.payload["nested"]["value"], identity))
        return batch(source, identity)

    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO, projector=projector)
    source = record(1)
    receipt = ingress.submit(source, source_cursor="bar:1")
    source.payload["nested"]["value"] = 2
    assert entered.wait(2)
    assert not receipt.done()
    release.set()
    deadline = Event()
    def finish():
        ingress.close()
        deadline.set()
    closer = Thread(target=finish)
    closer.start()
    assert not deadline.wait(0.05)
    writer.receipts[0].set_result("commit-1")
    closer.join(2)
    assert deadline.is_set()
    assert receipt.result() == "commit-1"
    assert observed[0][0] == 1
    assert observed[0][1]["source_cursor"] == "bar:1"


def test_ingress_rejects_noncontiguous_sequence_and_full_queue_without_loss():
    writer = Writer()
    entered = Event()
    release = Event()

    def projector(source, **identity):
        entered.set()
        release.wait(2)
        return batch(source, identity)

    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO, capacity=1, projector=projector)
    first = ingress.submit(record(1), source_cursor="bar:1")
    assert entered.wait(2)
    second = ingress.submit(record(2), source_cursor="bar:2")
    with pytest.raises(JournalIngressFull):
        ingress.submit(record(3), source_cursor="bar:3")
    with pytest.raises(ValueError, match="contiguous"):
        ingress.submit(record(4), source_cursor="bar:4")
    release.set()
    closer = Thread(target=ingress.close)
    closer.start()
    deadline = monotonic() + 2
    while len(writer.receipts) < 2 and monotonic() < deadline:
        assert closer.is_alive()
        Event().wait(0.01)
    assert len(writer.receipts) == 2
    writer.receipts[0].set_result("commit-1")
    writer.receipts[1].set_result("commit-2")
    closer.join(2)
    assert first.result() == "commit-1"
    assert second.result() == "commit-2"


def test_ingress_projection_failure_fails_queued_records():
    writer = Writer()
    entered = Event()
    release = Event()

    def projector(_source, **_identity):
        entered.set()
        release.wait(2)
        raise ValueError("unmodeled source field")

    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO, projector=projector)
    first = ingress.submit(record(1), source_cursor="bar:1")
    assert entered.wait(2)
    second = ingress.submit(record(2), source_cursor="bar:2")
    release.set()
    with pytest.raises(RuntimeError, match="did not drain"):
        ingress.close()
    with pytest.raises(ValueError, match="unmodeled"):
        first.result()
    with pytest.raises(ValueError, match="unmodeled"):
        second.result()
    assert writer.batches == []


def test_ingress_projects_real_normalized_commission_without_disk_or_clickhouse():
    class ImmediateWriter(Writer):
        def submit(self, projected):
            receipt = super().submit(projected)
            receipt.set_result(projected.batch_id)
            return receipt

    writer = ImmediateWriter()
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO)
    fee = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "execution", "commission", "exec-1", "DU1",
        {"execution_id": "exec-1", "commission": 1.25,
         "currency": "USD", "status": "final",
         "time_authority": "execution"},
    )
    receipt = ingress.submit(fee, source_cursor="bar:1")
    ingress.close()
    assert receipt.result() == writer.batches[0].batch_id
    assert writer.batches[0].commissions[0]["commission"] == "1.2500000000"


def test_cancelled_consumer_receipt_does_not_cancel_durable_publication():
    class ImmediateWriter(Writer):
        def submit(self, projected):
            receipt = super().submit(projected)
            receipt.set_result(projected.batch_id)
            return receipt

    writer = ImmediateWriter()
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO, projector=lambda source, **identity:
        batch(source, identity))
    receipt = ingress.submit(record(1), source_cursor="bar:1")
    receipt.cancel()
    ingress.close()
    assert receipt.cancelled()
    assert len(writer.batches) == 1


def test_slow_payload_snapshot_does_not_hold_admission_lock():
    entered = Event()
    release = Event()
    failures = []

    class SlowPayload(dict):
        def __deepcopy__(self, memo):
            entered.set()
            release.wait(2)
            return dict(self)

    ingress = TypedJournalIngress(
        Writer(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO)

    def submit_slow():
        try:
            ingress.submit(record(1, SlowPayload()), source_cursor="bar:1")
        except RuntimeError as exc:
            failures.append(str(exc))

    submitter = Thread(target=submit_slow)
    submitter.start()
    try:
        assert entered.wait(2)
        # Control-plane shutdown must not wait for another producer's copy.
        closer = Thread(target=ingress.close)
        closer.start()
        closer.join(0.5)
        assert not closer.is_alive()
    finally:
        release.set()
        submitter.join(2)
    assert failures == ["Typed journal ingress is unavailable"]


def test_external_writer_contention_retries_without_a_local_receipt():
    available = Event()
    attempted = Event()

    class ExternallyFullWriter(Writer):
        def submit(self, projected):
            attempted.set()
            if not available.is_set():
                raise JournalQueueFull("other producer owns the writer queue")
            receipt = super().submit(projected)
            receipt.set_result(projected.batch_id)
            return receipt

    writer = ExternallyFullWriter()
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projector=lambda source, **identity: batch(source, identity))
    receipt = ingress.submit(record(1), source_cursor="bar:1")
    assert attempted.wait(2)
    assert not receipt.done()
    available.set()
    ingress.close()
    assert receipt.result() == writer.batches[0].batch_id


def test_live_v4_ingress_uses_explicit_base_writer_and_rejects_special_families():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit(self, _projected):
            raise AssertionError("Live V4 must not use the legacy writer method")

        def submit_base_v4(self, projected):
            receipt = Writer.submit(self, projected)
            receipt.set_result(projected.batch_id)
            return receipt

    writer = LiveWriter()
    config = {"mode": "live"}
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live", "expected_config": config},
    )
    with pytest.raises(ValueError, match="specialized typed source"):
        ingress.submit(record(1), source_cursor="live:1")
    with pytest.raises(ValueError, match="recovery anchor"):
        ingress.submit(JournalRecord(
            str(uuid4()), "run-1", 1, AT, AT, "lifecycle", "run", "run-1", "",
            {"status": "completed", "processed_events": 0},
        ), source_cursor="live:1")
    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT, "lifecycle", "run", "run-1", "",
        {"status": "running", "config": config},
    )
    receipt = ingress.submit(source, source_cursor="live:1")
    ingress.close()
    assert receipt.result() == writer.batches[0].batch_id
    assert writer.batches[0].run_transitions[0]["status"] == "running"


def test_live_v4_ingress_requires_matching_pinned_mode():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "paper"

        def submit_base_v4(self, projected):
            raise AssertionError("Mode mismatch must fail before publication")

    with pytest.raises(ValueError, match="pinned mode"):
        TypedJournalIngress(
            LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
            first_sequence=1, prior_batch_id=ZERO,
            projection_context={"expected_mode": "live"},
        )
