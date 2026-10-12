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
    with pytest.raises(ValueError, match="not approved"):
        asyncio.run(service.snapshot("strategy-configuration:00000000-0000-0000-0000-000000000063"))
    with pytest.raises(TypeError): service.pins["other"] = "a" * 64


def test_current_http_configuration_options_include_certified_programme(monkeypatch):
    _, revision, reads = installed(monkeypatch)
    old = dict(revision_id="legacy", revision=1, label="Legacy", content_hash="a" * 64,
               run_plan_id="old", available_run_plans=[])
    monkeypatch.setattr(legacy, "certified_configuration_options", lambda candidate_id: (old, (old,)))
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
        assert [row["candidate_id"] for row in result["candidates"]] == ["legacy", revision["revision_id"]]
    asyncio.run(run())
    assert len(reads) == 1


def test_uninstalled_programme_selection_fails_before_any_legacy_read(monkeypatch):
    monkeypatch.setattr(selection_service, "_SERVICE", None)
    monkeypatch.setattr(legacy, "certified_configuration_options", lambda *_: pytest.fail("legacy read"))
    with pytest.raises(ValueError, match="not installed"):
        configuration.backtest_configuration_options("strategy-configuration:missing")
