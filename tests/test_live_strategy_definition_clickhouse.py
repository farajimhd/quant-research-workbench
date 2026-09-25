from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.backend.live_strategy_definition_authority import (
    DEFINITION, ENABLE_CHANGE, project_installed_definition,
)
from src.backend.live_strategy_definition_clickhouse import ClickHouseDefinitionStorage
from src.backend.live_strategy_definition_preflight import (
    operator_ddl, staged_definition_storage_preflight, staged_grants,
)
from src.backend.live_strategy_definition_publication import (
    KeeperDefinitionHeadFence, publish_installed_definition,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, STRATEGY_REVISION
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_live_strategy_definition_cold_reader import _actual_route_row


class FakeHttp:
    def __init__(self):
        self.sql = []
        self.rows = {DEFINITION.name: [], ENABLE_CHANGE.name: []}

    def execute(self, sql):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("INSERT INTO arte.", 1)[1].split(" ", 1)[0]
            self.rows[name].append(json.loads(sql.split("FORMAT JSONEachRow\n", 1)[1]))
            return ""
        if sql.startswith("SELECT ") and "FROM arte." in sql:
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            selected = [deepcopy(row) for row in self.rows[name]]
            for row in selected:
                key = "created_at" if name == DEFINITION.name else "changed_at"
                row[key] = row[key].replace("T", " ").replace("+00:00", "")
                if "enabled" in row:
                    row["enabled"] = int(row["enabled"])
            return "\n".join(json.dumps(row) for row in selected)
        raise AssertionError(sql)


def test_http_adapter_and_publisher_exact_fake_roundtrip() -> None:
    client = FakeHttp()
    storage = ClickHouseDefinitionStorage(client)
    keeper = KeeperDefinitionHeadFence(FakeKazoo(), endpoint="127.0.0.1:9181")
    saved = _actual_route_row()
    head = publish_installed_definition(storage, keeper, saved, owner_id="publisher")
    assert head.change_sequence == 1
    assert len(storage.read_definition_rows(
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_REVISION)) == 1
    assert len(storage.read_enable_change_rows(
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_REVISION)) == 1
    assert any("SETTINGS async_insert=0 FORMAT JSONEachRow" in sql for sql in client.sql)
    assert not any("FINAL" in sql or "CREATE TABLE" in sql for sql in client.sql)
    client.rows[DEFINITION.name].append(deepcopy(client.rows[DEFINITION.name][0]))
    with pytest.raises(ValueError, match="definition differs"):
        publish_installed_definition(
            storage, keeper, {**saved, "enabled": False}, owner_id="other",
            changed_at="2026-09-24T14:00:01+00:00")


def test_adapter_rejects_corrupt_insert_and_does_not_emit_sql() -> None:
    client = FakeHttp()
    storage = ClickHouseDefinitionStorage(client)
    row = project_installed_definition(_actual_route_row())
    with pytest.raises(ValueError, match="canonical typed content"):
        storage.insert_definition({**row, "content_hash": "0" * 64})
    assert client.sql == []


class FakeSystem:
    def __init__(self):
        self.parts = []
        self.policy = [{"disks": ["live_market_ssd"]}]

    def execute(self, sql):
        if "system.storage_policies" in sql:
            rows = self.policy
        elif "system.tables" in sql:
            rows = [dict(name=table.name, engine="MergeTree",
                         storage_policy="live_market_ssd",
                         partition_key=table.partition, sorting_key=table.order)
                    for table in (DEFINITION, ENABLE_CHANGE)]
        elif "system.columns" in sql:
            rows = [dict(table=table.name, name=name, type=kind)
                    for table in (DEFINITION, ENABLE_CHANGE)
                    for name, kind in table.columns]
        elif "system.parts" in sql:
            rows = self.parts
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_operator_only_plan_and_preflight_storage_placement() -> None:
    assert len(operator_ddl()) == 2
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in operator_ddl())
    assert staged_grants("trading_journal_writer") == tuple(
        f"GRANT SELECT, INSERT ON arte.{table.name} TO trading_journal_writer"
        for table in (DEFINITION, ENABLE_CHANGE))
    system = FakeSystem()
    staged_definition_storage_preflight(system)
    system.parts = [{"table": DEFINITION.name, "disk_name": "default"}]
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        staged_definition_storage_preflight(system)
    system.parts = []
    system.policy = [{"disks": ["default"]}]
    with pytest.raises(RuntimeError, match="SSD-only"):
        staged_definition_storage_preflight(system)
