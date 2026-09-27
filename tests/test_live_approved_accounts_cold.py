import json
import re

import pytest

from src.backend.live_approved_accounts_cold import (
    AccountsSectionHead, ClickHouseAccountsRows, KeeperAccountsHeadReader,
    cold_read_approved_accounts,
)
from src.backend.live_approved_accounts_typed import project_accounts_section
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_approved_accounts_typed import _section
from tests.test_live_signal_completion_keeper import FakeKazoo


class Client:
    def __init__(self, projected):
        self.tables = {
            "live_approved_accounts_section_typed_v1": [projected[0]],
            "live_approved_account_binding_typed_v1": list(projected[1]),
            "live_approved_account_mode_typed_v1": list(projected[2]),
        }
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        table = re.search(r"FROM arte\.([a-z_0-9]+)", sql).group(1)
        limit = int(re.search(r" LIMIT (\d+) FORMAT", sql).group(1))
        return "\n".join(json.dumps(row) for row in self.tables[table][:limit])


class Head:
    def __init__(self, digest):
        self.head = AccountsSectionHead("config-1", digest, 0)
        self.reads = 0
        self.after = None

    def read_head(self, configuration_revision_id):
        assert configuration_revision_id == "config-1"
        self.reads += 1
        if self.after and self.reads == 2:
            self.head = AccountsSectionHead("config-1", self.after, 1)
        return self.head


def _setup(monkeypatch):
    monkeypatch.setattr("src.backend.live_approved_accounts_cold.storage_preflight",
                        lambda client, *, tables: None)
    projected = project_accounts_section(_section(), configuration_revision_id="config-1")
    client = Client(projected)
    return ClickHouseAccountsRows(client), Head(projected[0]["content_hash"]), client


def test_exact_bounded_clickhouse_cold_read_under_stable_head(monkeypatch):
    rows, keeper, client = _setup(monkeypatch)
    assert cold_read_approved_accounts(
        rows, keeper, configuration_revision_id="config-1") == _section()
    assert keeper.reads == 2 and len(client.queries) == 3
    assert [int(re.search(r" LIMIT (\d+) FORMAT", sql).group(1))
            for sql in client.queries] == [2, 3, 5]
    assert all("SELECT " in sql and "FORMAT JSONEachRow" in sql
               and "INSERT" not in sql for sql in client.queries)


def test_cold_read_rejects_duplicate_corrupt_and_changed_head(monkeypatch):
    rows, keeper, client = _setup(monkeypatch)
    client.tables["live_approved_accounts_section_typed_v1"].append(
        dict(client.tables["live_approved_accounts_section_typed_v1"][0]))
    with pytest.raises(ValueError, match="parent differs"):
        cold_read_approved_accounts(rows, keeper, configuration_revision_id="config-1")
    rows, keeper, client = _setup(monkeypatch)
    client.tables["live_approved_account_mode_typed_v1"].pop()
    with pytest.raises(ValueError, match="child count"):
        cold_read_approved_accounts(rows, keeper, configuration_revision_id="config-1")
    rows, keeper, _ = _setup(monkeypatch)
    keeper.after = "f" * 64
    with pytest.raises(RuntimeError, match="head changed"):
        cold_read_approved_accounts(rows, keeper, configuration_revision_id="config-1")


def test_keeper_reader_requires_exact_existing_scalar_head():
    client = FakeKazoo()
    client.add_listener = lambda listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    reader = KeeperAccountsHeadReader(session)
    path = reader.path("config-1")
    client.ensure_path(path.rsplit("/", 1)[0])
    client.create(path, ("1\nconfig-1\n" + "a" * 64).encode())
    assert reader.read_head("config-1").content_hash == "a" * 64
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head("other")
