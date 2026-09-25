from copy import deepcopy
from dataclasses import replace

import pytest

from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3, staged_v3_ddl
from src.backend.backtest_squeeze_episode_v3 import (
    project_squeeze_row_v3, seal_squeeze_family_v3, verify_squeeze_family_v3,
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
        "batch_id": BATCH, "category": record.category,
        "entity_type": record.entity_type, "entity_id": record.entity_id,
        "account_id": record.account_id, "event_time": record.event_time.isoformat(),
    }
    base = {name: "" for name, _ in SQUEEZE_COMMIT_V3.columns
            if name not in {"backtest_squeeze_episode_count", "backtest_squeeze_episode_hash"}}
    base.update(run_id=record.run_id, batch_id=BATCH)
    return record, row, parent, base


def test_v3_contract_is_staged_and_v2_unchanged():
    v2 = next(t for t in VERSIONED_JOURNAL_V2_TABLES if t.name == "trading_commit_v2")
    assert "backtest_squeeze_episode_count" not in dict(v2.columns)
    assert list(dict(SQUEEZE_COMMIT_V3.columns))[-5:-3] == [
        "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash"]
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
