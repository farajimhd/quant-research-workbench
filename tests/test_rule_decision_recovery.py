import asyncio
from dataclasses import replace
from threading import Event, get_ident

import pytest

from src.backend import rule_decision_recovery as module
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


class Reader:
    def __init__(self):
        self.closed = Event()
    def execute(self, sql):
        assert sql == "SELECT getSetting('readonly')"
        return "1"
    def close(self):
        self.closed.set()


@pytest.mark.parametrize("movement", [None, "before", "after"])
def test_owned_page_runs_off_loop_and_checks_prefix_twice(monkeypatch, movement):
    reader = Reader()
    prefix = V4CommittedPrefix("run", 5, "batch", "source", "running", ("batch",))
    main_thread = get_ident()
    heads = []
    def head(client, run_id):
        assert client is reader and run_id == "run" and get_ident() != main_thread
        heads.append(True)
        changed = movement == "before" or movement == "after" and len(heads) == 2
        return replace(prefix, last_sequence=6) if changed else prefix
    def page(client, scope, **bounds):
        assert client is reader and scope == prefix and bounds == dict(after_sequence=0, limit=2)
        assert get_ident() != main_thread
        return ("synthetic-page",)
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    monkeypatch.setattr(module, "load_committed_rule_decision_page", page)
    async def run():
        return await module.read_rule_decision_page_async(lambda: reader, prefix, limit=2)
    if movement:
        with pytest.raises(ValueError, match="prefix changed"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == ("synthetic-page",)
    assert reader.closed.is_set()


def test_cancellation_drains_owned_reader(monkeypatch):
    reader = Reader()
    prefix = V4CommittedPrefix("run", 5, "batch", "source", "running", ("batch",))
    entered, release = Event(), Event()
    def head(*args):
        entered.set()
        assert release.wait(5)
        return prefix
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    monkeypatch.setattr(module, "load_committed_rule_decision_page", lambda *args, **kwargs: ())
    async def run():
        task = asyncio.create_task(module.read_rule_decision_page_async(lambda: reader, prefix))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not reader.closed.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reader.closed.is_set()
    asyncio.run(run())


def test_writable_reader_is_rejected_and_closed():
    reader = Reader()
    reader.execute = lambda sql: "0"
    prefix = V4CommittedPrefix("run", 5, "batch", "source", "running", ("batch",))
    with pytest.raises(ValueError, match="SELECT-only"):
        asyncio.run(module.read_rule_decision_page_async(lambda: reader, prefix))
    assert reader.closed.is_set()
