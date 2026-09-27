from __future__ import annotations

from datetime import date
from threading import Event

import pytest

from src.trading_runtime.arte_activation_insert_dispatch import (
    ActivationInsertDispatch, activation_insert_proof,
)
from src.trading_runtime.arte_activation_projection import (
    project_activation, strategy_one_activation_run_id,
)
from src.trading_runtime.arte_registered_activation_writer import (
    RegisteredActivationQueueFull, RegisteredActivationWriter,
)
from test_keeper_ownership import _Client, _Store
from tests.test_arte_activation_projection import _MemoryClient, _delivery


SESSION = date(2026, 8, 21)
RUN = strategy_one_activation_run_id(SESSION, mode="paper", run_plan_id="plan-1")


class _BlockingClient(_MemoryClient):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.fail = fail

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        if query_id is not None:
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("test INSERT was not released")
            if self.fail:
                raise TimeoutError("ClickHouse response lost")
        return super().execute(sql)


def _writer(client: _BlockingClient, *, capacity: int = 1):
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_run(RUN, has_ch_rows=False)
    writer = RegisteredActivationWriter(
        client, dispatch, session_date=SESSION, mode="paper",
        run_plan_ids=("plan-1",), capacity=capacity,
        preflight=lambda _: None)
    return writer, dispatch


def test_submission_is_nonblocking_and_receipt_waits_for_clickhouse() -> None:
    client = _BlockingClient()
    writer, dispatch = _writer(client)
    projected = project_activation(_delivery())
    try:
        receipt = writer.submit(projected, owner_id="worker-1")
        assert client.entered.wait(5)
        assert not receipt.done()
        second_delivery = _delivery()
        second_delivery["delivery_id"] = "plan-1:event-2"
        second_delivery["ticker"] = "OTHER"
        second_delivery["occurrence"]["ticker"] = "OTHER"
        with pytest.raises(RegisteredActivationQueueFull):
            writer.submit(project_activation(second_delivery), owner_id="worker-1")
        client.release.set()
        parent_hash = receipt.result(timeout=5)
        dispatch.close_for_cold(RUN)
        dispatch.assert_cold_receipts(RUN, {
            "plan-1:event-1": activation_insert_proof(
                "plan-1:event-1", parent_hash)})
    finally:
        client.release.set()
        writer.close(timeout_seconds=5)


def test_lost_response_makes_writer_fatal_and_cold_fence_impossible() -> None:
    client = _BlockingClient(fail=True)
    writer, dispatch = _writer(client)
    receipt = writer.submit(project_activation(_delivery()), owner_id="worker-1")
    assert client.entered.wait(5)
    client.release.set()
    with pytest.raises(TimeoutError, match="response lost"):
        receipt.result(timeout=5)
    with pytest.raises(RuntimeError, match="unavailable"):
        writer.submit(project_activation(_delivery()), owner_id="worker-1")
    with pytest.raises(RuntimeError, match="reconciliation"):
        writer.close(timeout_seconds=5)
    with pytest.raises(RuntimeError, match="unresolved"):
        dispatch.close_for_cold(RUN)
