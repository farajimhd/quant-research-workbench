from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType
import asyncio
import json
import re

import pytest

from src.market_engine.volume_rank_rules import VolumeUniverseRule
from src.trading_runtime.columnar_rule_program import InputField, Operation as O, RuleNode as N, RuleProgram
from src.trading_runtime.columnar_strategy_release import project_release, restore_release, ReleasePublisher
from src.trading_runtime.columnar_strategy_reader import read_program_release, load_program_release
from src.trading_runtime.columnar_strategy_schema import RELEASE, FIELD, RULE, NODE, UNIVERSE
from src.trading_runtime.optimized_program_specification import OptimizedProgramSpecification, STAGES


def bundle():
    field = InputField("history.test", "ratio", "arte.history_test_v1", "a" * 64, 1000)
    program = RuleProgram((N(O.FEATURE, field=field.name), N(O.CONSTANT),
                           N(O.GREATER, left=0, right=1)), 2)
    policy = dict(stop_fraction=.1, target_fraction=.2, reduce_fraction=.5,
                  trail_fraction=.1, add_minimum_profit=.01, reduce_minimum_profit=.02,
                  maximum_adds=3, cooldown_seconds=175, swing_left=2, swing_right=1)
    spec = OptimizedProgramSpecification("b" * 64, 24, 872,
        {stage: program for stage in STAGES}, {}, policy, 1000., 200., .005, 1.,
        VolumeUniverseRule(30, 10, .8, 50), "c" * 64)
    kwargs = dict(strategy_number=62, strategy_id="shared-program", release_id="00000000-0000-0000-0000-000000000062",
        code_hash="d" * 64, universe_source_hash="e" * 64,
        published_at=datetime(2026, 10, 11, tzinfo=timezone.utc))
    return spec, {field.name: field}, kwargs


def mutable(rows):
    return {name: tuple(dict(row) for row in family) for name, family in rows.items()}


def test_normalized_roundtrip_and_cash_are_immutable_parameters():
    spec, fields, kwargs = bundle()
    rows = project_release(spec, fields, **kwargs)
    result = restore_release(rows)
    assert result.header["entry_cash"] == 1000
    assert result.header["add_cash"] == 200
    assert result.programs == spec.programs
    changed = project_release(spec, fields, **kwargs, entry_cash=1500)
    assert restore_release(changed).header["content_hash"] != result.header["content_hash"]
    with pytest.raises(TypeError):
        result.header["entry_cash"] = 2000


@pytest.mark.parametrize("table", [FIELD, RULE, NODE, UNIVERSE])
def test_incomplete_or_changed_children_cannot_be_admitted(table):
    spec, fields, kwargs = bundle()
    rows = mutable(project_release(spec, fields, **kwargs))
    rows[table.name] = rows[table.name][1:]
    with pytest.raises(ValueError):
        restore_release(rows)


def test_wrong_foreign_key_and_extra_json_field_rejected():
    spec, fields, kwargs = bundle()
    for key, value in [("release_id", "00000000-0000-0000-0000-000000000001"), ("json", "{}")]:
        rows = mutable(project_release(spec, fields, **kwargs))
        rows[NODE.name][0][key] = value
        with pytest.raises(ValueError, match="columns or child foreign"):
            restore_release(rows)


class Storage:
    def __init__(self):
        self.rows = {table.name: () for table in (FIELD, RULE, NODE, UNIVERSE, RELEASE)}
        self.writes = []
        self.owner = True
        self.fail = None
        self.checked = False

    def preflight(self):
        self.checked = True

    def assert_owner(self):
        if not self.owner:
            raise RuntimeError("ownership lost")

    def read_number(self, number):
        return deepcopy(self.rows) if self.rows[RELEASE.name] else None

    def read_children(self, release_id):
        return {t.name: deepcopy(self.rows[t.name]) for t in (FIELD, RULE, NODE, UNIVERSE)}

    def insert_rows(self, table_name, rows):
        assert self.checked
        self.writes.append(table_name)
        self.rows[table_name] = tuple(dict(row) for row in rows)
        if self.fail == table_name:
            raise RuntimeError("ambiguous insert")


def test_publication_seals_last_and_exact_retry_does_not_duplicate():
    spec, fields, kwargs = bundle()
    rows = project_release(spec, fields, **kwargs)
    storage = Storage()
    publisher = ReleasePublisher(storage)
    first = asyncio.run(publisher.publish(rows))
    assert storage.writes == [FIELD.name, RULE.name, NODE.name, UNIVERSE.name, RELEASE.name]
    assert asyncio.run(publisher.publish(rows)).header == first.header
    assert len(storage.writes) == 5
    with pytest.raises(ValueError, match="different immutable"):
        asyncio.run(publisher.publish(project_release(spec, fields, **kwargs, add_cash=300)))


def test_ambiguous_insert_withholds_seal_and_refuses_retry():
    spec, fields, kwargs = bundle()
    rows = project_release(spec, fields, **kwargs)
    storage = Storage()
    storage.fail = NODE.name
    publisher = ReleasePublisher(storage)
    with pytest.raises(RuntimeError, match="ambiguous"):
        asyncio.run(publisher.publish(rows))
    assert storage.rows[RELEASE.name] == ()
    with pytest.raises(RuntimeError, match="stopped"):
        asyncio.run(publisher.publish(rows))
    storage.fail = None
    with pytest.raises(ValueError, match="explicit recovery"):
        asyncio.run(ReleasePublisher(storage).publish(rows))


def test_ownership_loss_prevents_all_writes():
    spec, fields, kwargs = bundle()
    storage = Storage()
    storage.owner = False
    with pytest.raises(RuntimeError, match="ownership lost"):
        asyncio.run(ReleasePublisher(storage).publish(project_release(spec, fields, **kwargs)))
    assert storage.writes == []


class Reader:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.extra_grant = ""
        self.bad_disk = False
        self.queries = []

    def close(self):
        self.closed = True

    def execute(self, query):
        self.queries.append(query)
        tables = (FIELD, RULE, NODE, UNIVERSE, RELEASE)
        def encode(rows):
            return "\n".join(json.dumps(dict(row), default=str) for row in rows)
        if query == "SELECT currentUser()":
            return "program_release_reader"
        if query == "SHOW GRANTS FINAL":
            return "\n".join([*(f"GRANT SELECT ON arte.{t.name} TO program_release_reader" for t in tables),
                *(f"GRANT SELECT ON system.{name} TO program_release_reader" for name in (
                    "tables", "columns", "parts", "storage_policies", "data_skipping_indices")),
                *([self.extra_grant] if self.extra_grant else [])])
        if "FROM system.storage_policies" in query:
            return encode([dict(disks=["live_market_ssd"])])
        if "FROM system.tables" in query:
            return encode([dict(name=t.name, engine="MergeTree", storage_policy="live_market_ssd",
                partition_key=t.partition, sorting_key=t.order) for t in tables])
        if "FROM system.columns" in query:
            return encode([dict(table=t.name, name=name, type=kind) for t in tables for name, kind in t.columns])
        if "FROM system.data_skipping_indices" in query:
            return ""
        if "FROM system.parts" in query:
            return encode([dict(table=FIELD.name, disk_name="default")]) if self.bad_disk else ""
        name = re.search(r"FROM arte\.([a-z0-9_]+)", query).group(1)
        return encode(self.rows[name])


def test_database_readback_checks_types_placement_hashes_and_approved_identity():
    spec, fields, kwargs = bundle()
    rows = project_release(spec, fields, **kwargs)
    reader = Reader(rows)
    identity = dict(strategy_number=62, expected_content_hash=rows[RELEASE.name][0]["content_hash"],
                    expected_code_hash="d" * 64, expected_winner_hash="b" * 64)
    result = asyncio.run(load_program_release(lambda: reader, **identity))
    assert result.programs == spec.programs and reader.closed
    assert all(query.startswith(("SELECT ", "SHOW GRANTS FINAL")) for query in reader.queries)
    with pytest.raises(ValueError, match="pinned approval"):
        read_program_release(Reader(rows), **{**identity, "expected_code_hash": "f" * 64})
    bad = Reader(rows)
    bad.bad_disk = True
    with pytest.raises(ValueError, match="outside live_market_ssd"):
        read_program_release(bad, **identity)
    bad = Reader(rows)
    bad.extra_grant = f"GRANT INSERT ON arte.{FIELD.name} TO program_release_reader"
    with pytest.raises(ValueError, match="unauthorized"):
        read_program_release(bad, **identity)


def test_database_duplicate_seals_fail_instead_of_selecting_latest():
    spec, fields, kwargs = bundle()
    rows = mutable(project_release(spec, fields, **kwargs))
    identity = dict(strategy_number=62, expected_content_hash=rows[RELEASE.name][0]["content_hash"],
                    expected_code_hash="d" * 64, expected_winner_hash="b" * 64)
    rows[RELEASE.name] *= 2
    with pytest.raises(ValueError, match="duplicate or excessive"):
        read_program_release(Reader(rows), **identity)
