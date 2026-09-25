"""Fake transport tests: no ClickHouse or Keeper connection is made."""
import json
from uuid import uuid4

import pytest

from src.backend.live_assignment_state_snapshot import STATE_COMMIT, STATE_TABLES
from src.backend.live_assignment_state_storage import (
    ClickHouseAssignmentStateStorage, _row,
)
from tests.test_live_assignment_state_snapshot import _project
from tests.test_arte_assignment_state_composite import KEY


class Client:
    def __init__(self, result=""):
        self.result = result
        self.calls = []

    def execute(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        return self.result


def _identity():
    return dict(run_id="live-run", assignment_id="assignment-1", revision=2,
                snapshot_id=str(uuid4()), session="2026-08-18")


def _commit(identity):
    return {**identity, "grouped_present": False, "child_count": 4,
            "child_hash": "a" * 64, "content_hash": "b" * 64}


def test_exact_state_commit_insert_uses_scalar_columns_and_stable_query_id():
    client = Client()
    storage = ClickHouseAssignmentStateStorage(client)
    row = _commit(_identity())
    storage.insert(STATE_COMMIT.name, [row])
    first_sql, first_options = client.calls[0]
    assert first_sql.startswith(f"INSERT INTO arte.{STATE_COMMIT.name} (")
    assert "FORMAT JSONEachRow\n" in first_sql
    assert json.loads(first_sql.split("FORMAT JSONEachRow\n", 1)[1]) == row
    assert first_options["query_id"]
    storage.insert(STATE_COMMIT.name, [row])
    assert client.calls[1] == client.calls[0]
    assert "JSON" not in {kind for _, kind in STATE_COMMIT.columns}


def test_state_read_scopes_identity_and_normalizes_clickhouse_bool():
    identity = _identity()
    row = _commit(identity)
    wire = {**row, "grouped_present": 0}
    client = Client(json.dumps(wire) + "\n")
    result = ClickHouseAssignmentStateStorage(client).read(STATE_COMMIT.name, identity)
    assert result == [row]
    sql, options = client.calls[0]
    assert options == {}
    assert f"FROM arte.{STATE_COMMIT.name}" in sql
    assert "assignment_id='assignment-1'" in sql
    assert "revision=2" in sql or "revision='2'" in sql
    client.result = json.dumps({**wire, "assignment_id": "other"}) + "\n"
    with pytest.raises(ValueError, match="another snapshot"):
        ClickHouseAssignmentStateStorage(client).read(STATE_COMMIT.name, identity)


def test_state_transport_rejects_unknown_table_types_and_mixed_identity():
    client = Client()
    storage = ClickHouseAssignmentStateStorage(client)
    row = _commit(_identity())
    with pytest.raises(ValueError, match="allowlisted"):
        storage.insert("arte.bars_v1", [row])
    with pytest.raises(ValueError, match="mixes snapshot"):
        storage.insert(STATE_COMMIT.name, [row, {**row, "revision": 3}])
    with pytest.raises(ValueError, match="not a valid"):
        _row(STATE_COMMIT, {**row, "child_count": True})
    with pytest.raises(ValueError, match="columns differ"):
        _row(STATE_COMMIT, {**row, "checkpoint_json": "{}"})
    assert client.calls == []


def test_every_projected_state_family_matches_its_scalar_wire_contract():
    rows, commit = _project()
    for table in STATE_TABLES:
        family = [commit] if table.name == STATE_COMMIT.name else rows[table.name]
        assert [_row(table, row) for row in family] == family


def test_every_state_family_reads_from_its_exact_snapshot_identity():
    rows, commit = _project()
    by_table = {**rows, STATE_COMMIT.name: [commit]}

    class FixtureClient:
        def execute(self, sql):
            for table in STATE_TABLES:
                if f"FROM arte.{table.name} WHERE " in sql:
                    return "\n".join(json.dumps(row) for row in by_table[table.name])
            raise AssertionError(sql)

    storage = ClickHouseAssignmentStateStorage(FixtureClient())
    for table in STATE_TABLES:
        assert storage.read(table.name, KEY) == by_table[table.name]
