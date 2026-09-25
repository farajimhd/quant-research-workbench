from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from src.backend.live_assignment_base_revision import (
    BASE_REVISION, project_base_revision, recover_base_revision,
)
from src.backend.live_assignment_base_preflight import (
    operator_ddl, staged_base_revision_preflight, staged_grants,
)
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
GENESIS = "0" * 64


def _assignment():
    return StrategyAssignment(
        assignment_id="as-1", strategy_id="early-squeeze-momentum",
        strategy_revision=47, account_id="acct", ticker="ABCD", conid=123,
        status=AssignmentStatus.WATCHING,
        permissions=StrategyPermissions(observe=True, enter=True),
        parameters={"entry": {"breakout_timeframe": "1s"}},
        state={"campaign_id": "c-1"}, source="live_signal",
        created_at=datetime(2026, 9, 24, 13, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 24, 13, 1, tzinfo=UTC),
    )


def _project(assignment=None):
    return project_base_revision(
        assignment or _assignment(), revision_sequence=1,
        parameter_content_hash=HASH_A, state_content_hash=HASH_B,
        previous_revision_hash=GENESIS,
    )


def _recover(rows):
    return recover_base_revision(
        rows, expected_assignment_id="as-1", expected_sequence=1,
        expected_hash=_project()["content_hash"],
        parameter_content_hash=HASH_A, state_content_hash=HASH_B,
        previous_revision_hash=GENESIS,
    )


def test_base_revision_exact_cold_read_and_policy():
    row = _project()
    assert _recover([row]) == row
    assert BASE_REVISION.name.endswith("_typed_v1")
    assert "assignment_id, revision_sequence" in BASE_REVISION.order
    assert not any("JSON" in kind or "Map" in kind for _, kind in BASE_REVISION.columns)


@pytest.mark.parametrize("change", [
    lambda row: {**row, "can_enter": False},
    lambda row: {**row, "source": "wrong"},
    lambda row: {**row, "extra": "unmodeled"},
    lambda row: {**row, "status": "not-a-status"},
    lambda row: {**row, "can_enter": 1},
])
def test_cold_read_rejects_corruption_and_unmodeled_columns(change):
    with pytest.raises(ValueError):
        _recover([change(_project())])


def test_cold_read_requires_single_externally_attested_row():
    row = _project()
    with pytest.raises(ValueError):
        _recover([])
    with pytest.raises(ValueError):
        _recover([row, row])
    with pytest.raises(ValueError):
        recover_base_revision([row], expected_assignment_id="as-1", expected_sequence=1,
                              expected_hash=row["content_hash"], parameter_content_hash=HASH_B,
                              state_content_hash=HASH_B, previous_revision_hash=GENESIS)


def test_projection_rejects_bad_revision_and_timestamps():
    with pytest.raises(ValueError):
        project_base_revision(_assignment(), revision_sequence=2,
                              parameter_content_hash=HASH_A, state_content_hash=HASH_B,
                              previous_revision_hash=GENESIS)
    with pytest.raises(ValueError):
        _project(replace(_assignment(), updated_at=datetime(2026, 9, 24, 12, tzinfo=UTC)))


class _FakeSystem:
    def __init__(self):
        self.policy = [{"disks": ["live_market_ssd"]}]
        self.table = [dict(name=BASE_REVISION.name, engine="MergeTree",
                           storage_policy="live_market_ssd",
                           partition_key=BASE_REVISION.partition,
                           sorting_key=BASE_REVISION.order)]
        self.columns = [dict(name=name, type=kind) for name, kind in BASE_REVISION.columns]
        self.parts = []
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        if "system.storage_policies" in sql:
            result = self.policy
        elif "system.tables" in sql:
            result = self.table
        elif "system.columns" in sql:
            result = self.columns
        elif "system.parts" in sql:
            result = self.parts
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in result)


def test_operator_plan_exact_grants_and_read_only_layout_preflight():
    assert operator_ddl() == (BASE_REVISION.ddl(),)
    assert "storage_policy = 'live_market_ssd'" in operator_ddl()[0]
    assert staged_grants(reader_principal="live_reader", writer_principal="live_writer") == (
        f"GRANT SELECT ON arte.{BASE_REVISION.name} TO live_reader",
        f"GRANT SELECT, INSERT ON arte.{BASE_REVISION.name} TO live_writer",
    )
    with pytest.raises(ValueError):
        staged_grants(reader_principal="live-reader", writer_principal="live_writer")
    fake = _FakeSystem()
    staged_base_revision_preflight(fake)
    assert len(fake.sql) == 4
    assert all(sql.startswith("SELECT ") for sql in fake.sql)


@pytest.mark.parametrize("mutate", [
    lambda fake: fake.policy[0].update(disks=["default"]),
    lambda fake: fake.table[0].update(storage_policy="default"),
    lambda fake: fake.table[0].update(sorting_key="assignment_id"),
    lambda fake: fake.columns.pop(),
    lambda fake: fake.parts.append({"disk_name": "default"}),
])
def test_preflight_rejects_missing_or_misplaced_contract(mutate):
    fake = _FakeSystem()
    mutate(fake)
    with pytest.raises(RuntimeError):
        staged_base_revision_preflight(fake)
