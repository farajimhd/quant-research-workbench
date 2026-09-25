"""V3 terminal seal never reinterprets its predecessor as V2."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
from src.backend.backtest_terminal_v3_fence import (
    TERMINAL_COMMIT_V3, load_terminal_v3_commit, project_terminal_v3_commit,
    staged_terminal_v3_ddl,
)
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_terminal_v2_fence import (
    AT, ATTEMPT, BATCH, PRIOR, RUN, _suffix,
)


def _fixture():
    base, events, transitions, accounts, positions = _suffix()
    prefix = V3CommittedPrefix(
        base.run_id, base.last_sequence, base.last_batch_id,
        base.source_cursor, base.status, base.batch_ids, ())
    kwargs = dict(
        account_ids=("DU1",), attempt_id=ATTEMPT, batch_id=BATCH,
        source_cursor="start", status="completed", committed_at=AT,
        events=events, transitions=transitions,
        accounts=accounts, positions=positions)
    return prefix, kwargs


def test_v3_terminal_is_distinct_complete_schema_and_cold_roundtrip():
    prefix, kwargs = _fixture()
    seal = project_terminal_v3_commit(prefix, **kwargs)
    assert seal["prior_v3_batch_id"] == PRIOR
    assert "prior_v2_batch_id" not in seal
    assert seal["content_hash"] == sha256(canonical_json({
        key: value for key, value in seal.items() if key != "content_hash"
    }).encode()).hexdigest()
    assert "live_market_ssd" in staged_terminal_v3_ddl()
    assert set(seal) == {name for name, _ in TERMINAL_COMMIT_V3.columns}
    rows = {
        "trading_backtest_terminal_commit_v3": [seal],
        "trading_event_v1": kwargs["events"],
        "trading_run_transition_v1": kwargs["transitions"],
        "trading_backtest_account_snapshot_v2": kwargs["accounts"],
        "trading_backtest_position_snapshot_v2": kwargs["positions"],
    }
    class Client:
        def execute(self, sql):
            table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(canonical_json(row) for row in rows[table])
    assert load_terminal_v3_commit(Client(), prefix, account_ids=("DU1",)) == seal
    rows["trading_backtest_terminal_commit_v3"] = [
        {**seal, "committed_at": "2026-08-18 14:00:00.000000"}]
    assert load_terminal_v3_commit(Client(), prefix, account_ids=("DU1",)) == seal
    rows["trading_backtest_terminal_commit_v3"] = [
        {**seal, "prior_v3_batch_id": BATCH}]
    with pytest.raises(RuntimeError, match="differs"):
        load_terminal_v3_commit(Client(), prefix, account_ids=("DU1",))


def test_v3_terminal_rejects_nonrunning_prefix_and_incomplete_snapshot():
    prefix, kwargs = _fixture()
    with pytest.raises(ValueError, match="running prefix"):
        project_terminal_v3_commit(replace(prefix, status="completed"), **kwargs)
    with pytest.raises(ValueError, match="details do not cover"):
        project_terminal_v3_commit(prefix, **{**kwargs, "positions": ()})
