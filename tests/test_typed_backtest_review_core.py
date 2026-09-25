from dataclasses import replace
from types import SimpleNamespace
import re
import pytest

from src.backend import typed_backtest_review_core as review
from src.trading_runtime.arte_journal_writer import (
    publish_typed_batch, publish_typed_run, publish_typed_run_context,
)
from tests.test_arte_journal_writer import MemoryClient, RUN, batch, run_context, run_row
from tests.test_backtest_terminal_v2_fence import (
    AT as V2_AT, ATTEMPT as V2_ATTEMPT, BATCH as V2_BATCH,
    RUN as V2_RUN, _suffix,
)
from src.backend.backtest_terminal_v2_fence import project_terminal_v2_commit


def scoped(client):
    client.base_url = f"memory://{id(client)}"
    client.user = "reader"
    client.password = "test-only"
    return client


def test_review_core_reads_real_run_commit_and_event_without_writes(monkeypatch):
    client = scoped(MemoryClient())
    publish_typed_run(client, {**run_row(), "mode": "backtest"})
    publish_typed_run_context(client, run_id=RUN, config=run_context(),
                              account_ids=("DU1",))
    publish_typed_batch(client, replace(batch(), status="completed"))
    prior_inserts = len(client.inserts)
    audits = []
    prefix_loads = []
    real_prefix = review.load_committed_prefix

    def verified_prefix(actual, run_id):
        prefix_loads.append(1)
        return real_prefix(actual, run_id)

    monkeypatch.setattr(review, "load_committed_prefix", verified_prefix)

    def audit(actual, run_id):
        audits.append((actual, run_id))
        return {"DU1": {"account_id": "DU1", "state_hash": "a" * 64}}

    monkeypatch.setattr(review, "audit_terminal_backtest_recovery", audit)
    cache = review.AuditedSessionCache()
    page = review.load_typed_backtest_review_core(client, RUN, limit=1, cache=cache)
    assert page["review_only"] and not page["full_saved_review"]
    assert page["run"]["mode"] == "backtest"
    assert page["status"] == "completed"
    assert page["committed_sequence"] == page["next_sequence"] == 1
    assert page["complete"] and len(page["events"]) == 1
    assert page["events"][0]["event"]["category"] == "run_state"
    assert page["events"][0]["detail_family"] is None
    assert page["account_snapshot_count"] == 1
    assert len(page["account_snapshot_hash"]) == 64
    assert audits == [(client, RUN)]
    assert len(client.inserts) == prior_inserts
    assert review.load_typed_backtest_review_core(
        client, RUN, after_sequence=1, cache=cache)["events"] == ()
    assert audits == [(client, RUN)]  # A second page did not rescan the run.
    assert len(prefix_loads) == 1  # Nor did it re-verify every commit batch.


def test_review_core_rejects_nonterminal_audit_failure_and_changed_authority(monkeypatch):
    prefix = SimpleNamespace(status="running", last_sequence=1)
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_args: {
        "mode": "backtest", "account_ids": ("DU1",)})
    monkeypatch.setattr(review, "load_committed_prefix", lambda *_args: prefix)
    client = scoped(SimpleNamespace())
    with pytest.raises(ValueError, match="terminal committed"):
        review.load_typed_backtest_review_core(client, "run")
    prefix.status = "completed"
    monkeypatch.setattr(review, "audit_terminal_backtest_recovery",
                        lambda *_args: (_ for _ in ()).throw(RuntimeError("audit failed")))
    with pytest.raises(RuntimeError, match="audit failed"):
        review.load_typed_backtest_review_core(client, "run")
    monkeypatch.setattr(review, "audit_terminal_backtest_recovery",
                        lambda *_args: {"FOREIGN": {}})
    monkeypatch.setattr(review, "_head_matches", lambda *_args: True)
    with pytest.raises(RuntimeError, match="authority changed"):
        review.load_typed_backtest_review_core(client, "run")


def test_head_change_forces_new_full_audit(monkeypatch):
    client = scoped(MemoryClient())
    publish_typed_run(client, {**run_row(), "mode": "backtest"})
    publish_typed_run_context(client, run_id=RUN, config=run_context(),
                              account_ids=("DU1",))
    publish_typed_batch(client, replace(batch(), status="completed"))
    calls = []
    monkeypatch.setattr(review, "audit_terminal_backtest_recovery",
                        lambda *_args: calls.append(1) or {"DU1": {}})
    cache = review.AuditedSessionCache()
    review.load_typed_backtest_review_core(client, RUN, cache=cache)
    client.tables["trading_commit_v1"][0]["source_cursor"] = "changed"
    review.load_typed_backtest_review_core(client, RUN, cache=cache)
    assert len(calls) == 2  # Changed head was not served from stale audit.


def test_lru_eviction_reaudits_on_return(monkeypatch):
    clients = [scoped(MemoryClient()), scoped(MemoryClient())]
    for client in clients:
        publish_typed_run(client, {**run_row(), "mode": "backtest"})
        publish_typed_run_context(client, run_id=RUN, config=run_context(),
                                  account_ids=("DU1",))
        publish_typed_batch(client, replace(batch(), status="completed"))
    calls = []
    monkeypatch.setattr(review, "audit_terminal_backtest_recovery",
                        lambda client, _run: calls.append(client) or {"DU1": {}})
    cache = review.AuditedSessionCache(max_sessions=1)
    for client in (clients[0], clients[1], clients[0]):
        review.load_typed_backtest_review_core(client, RUN, cache=cache)
    assert calls == [clients[0], clients[1], clients[0]]


def test_cache_expiry_reaudits_and_large_prefix_has_no_arbitrary_cap(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(review, "monotonic", lambda: clock[0])
    context = {"mode": "backtest", "account_ids": ("DU1",)}
    prefix = SimpleNamespace(status="completed", last_sequence=1001,
                             last_batch_id="batch", source_cursor="end")
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_args: context)
    monkeypatch.setattr(review, "load_committed_prefix", lambda *_args: prefix)
    monkeypatch.setattr(review, "_head_matches", lambda *_args: True)
    monkeypatch.setattr(review, "load_typed_event_page", lambda *_args, **_kwargs: (
        SimpleNamespace(event={"sequence": 1}, detail_family=None, detail=None),))
    audits = []
    monkeypatch.setattr(review, "audit_terminal_backtest_recovery",
                        lambda *_args: audits.append(1) or {"DU1": {}})
    client = scoped(SimpleNamespace())
    cache = review.AuditedSessionCache(ttl_seconds=5)
    assert review.load_typed_backtest_review_core(
        client, "run", cache=cache)["committed_sequence"] == 1001
    assert review.load_typed_backtest_review_core(
        client, "run", cache=cache)["next_sequence"] == 1
    assert len(audits) == 1
    clock[0] = 6.0
    review.load_typed_backtest_review_core(client, "run", cache=cache)
    assert len(audits) == 2


def test_v2_terminal_review_pages_only_attested_suffix(monkeypatch):
    prefix, events, transitions, accounts, positions = _suffix()
    seal = project_terminal_v2_commit(
        prefix, attempt_id=V2_ATTEMPT, batch_id=V2_BATCH,
        account_ids=("DU1",), source_cursor="start", status="completed",
        committed_at=V2_AT, events=events, transitions=transitions,
        accounts=accounts, positions=positions)
    tables = {
        "trading_event_v1": events,
        "trading_run_transition_v1": transitions,
        "trading_backtest_account_snapshot_v2": accounts,
        "trading_backtest_position_snapshot_v2": positions,
    }
    queries = []
    def selected(_client, sql):
        queries.append(sql)
        table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        rows = list(tables[table])
        assert f"batch_id=toUUID('{V2_BATCH}')" in sql
        match = re.search(r"AND sequence>(\d+)", sql)
        if match:
            rows = [row for row in rows if row["sequence"] > int(match.group(1))]
        if "AND record_id IN (" in sql:
            identities = set(re.findall(r"toUUID\('([^']+)'\)",
                                        sql.split("AND record_id IN (", 1)[1]))
            rows = [row for row in rows if row["record_id"] in identities]
        match = re.search(r"LIMIT (\d+)", sql)
        return rows[:int(match.group(1))] if match else rows
    monkeypatch.setattr(review, "_rows", selected)
    context = {"mode": "backtest", "account_ids": ("DU1",)}
    monkeypatch.setattr(review, "load_typed_run_context", lambda *_: context)
    monkeypatch.setattr(review, "load_committed_prefix", lambda *_, **__: prefix)
    monkeypatch.setattr(review, "load_attested_terminal_v2_accounts",
                        lambda *_, **__: {"DU1": {"state_hash": "a" * 64}})
    monkeypatch.setattr(review, "audit_terminal_v2_run", lambda *_, **__: seal)
    monkeypatch.setattr(review, "load_typed_event_page",
                        lambda *_, **__: (SimpleNamespace(
                            event={"sequence": 1}, detail_family=None, detail=None),))
    keeper = object()
    pages = [review.load_typed_backtest_review_core_v2(
        object(), keeper, V2_RUN, after_sequence=sequence, limit=1)
        for sequence in range(4)]
    assert [page["next_sequence"] for page in pages] == [1, 2, 3, 4]
    assert [page["events"][0]["detail_family"] for page in pages] == [
        None, "trading_backtest_account_snapshot_v2",
        "trading_backtest_position_snapshot_v2", "trading_run_transition_v1"]
    assert pages[-1]["complete"] and pages[-1]["status"] == "completed"
    assert all(page["account_snapshot_count"] == 1 for page in pages)
    queries.clear()
    full_suffix = review.load_typed_backtest_review_core_v2(
        object(), keeper, V2_RUN, after_sequence=1, limit=3)
    assert [row["event"]["sequence"] for row in full_suffix["events"]] == [2, 3, 4]
    assert len(queries) == 4  # One event query and one per typed detail family.
    assert review.load_typed_backtest_review_core_v2(
        object(), keeper, V2_RUN, after_sequence=4)["events"] == ()
    monkeypatch.setattr(review, "load_attested_terminal_v2_accounts",
                        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("no proof")))
    with pytest.raises(RuntimeError, match="no proof"):
        review.load_typed_backtest_review_core_v2(object(), keeper, V2_RUN)
