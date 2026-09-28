import json

import pytest

from scripts.clickhouse import provision_strategy_one_live_assignment as subject


class Client:
    def __init__(self, *, installed=False, policy=True, old=False, rows=0,
                 parts=0):
        self.installed = installed
        self.policy = policy
        self.old = old
        self.rows = rows
        self.parts = parts
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)
        if "FROM system.storage_policies" in sql:
            return json.dumps({"disks": ["live_market_ssd"]}) if self.policy else ""
        if "FROM system.tables" in sql:
            return json.dumps({"name": subject.TABLE.name}) if self.installed else ""
        if "FROM system.columns" in sql:
            contract = subject._EMPTY_PREPUBLICATION if self.old else subject.TABLE
            return "\n".join(json.dumps({"name": name, "type": kind})
                             for name, kind in contract.columns)
        if "SELECT count() FROM arte." in sql:
            return str(self.rows)
        if "SELECT count() FROM system.parts" in sql:
            return str(self.parts)
        if sql == f"DROP TABLE arte.{subject.TABLE.name} SYNC":
            self.installed = False
            return ""
        if sql == subject.TABLE.ddl():
            self.installed = True
            self.old = False
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


def test_empty_prepublication_layout_can_be_replaced_only_explicitly(monkeypatch):
    verified = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda _client, *, tables: verified.append(tables))
    client = Client(installed=True, old=True)
    with pytest.raises(RuntimeError, match="no automatic replacement"):
        subject.apply(client)
    assert subject.apply(client, replace_empty_v1=True)
    assert verified == [(subject._EMPTY_PREPUBLICATION,), (subject.TABLE,)]
    assert client.calls.count(f"DROP TABLE arte.{subject.TABLE.name} SYNC") == 1
    assert client.calls.count(subject.TABLE.ddl()) == 1


@pytest.mark.parametrize("rows,parts", [(1, 0), (0, 1)])
def test_replacement_refuses_rows_or_parts(monkeypatch, rows, parts):
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    client = Client(installed=True, old=True, rows=rows, parts=parts)
    with pytest.raises(RuntimeError, match="rows or parts"):
        subject.apply(client, replace_empty_v1=True)
    assert not any(sql.startswith("DROP TABLE") for sql in client.calls)


def test_dry_run_never_opens_a_connection(monkeypatch, capsys):
    monkeypatch.setattr(subject, "_admin_client",
                        lambda *_args: pytest.fail("dry run opened ClickHouse"))
    assert subject.main([]) == 0
    assert subject.TABLE.name in capsys.readouterr().out
