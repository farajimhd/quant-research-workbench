from __future__ import annotations

from datetime import date
from threading import Event

import pytest

from src.backend.signal_dispatch_insert_dispatch import (
    SignalDispatchInsertDispatch, dispatch_run_id,
)
from src.backend.signal_dispatch_registered_writer import (
    RegisteredDispatchCursorWriter,
)
import src.backend.signal_dispatch_registered_writer as writer_module
from src.backend.signal_dispatch_typed_cursor import project_dispatch_ack
from src.backend.signal_dispatch_typed_cursor import project_dispatch_intents
from src.backend.live_activation_dispatch_admission import (
    AdmissionBatch, TypedActivationDispatchAdmission,
)
from src.trading_runtime.arte_activation_insert_dispatch import (
    ActivationInsertDispatch, activation_insert_proof,
)
from src.trading_runtime.arte_activation_projection import (
    strategy_one_activation_run_id,
)
from src.trading_runtime.arte_registered_activation_writer import (
    RegisteredActivationWriter,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_keeper_ownership import _Client, _Store
from tests.test_signal_dispatch_registered_publication import _MemoryClient
from tests.test_arte_registered_activation_writer import _BlockingClient as _ActivationClient
from tests.test_signal_dispatch_typed_cursor import (
    ACTIVATION_HASH, _input, _intents,
)


RUN = dispatch_run_id("2026-09-24", "approved-revision-1")


class _BlockingClient(_MemoryClient):
    def __init__(self, *, lose_table: str = "") -> None:
        super().__init__(lose_table=lose_table)
        self.entered = Event()
        self.release = Event()

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        if query_id is not None:
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("test dispatch INSERT was not released")
        return super().execute(sql, query_id=query_id)


def _writer(client, verifier):
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_session(RUN, has_ch_rows=False)
    writer = RegisteredDispatchCursorWriter(
        client, dispatch, session_key="2026-09-24",
        configuration_revision_id="approved-revision-1",
        source_commit_verifier=verifier, preflight=lambda _: None)
    return writer, dispatch


def _packets():
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [{
        "delivery_id": delivery["delivery_id"],
        "ack_kind": "activation_durable",
        "activation_receipt_hash": ACTIVATION_HASH,
    }], acknowledged_at="2026-09-24T14:00:01+00:00")
    return intents, acks


def test_default_registered_writer_preflight_uses_installed_v2_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_session(RUN, has_ch_rows=False)
    checked = []
    monkeypatch.setattr(writer_module, "staged_live_signal_storage_preflight",
                        lambda client: checked.append(("layout", client)))
    monkeypatch.setattr(writer_module, "fixed_backtest_v2_preflight",
                        lambda client: checked.append(("v2", client)))
    client = _MemoryClient()
    writer = RegisteredDispatchCursorWriter(
        client, dispatch, session_key="2026-09-24",
        configuration_revision_id="approved-revision-1",
        source_commit_verifier=lambda _: None)
    try:
        assert checked == [("layout", client), ("v2", client)]
    finally:
        writer.close(timeout_seconds=5)


def test_writer_receipt_waits_for_insert_and_ack_extends_intent() -> None:
    intents, acks = _packets()
    client = _BlockingClient()
    verified = []
    writer, dispatch = _writer(client, verified.append)
    try:
        with pytest.raises(ValueError, match="predecessor"):
            writer.submit_ack(acks)
        intent_receipt = writer.submit_intents(intents)
        assert client.entered.wait(5)
        assert not intent_receipt.done()
        client.release.set()
        assert intent_receipt.result(timeout=5) == intents["commit"]["content_hash"]
        assert verified == [intents]
        ack_receipt = writer.submit_ack(acks)
        assert ack_receipt.result(timeout=5) == acks["commit"]["content_hash"]
        dispatch.close_for_cold(RUN)
        dispatch.assert_cold_receipts(RUN, {1: (
            intents["commit"]["content_hash"],
            acks["commit"]["content_hash"])})
    finally:
        client.release.set()
        writer.close(timeout_seconds=5)


def test_source_proof_failure_stops_writer_before_any_insert() -> None:
    intents, _ = _packets()
    client = _MemoryClient()
    def reject(_):
        raise RuntimeError("source Keeper proof is absent")
    writer, dispatch = _writer(client, reject)
    receipt = writer.submit_intents(intents)
    with pytest.raises(RuntimeError, match="source Keeper proof is absent"):
        receipt.result(timeout=5)
    assert all(not rows for rows in client.rows.values())
    with pytest.raises(RuntimeError, match="reconciliation"):
        writer.close(timeout_seconds=5)
    # No INSERT was ever registered, so the empty gate is still closable;
    # the failed source proof nevertheless prevented live admission.
    dispatch.close_for_cold(RUN)


def test_lost_ack_response_fails_writer_and_cold_fence() -> None:
    intents, acks = _packets()
    client = _MemoryClient()
    writer, dispatch = _writer(client, lambda _: None)
    assert writer.submit_intents(intents).result(timeout=5)
    client.lose_table = "signal_dispatch_ack_commit_typed_v1"
    with pytest.raises(TimeoutError, match="lost dispatch INSERT response"):
        writer.submit_ack(acks).result(timeout=5)
    with pytest.raises(RuntimeError, match="reconciliation"):
        writer.close(timeout_seconds=5)
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)


def test_registered_intent_activation_ack_chain_admits_after_both_receipts() -> None:
    occurrence, delivery = _input()
    occurrence["evidence"] = {"market.last_price": 3.83}
    occurrence["field_evidence"] = {}
    delivery["occurrence"] = occurrence
    class Authority:
        def read_exact(self, event_id):
            return occurrence if event_id == delivery["event_id"] else None
    intents = project_dispatch_intents(
        [delivery], session_key="2026-09-24", source_batch_sequence=1,
        source_cursor_commit_hash="b" * 64,
        configuration_revision_id="approved-revision-1",
        occurrence_authority=Authority())
    cursor_client = _MemoryClient()
    cursor_writer, cursor_dispatch = _writer(cursor_client, lambda _: None)
    activation_client = _ActivationClient()
    activation_client.release.set()
    activation_dispatch = ActivationInsertDispatch(
        _Client(_Store(), 11), strategy_one=True)
    activation_run = strategy_one_activation_run_id(
        date(2026, 9, 24),
        mode="paper", run_plan_id="plan-1")
    activation_dispatch.initialize_new_run(activation_run, has_ch_rows=False)
    activation_writer = RegisteredActivationWriter(
        activation_client, activation_dispatch,
        session_date=date(2026, 9, 24),
        mode="paper", run_plan_ids=("plan-1",), preflight=lambda _: None)
    admitted: list[dict] = []
    lane = TypedActivationDispatchAdmission(
        activation_writer, cursor_writer, admitted.extend)
    try:
        assert cursor_writer.submit_intents(intents).result(timeout=5) == (
            intents["commit"]["content_hash"])
        result = lane.submit(AdmissionBatch(
            intents, (delivery,), "worker-1", "2026-09-24T14:00:01+00:00"))
        assert result.result(timeout=5)
        assert [row["delivery_id"] for row in admitted] == [delivery["delivery_id"]]
        cursor_dispatch.close_for_cold(RUN)
        activation_dispatch.close_for_cold(activation_run)
        from src.trading_runtime.arte_strategy_one_activation_schema import (
            strategy_one_activation_table,
        )
        parent_hash = activation_client.rows[
            strategy_one_activation_table("trading_activation_v1")][0]["content_hash"]
        activation_dispatch.assert_cold_receipts(activation_run, {
            delivery["delivery_id"]: activation_insert_proof(
                delivery["delivery_id"], parent_hash)})
    finally:
        lane.close(timeout=5)
        activation_writer.close(timeout_seconds=5)
        cursor_writer.close(timeout_seconds=5)
