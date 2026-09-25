import json
import re

import pytest

from src.backend.backtest_terminal_v2_preflight import (
    terminal_v2_operator_preflight, terminal_v2_permission_preflight,
    terminal_v2_storage_preflight, terminal_v2_operator_provisioning_sql,
    terminal_v2_keeper_proof_preflight, _INSERT,
)
from src.trading_runtime.arte_journal_schema import (
    BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES, MARKET_READ_TABLES, TABLES,
    VERSIONED_JOURNAL_V2_TABLES,
)
from tests.test_arte_journal_v2_profile import V2Catalog


class FullV2Catalog(V2Catalog):
    def __init__(self):
        super().__init__()
        terminal = {table.name for table in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES}
        self.tables += BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES
        self.writable |= terminal
        self.readable |= terminal


class FakeCatalog:
    def __init__(self):
        self.contracts = {table.name: table for table in
                          (*TABLES, *BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES)}
        self.read = set(self.contracts) | MARKET_READ_TABLES
        self.insert = set(_INSERT)
        self.policy_disks = ["live_market_ssd"]
        self.bad_parts = []
        self.missing_indexes = []
        self.column_override = {}
        self.extra_grant = None
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)
        assert sql.startswith(("SELECT ", "SHOW GRANTS", "CHECK GRANT "))
        if sql == "SELECT currentUser()":
            return "terminal_v2"
        if sql == "SHOW GRANTS":
            grants = [
                *(f"GRANT SELECT ON arte.{name} TO terminal_v2" for name in sorted(self.read)),
                *(f"GRANT INSERT ON arte.{name} TO terminal_v2" for name in sorted(self.insert)),
                *(f"GRANT SELECT ON system.{name} TO terminal_v2" for name in (
                    "storage_policies", "tables", "columns", "parts",
                    "data_skipping_indices")),
            ]
            if self.extra_grant:
                grants.append(self.extra_grant)
            return "\n".join(grants)
        if sql.startswith("CHECK GRANT "):
            match = re.fullmatch(r"CHECK GRANT (.+) ON (.+)", sql)
            assert match
            privilege, scope = match.groups()
            database, table = scope.split(".", 1)
            return ("1" if database == "arte" and
                    ((privilege == "SELECT" and table in self.read)
                     or (privilege == "INSERT" and table in self.insert))
                    else "0")
        if "FROM system.storage_policies" in sql:
            rows = [{"disks": self.policy_disks}]
        else:
            names = set(re.findall(r"'([^']+)'", sql.split("name IN (", 1)[1].split(")", 1)[0]
                                   if "name IN (" in sql else
                                   sql.split("table IN (", 1)[1].split(")", 1)[0]
                                   if "table IN (" in sql else ""))
            if "FROM system.tables" in sql:
                rows = [{
                    "name": name, "engine": "MergeTree",
                    "storage_policy": "live_market_ssd",
                    "partition_key": self.contracts[name].partition,
                    "sorting_key": self.contracts[name].order,
                } if name in self.contracts else {"name": name}
                    for name in sorted(names)]
            elif "FROM system.columns" in sql:
                rows = [
                    {"table": name, "name": column, "type": kind}
                    for name in sorted(names) if name in self.contracts
                    for column, kind in self.column_override.get(
                        name, self.contracts[name].columns)
                ]
            elif "FROM system.data_skipping_indices" in sql:
                rows = [{"table": name, "name": "batch_id_bloom_v1",
                         "type": "bloom_filter", "expr": "batch_id",
                         "granularity": 1}
                        for name in sorted(names)]
            elif "FROM system.parts" in sql:
                rows = self.missing_indexes if "secondary_indices_compressed_bytes" in sql else self.bad_parts
            else:
                raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_operator_preflight_checks_exact_v2_layout_parts_and_narrow_grants():
    client = FullV2Catalog()
    terminal_v2_operator_preflight(client)
    client.legacy_insert = True
    with pytest.raises(ValueError, match="unauthorized INSERT"):
        terminal_v2_operator_preflight(client)
    client.legacy_insert = False
    client.tables = tuple(table for table in client.tables
                          if table.name != "trading_backtest_terminal_commit_v2")
    with pytest.raises(ValueError, match="tables are missing"):
        terminal_v2_operator_preflight(client)


def test_operator_preflight_rejects_wrong_disk_schema_or_unindexed_parts():
    client = FakeCatalog()
    client.policy_disks = ["default"]
    with pytest.raises(RuntimeError, match="SSD-only"):
        terminal_v2_storage_preflight(client)
    client = FakeCatalog()
    table = BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES[0]
    client.column_override[table.name] = table.columns[:-1]
    with pytest.raises(RuntimeError, match="columns differ"):
        terminal_v2_storage_preflight(client)
    client = FakeCatalog()
    client.bad_parts = [{"table": table.name, "disk_name": "default"}]
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        terminal_v2_storage_preflight(client)
    client = FakeCatalog()
    client.missing_indexes = [{"table": table.name, "name": "part_1"}]
    with pytest.raises(RuntimeError, match="without materialized indexes"):
        terminal_v2_storage_preflight(client)


def test_operator_preflight_rejects_market_write_and_broad_grants():
    client = FakeCatalog()
    client.extra_grant = "GRANT INSERT ON arte.bars_v1 TO terminal_v2"
    with pytest.raises(RuntimeError, match="unauthorized INSERT"):
        terminal_v2_permission_preflight(client)


def test_provisioning_sql_stages_v2_and_exact_portfolio_writer_grants():
    sql = terminal_v2_operator_provisioning_sql("terminal_v2")
    expected = len(TABLES) - 2 + len(VERSIONED_JOURNAL_V2_TABLES)
    expected += len(BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES)
    assert len([statement for statement in sql if statement.startswith("CREATE TABLE")]) == expected
    assert "GRANT INSERT ON arte.trading_backtest_snapshot_anchor_v1 TO terminal_v2" in sql
    assert "GRANT INSERT ON arte.trading_portfolio_snapshot_commit_v1 TO terminal_v2" in sql
    assert "GRANT INSERT ON arte.trading_portfolio_policy_commit_v2 TO terminal_v2" in sql
    assert "GRANT INSERT ON arte.trading_strategy_signal_v2 TO terminal_v2" in sql
    assert "GRANT INSERT ON arte.trading_commit_v2 TO terminal_v2" in sql
    assert "REVOKE INSERT ON arte.trading_strategy_signal_v1 FROM terminal_v2" in sql
    assert "REVOKE INSERT ON arte.trading_commit_v1 FROM terminal_v2" in sql
    assert "GRANT INSERT ON arte.trading_commit_v1 TO terminal_v2" not in sql
    assert "GRANT SELECT ON arte.bars_v1 TO terminal_v2" in sql
    assert not any("GRANT INSERT ON arte.bars_v1" in statement for statement in sql)
    with pytest.raises(ValueError, match="unsafe"):
        terminal_v2_operator_provisioning_sql("bad; DROP TABLE")


def test_keeper_proof_preflight_reads_required_namespaces_only():
    from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
    keeper = object.__new__(KeeperOwnershipCoordinator)
    class Client:
        connected = True
        client_state = "CONNECTED"
        calls = []
        missing = False
        def exists(self, path):
            self.calls.append(path)
            return None if self.missing else object()
    keeper._client = Client()
    terminal_v2_keeper_proof_preflight(keeper)
    assert len(keeper._client.calls) == 2
    assert all(path.startswith("/trading/ownership/v1/")
               for path in keeper._client.calls)
    keeper._client.missing = True
    with pytest.raises(RuntimeError, match="namespace is absent"):
        terminal_v2_keeper_proof_preflight(keeper)
    client = FakeCatalog()
    client.insert.add("bars_v1")
    with pytest.raises(RuntimeError, match="unauthorized INSERT"):
        terminal_v2_permission_preflight(client)
    client = FakeCatalog()
    client.extra_grant = "GRANT INSERT ON arte.* TO terminal_v2"
    with pytest.raises(RuntimeError, match="unauthorized INSERT"):
        terminal_v2_permission_preflight(client)
