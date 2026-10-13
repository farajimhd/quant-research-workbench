"""Native preparation composition; each authority has separate integration tests."""
import asyncio
from inspect import signature, Parameter
from types import SimpleNamespace

import pytest

from src.backend import program_app_preparation as flow


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
