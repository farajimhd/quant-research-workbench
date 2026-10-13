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
