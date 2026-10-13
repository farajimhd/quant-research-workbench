"""Public route composition; native cold binding has independent tests."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.backend.replay_run_service import ReplayRunService
from src.backend import program_controller_recovery as recovery
from tests.test_program_configuration_selection_service import installed

RUN = "62908518-9fd4-4a8e-8c90-2162ccb237e1"


@pytest.mark.parametrize("failure", [None, "fresh", "changed"])
def test_public_resume_uses_matching_staged_cold_gate_before_disk_or_legacy(monkeypatch, tmp_path, failure):
    selector, _, _ = installed(monkeypatch)
    service = ReplayRunService(runtime_root=tmp_path, allow_typed_backtest_resume=True)
    controller = SimpleNamespace(run_id=RUN)
    selector._prepared = (service, controller)
    calls = []
    async def staged(actual):
        assert actual is service
        calls.append("stage")
        if failure == "changed":
            other = SimpleNamespace(run_id="00000000-0000-0000-0000-000000000001")
            selector._prepared = (service, other)
            return other
        return controller
    def cold(actual):
        assert actual is controller
        calls.append("cold")
        if failure == "fresh": raise RuntimeError("missing adopted cold controller")
    async def launch(actual_service, actual_controller):
        assert (actual_service, actual_controller) == (service, controller)
        calls.append("launch")
        return controller
    monkeypatch.setattr(selector, "staged_controller", staged)
    monkeypatch.setattr(selector, "launch_controller", launch)
    monkeypatch.setattr(recovery, "require_recovered_program_controller", cold)
    monkeypatch.setattr(service, "_load_typed_backtest_resume_definition", lambda *_:
        pytest.fail("Programme resume entered legacy definition lookup"))
    monkeypatch.setattr(Path, "is_file", lambda *_: pytest.fail("Prepared programme resume read disk authority"))
    async def run():
        if failure:
            with pytest.raises(RuntimeError, match="adopted cold|handoff changed"):
                await service.resume(RUN)
        else:
            assert await service.resume(RUN) is controller
    asyncio.run(run())
    assert calls == (["stage"] if failure == "changed" else
                     ["stage", "cold"] if failure == "fresh" else ["stage", "cold", "launch"])
    assert not service._runs


def test_resume_handoff_does_not_match_another_service_or_run(monkeypatch, tmp_path):
    selector, _, _ = installed(monkeypatch)
    service = ReplayRunService(runtime_root=tmp_path)
    selector._prepared = (service, SimpleNamespace(run_id=RUN))
    async def unexpected(*args): pytest.fail("Unrelated prepared controller was reviewed")
    monkeypatch.setattr(selector, "staged_controller", unexpected)
    async def run():
        assert await selector.prepared_resume_controller(service, "other") is None
        assert await selector.prepared_resume_controller(object(), RUN) is None
    asyncio.run(run())


def test_unprepared_programme_definition_rejects_before_legacy_market_lookup(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer, arte_backtest_definition as definitions
    from src.backend import backtest_v3_clients as market
    closed = []
    class Reader:
        def execute(self, sql):
            if sql == "SELECT getSetting('readonly')": return "1"
            if "SELECT count() FROM arte.trading_run_v1" in sql: return "1"
            assert "SELECT status FROM arte.trading_commit_v4" in sql
            return ""
        def close(self): closed.append(True)
    monkeypatch.setattr(writer, "backtest_v4_operator_client_from_env", Reader)
    monkeypatch.setattr(writer, "load_typed_run_context", lambda *_: {})
    monkeypatch.setattr(definitions, "load_backtest_definition", lambda *a, **k:
        {"definition": {"configuration_revision_id": "strategy-configuration:" + RUN}})
    monkeypatch.setattr(market, "v3_client", lambda *_: pytest.fail("Programme definition entered legacy market lookup"))
    with pytest.raises(ValueError, match="prepared cold controller handoff"):
        ReplayRunService._load_typed_backtest_resume_definition(RUN)
    assert closed == [True]
