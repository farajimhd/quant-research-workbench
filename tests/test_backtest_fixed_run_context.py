"""Fixed Backtest run-context publication stays outside the active launch path."""
from __future__ import annotations

import pytest

from src.backend import backtest_fixed_run_context as subject


RUN = "00000000-0000-0000-0000-000000000a01"
CONTEXT = {"run_id": RUN, "mode": "backtest", "account_ids": ("DU1",)}
RUN_ROW = {
    "run_id": RUN, "run_month": "2026-08-01", "mode": "backtest",
    "evaluation_interval_ms": 100, "session_date": "2026-08-18",
    "configuration_hash": "a" * 64, "code_hash": "b" * 64,
    "market_plan_token": "market-token",
    "started_at": "2026-08-18T08:00:00+00:00",
}
CONFIG = {
    "strategy_id": "S", "strategy_revision": 1, "anchor_date": "2026-08-18",
    "run_plan_id": "plan-1", "safety_supervisor_enabled": True,
    "checkpoint_interval_events": 100, "write_progress_checkpoints": True,
}


def test_strict_publication_cold_verifies_two_clients_and_releases(monkeypatch):
    calls = []
    read, terminal = object(), object()
    class Barrier:
        def verify_run_context_receipt(self, client):
            calls.append(("receipt", client))
            return CONTEXT
        def release(self):
            calls.append("release")
    class Dispatch:
        def initialize_new_run(self, run_id):
            calls.append(("gate", run_id))
        def acquire_cold_barrier(self, run_id):
            calls.append(("cold", run_id))
            return Barrier()
    monkeypatch.setattr(subject, "TypedInsertDispatch", Dispatch)
    dispatch = Dispatch()
    class Writer:
        typed_insert_strict = True
        typed_insert_dispatch = dispatch
    writer = Writer()
    monkeypatch.setattr(subject, "publish_typed_run",
                        lambda client, run: calls.append(("parent", client, run)))
    monkeypatch.setattr(subject, "publish_typed_run_context",
                        lambda client, **kw: calls.append(("context", client, kw)))
    run = RUN_ROW
    assert subject.publish_fixed_run_context(
        writer, read, terminal, dispatch, run=run, config=CONFIG,
        account_ids=("DU1",)) == CONTEXT
    assert calls == [
        ("gate", RUN), ("parent", writer, run),
        ("context", writer, {"run_id": RUN, "config": CONFIG,
                              "account_ids": ("DU1",)}),
        ("cold", RUN), ("receipt", read), ("receipt", terminal), "release",
    ]


def test_cold_receipt_failure_never_returns_context_and_releases(monkeypatch):
    released = []
    read, terminal = object(), object()
    class Barrier:
        def verify_run_context_receipt(self, client):
            if client is terminal:
                raise RuntimeError("Keeper receipt differs")
            return CONTEXT
        def release(self):
            released.append(True)
    class Dispatch:
        def acquire_cold_barrier(self, run_id):
            assert run_id == RUN
            return Barrier()
    monkeypatch.setattr(subject, "TypedInsertDispatch", Dispatch)
    with pytest.raises(RuntimeError, match="receipt differs"):
        subject.verify_fixed_run_context(Dispatch(), read, terminal, run_id=RUN)
    assert released == [True]


def test_publication_rejects_unfenced_writer_before_creating_run(monkeypatch):
    class Dispatch:
        def initialize_new_run(self, _run_id):
            raise AssertionError("must not create gate")
    monkeypatch.setattr(subject, "TypedInsertDispatch", Dispatch)
    with pytest.raises(ValueError, match="strict typed"):
        subject.publish_fixed_run_context(
            object(), object(), object(), Dispatch(),
            run={"run_id": RUN, "mode": "backtest"}, config={},
            account_ids=("DU1",))


@pytest.mark.parametrize("run,config,accounts", [
    ({**RUN_ROW, "evaluation_interval_ms": None}, CONFIG, ("DU1",)),
    ({**RUN_ROW, "code_hash": "invalid"}, CONFIG, ("DU1",)),
    ({**RUN_ROW, "run_month": "2026-09-01"}, CONFIG, ("DU1",)),
    (RUN_ROW, {**CONFIG, "checkpoint_interval_events": 0}, ("DU1",)),
    (RUN_ROW, {**CONFIG, "run_plan_id": "[1]"}, ("DU1",)),
    (RUN_ROW, CONFIG, ("DU1", "DU1")),
])
def test_invalid_local_payload_never_creates_keeper_gate(
    monkeypatch, run, config, accounts,
):
    created = []
    class Dispatch:
        def initialize_new_run(self, run_id):
            created.append(run_id)
    monkeypatch.setattr(subject, "TypedInsertDispatch", Dispatch)
    dispatch = Dispatch()
    class Writer:
        typed_insert_strict = True
        typed_insert_dispatch = dispatch
    with pytest.raises(ValueError):
        subject.publish_fixed_run_context(
            Writer(), object(), object(), dispatch, run=run,
            config=config, account_ids=accounts)
    assert created == []
