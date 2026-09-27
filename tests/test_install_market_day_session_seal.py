from __future__ import annotations

import json

import pytest

from scripts.clickhouse.install_market_day_session_seal import install
from src.trading_runtime.arte_market_day_session_seal import SESSION_SEAL


class Client:
    def __init__(self, *, present=False, disk="live_market_ssd"):
        self.present = present
        self.disk = disk
        self.created = 0

    def execute(self, sql):
        if sql.startswith("CREATE TABLE"):
            assert not self.present and sql == SESSION_SEAL.ddl()
            self.present = True
            self.created += 1
            return ""
        if "FROM system.storage_policies" in sql:
            return json.dumps({"disks": [self.disk]})
        if "FROM system.tables" in sql:
            if not self.present:
                return ""
            row = {"name": SESSION_SEAL.name}
            if "engine,storage_policy" in sql:
                row.update(engine="MergeTree", storage_policy=self.disk,
                           partition_key=SESSION_SEAL.partition,
                           sorting_key=SESSION_SEAL.order)
            return json.dumps(row)
        if "FROM system.columns" in sql:
            return "\n".join(json.dumps(dict(table=SESSION_SEAL.name,
                name=name, type=kind)) for name, kind in SESSION_SEAL.columns)
        if "FROM system.data_skipping_indices" in sql or "FROM system.parts" in sql:
            return ""
        raise AssertionError(sql)


def test_session_seal_installer_dry_run_and_exact_create():
    client = Client()
    assert install(client, apply=False) == "absent_plan_only"
    assert client.created == 0
    assert install(client, apply=True) == "created_verified"
    assert client.created == 1
    assert install(client, apply=True) == "verified_existing"
    assert client.created == 1


def test_session_seal_installer_rejects_off_policy_state():
    with pytest.raises(RuntimeError, match="SSD-only"):
        install(Client(disk="default"), apply=True)
    with pytest.raises(ValueError, match="SSD-only"):
        install(Client(present=True, disk="default"), apply=False)
