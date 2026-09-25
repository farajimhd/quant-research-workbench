from copy import deepcopy
from dataclasses import replace
import json
import re

import pytest

from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3, staged_v3_ddl
from src.backend.backtest_squeeze_episode_v3 import (
    load_verified_squeeze_v3_run, project_squeeze_row_v3,
    seal_squeeze_family_v3, verify_squeeze_family_v3,
)
from src.trading_runtime.arte_journal_schema import VERSIONED_JOURNAL_V2_TABLES
from tests.test_backtest_squeeze_episode_projection import BATCH, PLAN, QUERY, _record


def _fixture():
    record = _record()
    row = project_squeeze_row_v3(
        record, batch_id=BATCH, expected_market_plan_token=PLAN,
        expected_query_sha256=QUERY)
    parent = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": row["event_month"],
        "batch_id": BATCH, "category": record.category,
        "entity_type": record.entity_type, "entity_id": record.entity_id,
        "account_id": record.account_id, "event_time": record.event_time.isoformat(),
    }
    base = {name: "" for name, _ in SQUEEZE_COMMIT_V3.columns
            if name not in {"backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
                            "portfolio_reservation_reason_count",
                            "portfolio_reservation_reason_hash"}}
    base.update(run_id=record.run_id, batch_id=BATCH)
    return record, row, parent, base


def test_v3_contract_is_staged_and_v2_unchanged():
    v2 = next(t for t in VERSIONED_JOURNAL_V2_TABLES if t.name == "trading_commit_v2")
    assert "backtest_squeeze_episode_count" not in dict(v2.columns)
    assert list(dict(SQUEEZE_COMMIT_V3.columns))[-7:-3] == [
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
        "portfolio_reservation_reason_count", "portfolio_reservation_reason_hash"]
    assert all("live_market_ssd" in ddl for ddl in staged_v3_ddl())


def test_closed_row_and_v3_seal_roundtrip():
    _, row, parent, base = _fixture()
    commit = seal_squeeze_family_v3(base, [row], [parent])
    assert commit["backtest_squeeze_episode_count"] == 1
    assert verify_squeeze_family_v3(commit, [row], [parent]) == (row,)


@pytest.mark.parametrize("target,key,value", [
    ("row", "content_hash", "0" * 64),
    ("row", "batch_id", "00000000-0000-0000-0000-000000000999"),
    ("parent", "entity_id", "f" * 64),
    ("parent", "event_time", "2026-08-18T13:59:59+00:00"),
    ("commit", "backtest_squeeze_episode_count", 2),
    ("commit", "backtest_squeeze_episode_hash", "0" * 64),
])
def test_v3_rejects_identity_causality_and_seal_tamper(target, key, value):
    _, row, parent, base = _fixture()
    commit = seal_squeeze_family_v3(base, [row], [parent])
    inputs = {"row": deepcopy(row), "parent": deepcopy(parent), "commit": deepcopy(commit)}
    inputs[target][key] = value
    with pytest.raises(ValueError):
        verify_squeeze_family_v3(inputs["commit"], [inputs["row"]], [inputs["parent"]])


def test_dynamic_signal_occurrence_remains_rejected():
    record, _, _, _ = _fixture()
    payload = dict(record.payload)
    payload["field_evidence"] = {"some_rule": 42}
    with pytest.raises(ValueError, match="unmodeled"):
        project_squeeze_row_v3(
            replace(record, payload=payload), batch_id=BATCH,
            expected_market_plan_token=PLAN, expected_query_sha256=QUERY)


class _FakeColdClient:
    def __init__(self, commit, children, parents, *, old_fence=False,
                 reservation_events=(), reservation_parents=(), reasons=()):
        self.commit = commit
        self.children = children
        self.parents = parents
        self.reservation_events = reservation_events
        self.reservation_parents = reservation_parents
        self.reasons = reasons
        self.old_fence = old_fence
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        if "FROM arte.trading_commit_v1" in sql or "FROM arte.trading_commit_v2" in sql:
            rows = [{"batch_id": BATCH}] if self.old_fence else []
        elif "FROM arte.trading_commit_v3" in sql:
            rows = self.commit if isinstance(self.commit, list) else [self.commit]
        elif "FROM arte.trading_event_v1" in sql:
            rows = (self.reservation_events if "category='portfolio_management'" in sql
                    else self.parents)
        elif "FROM arte.trading_backtest_squeeze_episode_v1" in sql:
            rows = self.children
        elif "FROM arte.trading_portfolio_reservation_event_v1" in sql:
            rows = self.reservation_parents
        elif "FROM arte.trading_portfolio_reservation_reason_v1" in sql:
            rows = self.reasons
        else:
            raise AssertionError(sql)
        match = re.search(r"batch_id=toUUID\('([^']+)'\)", sql)
        if match:
            rows = [row for row in rows if row["batch_id"] == match.group(1)]
        return "\n".join(json.dumps(row) for row in rows)


def _cold_fixture():
    _, child, parent, base = _fixture()
    base.update(prior_batch_id="00000000-0000-0000-0000-000000000000",
                first_sequence=1, last_sequence=1, event_count=1,
                status="completed", source_cursor="bar:1")
    commit = seal_squeeze_family_v3(base, [child], [parent])
    stored_child = dict(child)
    for key in ("episode_started_at", "expires_at"):
        stored_child[key] = stored_child[key].replace("T", " ").replace("+00:00", "")
    stored_parent = dict(parent, sequence=1)
    stored_parent["event_time"] = (
        stored_parent["event_time"].replace("T", " ").replace("+00:00", "") + "000")
    return commit, stored_child, stored_parent


def test_cold_v3_reader_uses_only_v3_fence_and_verifies_shared_batch(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as module
    commit, child, parent = _cold_fixture()
    checked = []
    monkeypatch.setattr(module, "storage_preflight", lambda client, *, tables: checked.append(tables))
    monkeypatch.setattr(module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: checked.append(
                            (commits[0]["batch_id"], journal_profile)))
    client = _FakeColdClient(commit, [child], [parent])
    result = load_verified_squeeze_v3_run(
        client, commit["run_id"], expected_market_plan_token=PLAN,
        expected_query_sha256=QUERY)
    assert result[0]["episode_id"] == child["episode_id"]
    assert checked[-1] == (BATCH, "backtest_v2")
    assert any(t.name == "trading_commit_v3" for t in checked[0])
    assert any(t.name == "trading_commit_v2" for t in checked[0])  # mixed-fence check
    assert all("bt_" not in query and "sqlite" not in query.lower() for query in client.queries)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "future_tail", "old_fence"])
def test_cold_v3_reader_rejects_missing_duplicate_extra_and_future_parent(monkeypatch, mutation):
    import src.backend.backtest_squeeze_episode_v3 as module
    monkeypatch.setattr(module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    commit, child, parent = _cold_fixture()
    children, parents = [child], [parent]
    if mutation == "missing":
        children = []
    elif mutation == "duplicate":
        children = [child, child]
    elif mutation == "extra":
        children = [child, dict(child, record_id="00000000-0000-0000-0000-000000000987")]
    elif mutation == "future_tail":
        parents = [dict(parent, sequence=2)]
    client = _FakeColdClient(commit, children, parents, old_fence=mutation == "old_fence")
    with pytest.raises((ValueError, RuntimeError)):
        load_verified_squeeze_v3_run(
            client, commit["run_id"], expected_market_plan_token=PLAN,
            expected_query_sha256=QUERY)


def test_cold_v3_reader_verifies_whole_chain_and_rejects_gap(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as module
    monkeypatch.setattr(module, "storage_preflight", lambda client, *, tables: None)
    checked = []
    monkeypatch.setattr(module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: checked.append(
                            commits[0]["batch_id"]))
    first, child, parent = _cold_fixture()
    first["status"] = "running"
    second_batch = "00000000-0000-0000-0000-000000000a99"
    second_base = {key: value for key, value in first.items()
                   if key not in {"backtest_squeeze_episode_count",
                                  "backtest_squeeze_episode_hash",
                                  "portfolio_reservation_reason_count",
                                  "portfolio_reservation_reason_hash"}}
    second_base.update(batch_id=second_batch, prior_batch_id=BATCH,
                       first_sequence=2, last_sequence=2, status="completed")
    second = seal_squeeze_family_v3(second_base, [], [])
    client = _FakeColdClient([first, second], [child], [parent])
    assert len(load_verified_squeeze_v3_run(
        client, first["run_id"], expected_market_plan_token=PLAN,
        expected_query_sha256=QUERY)) == 1
    assert checked == [BATCH, second_batch]
    second["first_sequence"] = 3
    with pytest.raises(RuntimeError, match="contiguous"):
        load_verified_squeeze_v3_run(
            client, first["run_id"], expected_market_plan_token=PLAN,
            expected_query_sha256=QUERY)


def test_cold_v3_reader_rejects_unpinned_source(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as module
    monkeypatch.setattr(module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    commit, child, parent = _cold_fixture()
    client = _FakeColdClient(commit, [child], [parent])
    with pytest.raises(RuntimeError, match="pinned"):
        load_verified_squeeze_v3_run(
            client, commit["run_id"], expected_market_plan_token="d" * 64,
            expected_query_sha256=QUERY)
