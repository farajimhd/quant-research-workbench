"""Composition and custody cleanup; native component proofs have separate tests."""
import asyncio
from datetime import date
from inspect import signature, Parameter
from types import SimpleNamespace
from threading import Event

import pytest

from src.backend import program_app_preparation as flow
from src.backend import backtest_program_journal_bootstrap as journal
from src.backend import program_runtime_recovery as runtime_builder
from src.backend import backtest_v4_run_context as accounts
from tests.test_program_signal_source import source_context_for


@pytest.mark.parametrize("failure", [None, "build", "initialize", "adopt", "bind", "cancel"])
def test_recovered_app_preparation_orders_native_steps_and_closes_owned_failure(monkeypatch, failure):
    calls = []
    release, service, packet = (object() for _ in range(3))
    population = SimpleNamespace(source_revision='recovered-source-origin')
    evidence = SimpleNamespace(financial=SimpleNamespace(portfolio=object(), completed_at=object()))
    controller = SimpleNamespace(_runtime=None, _journal=None,
        _program_strategy=SimpleNamespace(program_release=release),
        run_id='actual-recovered-run',
        definition=SimpleNamespace(mode=SimpleNamespace(value='backtest'), session_date=date(2026, 10, 15),
            configuration_revision={"revision_id": 'recovered-config', "payload": {"strategy": {}}}),
        _program_launch_plan=None, _program_feature_cache=object(), _program_dispatch_failure=None)
    source = SimpleNamespace(source_context=source_context_for(controller, population.source_revision))
    async def review(*args): calls.append("review")
    async def stage(*args): calls.append("stage"); return controller
    monkeypatch.setattr(flow, "program_configuration_selection_service",
        lambda: SimpleNamespace(_review_controller=review, stage_controller=stage))
    monkeypatch.setattr(accounts, "historical_strategy_one_portfolio_profiles", lambda _: ((), ()))
    async def inputs(*args, **kwargs): calls.append("inputs"); return ("work",)
    monkeypatch.setattr(flow, "prepare_program_session_input_plan", inputs)
    assembly = SimpleNamespace(journal=SimpleNamespace(close=lambda: calls.append("journal-close")),
        writer=SimpleNamespace(close=lambda: calls.append("writer-close")))
    anchor = object()
    entered, release_worker = Event(), Event()
    native_signature = signature(journal.assemble_resumed_program_v4_journal)
    def assemble(**kwargs):
        calls.append("assembly")
        if failure == "cancel":
            entered.set()
            assert release_worker.wait(timeout=5)
        return assembly, anchor
    assemble.__signature__ = native_signature
    monkeypatch.setattr(journal, "assemble_resumed_program_v4_journal", assemble)
    async def initialize(**kwargs):
        assert kwargs == {"record_lifecycle": False}
        calls.append("initialize")
        if failure == "initialize": raise RuntimeError("initialize failed")
    async def close_runtime(): calls.append("runtime-close")
    runtime = SimpleNamespace(initialize=initialize, close_background_tasks=close_runtime)
    def build(config, strategy, buffer, **kwargs):
        calls.append("build")
        assert buffer is assembly.journal and kwargs["portfolio_recovery"] is evidence.financial.portfolio
        if failure == "build": raise RuntimeError("build failed")
        return runtime
    monkeypatch.setattr(runtime_builder, "build_recovered_program_runtime", build)
    async def adopt(**kwargs):
        calls.append("adopt")
        assert kwargs["runtime"] is runtime and kwargs["anchor"] is anchor
        if failure == "adopt": raise RuntimeError("adopt failed")
        controller._runtime, controller._journal = runtime, assembly.journal
    async def bind(work, **kwargs):
        assert work == ("work",)
        calls.append("bind")
        if failure == "bind": raise RuntimeError("bind failed")
    async def close_controller(): calls.append("controller-close")
    controller.adopt_program_cold_runtime, controller.bind_recovered_program_session = adopt, bind
    controller._close_fixed_journal = close_controller
    scope = {name: object() for name, parameter in native_signature.parameters.items()
        if parameter.default is Parameter.empty}
    scope.update(recovery_evidence=evidence, release=release,
        broker_contract=SimpleNamespace(config=object()))
    async def run():
        kwargs = dict(evidence=evidence, cohorts=(object(),), scopes=({},), journal_scope=scope,
            source=source, packet=packet, population=population, oms_image=object(), broker_image=object(),
            intent_planner=object(), keeper_session=object(), lease=object(),
            checkpoint_connection_factory=lambda: None, checkpoint_concurrency=2)
        if failure == "cancel":
            pending = asyncio.create_task(flow.complete_recovered_program_app_preparation(service, controller, **kwargs))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                pending.cancel()
                release_worker.set()
                with pytest.raises(asyncio.CancelledError): await pending
            finally:
                release_worker.set()
                if not pending.done():
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError): await pending
        elif failure:
            with pytest.raises(RuntimeError, match=failure + " failed"):
                await flow.complete_recovered_program_app_preparation(service, controller, **kwargs)
        else:
            assert await flow.complete_recovered_program_app_preparation(service, controller, **kwargs) is controller
    asyncio.run(run())
    stages = ["review", "inputs", "assembly", "build", "initialize", "adopt", "bind", "stage"]
    if failure == "cancel":
        assert calls == stages[:4] + ["runtime-close", "writer-close", "journal-close"]
        assert controller._runtime is None and controller._journal is None
    elif failure:
        expected = stages[:stages.index(failure) + 1]
        if failure != "build": expected.append("runtime-close")
        expected += ["controller-close"] if failure == "bind" else ["writer-close", "journal-close"]
        assert calls == expected
    else:
        assert calls == stages
