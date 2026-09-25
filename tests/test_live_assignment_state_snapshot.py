from copy import deepcopy

import pytest

from src.backend.live_assignment_state_snapshot import (
    STATE_COMMIT, STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    recover_attested_assignment,
)
from src.backend.live_assignment_base_revision import project_base_revision
from src.trading_runtime.arte_long_momentum_parameter_journal import PARAMETER_TABLES
from tests.test_live_assignment_base_revision import (
    _assignment, HASH_A, GENESIS, CHILD_REFS,
)
from src.backend.live_assignment_base_preflight import (
    staged_state_operator_ddl, staged_state_grants, staged_state_storage_preflight,
    ASSIGNMENT_CHILD_TABLES, staged_assignment_child_operator_ddl,
    staged_assignment_child_grants, staged_assignment_child_storage_preflight,
)
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_arte_long_momentum_squeeze_v7_evidence import _level


class FakeStorage:
    def __init__(self, rows, commit, identity=KEY):
        self.rows = {**deepcopy(rows), STATE_COMMIT.name: [deepcopy(commit)]}
        self.identity = identity

    def read(self, table, identity):
        assert identity == self.identity
        return deepcopy(self.rows[table])


def _project():
    return project_state_snapshot(_state(), **KEY)


def _recover(storage, commit):
    return recover_state_snapshot(storage, **KEY,
                                  expected_commit_hash=commit["content_hash"])


def test_state_snapshot_exact_fake_cold_read():
    rows, commit = _project()
    assert _recover(FakeStorage(rows, commit), commit) == _state()
    assert commit["child_count"] == sum(len(value) for value in rows.values())
    assert len({table.name for table in STATE_TABLES}) == len(STATE_TABLES)
    assert all("storage_policy = 'live_market_ssd'" in table.ddl()
               for table in STATE_TABLES)


def test_state_snapshot_restores_ordered_v7_and_progress_families():
    source = _state()
    source["squeeze_entry"].update(
        anchor=_level(), broken_levels=["r2", "r1"], target_multiplier=8)
    source["squeeze_breakout"].update(
        session="2026-09-24", activated_at=100.0,
        macd_1s=dict(open=True, episode_id=100.0, observed_at=101.0,
                     line=0.4, signal=0.3),
        session_targets=dict(broken_levels=["r2", "r1"], target_multiplier=8),
        latest_broken_resistance=_level(),
        frozen_gap={"levels": [_level(), {**_level(), "unified_level_id": "v7-2"}]},
    )
    rows, commit = project_state_snapshot(source, **KEY)
    assert _recover(FakeStorage(rows, commit), commit) == source


def test_state_snapshot_rejects_missing_duplicate_and_unmodeled_state():
    rows, commit = _project()
    storage = FakeStorage(rows, commit)
    storage.rows[STATE_COMMIT.name] = []
    with pytest.raises(ValueError, match="commit"):
        _recover(storage, commit)
    storage = FakeStorage(rows, commit)
    storage.rows[STATE_COMMIT.name].append(deepcopy(commit))
    with pytest.raises(ValueError, match="commit"):
        _recover(storage, commit)
    with pytest.raises(ValueError, match="unmodeled"):
        project_state_snapshot({**_state(), "mystery": 1}, **KEY)


def test_state_snapshot_rejects_missing_child_and_wrong_head():
    rows, commit = _project()
    storage = FakeStorage(rows, commit)
    table = next(name for name, values in rows.items() if values)
    storage.rows[table].pop()
    with pytest.raises(ValueError, match="fence"):
        _recover(storage, commit)
    with pytest.raises(ValueError, match="unattested"):
        recover_state_snapshot(FakeStorage(rows, commit), **KEY,
                               expected_commit_hash="0" * 64)


class FakeSystem:
    def __init__(self):
        self.policy = [{"disks": ["live_market_ssd"]}]
        self.tables = [dict(name=table.name, engine="MergeTree",
                            storage_policy="live_market_ssd",
                            partition_key=table.partition, sorting_key=table.order)
                       for table in STATE_TABLES]
        self.columns = [dict(table=table.name, name=name, type=kind)
                        for table in STATE_TABLES for name, kind in table.columns]
        self.parts = []
        self.sql = []

    def execute(self, sql):
        import json
        self.sql.append(sql)
        if "system.storage_policies" in sql:
            result = self.policy
        elif "system.tables" in sql:
            result = self.tables
        elif "system.columns" in sql:
            result = self.columns
        elif "system.parts" in sql:
            result = self.parts
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in result)


def test_state_operator_plan_and_exact_read_only_preflight():
    assert len(staged_state_operator_ddl()) == len(STATE_TABLES)
    assert all("storage_policy = 'live_market_ssd'" in ddl
               for ddl in staged_state_operator_ddl())
    grants = staged_state_grants(reader_principal="live_reader",
                                 writer_principal="live_writer")
    assert len(grants) == 2 * len(STATE_TABLES)
    assert f"GRANT SELECT ON arte.{STATE_COMMIT.name} TO live_reader" in grants
    assert f"GRANT SELECT, INSERT ON arte.{STATE_COMMIT.name} TO live_writer" in grants
    fake = FakeSystem()
    staged_state_storage_preflight(fake)
    assert len(fake.sql) == 4 and all(sql.startswith("SELECT ") for sql in fake.sql)
    fake.parts = [{"table": STATE_COMMIT.name, "disk_name": "default"}]
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        staged_state_storage_preflight(fake)


def test_complete_parameter_and_state_child_install_inventory():
    assert len(staged_assignment_child_operator_ddl()) == len(ASSIGNMENT_CHILD_TABLES)
    assert len(staged_assignment_child_grants(
        reader_principal="live_reader", writer_principal="live_writer")) == 2 * len(
            ASSIGNMENT_CHILD_TABLES)
    fake = FakeSystem()
    fake.tables = [dict(name=table.name, engine="MergeTree",
                        storage_policy="live_market_ssd",
                        partition_key=table.partition, sorting_key=table.order)
                   for table in ASSIGNMENT_CHILD_TABLES]
    fake.columns = [dict(table=table.name, name=name, type=kind)
                    for table in ASSIGNMENT_CHILD_TABLES for name, kind in table.columns]
    staged_assignment_child_storage_preflight(fake)
    fake.columns.pop()
    with pytest.raises(RuntimeError, match="columns"):
        staged_assignment_child_storage_preflight(fake)


def test_base_state_parameter_attested_join_requires_matching_parameter_commit(monkeypatch):
    state_identity = {**KEY, "revision": 1}
    rows, state_commit = project_state_snapshot(_state(), **state_identity)
    base = project_base_revision(_assignment(), revision_sequence=1,
                                 **CHILD_REFS,
                                 parameter_content_hash=HASH_A,
                                 state_content_hash=state_commit["content_hash"],
                                 previous_revision_hash=GENESIS)
    monkeypatch.setattr("src.backend.live_assignment_state_snapshot.load_attested_parameters",
                        lambda *args, **kwargs: {"closed": True})

    class ParameterStorage:
        def __init__(self, digest):
            self.digest = digest

        def read(self, table, identity):
            assert table == PARAMETER_TABLES[-1].name
            return [{"content_hash": self.digest}]

    kwargs = dict(base_rows=[base], state_storage=FakeStorage(rows, state_commit,
                                                              state_identity),
                  parameter_admission=object(),
                  assignment_id=KEY["assignment_id"], revision_sequence=1,
                  expected_base_hash=base["content_hash"],
                  previous_revision_hash=GENESIS)
    # The base row cannot be joined to a different assignment identity.
    with pytest.raises(ValueError, match="attestation"):
        recover_attested_assignment(parameter_storage=ParameterStorage(HASH_A), **kwargs)
    matching_assignment = _assignment()
    from dataclasses import replace
    matching_assignment = replace(matching_assignment, assignment_id=KEY["assignment_id"])
    base = project_base_revision(matching_assignment, revision_sequence=1,
                                 **CHILD_REFS,
                                 parameter_content_hash=HASH_A,
                                 state_content_hash=state_commit["content_hash"],
                                 previous_revision_hash=GENESIS)
    kwargs["base_rows"] = [base]
    kwargs["expected_base_hash"] = base["content_hash"]
    with pytest.raises(ValueError, match="parameter hash"):
        recover_attested_assignment(parameter_storage=ParameterStorage("b" * 64), **kwargs)
    recovered = recover_attested_assignment(
        parameter_storage=ParameterStorage(HASH_A), **kwargs)
    assert recovered.assignment_id == KEY["assignment_id"]
    assert recovered.state == _state()
    assert recovered.parameters == {"closed": True}


def test_two_state_revisions_reuse_one_immutable_parameter_snapshot(monkeypatch):
    from dataclasses import replace
    from tests.test_live_assignment_base_revision import PARAMETER_SNAPSHOT

    seen = []
    def attested_parameters(_storage, _admission, **identity):
        seen.append(identity["snapshot_id"])
        return {"closed": True}
    monkeypatch.setattr("src.backend.live_assignment_state_snapshot.load_attested_parameters",
                        attested_parameters)

    class Parameters:
        def read(self, table, identity):
            assert identity["snapshot_id"] == PARAMETER_SNAPSHOT
            return [{"content_hash": HASH_A}]

    assignment = replace(_assignment(), assignment_id=KEY["assignment_id"])
    prior = GENESIS
    for revision, snapshot in ((1, KEY["snapshot_id"]),
                               (2, "5a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f")):
        identity = {**KEY, "revision": revision, "snapshot_id": snapshot}
        state = _state()
        rows, state_commit = project_state_snapshot(state, **identity)
        base = project_base_revision(
            assignment, revision_sequence=revision,
            **{**CHILD_REFS, "state_snapshot_id": snapshot,
               "state_snapshot_revision": revision},
            parameter_content_hash=HASH_A,
            state_content_hash=state_commit["content_hash"],
            previous_revision_hash=prior)
        recovered = recover_attested_assignment(
            base_rows=[base], state_storage=FakeStorage(rows, state_commit, identity),
            parameter_storage=Parameters(), parameter_admission=object(),
            assignment_id=KEY["assignment_id"], revision_sequence=revision,
            expected_base_hash=base["content_hash"],
            previous_revision_hash=prior)
        assert recovered.state == state
        prior = base["content_hash"]
    assert seen == [PARAMETER_SNAPSHOT, PARAMETER_SNAPSHOT]
