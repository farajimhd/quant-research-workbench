"""Fake-only transport checks; these tests never connect to ClickHouse."""
from copy import deepcopy
import json

import pytest

from src.backend.live_assignment_base_storage import ClickHouseAssignmentBaseStorage
from tests.test_live_assignment_base_revision import _project


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, *, query_id=None):
        self.calls.append((sql, query_id))
        if sql.startswith("SELECT "):
            return "\n".join(json.dumps(row) for row in self.rows)
        if sql.startswith("INSERT "):
            return ""
        raise AssertionError("unexpected ClickHouse operation")


def _clickhouse_wire(row):
    wire = deepcopy(row)
    for key in ("created_at", "updated_at"):
        wire[key] = wire[key].replace("T", " ").replace("+00:00", "")
    for key in ("can_observe", "can_enter", "can_add", "can_reduce",
                "can_exit", "can_reenter"):
        wire[key] = int(wire[key])
    wire["parameter_snapshot_id"] = wire["parameter_snapshot_id"].upper()
    return wire


def test_real_wire_spelling_normalizes_to_attested_base_row():
    row = _project()
    client = Client([_clickhouse_wire(row)])
    storage = ClickHouseAssignmentBaseStorage(client)
    assert storage.read_base_rows("as-1") == [row]
    sql, query_id = client.calls[0]
    assert "SELECT schema_version,assignment_id" in sql
    assert "WHERE assignment_id='as-1'" in sql
    assert "ORDER BY revision_sequence FORMAT JSONEachRow" in sql
    assert query_id is None


def test_single_insert_is_typed_and_retry_stable_without_actual_database():
    row = _project()
    client = Client([])
    storage = ClickHouseAssignmentBaseStorage(client)
    storage.insert_base(row)
    sql, query_id = client.calls[0]
    assert sql.startswith("INSERT INTO arte.live_strategy_assignment_base_revision_typed_v1 (")
    assert "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1" in sql
    assert "insert_deduplication_token='assignment-base:as-1:1:" in sql
    assert json.loads(sql.split("FORMAT JSONEachRow\n", 1)[1]) == row
    assert query_id
    storage.insert_base(row)
    assert client.calls[1] == client.calls[0]


@pytest.mark.parametrize("change", [
    lambda row: {**row, "can_enter": 2},
    lambda row: {**row, "created_at": "2026-09-24 13:00:00.000000001"},
    lambda row: {**row, "content_hash": "0" * 64},
    lambda row: {**row, "unmodeled": "x"},
])
def test_bad_clickhouse_readback_fails_closed(change):
    client = Client([change(_clickhouse_wire(_project()))])
    with pytest.raises((ValueError, TypeError)):
        ClickHouseAssignmentBaseStorage(client).read_base_rows("as-1")


def test_query_does_not_accept_a_different_assignment_or_swallow_duplicates():
    row = _clickhouse_wire(_project())
    client = Client([row, row])
    assert len(ClickHouseAssignmentBaseStorage(client).read_base_rows("as-1")) == 2
    client.rows = [{**row, "assignment_id": "other"}]
    with pytest.raises(ValueError, match="another identity"):
        ClickHouseAssignmentBaseStorage(client).read_base_rows("as-1")
