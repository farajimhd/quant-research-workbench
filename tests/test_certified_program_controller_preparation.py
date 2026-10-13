import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend import certified_program_configuration as admission
from src.backend.strategy_configuration_materialization import materialize_program_configuration
from src.backend.replay_run_service import ReplayRunService, RunMode
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.trading_runtime.columnar_strategy_release import restore_release
from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime import strategy_configuration_schema as schema
from tests.test_strategy_configuration_selection import children, prepare
from tests.test_program_strategy_registry import installed
from tests.test_columnar_strategy_release import Reader
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize("failure", [None, "review", "mutated"])
def test_certified_selection_prepares_native_service_controller_without_starting(monkeypatch, tmp_path, failure):
    registration, release_rows, selected = installed(monkeypatch)
    release = restore_release(release_rows)
    policy = PortfolioPolicy(policy_id="central")
    digest = _policy_rows(policy)[0]
    rows = children()
    rows[schema.ACCOUNT.name][0]["policy_hash"] = digest
    packet = prepare(rows, strategy_id=registration.strategy_id, strategy_revision=registration.revision,
        program_release_id=release.header["release_id"], program_release_hash=registration.expected_content_hash)
    revision = materialize_program_configuration(packet, release, {digest: policy})
    definition, _ = definition_and_actor()
    definition = replace(definition, configuration_revision=revision)
    account, = historical_simulated_account_ids(mode=RunMode.BACKTEST, configuration=revision["payload"])
    selected = replace(selected, account_id=account)
    current = {**revision, "content_hash": "c" * 64} if failure == "review" else revision
    async def load(**kwargs):
        if failure == "mutated": revision["selection_hash"] = "d" * 64
        return SimpleNamespace(revision=current, selection=packet, release=release)
    monkeypatch.setattr(admission, "load_certified_program_configuration", load)
    service = ReplayRunService(runtime_root=tmp_path)
    from src.backend import program_configuration_selection_service as app_selection
    clients = []
    def factory():
        client = Reader(release_rows)
        clients.append(client)
        return client
    async def run():
        kwargs = dict(assignments=(selected,), listing_by_assignment={selected.assignment_id: "listing"},
            liquidity_resolution_ms=100, selection_client_factory=lambda: None,
            release_client_factory=factory, policy_client_factory=lambda: None,
            policy_dispatch=object(), concurrency=2)
        selector = app_selection.ProgramConfigurationSelectionService(
            approved_selections={packet.header["configuration_id"]: packet.header["content_hash"]},
            **{key: kwargs[key] for key in ("selection_client_factory", "release_client_factory",
                "policy_client_factory", "policy_dispatch", "concurrency")})
        monkeypatch.setattr(app_selection, "_SERVICE", selector)
        monkeypatch.setattr(app_selection, "load_certified_program_configuration", load)
        preparation = dict(assignments=kwargs["assignments"], listing_by_assignment=kwargs["listing_by_assignment"],
            liquidity_resolution_ms=kwargs["liquidity_resolution_ms"])
        if failure:
            with pytest.raises(ValueError, match="changed since UI review"):
                await selector.prepare_controller(service, definition, **preparation)
        else:
            controller = await selector.prepare_controller(service, definition, **preparation)
            assert controller._program_configuration_selection is packet
            assert controller._program_strategy.program_release.header["content_hash"] == registration.expected_content_hash
            assert controller._runtime is None and controller._task is None and controller.status == "created"
            with pytest.raises(RuntimeError, match="prepared typed launch handoff"):
                await selector.launch_controller(service, controller)
            with pytest.raises(RuntimeError, match="prepared typed launch handoff"):
                await selector.stage_controller(service, controller)
            assert selector._prepared is None
            with pytest.raises(ValueError, match="no bound native launch"):
                await selector.staged_controller(service)
            # Only isolate the slot lifecycle here. The native bound proof's
            # positive/negative execution cases run in the cohort integration.
            from src.backend import backtest_program_launch as handoff
            async def bound(owned):
                assert owned is controller
            with monkeypatch.context() as slot:
                slot.setattr(handoff, "require_bound_program_start", bound)
                assert await selector.stage_controller(service, controller) is controller
                retained = selector._prepared
                assert await selector.stage_controller(service, controller) is controller
                assert selector._prepared is retained
                assert await selector.staged_controller(service) is controller
                from datetime import time
                scope = dict(configuration_revision=revision, sessions=(definition.session_date,),
                    start_time=definition.start_time, end_time=definition.end_time or time(20),
                    initial_cash=definition.initial_cash)
                preflight = await selector.preflight(service, **scope)
                assert preflight["prepared_run_id"] == controller.run_id and preflight["strategy_run_ready"] is True
                assert preflight["execution_interval"] == "100ms"
                with pytest.raises(ValueError, match="prepared session"):
                    await selector.preflight(service, **{**scope, "initial_cash": definition.initial_cash + 1})
                assert not service._runs and controller._task is None
            assert not service._runs and controller._task is None
            revision["selection_hash"] = "f" * 64
            before = len(clients)
            with pytest.raises(ValueError, match="not approved"):
                await selector.prepare_controller(service, definition, **preparation)
            assert len(clients) == before
            with pytest.raises(ValueError, match="not approved"):
                await selector.launch_controller(service, controller)
    asyncio.run(run())
    assert not service._runs and not list(tmp_path.iterdir())
    assert all(client.closed for client in clients)
    if failure: assert not clients
