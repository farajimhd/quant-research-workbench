from copy import deepcopy
from dataclasses import dataclass
import json

import pytest

from src.backend.live_assignment_roster import (
    CHANGE, KEEPER_HEAD_PATH, REVISION, ROSTER_ID, ZERO_HASH,
    ClickHouseRosterStorage, ManagedRosterHeadReader, RosterChange, RosterHead,
    audit_cold_roster, project_roster_revision,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


@dataclass(frozen=True)
class Assignment:
    assignment_id: str


@dataclass(frozen=True)
class BaseHead:
    sequence: int
    content_hash: str


class Storage:
    def __init__(self, revisions):
        self.parents = [parent for parent, _ in revisions]
        self.children = {parent["sequence"]: list(children)
                         for parent, children in revisions}

    def read_revisions(self, *, limit):
        return deepcopy(self.parents[:limit])

    def read_changes(self, sequence, *, limit):
        return deepcopy(self.children[sequence][:limit])


class Heads:
    def __init__(self, head, bases):
        self.head, self.bases = head, bases
        self.reads = 0

    def read_head(self, assignment_id=None):
        self.reads += 1
        return self.head if assignment_id is None else self.bases[assignment_id]


def _fixture():
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    first = project_roster_revision(
        [RosterChange("assignment-a", "upsert", 1, a),
         RosterChange("assignment-b", "upsert", 1, b)],
        sequence=1, previous_hash=ZERO_HASH)
    second = project_roster_revision(
        [RosterChange("assignment-a", "upsert", 2, c),
         RosterChange("assignment-b", "remove")],
        sequence=2, previous_hash=first[0]["content_hash"])
    storage = Storage((first, second))
    heads = Heads(RosterHead(2, second[0]["content_hash"], 5),
                  {"assignment-a": BaseHead(2, c)})
    return storage, heads


def test_normalized_delta_cold_enumeration_reuses_base_identity():
    storage, heads = _fixture()
    assert "storage_policy = 'live_market_ssd'" in REVISION.ddl()
    assert "storage_policy = 'live_market_ssd'" in CHANGE.ddl()
    assert all("JSON" not in kind and "Map" not in kind
               for table in (REVISION, CHANGE) for _, kind in table.columns)
    assert {name for name, _ in REVISION.columns} == {
        "roster_id", "sequence", "previous_hash", "change_count",
        "change_hash", "content_hash"}
    recovered = audit_cold_roster(
        storage, heads, heads, lambda key: Assignment(key))
    assert recovered == {"assignment-a": Assignment("assignment-a")}
    assert storage.parents[0]["roster_id"] == ROSTER_ID
    assert heads.reads == 4  # roster before/after plus base before/after


@pytest.mark.parametrize("mutation", ["gap", "duplicate", "future", "hash", "ordinal", "child"])
def test_cold_enumeration_rejects_missing_duplicate_or_conflicting_rows(mutation):
    storage, heads = _fixture()
    if mutation == "gap":
        storage.parents.pop(0)
    elif mutation == "duplicate":
        storage.parents[1] = deepcopy(storage.parents[0])
    elif mutation == "future":
        storage.parents.append(deepcopy(storage.parents[1]))
    elif mutation == "hash":
        storage.parents[1]["content_hash"] = "f" * 64
    elif mutation == "ordinal":
        storage.children[1][1]["ordinal"] = 0
    else:
        storage.children[2].pop()
    with pytest.raises(ValueError):
        audit_cold_roster(storage, heads, heads, lambda key: Assignment(key))


def test_cold_enumeration_rejects_stale_base_or_concurrent_head_change():
    storage, heads = _fixture()
    heads.bases["assignment-a"] = BaseHead(1, "a" * 64)
    with pytest.raises(ValueError, match="base head"):
        audit_cold_roster(storage, heads, heads, lambda key: Assignment(key))
    storage, heads = _fixture()

    class ChangingHeads(Heads):
        def read_head(self, assignment_id=None):
            if assignment_id is None and self.reads:
                self.head = RosterHead(3, "f" * 64, 6)
            return super().read_head(assignment_id)

    changing = ChangingHeads(heads.head, heads.bases)
    with pytest.raises(RuntimeError, match="changed"):
        audit_cold_roster(storage, changing, changing, lambda key: Assignment(key))


def test_roster_projection_rejects_ambiguous_or_untyped_delta():
    for changes in (
        [RosterChange("assignment-b", "upsert", 1, "a" * 64),
         RosterChange("assignment-a", "upsert", 1, "b" * 64)],
        [RosterChange("assignment-a", "remove")],
        [RosterChange("assignment-a", "upsert", 1, "not-a-hash")],
        [RosterChange("assignment-a", "upsert", True, "a" * 64)],
        [RosterChange("assignment-a", "unknown", 1, "a" * 64)],
    ):
        if changes[0].operation == "remove":
            # Syntactically valid removal, but invalid against empty genesis.
            row = project_roster_revision(changes, sequence=1, previous_hash=ZERO_HASH)
            storage = Storage((row,))
            heads = Heads(RosterHead(1, row[0]["content_hash"], 0), {})
            with pytest.raises(ValueError, match="no prior member"):
                audit_cold_roster(storage, heads, heads, lambda key: Assignment(key))
        else:
            with pytest.raises(ValueError):
                project_roster_revision(changes, sequence=1, previous_hash=ZERO_HASH)


def test_managed_keeper_head_reader_exact_wire_and_session():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    reader = ManagedRosterHeadReader(session)
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head()
    client.ensure_path(KEEPER_HEAD_PATH.rsplit("/", 1)[0])
    client.create(KEEPER_HEAD_PATH,
                  f"1\n{ROSTER_ID}\n2\n{'a' * 64}".encode())
    assert reader.read_head() == RosterHead(2, "a" * 64, 0)
    client.transaction().set_data(
        KEEPER_HEAD_PATH, f"1\n{ROSTER_ID}\n02\n{'a' * 64}".encode(),
        version=0).commit()
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head()
    session._on_state("SUSPENDED")
    with pytest.raises(RuntimeError, match="unavailable"):
        reader.read_head()


def test_managed_keeper_reader_rejects_session_loss_mid_read():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    client.ensure_path(KEEPER_HEAD_PATH.rsplit("/", 1)[0])
    client.create(KEEPER_HEAD_PATH,
                  f"1\n{ROSTER_ID}\n1\n{'a' * 64}".encode())
    original = client.get

    def lost(path):
        result = original(path)
        session._on_state("SUSPENDED")
        return result

    client.get = lost
    with pytest.raises(RuntimeError, match="changed"):
        ManagedRosterHeadReader(session).read_head()


def test_clickhouse_storage_uses_exact_bounded_selects_and_cold_audit():
    source, heads = _fixture()

    class Client:
        def __init__(self):
            self.sql = []

        def execute(self, sql):
            self.sql.append(sql)
            assert sql.startswith("SELECT ") and sql.endswith(" FORMAT JSONEachRow")
            if f"FROM arte.{REVISION.name}" in sql:
                assert sql.startswith("SELECT " + ",".join(
                    name for name, _ in REVISION.columns) + " FROM ")
                assert "ORDER BY sequence LIMIT 10001" in sql
                rows = source.parents
            else:
                assert f"FROM arte.{CHANGE.name}" in sql
                assert sql.startswith("SELECT " + ",".join(
                    name for name, _ in CHANGE.columns) + " FROM ")
                assert "ORDER BY ordinal LIMIT 65536" in sql
                sequence = int(sql.split("AND sequence=", 1)[1].split(" ", 1)[0])
                rows = source.children[sequence]
            assert f"roster_id='{ROSTER_ID}'" in sql
            return "\n".join(json.dumps(row) for row in rows)

    client = Client()
    storage = ClickHouseRosterStorage(client)
    assert audit_cold_roster(storage, heads, heads, lambda key: Assignment(key)) == {
        "assignment-a": Assignment("assignment-a")}
    assert len(client.sql) == 3


def test_clickhouse_storage_rejects_unbounded_or_mixed_rows_without_write():
    source, _ = _fixture()

    class Client:
        def __init__(self, rows):
            self.rows, self.sql = rows, []

        def execute(self, sql):
            self.sql.append(sql)
            assert sql.startswith("SELECT ")
            return "\n".join(json.dumps(row) for row in self.rows)

    client = Client([dict(source.parents[0], roster_id="other")])
    storage = ClickHouseRosterStorage(client)
    with pytest.raises(ValueError, match="unmodeled"):
        storage.read_revisions(limit=2)
    client.rows = [dict(source.parents[0], unexpected=1)]
    with pytest.raises(ValueError, match="unmodeled"):
        storage.read_revisions(limit=2)
    client.rows = [dict(source.children[1][0], sequence=2)]
    with pytest.raises(ValueError, match="unmodeled"):
        storage.read_changes(1, limit=2)
    with pytest.raises(ValueError, match="limit"):
        storage.read_revisions(limit=True)
    assert all(sql.startswith("SELECT ") for sql in client.sql)
