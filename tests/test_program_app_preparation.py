"""Native preparation composition; each authority has separate integration tests."""
import asyncio
from inspect import signature, Parameter
from types import SimpleNamespace

import pytest

from src.backend import program_app_preparation as flow
from src.backend import program_configuration_selection_service as selection
from src.backend.replay_run_service import ReplayRunCapacityError


@pytest.mark.parametrize("failure,cleanup_failure", [(None, None), ("signals", None),
    ("signals", "runtime"), ("signals", "journal")])
def test_preparation_orders_existing_gates_and_closes_adopted_failure(monkeypatch, failure, cleanup_failure):
    stages = []
    controller = SimpleNamespace(_runtime=None, _journal=None, _task=None,
        _program_feature_cache=object(), _program_launch_plan=None, _program_dispatch_failure=None)
    publisher = object()
    service = object()
    async def review(owned_service, owned):
        assert owned_service is service and owned is controller
        stages.append("review")
    async def prepare(owned, cohorts, **kwargs):
        assert owned is controller
        stages.append("inputs")
        return ("prepared",)
    async def journal(**kwargs):
        stages.append("journal")
        controller._journal = object()
    async def runtime(**kwargs):
        assert kwargs == dict(record_configuration=False, record_lifecycle=False)
        stages.append("runtime")
        async def stop():
            stages.append("runtime-close")
            if cleanup_failure == "runtime": raise RuntimeError("runtime close failed")
        controller._runtime = SimpleNamespace(close_background_tasks=stop)
    async def signals(actual, **kwargs):
        assert actual is publisher
        stages.append("signals")
        if failure: raise RuntimeError("signals failed")
    async def bind(owned, work, **kwargs):
        assert owned is controller and work == ("prepared",)
        stages.append("bind")
    async def stage(owned_service, owned):
        assert owned_service is service and owned is controller
        stages.append("stage")
        return owned
    async def close():
        stages.append("close")
        if cleanup_failure == "journal": raise RuntimeError("journal close failed")
    controller.attach_prepared_program_journal = journal
    controller._initialize_runtime = runtime
    controller.bootstrap_program_signals = signals
    controller._close_fixed_journal = close
    monkeypatch.setattr(flow, "program_configuration_selection_service",
        lambda: SimpleNamespace(_review_controller=review, stage_controller=stage))
    monkeypatch.setattr(flow, "prepare_program_session_input_plan", prepare)
    monkeypatch.setattr(flow, "bind_prepared_program_launch", bind)
    journal_scope = {name: object() for name, parameter in signature(flow.attach_program_journal).parameters.items()
        if name != "controller" and parameter.default is Parameter.empty}
    async def run():
        kwargs = dict(cohorts=(object(),), scopes=({"source_publisher": publisher},),
            journal_scope=journal_scope, signal_publisher=publisher, signal_scope={},
            checkpoint_connection_factory=lambda: None, checkpoint_concurrency=2)
        if failure:
            if cleanup_failure:
                with pytest.raises(BaseExceptionGroup, match="preparation and cleanup failed") as error:
                    await flow.complete_program_app_preparation(service, controller, **kwargs)
                assert str(error.value.exceptions[0]) == "signals failed"
            else:
                with pytest.raises(RuntimeError, match="signals failed"):
                    await flow.complete_program_app_preparation(service, controller, **kwargs)
            assert stages == ["review", "inputs", "journal", "runtime", "signals", "runtime-close", "close"]
            assert controller._program_dispatch_failure is not None
        else:
            assert await flow.complete_program_app_preparation(service, controller, **kwargs) is controller
            assert stages == ["review", "inputs", "journal", "runtime", "signals", "bind", "stage"]
        assert controller._task is None
    asyncio.run(run())


def test_startup_reservation_blocks_foreign_staging_and_launch(monkeypatch):
    selector = selection.ProgramConfigurationSelectionService(
        approved_selections={"00000000-0000-0000-0000-000000000001": "a" * 64},
        selection_client_factory=lambda: None, release_client_factory=lambda: None,
        policy_client_factory=lambda: None, policy_dispatch=object(), concurrency=1)
    from src.backend import backtest_program_launch as launch
    service, owned, foreign = object(), object(), object()
    async def review(*args): pass
    async def bound(*args): pass
    monkeypatch.setattr(selector, "_review_controller", review)
    monkeypatch.setattr(launch, "require_bound_program_start", bound)
    selector._preparing = True
    selector._preparation_controller = owned
    async def run():
        with pytest.raises(RuntimeError, match="owns another handoff"):
            await selector.stage_controller(service, foreign)
        assert selector._prepared is None
        assert await selector.stage_controller(service, owned) is owned
        with pytest.raises(ReplayRunCapacityError, match="already in progress"):
            await selector.launch_controller(service, owned)
        assert selector._prepared == (service, owned) and selector._launching is None
    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "controller", "funnel", "cancel"])
@pytest.mark.parametrize("recovered", [False, True])
def test_app_startup_handoff_reserves_before_preparation_and_releases_on_failure(monkeypatch, failure, recovered):
    selector = selection.ProgramConfigurationSelectionService(
        approved_selections={"00000000-0000-0000-0000-000000000001": "a" * 64},
        selection_client_factory=lambda: None, release_client_factory=lambda: None,
        policy_client_factory=lambda: None, policy_dispatch=object(), concurrency=1)
    monkeypatch.setattr(selection, "_SERVICE", selector)
    service, definition, controller = object(), object(), object()
    calls = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        kwargs = dict(assignments=(object(),), listing_by_assignment={"assignment": "listing"},
            liquidity_resolution_ms=1000, cohorts=(object(),), scopes=({},), journal_scope={},
            signal_publisher=object(), signal_scope={}, checkpoint_connection_factory=lambda: None,
            checkpoint_concurrency=2, history_post=object())

        async def prepare(actual_service, actual_definition, **actual):
            assert (actual_service, actual_definition) == (service, definition)
            assert actual == {key: kwargs[key] for key in
                ("assignments", "listing_by_assignment", "liquidity_resolution_ms")}
            assert selector._preparing and selector._preparation_controller is None
            calls.append("controller")
            entered.set()
            await release.wait()
            if failure == "controller": raise RuntimeError("controller failed")
            return controller

        async def complete(actual_service, actual_controller, **actual):
            assert (actual_service, actual_controller) == (service, controller)
            assert selector._preparing and selector._preparation_controller is controller
            expected = {key: value for key, value in kwargs.items()
                if key not in ("assignments", "listing_by_assignment", "liquidity_resolution_ms")}
            if recovered: expected["evidence"] = definition
            assert actual == expected
            calls.append("funnel")
            if failure == "funnel": raise RuntimeError("funnel failed")
            # Only the startup reservation is isolated here; the native funnel
            # ordering, failure cleanup and broker path have integration tests.
            selector._prepared = (service, controller)
            return controller

        monkeypatch.setattr(selector, "prepare_recovered_controller" if recovered else "prepare_controller", prepare)
        monkeypatch.setattr(flow, "complete_recovered_program_app_preparation" if recovered else "complete_program_app_preparation", complete)
        prepare_app = selector.prepare_recovered_app_controller if recovered else selector.prepare_app_controller
        pending = asyncio.create_task(prepare_app(service, definition, **kwargs))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(ReplayRunCapacityError, match="already occupied"):
                await prepare_app(service, definition, **kwargs)
            assert calls == ["controller"]
            if failure == "cancel":
                pending.cancel()
            release.set()
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError): await pending
            elif failure:
                with pytest.raises(RuntimeError, match=failure + " failed"): await pending
            else:
                assert await pending is controller
                assert selector._prepared == (service, controller)
                with pytest.raises(ReplayRunCapacityError, match="already occupied"):
                    await prepare_app(service, definition, **kwargs)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError): await pending
        assert not selector._preparing and selector._preparation_controller is None
        if failure: assert selector._prepared is None
        assert calls == (["controller", "funnel"] if failure in (None, "funnel") else ["controller"])
    asyncio.run(run())
