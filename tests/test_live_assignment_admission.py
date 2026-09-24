from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.backend.live_assignment_admission import AssignmentAdmissionLane
from src.backend.live_strategy_runtime_service import (
    LiveStrategyRuntimeSupervisor, _upsert_runtime_assignments,
)


def _state():
    installed = []
    return ({"strategy": SimpleNamespace(assignments=lambda: installed,
                                          upsert_assignment=installed.append),
             "planner": SimpleNamespace(upsert_instrument=Mock())}, installed)


def _assignment():
    return SimpleNamespace(assignment_id="a-1", conid=1, ticker="ABC",
                           payload=lambda: {"assignment_id": "a-1"})


def _wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_bounded_control_lane_never_blocks_market_consumer_or_admits_before_ack() -> None:
    release = threading.Event()
    started = threading.Event()

    class Publisher:
        async def publish(self, assignment, *, configuration_revision_id):
            started.set()
            await asyncio.to_thread(release.wait)

    lane = AssignmentAdmissionLane(Publisher(), capacity=1)
    state, installed = _state()
    configuration = {"accounts": {"bindings": []}, "assignments": [{"assignment_id": "a-1"}]}
    try:
        with patch("src.backend.live_strategy_runtime_service._assignment", return_value=_assignment()), \
             patch("src.backend.live_strategy_runtime_service.trading_journal") as sqlite:
            _upsert_runtime_assignments(state, configuration, typed_admission=lane,
                                        configuration_revision_id="revision-1")
            assert installed == []
            assert started.wait(1)
            with pytest.raises(ValueError, match="already pending"):
                lane.submit(_assignment(), configuration_revision_id="revision-1")
            _upsert_runtime_assignments(state, configuration, typed_admission=lane,
                                        configuration_revision_id="revision-1")
            assert installed == []
            with pytest.raises(RuntimeError, match="capacity"):
                lane.submit(SimpleNamespace(assignment_id="a-2"),
                            configuration_revision_id="revision-1")
            release.set()
            _wait_until(lambda: lane._pending["a-1"][2].done())
            _upsert_runtime_assignments(state, configuration, typed_admission=lane,
                                        configuration_revision_id="revision-1")
            assert len(installed) == 1
            assert state["planner"].upsert_instrument.call_count == 1
            sqlite.assert_not_called()
    finally:
        release.set()
        lane.close()


def test_failed_publication_is_sticky_and_cannot_admit_or_fallback() -> None:
    class Publisher:
        async def publish(self, assignment, *, configuration_revision_id):
            raise RuntimeError("durable write failed")

    lane = AssignmentAdmissionLane(Publisher())
    state, installed = _state()
    configuration = {"accounts": {"bindings": []}, "assignments": [{"assignment_id": "a-1"}]}
    try:
        with patch("src.backend.live_strategy_runtime_service._assignment", return_value=_assignment()), \
             patch("src.backend.live_strategy_runtime_service.trading_journal") as sqlite:
            _upsert_runtime_assignments(state, configuration, typed_admission=lane,
                                        configuration_revision_id="revision-1")
            _wait_until(lambda: lane._pending["a-1"][2].done())
            with pytest.raises(RuntimeError, match="publication failed"):
                _upsert_runtime_assignments(state, configuration, typed_admission=lane,
                                            configuration_revision_id="revision-1")
            assert installed == []
            sqlite.assert_not_called()
    finally:
        lane.close()


def test_stale_configuration_ack_fails_closed() -> None:
    class Publisher:
        async def publish(self, assignment, *, configuration_revision_id):
            return None

    lane = AssignmentAdmissionLane(Publisher())
    try:
        lane.submit(_assignment(), configuration_revision_id="revision-1")
        _wait_until(lambda: lane._pending["a-1"][2].done())
        with pytest.raises(RuntimeError, match="stale assignment"):
            lane.drain_acknowledged(configuration_revision_id="revision-2")
        with pytest.raises(RuntimeError, match="unavailable"):
            lane.submit(SimpleNamespace(assignment_id="a-2"),
                        configuration_revision_id="revision-2")
    finally:
        lane.close()


def test_orphan_ack_is_sticky_and_cannot_be_republished() -> None:
    class Publisher:
        async def publish(self, assignment, *, configuration_revision_id):
            return None

    lane = AssignmentAdmissionLane(Publisher())
    try:
        lane.submit(_assignment(), configuration_revision_id="revision-1")
        _wait_until(lambda: lane._pending["a-1"][2].done())
        with pytest.raises(RuntimeError, match="orphan assignment"):
            lane.drain_acknowledged(configuration_revision_id="revision-1",
                                    allowed_assignment_ids=set())
        with pytest.raises(RuntimeError, match="unavailable"):
            lane.submit(_assignment(), configuration_revision_id="revision-1")
    finally:
        lane.close()


def test_injected_lane_keeps_supervisor_disabled_before_sqlite_hydration() -> None:
    lane = AssignmentAdmissionLane(SimpleNamespace(publish=Mock()))
    try:
        assert not lane._thread.is_alive()
        supervisor = LiveStrategyRuntimeSupervisor(typed_assignment_admission=lane)
        with patch.object(supervisor, "_hydrate_activations") as hydrate:
            supervisor.start()
        assert supervisor.snapshot()["state"] == "degraded"
        hydrate.assert_not_called()
        assert not lane._thread.is_alive()
        with patch("src.backend.live_strategy_runtime_service.trading_journal") as sqlite:
            with pytest.raises(RuntimeError, match="refusing SQLite fallback"):
                supervisor.submit([{"run_plan_id": "plan-1", "ticker": "ABC"}])
            with pytest.raises(RuntimeError, match="market delivery cutover"):
                supervisor.submit_market_rows([{"ticker": "ABC"}], as_of="2026-08-18T08:00:00Z")
            sqlite.assert_not_called()
        assert supervisor._queue.empty()
        assert supervisor._activations == {}
    finally:
        lane.close()


def test_typed_delivery_authority_cannot_fall_back_without_injected_lane() -> None:
    supervisor = LiveStrategyRuntimeSupervisor()
    with patch.dict("os.environ", {"TRADING_SIGNAL_DELIVERY_AUTHORITY": "typed"}), \
         patch.object(supervisor, "_hydrate_activations") as hydrate, \
         patch("src.backend.live_strategy_runtime_service.trading_journal") as sqlite:
        supervisor.start()
        assert supervisor.snapshot()["state"] == "degraded"
        with pytest.raises(RuntimeError, match="refusing SQLite fallback"):
            supervisor.submit([{"run_plan_id": "plan-1", "ticker": "ABC"}])
        with pytest.raises(RuntimeError, match="market delivery cutover"):
            supervisor.submit_market_rows([{"ticker": "ABC"}], as_of="2026-08-18T08:00:00Z")
        hydrate.assert_not_called()
        sqlite.assert_not_called()
