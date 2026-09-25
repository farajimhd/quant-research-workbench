from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_observation_clock import (
    TABLE, normalize_observation_state, project_observation_clock,
    restore_observation_clock,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(values):
    return project_observation_clock(
        values, assignment_id=KEY["assignment_id"], revision=KEY["revision"],
        snapshot_id=KEY["snapshot_id"], session=KEY["session"])


def test_observation_clock_exact_utc_instant_and_price_roundtrip():
    values = dict(last_observed_at="2026-09-24T13:00:00.123456+00:00",
                  last_price=101.25, previous_observed_price=None)
    assert restore_observation_clock(_project(values)) == values
    assert restore_observation_clock(_project({})) == {}
    assert "DateTime64(6, 'UTC')" in dict(TABLE.columns)["last_observed_at"]


def test_zero_microsecond_producer_clock_normalizes_without_mutating_source():
    source = {"last_observed_at": "2026-09-24T13:00:00+00:00", "last_price": 101.0}
    canonical = "2026-09-24T13:00:00.000000+00:00"
    assert _project(source)["last_observed_at"] == canonical
    assert restore_observation_clock(_project(source))["last_observed_at"] == canonical
    assert normalize_observation_state(source)["last_observed_at"] == canonical
    assert source["last_observed_at"] == "2026-09-24T13:00:00+00:00"


def test_observation_clock_composite_and_cold_state_roundtrip():
    source = {**_state(), "last_observed_at": "2026-09-24T13:00:00.123456+00:00",
              "last_price": 101.25, "previous_observed_price": 101.0}
    rows = project_modeled_assignment_state(source, **KEY)
    assert restore_modeled_assignment_state(rows, **KEY) == source
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert TABLE in STATE_TABLES
    flat, commit = project_state_snapshot(source, **KEY)
    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])
    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == source


@pytest.mark.parametrize("values", [
    {"last_observed_at": "2026-09-24T13:00:00.123+00:00"},
    {"last_observed_at": "2026-09-24T09:00:00.123456-04:00"},
    {"last_observed_at": "2026-09-24T13:00:00.123456"},
    {"last_observed_at": None},
    {"last_price": 101}, {"last_price": float("nan")},
    {"previous_observed_price": True}, {"unknown": 1},
])
def test_observation_clock_rejects_noncanonical_or_untyped(values):
    with pytest.raises(ValueError):
        _project(values)


def test_observation_clock_rejects_tampering():
    row = _project({"last_price": 101.0})
    with pytest.raises(ValueError):
        restore_observation_clock({**row, "last_price": 102.0})


def test_observation_clock_clickhouse_wire_restores_exact_utc_text():
    import json
    from src.backend.live_assignment_state_storage import ClickHouseAssignmentStateStorage
    row = _project({"last_observed_at": "2026-09-24T13:00:00.123456+00:00",
                    "last_price": 101.0})
    wire = {**row, "last_observed_at": "2026-09-24 13:00:00.123456",
            "last_observed_at_present": 1, "last_price_present": 1,
            "previous_observed_price_present": 0}
    class Client:
        def execute(self, sql):
            return json.dumps(wire)
    identity = dict(run_id=KEY["run_id"], assignment_id=KEY["assignment_id"],
                    revision=KEY["revision"], snapshot_id=KEY["snapshot_id"],
                    session=KEY["session"])
    assert ClickHouseAssignmentStateStorage(Client()).read(TABLE.name, identity) == [row]
