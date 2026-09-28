import json

import pytest

from scripts.clickhouse import provision_strategy_one_live_assignment as subject


class Client:
    def __init__(self, *, installed=False, policy=True):
        self.installed = installed
        self.policy = policy
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)
        if "FROM system.storage_policies" in sql:
            return json.dumps({"disks": ["live_market_ssd"]}) if self.policy else ""
        if "FROM system.tables" in sql:
            return json.dumps({"name": subject.TABLE.name}) if self.installed else ""
        if sql == subject.TABLE.ddl():
            self.installed = True
            return ""
        raise AssertionError(sql)


def test_operator_install_is_idempotent_and_never_inserts(monkeypatch):
    verified = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda client, *, tables: verified.append((client, tables)))
    client = Client()
    assert subject.apply(client)
    assert not subject.apply(client)
    assert client.calls.count(subject.TABLE.ddl()) == 1
    assert verified == [(client, (subject.TABLE,)), (client, (subject.TABLE,))]
    assert not any("INSERT" in sql or "DROP" in sql for sql in client.calls)


def test_operator_install_fails_before_ddl_without_ssd(monkeypatch):
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    client = Client(policy=False)
    with pytest.raises(RuntimeError, match="SSD-only"):
        subject.apply(client)
    assert subject.TABLE.ddl() not in client.calls


def test_dry_run_never_opens_a_connection(monkeypatch, capsys):
    monkeypatch.setattr(subject, "_admin_client",
                        lambda *_args: pytest.fail("dry run opened ClickHouse"))
    assert subject.main([]) == 0
    assert subject.TABLE.name in capsys.readouterr().out
