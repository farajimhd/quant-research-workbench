"""The operator installer is resumable and never inserts market or journal rows."""
import json

import pytest

from scripts.clickhouse import install_trading_journal_layout as install


class Client:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT disks FROM system.storage_policies"):
            return json.dumps({"disks": ["live_market_ssd"]})
        if sql.startswith("SELECT count() FROM system.databases"):
            return "1"
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
            return ""
        raise AssertionError(sql)


def test_plan_is_read_only_and_apply_verifies_each_new_table(monkeypatch):
    contracts = install.fixed_backtest_v2_contracts()
    missing = (contracts[-2].name, contracts[-1].name)
    monkeypatch.setattr(install, "plan_missing", lambda _: (missing, ()))
    verified = []
    monkeypatch.setattr(install, "storage_preflight",
                        lambda _, *, tables: verified.append(tuple(t.name for t in tables)))
    client = Client()
    assert install.install_missing(client, apply=False) == (len(contracts) - 2, 0)
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.install_missing(client, apply=True) == (len(contracts) - 2, 2)
    assert verified == [(missing[0],), (missing[1],), tuple(t.name for t in contracts)]
    writes = [sql for sql in client.statements if not sql.startswith("SELECT ")]
    assert len(writes) == 2
    assert all(sql.startswith("CREATE TABLE IF NOT EXISTS arte.trading_") for sql in writes)
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in writes)


def test_installer_fails_before_ddl_when_policy_is_not_ssd(monkeypatch):
    client = Client()
    monkeypatch.setattr(install, "plan_missing",
                        lambda _: pytest.fail("must not inspect tables after bad policy"))
    client.execute = lambda sql: json.dumps({"disks": ["default"]})
    with pytest.raises(RuntimeError, match="SSD-only"):
        install.install_missing(client, apply=True)
