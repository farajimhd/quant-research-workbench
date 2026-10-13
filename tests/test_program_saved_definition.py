"""Cold definition admission using central codecs, without restart or writers."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from src.backend import program_saved_definition as cold
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from tests.test_program_configuration_selection_service import installed


@pytest.mark.parametrize("failure", [None, "terminal", "code", "configuration", "moved", "reuse", "writable"])
def test_running_definition_uses_approved_selection_and_closes_owned_clients(monkeypatch, failure):
    _, revision, _ = installed(monkeypatch)
    run_id = str(uuid4())
    prefix = V4CommittedPrefix(run_id, 10, "batch", "cursor", "running", ("batch",))
    if failure == "terminal": prefix = replace(prefix, status="completed")
    context = dict(mode="backtest", code_hash="a" * 64,
        configuration_hash=revision["content_hash"] if failure != "configuration" else "b" * 64)
    saved = dict(definition={"configuration_revision_id": revision["revision_id"]})
    clients = []
    class Reader:
        closed = False
        def execute(self, query):
            assert query == "SELECT getSetting('readonly')"
            return "0" if failure == "writable" else "1"
        def close(self): self.closed = True
    def factory():
        if failure == "reuse" and clients: return clients[0]
        reader = Reader()
        clients.append(reader)
        return reader
    def get_prefix(client, identity):
        assert identity == run_id
        return replace(prefix, last_sequence=11) if failure == "moved" and len(clients) > 1 else prefix
    monkeypatch.setattr(cold, "load_verified_v4_prefix", get_prefix)
    monkeypatch.setattr(cold, "load_typed_run_context", lambda *_: context)
    monkeypatch.setattr(cold, "load_backtest_definition", lambda *args, **kwargs: saved)
    async def run():
        if failure:
            with pytest.raises((ValueError, RuntimeError), match="running V4|source pins|committed run|changed|reuse|read-only"):
                await cold.load_program_saved_definition(factory, run_id,
                    expected_code_hash="c" * 64 if failure == "code" else "a" * 64)
        else:
            result = await cold.load_program_saved_definition(factory, run_id, expected_code_hash="a" * 64)
            assert result.prefix == prefix and result.revision["content_hash"] == context["configuration_hash"]
            assert len(clients) == 2
            with pytest.raises(TypeError): result.saved["definition"]["configuration_revision_id"] = "other"
    asyncio.run(run())
    assert clients and all(reader.closed for reader in clients)


@pytest.mark.parametrize("failure", [None, "terminal", "source", "interval", "rows"])
def test_programme_reconstruction_uses_native_normalized_codec(failure):
    from tests.test_arte_backtest_definition import _definition, RUN_MONTH
    from src.trading_runtime.arte_backtest_definition import prepare_backtest_definition
    run_id = str(uuid4())
    revision = {"revision_id": "strategy-configuration:" + str(uuid4()), "content_hash": "a" * 64}
    original = _definition(configuration_revision=revision, execution_interval="1s",
        experimental_structure_book="", market_data_plan={**_definition().market_data_plan,
            "execution_interval": {"milliseconds": 1000}})
    saved = prepare_backtest_definition(run_id, original, run_month=RUN_MONTH)
    context = dict(run_id=run_id, mode="backtest", run_month=RUN_MONTH.isoformat(),
        session_date=original.session_date.isoformat(), evaluation_interval_ms=1000,
        configuration_hash=revision["content_hash"], market_plan_token=original.market_data_plan["token"])
    prefix = V4CommittedPrefix(run_id, 10, "batch", "cursor",
        "completed" if failure == "terminal" else "running", ("batch",))
    preflight = dict(ready=failure != "source", strategy_run_ready=True,
        execution_interval="100ms" if failure == "interval" else "1s",
        checks=[dict(required=True, status="ready")], window=dict(sessions=[context["session_date"]]),
        market_data_plan=original.market_data_plan, causal_v7_plan=original.causal_v7_plan)
    if failure == "rows":
        saved["definition"]["initial_cash"] = "200000.0000000000"
    evidence = cold.ProgramSavedDefinitionEvidence(prefix, cold._freeze(context),
        cold._freeze(saved), cold._freeze(revision))
    if failure:
        with pytest.raises((ValueError, RuntimeError), match="running definition|certified authority|normalized rows"):
            cold.reconstruct_program_saved_definition(evidence, cold._freeze(preflight))
    else:
        reconstructed = cold.reconstruct_program_saved_definition(evidence, cold._freeze(preflight))
        assert reconstructed.execution_interval == "1s"
        assert prepare_backtest_definition(run_id, reconstructed, run_month=RUN_MONTH) == saved
        reconstructed.configuration_revision["content_hash"] = "b" * 64
        assert evidence.revision["content_hash"] == "a" * 64
