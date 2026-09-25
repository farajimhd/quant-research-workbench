import json

import pytest

from src.backend.live_plan_membership import MEMBER, PARENT, TABLES, WATCH
import src.backend.live_plan_membership_clickhouse as module
from src.backend.live_plan_membership_clickhouse import LivePlanMembershipClickHouseRows


class Client:
    def __init__(self):
        self.calls = []
        self.response = ""

    def execute(self, sql):
        self.calls.append(sql)
        return self.response


@pytest.fixture(autouse=True)
def _verified_layout(monkeypatch):
    monkeypatch.setattr(module, "storage_preflight",
                        lambda client, *, tables: None)


def _row(contract):
    values = {}
    for name, kind in contract.columns:
        values[name] = (1 if kind.startswith("UInt") else
                        "2026-09-24" if kind == "Date" else
                        "a" * 64 if kind == "FixedString(64)" else "x")
    return values


def test_reads_are_keyed_bounded_and_exactly_projected():
    client = Client()
    rows = LivePlanMembershipClickHouseRows(client)
    client.response = json.dumps(_row(PARENT)) + "\n"
    assert len(rows.read_revisions(configuration_revision_id="cfg",
                                   session_key="2026-09-24", limit=2)) == 1
    assert "FROM arte.live_plan_membership_revision_typed_v1" in client.calls[-1]
    assert "session_key=toDate('2026-09-24')" in client.calls[-1]
    assert "ORDER BY membership_sequence LIMIT 2" in client.calls[-1]
    client.response = json.dumps(_row(MEMBER)) + "\n"
    rows.read_members(configuration_revision_id="cfg", session_key="2026-09-24",
                      membership_sequence=1, limit=2)
    assert "AND membership_sequence=1 ORDER BY assignment_id" in client.calls[-1]
    client.response = json.dumps(_row(WATCH)) + "\n"
    rows.read_watches(configuration_revision_id="cfg", session_key="2026-09-24",
                      membership_sequence=1, limit=2)
    assert "ORDER BY run_plan_id,ticker" in client.calls[-1]
    with pytest.raises(ValueError, match="bound"):
        rows.read_revisions(configuration_revision_id="cfg",
                            session_key="2026-09-24", limit=100_002)
    with pytest.raises(ValueError, match="sequence"):
        rows.read_members(configuration_revision_id="cfg",
                          session_key="2026-09-24", membership_sequence=0, limit=1)
    client.response = json.dumps({**_row(PARENT), "payload_json": "{}"})
    with pytest.raises(RuntimeError, match="projection"):
        rows.read_revisions(configuration_revision_id="cfg",
                            session_key="2026-09-24", limit=1)


def test_inserts_use_only_typed_columns_and_retry_stable_tokens():
    client = Client()
    rows = LivePlanMembershipClickHouseRows(client)
    member = _row(MEMBER)
    watch = _row(WATCH)
    parent = _row(PARENT)
    for row in (member, watch, parent):
        row["configuration_revision_id"] = "cfg"
        row["session_key"] = "2026-09-24"
        row["membership_sequence"] = 1
    rows.insert_members((member,))
    rows.insert_watches((watch,))
    rows.insert_revision(parent)
    assert [sql.split(" (")[0] for sql in client.calls] == [
        f"INSERT INTO arte.{MEMBER.name}", f"INSERT INTO arte.{WATCH.name}",
        f"INSERT INTO arte.{PARENT.name}"]
    assert all("wait_for_async_insert=1" in sql for sql in client.calls)
    assert all("payload_json" not in sql and "blob" not in sql for sql in client.calls)
    for sql in client.calls:
        wire = json.loads(sql.split("FORMAT JSONEachRow\n", 1)[1])
        assert set(wire) in ({name for name, _ in MEMBER.columns},
                             {name for name, _ in WATCH.columns},
                             {name for name, _ in PARENT.columns})
    first = client.calls[0]
    rows.insert_members((member,))
    assert client.calls[-1] == first
    with pytest.raises(ValueError, match="columns"):
        rows.insert_revision({**parent, "payload_json": "{}"})


def test_insertion_rejects_mixed_child_scopes_and_unsafe_read_identity():
    client = Client()
    rows = LivePlanMembershipClickHouseRows(client)
    first = _row(MEMBER)
    second = {**first, "membership_sequence": 2}
    with pytest.raises(ValueError, match="mixes scopes"):
        rows.insert_members((first, second))
    with pytest.raises(ValueError, match="identity"):
        rows.read_revisions(configuration_revision_id="bad\nidentity",
                            session_key="2026-09-24", limit=1)
    assert client.calls == []


def test_transport_requires_verified_ssd_layout_before_use(monkeypatch):
    seen = []
    def blocked(client, *, tables):
        seen.append(tables)
        raise RuntimeError("wrong part placement")
    monkeypatch.setattr(module, "storage_preflight", blocked)
    with pytest.raises(RuntimeError, match="wrong part placement"):
        LivePlanMembershipClickHouseRows(Client())
    assert seen == [TABLES]
