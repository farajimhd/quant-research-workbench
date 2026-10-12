import asyncio
from dataclasses import replace

import pytest

from src.backend.strategy_configuration_materialization import materialize_program_configuration
from src.backend.backtest_v4_run_context import historical_simulated_account_ids, historical_strategy_one_portfolio_profiles
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime import strategy_configuration_schema as schema
from tests.test_strategy_configuration_selection import children, prepare
from tests.test_program_controller_runtime import definition_and_actor


def configured():
    definition, actor = definition_and_actor()
    release = actor.program_release
    policy = PortfolioPolicy(policy_id="selected", revision=2)
    digest = _policy_rows(policy)[0]
    rows = children()
    rows[schema.ACCOUNT.name][0]["policy_hash"] = digest
    packet = prepare(rows, strategy_id=release.header["strategy_id"],
        strategy_revision=release.header["strategy_number"],
        program_release_id=release.header["release_id"],
        program_release_hash=release.header["content_hash"], oms_limit_offset_bps=2.)
    return definition, actor, packet, {digest: policy}


def test_configuration_binds_existing_catalogue_policies_without_changing_release_sizing():
    _, actor, packet, policies = configured()
    revision = materialize_program_configuration(packet, actor.program_release, policies)
    payload = revision["payload"]
    profiles, groups = historical_strategy_one_portfolio_profiles(payload)
    assert profiles[0].policy == next(iter(policies.values()))
    assert profiles[0].account_key == "research"
    assert groups[0].account_keys == ("research",)
    assert payload["oms"]["limit_offset_bps"] == 2.
    assert payload["strategy"]["parameters"] == {}
    assert payload["run_plan"]["signal_stream_ids"] == ["stream-1"]
    assert payload["market_discovery"]["signal_streams"][0]["inclusion_rule_sets"] == ["up"]
    assert actor.program_release.header["entry_cash"] == 1000
    assert actor.program_release.header["add_cash"] == 200
    assert revision["selection_hash"] == packet.header["content_hash"]
    payload["accounts"]["bindings"][0]["enabled"] = False
    assert materialize_program_configuration(packet, actor.program_release, policies)["payload"]["accounts"]["bindings"][0]["enabled"]


def test_materialized_selection_drives_native_vectorized_signal_stream_resolution():
    from src.backend.signal_stream_runtime_service import SignalStreamRuntime
    from datetime import datetime, timezone
    _, actor, packet, policies = configured()
    revision = materialize_program_configuration(packet, actor.program_release, policies)
    runtime = SignalStreamRuntime()
    runtime._hydrated = True  # Empty synthetic source state; no persistence is consulted.
    stage = runtime.stage_resolve(revision["payload"], [{"ticker": "ABC", "price": 12.,
        "market.last_price@1s": 12., "rule_set:up": True}],
        as_of=datetime(2026, 10, 12, 14, tzinfo=timezone.utc), data_fields_projected=True)
    assert len(stage.occurrences) == 1
    assert stage.occurrences[0]["signal_stream_id"] == "stream-1"
    assert stage.occurrences[0]["ticker"] == "ABC"


@pytest.mark.parametrize("failure", ["missing", "extra", "policy", "release"])
def test_configuration_rejects_unverified_reference_mismatches(failure):
    _, actor, packet, policies = configured()
    release = actor.program_release
    if failure == "missing": policies = {}
    if failure == "extra": policies = {**policies, "c" * 64: next(iter(policies.values()))}
    if failure == "policy": policies = {digest: replace(policy, revision=3) for digest, policy in policies.items()}
    if failure == "release": release = replace(release, header={**release.header, "content_hash": "c" * 64})
    with pytest.raises(ValueError):
        materialize_program_configuration(packet, release, policies)


def test_materialized_selection_initializes_existing_portfolio_oms_and_broker(tmp_path):
    definition, original, packet, policies = configured()
    revision = materialize_program_configuration(packet, original.program_release, policies)
    account, = historical_simulated_account_ids(mode=RunMode.BACKTEST, configuration=revision["payload"])
    assignment = replace(original.assignments()[0], account_id=account)
    actor = AssignedProgramStrategy(original.program_release, (assignment,), {assignment.assignment_id: "listing"})
    controller = ReplayRunController(replace(definition, configuration_revision=revision),
        runtime_root=tmp_path, program_strategy=actor, program_liquidity_resolution_ms=100)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    async def run():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            assert controller._runtime.strategy is actor
            assert controller._runtime.portfolio.states[account].profile.policy == next(iter(policies.values()))
            assert controller._runtime.order_manager is not None
            assert controller._planner.limit_offset_bps == 2.
            assert controller._runtime.broker.config.liquidity_resolution_ms == 100
        finally:
            await controller._runtime.finish()
    asyncio.run(run())
    assert not list(tmp_path.iterdir())
