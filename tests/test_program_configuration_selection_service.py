import asyncio
from types import SimpleNamespace

import pytest
from httpx import AsyncClient, ASGITransport

from src.backend import program_configuration_selection_service as selection_service
from src.backend import trading_configuration_service as configuration
from src.backend import backtest_configuration_option_reader as legacy
from src.backend.strategy_configuration_materialization import materialize_program_configuration
from tests.test_strategy_configuration_materialization import configured


def installed(monkeypatch):
    _, actor, packet, policies = configured()
    revision = materialize_program_configuration(packet, actor.program_release, policies)
    reads = []
    async def load(**kwargs):
        reads.append(kwargs)
        return SimpleNamespace(revision=revision, selection=packet)
    monkeypatch.setattr(selection_service, "load_certified_program_configuration", load)
    monkeypatch.setattr(selection_service, "_SERVICE", None)
    service = selection_service.ProgramConfigurationSelectionService(
        approved_selections={packet.header["configuration_id"]: packet.header["content_hash"]},
        selection_client_factory=lambda: None, release_client_factory=lambda: None,
        policy_client_factory=lambda: None, policy_dispatch=object(), concurrency=2)
    selection_service.install_program_configuration_selection_service(service)
    return service, revision, reads


def test_selection_snapshot_and_current_configuration_boundary_use_approved_pins(monkeypatch):
    service, revision, reads = installed(monkeypatch)
    selected = configuration.backtest_configuration_snapshot(revision["run_plan_id"], candidate_id=revision["revision_id"])
    assert selected["content_hash"] == revision["content_hash"]
    assert selected["available_run_plans"][0]["run_plan_id"] == revision["run_plan_id"]
    assert reads[0]["expected_content_hash"] == revision["selection_hash"]
    selected["payload"]["oms"]["limit_offset_bps"] = 99
    assert configuration.backtest_configuration_snapshot(candidate_id=revision["revision_id"])["payload"]["oms"]["limit_offset_bps"] != 99
    with pytest.raises(ValueError, match="run plan"):
        configuration.backtest_configuration_snapshot("other", candidate_id=revision["revision_id"])


def test_execution_binding_pin_is_forwarded_and_uncertified_snapshot_rejected(monkeypatch):
    original, revision, reads = installed(monkeypatch)
    identity = revision['revision_id'].removeprefix('strategy-configuration:')
    service = selection_service.ProgramConfigurationSelectionService(approved_selections=original.pins,
        approved_execution_bindings={identity: 'b' * 64}, **dict(original.authorities))
    with pytest.raises(ValueError, match='execution binding'):
        asyncio.run(service.snapshot(revision['revision_id']))
    revision['execution_binding_hash'] = 'b' * 64
    result = asyncio.run(service.snapshot(revision['revision_id']))
    assert result['execution_binding_hash'] == reads[-1]['expected_execution_binding_hash'] == 'b' * 64
    with pytest.raises(ValueError, match="not approved"):
        asyncio.run(service.snapshot("strategy-configuration:00000000-0000-0000-0000-000000000063"))
    with pytest.raises(TypeError): service.pins["other"] = "a" * 64


def test_execution_risk_pin_is_independent_explicit_and_forwarded(monkeypatch):
    original, revision, reads = installed(monkeypatch)
    identity = revision['revision_id'].removeprefix('strategy-configuration:')
    service = selection_service.ProgramConfigurationSelectionService(approved_selections=original.pins,
        approved_execution_bindings={identity: 'b' * 64}, approved_execution_risks={identity: 'c' * 64},
        **dict(original.authorities))
    revision['execution_binding_hash'] = 'b' * 64
    with pytest.raises(ValueError, match='execution risk'):
        asyncio.run(service.snapshot(revision['revision_id']))
    revision['execution_risk_hash'] = 'c' * 64
    assert asyncio.run(service.snapshot(revision['revision_id']))['execution_risk_hash'] == 'c' * 64
    assert reads[-1]['expected_execution_risk_hash'] == 'c' * 64


def test_current_http_configuration_options_include_certified_programme(monkeypatch):
    _, revision, reads = installed(monkeypatch)
    monkeypatch.setattr(legacy, "certified_configuration_options", lambda candidate_id: pytest.fail("Unrelated legacy catalogue read"))
    monkeypatch.setattr(configuration, "trading_journal", lambda: pytest.fail("SQLite fallback"))
    from src.backend.app import app
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/trading/backtest/configuration-options",
                                        params={"candidate_id": revision["revision_id"]})
        assert response.status_code == 200
        result = response.json()
        assert result["candidate_id"] == revision["revision_id"]
        assert result["run_plan_id"] == revision["run_plan_id"]
        assert [row["candidate_id"] for row in result["candidates"]] == [revision["revision_id"]]
    asyncio.run(run())
    assert len(reads) == 1


def test_managed_default_options_use_approved_programme_without_legacy_reads(monkeypatch):
    _, revision, _ = installed(monkeypatch)
    monkeypatch.setattr(legacy, 'certified_configuration_options', lambda *_: pytest.fail('Legacy default read'))
    result = configuration.backtest_configuration_options()
    assert result['candidate_id'] == revision['revision_id']
    assert [row['candidate_id'] for row in result['candidates']] == [revision['revision_id']]


def test_explicit_numbered_options_preserve_catalogue_and_include_programme(monkeypatch):
    _, revision, _ = installed(monkeypatch)
    old = dict(revision_id="legacy", revision=1, label="Legacy", content_hash="a" * 64,
               run_plan_id="old", available_run_plans=[])
    calls = []
    def numbered(candidate_id):
        calls.append(candidate_id)
        return old, (old,)
    monkeypatch.setattr(legacy, "certified_configuration_options", numbered)
    result = configuration.backtest_configuration_options('legacy')
    assert calls == ['legacy']
    assert result["candidate_id"] == "legacy"
    assert [row["candidate_id"] for row in result["candidates"]] == ["legacy", revision["revision_id"]]


def test_unapproved_programme_options_fail_without_legacy_reads(monkeypatch):
    _, _, reads = installed(monkeypatch)
    monkeypatch.setattr(legacy, "certified_configuration_options", lambda *_: pytest.fail("legacy read"))
    with pytest.raises(ValueError, match="not approved"):
        configuration.backtest_configuration_options("strategy-configuration:00000000-0000-0000-0000-000000000063")
    assert reads == []


def test_uninstalled_programme_selection_fails_before_any_legacy_read(monkeypatch):
    monkeypatch.setattr(selection_service, "_SERVICE", None)
    monkeypatch.setattr(legacy, "certified_configuration_options", lambda *_: pytest.fail("legacy read"))
    with pytest.raises(ValueError, match="not installed"):
        configuration.backtest_configuration_options("strategy-configuration:missing")


def test_programme_selection_never_enters_legacy_market_preflight(monkeypatch):
    from datetime import date
    from src.backend.replay_run_service import backtest_preflight
    from src.backend import backtest_market_data
    _, revision, _ = installed(monkeypatch)
    monkeypatch.setattr(backtest_market_data, "effective_execution_interval",
        lambda *_: pytest.fail("Programme inferred a legacy execution clock"))
    with pytest.raises(ValueError, match="bound native launch"):
        backtest_preflight(anchor_date=date(2026, 10, 12), session_count=1,
            configuration_revision=revision)


@pytest.mark.parametrize('failure', [None, 'running', 'selection', 'changed_owner'])
def test_recorded_comparison_routes_only_completed_approved_app_owner(monkeypatch, tmp_path, failure):
    from dataclasses import replace
    from src.backend.replay_run_service import ReplayRunService, ReplayRunController
    from tests.test_program_controller_runtime import definition_and_actor
    from src.backend.signal_stream_runtime_service import SignalStreamRuntime
    from src.backend.program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.backend import recorded_program_comparison as reader
    service, revision, _ = installed(monkeypatch)
    _, actor, selection, _ = configured()
    owner = ReplayRunService(runtime_root=tmp_path)
    definition, _ = definition_and_actor()
    controller = ReplayRunController(replace(definition, configuration_revision=revision), runtime_root=tmp_path,
        program_strategy=actor, program_liquidity_resolution_ms=1000)
    controller.status = 'running' if failure == 'running' else 'completed'
    controller._program_configuration_selection = None if failure == 'selection' else selection
    controller._program_signal_runtime = SignalStreamRuntime()
    prefix = V4CommittedPrefix(controller.run_id, 100, 'batch', 'cursor', 'completed', ('batch',))
    packet = RecoveredProgramPolicy(actor, 100, 0, 0, {}, controller.run_id, (),
        program_policy_fingerprint(actor), _RECOVERY_PROOF)
    calls = []
    reference, blocks, result = object(), (object(),), object()
    async def joined(factory, scope, recovered, inputs, source, frozen, **bounds):
        assert scope is prefix and recovered is packet and inputs is blocks and frozen is reference
        assert source is controller._program_signal_runtime and bounds == dict(day=definition.session_date.isoformat(), maximum_measure_rows=100)
        calls.append(True)
        if failure == 'changed_owner': controller._program_signal_runtime = SignalStreamRuntime()
        return result
    monkeypatch.setattr(reader, 'read_recorded_program_session_metrics', joined)
    async def run():
        return await service.read_recorded_comparison(owner, controller, reader_factory=lambda: None,
            prefix=prefix, packet=packet, blocks=blocks, reference=reference, maximum_measure_rows=100)
    if failure:
        with pytest.raises((ValueError, RuntimeError)): asyncio.run(run())
    else:
        assert asyncio.run(run()) is result
    assert calls == ([True] if failure in (None, 'changed_owner') else [])
