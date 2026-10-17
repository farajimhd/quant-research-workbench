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
@pytest.mark.parametrize("observations", [False, True])
def test_owned_page_runs_off_loop_and_checks_prefix_twice(monkeypatch, movement, observations):
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
        if observations:
            return await module.read_rule_decision_observation_page_async(lambda: reader, prefix, limit=2,
                maximum_children=8)
        return await module.read_rule_decision_page_async(lambda: reader, prefix, limit=2)
    if movement:
        with pytest.raises(ValueError, match="prefix changed"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == ()
    assert reader.closed.is_set()


@pytest.mark.parametrize("observations", [False, True])
def test_cancellation_drains_owned_reader(monkeypatch, observations):
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
        task = asyncio.create_task(module.read_rule_decision_observation_page_async(lambda: reader, prefix,
            maximum_children=8) if observations else module.read_rule_decision_page_async(lambda: reader, prefix))
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


@pytest.mark.parametrize("observations", [False, True])
def test_writable_reader_is_rejected_and_closed(observations):
    reader = Reader()
    reader.execute = lambda sql: "0"
    prefix = V4CommittedPrefix("run", 5, "batch", "source", "running", ("batch",))
    with pytest.raises(ValueError, match="SELECT-only"):
        asyncio.run(module.read_rule_decision_observation_page_async(lambda: reader, prefix, maximum_children=8)
            if observations else module.read_rule_decision_page_async(lambda: reader, prefix))
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


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("observations", [False, True])
@pytest.mark.parametrize("change", [None, "missing", "duplicate", "hash", "parent", "inventory",
                                    "inventory_missing", "inventory_conflict"])
def test_owned_completion_page_follows_sealed_v4_writer_price_family(monkeypatch, exact, observations, change):
    from uuid import uuid4
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4, verify_commit_v4
    from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
    from src.trading_runtime.signals import StrategySignal
    from src.trading_runtime.journal_contract import JournalRecord
    from tests.test_rule_decision_basis_projection import values
    from tests.test_arte_journal_writer import MemoryClient

    scope = {**values("wait"), "basis_sequence": 0, "decision_sequence": 1}
    at = scope["boundary_at"]
    price = 10.001000000000001 if exact else 10.
    signal = StrategySignal(scope["decision_id"], "program_boundary_completed", "ABC", at,
        "wait", "neutral", 0., 1., "program:wait", working_timeframe="1s",
        metadata=dict(assignment_id=scope["assignment_id"], reference_price=price, status="completed",
            reason_code="program:wait", reason_detail="wait", correlation_id=scope["decision_id"],
            causation_id=scope["source_commit_hash"]))
    record = JournalRecord(scope["record_id"], scope["run_id"], 1, at, at,
        "strategy_decision", "signal", scope["decision_id"], scope["account_id"],
        {**signal.payload(), "strategy_id": "program", "strategy_revision": 1,
         "correlation_id": scope["decision_id"], "causation_id": scope["source_commit_hash"]})
    base = project_journal_record(record, run_month=scope["event_month"], attempt_id=str(uuid4()),
        batch_id=scope["batch_id"], prior_batch_id=str(uuid4()), source_cursor="source",
        exact_signal_prices=exact, expected_mode="backtest")
    families = tuple((writer._v4_family_table(name), rows) for name, rows in writer._sealed_families(base) if rows)
    commit, seals = prepare_commit_v4(run_id=base.run_id, run_month=base.run_month,
        attempt_id=base.attempt_id, batch_id=base.batch_id, prior_batch_id=base.prior_batch_id,
        first_sequence=1, last_sequence=1, source_cursor="source", status="running",
        sealed_families=families, committed_at=at)
    verify_commit_v4(commit, seals, detail_identities={name: tuple(
        (row["record_id"], row["content_hash"]) for row in rows) for name, rows in families})
    client = MemoryClient()
    for name, rows in families:
        client.tables[name] = [writer._wire_row(name, row) for row in rows]
    client.tables["trading_commit_v4"] = [commit]
    client.tables["trading_commit_family_v4"] = list(seals)
    client.close = lambda: None
    execute = client.execute
    client.execute = lambda sql: "1" if sql == "SELECT getSetting('readonly')" else execute(sql)
    prefix = V4CommittedPrefix(base.run_id, 1, base.batch_id, "source", "running", (base.batch_id,))
    parent, children = project_rule_decision_basis(scope, [])
    rows = ((parent, children),)
    monkeypatch.setattr(module, "load_verified_v4_prefix", lambda *_: prefix)
    monkeypatch.setattr(module, "load_committed_rule_decision_page", lambda *_, **__: rows)
    monkeypatch.setattr(module, "load_decision_observation_page", lambda *_, **__: rows)
    family = "trading_strategy_signal_exact_price_v2" if exact else "trading_strategy_signal_v2"
    if change == "missing":
        client.tables[family].clear()
    elif change == "duplicate":
        client.tables[family] *= 2
    elif change == "hash":
        client.tables[family][0]["reason"] = "tampered"
    elif change == "parent":
        raw = {key: value for key, value in dict(families)[family][0].items() if key != "content_hash"}
        client.tables[family][0] = writer._wire_row(family, writer.typed_row(family, {**raw, "account_id": "other"}))
    elif change == "inventory":
        client.tables["trading_commit_family_v4"] += [next(
            row for row in seals if row["family_name"] == family)]
    elif change == "inventory_missing":
        client.tables["trading_commit_family_v4"] = [row for row in seals if row["family_name"] != family]
    elif change == "inventory_conflict":
        alternative = "trading_strategy_signal_v2" if exact else "trading_strategy_signal_exact_price_v2"
        client.tables["trading_commit_family_v4"] += [{**next(
            row for row in seals if row["family_name"] == family), "family_name": alternative}]

    async def run():
        if observations:
            return await module.read_rule_decision_observation_page_async(lambda: client, prefix, maximum_children=8)
        return await module.read_rule_decision_page_async(lambda: client, prefix)
    if change:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == rows
        event, = module.load_typed_event_page(client, prefix)
        assert float(event.detail["decision_reference_price"]).hex() == price.hex()
        assert event.detail_family == "trading_strategy_signal_v2"
        assert any("FROM arte.trading_commit_family_v4 " in sql and "LIMIT 3 " in sql
                   for sql in client.selects)
        if exact:
            assert module.load_typed_event_page(client, prefix, exact_prices=True) == (event,)
        else:
            with pytest.raises(RuntimeError, match="committed mode"):
                module.load_typed_event_page(client, prefix, exact_prices=True)
