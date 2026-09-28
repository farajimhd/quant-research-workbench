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
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


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


def test_live_v4_entry_ingress_seals_proposal_off_actor_before_receipt():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Entry must not use the base writer method")

        def submit_strategy_one_entry_v4(self, unit):
            receipt = Writer.submit(self, unit)
            receipt.set_result(unit.base.batch_id)
            return receipt

    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    config = {"strategy_id": "early-squeeze-strategy", "strategy_revision": 1}
    source = JournalRecord(
        str(uuid4()), "run-1", 1, intent.event_time, AT,
        "strategy", "strategy_intent", intent.intent_id, "DU1",
        {**intent.payload(), **config},
    )
    writer = LiveWriter()
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live", "expected_config": config},
    )
    receipt = ingress.submit_strategy_one_entry(
        source, proposal=proposal, session_date=session,
        source_cursor="boundary-31000")
    ingress.close()
    assert receipt.result() == writer.batches[0].base.batch_id
    assert writer.batches[0].base.intents[0]["intent_id"] == intent.intent_id
    assert writer.batches[0].entry_evidence[0]["boundary_ms"] == 31_000


def test_live_v5_broker_reply_ingress_uses_worker_and_snapshots_response():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Broker reply must not use the base family")

        def submit_broker_acknowledgement_v5(self, unit):
            receipt = Writer.submit(self, unit)
            receipt.set_result(unit.base.batch_id)
            return receipt

    response = {"order_id": "1001", "order_status": "PreSubmitted",
                "encrypt_message": "1"}
    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "broker", "order_acknowledgement", "1001", "DU1",
        {**response, "order_group_id": "group-1",
         "decision_to_submit_ms": 1.25})
    writer = LiveWriter()
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live"})
    receipt = ingress.submit_broker_acknowledgement_v5(
        source, source_cursor="broker:1001", provider="ibkr_cpapi",
        client_order_id="client-1", order_group_id="group-1",
        intent_id="intent-1", response=response, decision_to_submit_ms=1.25,
        correlation_id="corr-1", causation_id="cause-1")
    response["order_status"] = "Changed"
    source.payload["order_status"] = "Changed"
    ingress.close()
    assert receipt.result() == writer.batches[0].base.batch_id
    assert writer.batches[0].acknowledgement["order_status"] == "PreSubmitted"
    assert writer.batches[0].base.events[0]["correlation_id"] == "corr-1"


def test_live_v5_broker_reply_rejects_generic_ingress():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Broker reply must use its typed source")

        def submit_broker_acknowledgement_v5(self, unit):
            raise AssertionError("Invalid source must not publish")

    ingress = TypedJournalIngress(
        LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live"})
    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "broker", "order_acknowledgement", "1001", "DU1",
        {"order_id": "1001"})
    with pytest.raises(ValueError, match="specialized typed source"):
        ingress.submit(source, source_cursor="broker:1001")
    ingress.close()


def test_live_v5_reply_ingress_reaches_verified_keeper_commit(monkeypatch):
    from src.backend import live_strategy_one_v4_principal as live_principal
    from src.trading_runtime import arte_journal_writer as writer_module
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    from tests.test_live_signal_completion_keeper import FakeKazoo

    keeper_client = FakeKazoo()
    keeper_client.add_listener = lambda listener: None
    keeper_client.remove_listener = lambda listener: None
    keeper_client.stop = lambda: None
    keeper_client.close = lambda: None
    session = ManagedKeeperSession(keeper_client)
    session._on_state("CONNECTED")
    lease = live_principal.LiveV4KeeperLease.acquire(
        session, run_id="run-1", owner_id="live-ack-ingress-test")
    client = attached_v4_client()
    client.live_v4_lease = lease
    monkeypatch.setattr(live_principal, "live_v4_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {
                            "mode": "live", "account_ids": ("DU1",)})
    writer = ArteJournalWriter(
        client, run_id="run-1", journal_profile="live_v4",
        coalesce_batches=False)
    ingress = TypedJournalIngress(
        writer, run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live"})
    response = {"order_id": "1001", "order_status": "PreSubmitted",
                "encrypt_message": "1"}
    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "broker", "order_acknowledgement", "1001", "DU1",
        {**response, "order_group_id": "group-1",
         "decision_to_submit_ms": 1.25})
    try:
        receipt = ingress.submit_broker_acknowledgement_v5(
            source, source_cursor="broker:1001", provider="ibkr_cpapi",
            client_order_id="client-1", order_group_id="group-1",
            intent_id="intent-1", response=response,
            decision_to_submit_ms=1.25,
            correlation_id="corr-1", causation_id="cause-1")
        ingress.close()
        assert receipt.result(timeout=5)
        assert load_verified_v4_prefix(client, "run-1").last_sequence == 1
        assert [row["provider"] for row in client.tables[
            "trading_broker_acknowledgement_v5"]] == ["ibkr_cpapi"]
    finally:
        ingress.close()
        writer.close()
        lease.release()
        session.close()


def test_live_v4_entry_ingress_rejects_changed_proposal_without_publication():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Entry must not use the base writer method")

        def submit_strategy_one_entry_v4(self, unit):
            raise AssertionError("Changed source must fail before publication")

    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    config = {"strategy_id": "early-squeeze-strategy", "strategy_revision": 1}
    source = JournalRecord(
        str(uuid4()), "run-1", 1, intent.event_time, AT,
        "strategy", "strategy_intent", intent.intent_id, "DU1",
        {**intent.payload(), **config},
    )
    ingress = TypedJournalIngress(
        LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live", "expected_config": config},
    )
    from dataclasses import replace
    receipt = ingress.submit_strategy_one_entry(
        source, proposal=replace(proposal, initial_target=13.),
        session_date=session, source_cursor="boundary-31000")
    with pytest.raises(RuntimeError, match="did not drain"):
        ingress.close()
    with pytest.raises(ValueError, match="differs from its typed intent"):
        receipt.result()


def test_live_v4_entry_ingress_commits_and_recovers_normalized_families():
    from src.trading_runtime.arte_journal_commit_v4 import (
        load_verified_v4_prefix, publish_strategy_one_entry_batch_v4,
    )
    from src.trading_runtime.arte_strategy_one_entry_journal import (
        ENTRY_EVIDENCE, load_committed_strategy_one_entry_page,
    )
    from tests.test_arte_journal_commit_v4 import attached_v4_client

    client = attached_v4_client()

    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Entry must retain its numbered evidence")

        def submit_strategy_one_entry_v4(self, unit):
            receipt = Future()
            receipt.set_result(publish_strategy_one_entry_batch_v4(
                client, unit.base, entry_evidence=unit.entry_evidence))
            return receipt

    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    config = {"strategy_id": "early-squeeze-strategy", "strategy_revision": 1}
    source = JournalRecord(
        str(uuid4()), "run-1", 1, intent.event_time, AT,
        "strategy", "strategy_intent", intent.intent_id, "DU1",
        {**intent.payload(), **config},
    )
    ingress = TypedJournalIngress(
        LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={"expected_mode": "live", "expected_config": config},
    )
    receipt = ingress.submit_strategy_one_entry(
        source, proposal=proposal, session_date=session,
        source_cursor="boundary-31000")
    ingress.close()
    assert receipt.result() in {row["batch_id"] for row in client.tables["trading_commit_v4"]}
    assert len(client.tables[ENTRY_EVIDENCE.name]) == 1
    prefix = load_verified_v4_prefix(client, "run-1")
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert len(page.entries) == 1
    assert page.entries[0].proposal == proposal
    assert page.entries[0].intent == intent


def test_live_v4_protection_ingress_commits_numbered_rows_off_actor():
    from src.trading_runtime.arte_journal_commit_v4 import (
        load_verified_v4_prefix, publish_protection_change_batch_v4,
    )
    from src.backend.backtest_protection_change_v3 import TABLES
    from tests.test_arte_journal_commit_v4 import attached_v4_client

    client = attached_v4_client()

    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Protection must retain its numbered rows")

        def submit_protection_change_v4(self, unit):
            receipt = Future()
            receipt.set_result(publish_protection_change_batch_v4(
                client, unit.base, change=unit.change,
                entry_orders=unit.entry_orders))
            return receipt

    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "protection", "protection_change", "broker-1", "DU1",
        {"schema_version": 1, "order_group_id": "group-1",
         "entry_order_ids": ["entry-1", "entry-2"],
         "order_id": "broker-1", "client_order_id": "client-1",
         "kind": "stop", "phase": "effective", "price": 5.75,
         "active": True, "ticker": "AAA", "source_intent_id": "intent-1",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
         "action": "enter_long", "intent_id": "intent-1",
         "correlation_id": "correlation-1", "causation_id": "causation-1"},
    )
    ingress = TypedJournalIngress(
        LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={
            "expected_mode": "live",
            "expected_config": {"strategy_id": "early-squeeze-strategy",
                                "strategy_revision": 1},
        },
    )
    receipt = ingress.submit_protection_change(
        source, source_cursor="boundary-31000")
    source.payload["entry_order_ids"].append("entry-3")
    ingress.close()
    assert receipt.result() in {row["batch_id"] for row in client.tables["trading_commit_v4"]}
    assert len(client.tables[TABLES[0].name]) == 1
    assert len(client.tables[TABLES[1].name]) == 2
    assert load_verified_v4_prefix(client, "run-1").last_sequence == 1


def test_live_v4_protection_ingress_rejects_unpinned_strategy():
    class LiveWriter(Writer):
        journal_profile = "live_v4"
        run_mode = "live"

        def submit_base_v4(self, unit):
            raise AssertionError("Wrong strategy must not be published")

        def submit_protection_change_v4(self, unit):
            raise AssertionError("Wrong strategy must not be published")

    source = JournalRecord(
        str(uuid4()), "run-1", 1, AT, AT,
        "protection", "protection_change", "broker-1", "DU1",
        {"strategy_id": "other", "strategy_revision": 1},
    )
    ingress = TypedJournalIngress(
        LiveWriter(), run_id="run-1", attempt_id=str(uuid4()),
        first_sequence=1, prior_batch_id=ZERO,
        projection_context={
            "expected_mode": "live",
            "expected_config": {"strategy_id": "early-squeeze-strategy",
                                "strategy_revision": 1},
        },
    )
    receipt = ingress.submit_protection_change(
        source, source_cursor="boundary-31000")
    with pytest.raises(RuntimeError, match="did not drain"):
        ingress.close()
    with pytest.raises(ValueError, match="pinned strategy"):
        receipt.result()
