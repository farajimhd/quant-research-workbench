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
        return ()
    monkeypatch.setattr(module, "load_verified_v4_prefix", head)
    monkeypatch.setattr(module, "load_committed_rule_decision_page", page)
    async def run():
        return await module.read_rule_decision_page_async(lambda: reader, prefix, limit=2)
    if movement:
        with pytest.raises(ValueError, match="prefix changed"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == ()
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


@pytest.mark.parametrize("change", [None, "missing", "assignment", "source"])
def test_normalized_completion_join_rejects_substitutions(change):
    from tests.test_rule_decision_basis_projection import values
    from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
    from src.trading_runtime.arte_journal_reader import TypedJournalEvent
    parent, children = project_rule_decision_basis(values("wait"), [])
    common = {key: parent[key] for key in ("run_id", "record_id", "batch_id", "account_id", "event_month")}
    event = dict(common, sequence=parent["decision_sequence"], entity_id=parent["decision_id"],
        correlation_id=parent["decision_id"], category="strategy_decision", entity_type="signal",
        event_time=parent["boundary_at"], recorded_at=parent["boundary_at"], causation_id=parent["source_commit_hash"])
    detail = dict(common, signal_id=parent["decision_id"], signal_type="program_boundary_completed",
        decision_status="completed", source_event_time=parent["boundary_at"],
        decision_assignment_id=parent["assignment_id"], decision_reason_detail="wait")
    if change == "assignment":
        detail["decision_assignment_id"] = "other"
    if change == "source":
        event["causation_id"] = "e" * 64
    events = () if change == "missing" else (TypedJournalEvent(event, "trading_strategy_signal_v2", detail),)
    rows = ((parent, children),)
    if change:
        with pytest.raises(ValueError):
            module.verify_decision_completion_page(rows, events)
    else:
        assert module.verify_decision_completion_page(rows, events) is rows


@pytest.mark.parametrize("change", [None, "missing", "duplicate", "sequence", "account"])
def test_committed_semantic_intent_join_uses_exact_records(monkeypatch, change):
    from tests.test_rule_decision_basis_projection import values, intent_record
    from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
    parent, children = project_rule_decision_basis(values(), [dict(stage="entry", strategy_intent_id="intent")])
    prefix = V4CommittedPrefix("run", 5, parent["batch_id"], "source", "running", (parent["batch_id"],))
    record = intent_record()
    if change == "sequence":
        record = replace(record, sequence=5)
    if change == "account":
        record = replace(record, account_id="other")
    events = [dict(record_id=record.record_id, entity_id="intent")]
    if change == "missing":
        events = []
    if change == "duplicate":
        events *= 2
    def read(client, sql):
        assert "batch_id IN" in sql and "LIMIT 2" in sql
        return events
    def intents(client, scope, **bounds):
        assert scope == prefix and bounds == dict(record_ids=(record.record_id,), limit=1)
        return (record,)
    monkeypatch.setattr(module, "_rows", read)
    monkeypatch.setattr(module, "load_committed_strategy_intent_page", intents)
    rows = ((parent, children),)
    if change:
        with pytest.raises(ValueError):
            module.join_decision_intents(object(), prefix, rows)
    else:
        assert module.join_decision_intents(object(), prefix, rows) is rows
