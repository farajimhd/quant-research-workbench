"""Operator-owned modify-command DDL never creates an incompatible table."""
import pytest

from scripts.clickhouse import provision_strategy_one_modify_command as subject


class Client:
    def __init__(self, *, present=False, policy=True):
        self.present = present
        self.policy = policy
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if "FROM system.storage_policies" in sql:
            return "1" if self.policy else "0"
        if "FROM system.tables" in sql:
            return "1" if self.present else "0"
        if sql.startswith("CREATE TABLE"):
            self.present = True
            return ""
        raise AssertionError("Unexpected operator SQL")


def test_dry_run_never_creates_or_inserts(monkeypatch):
    checked = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda *_args, **_kwargs: checked.append(True))
    client = Client()
    assert subject.reconcile_layout(client, apply=False) is False
    assert not checked
    assert all(sql.startswith("SELECT ") for sql in client.statements)


def test_missing_table_is_created_once_with_ssd_then_verified(monkeypatch):
    checked = []
    monkeypatch.setattr(subject, "storage_preflight",
                        lambda _client, *, tables: checked.extend(tables))
    client = Client()
    assert subject.reconcile_layout(client, apply=True) is True
    assert checked == [subject.MODIFY_COMMAND]
    assert "live_market_ssd" in client.statements[-1]
    assert subject.reconcile_layout(client, apply=True) is False
    assert len([sql for sql in client.statements
                if sql.startswith("CREATE TABLE")]) == 1


def test_missing_policy_fails_before_ddl():
    client = Client(policy=False)
    with pytest.raises(RuntimeError, match="live_market_ssd"):
        subject.reconcile_layout(client, apply=True)
    assert all(sql.startswith("SELECT ") for sql in client.statements)
