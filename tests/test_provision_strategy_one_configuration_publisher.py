from __future__ import annotations

import pytest

from scripts.clickhouse import provision_strategy_one_configuration_publisher as subject


class Admin:
    def __init__(self, present="0"):
        self.present, self.sql = present, []
        self.publisher = None

    def execute(self, query):
        self.sql.append(query)
        if "FROM system.users" in query:
            return self.present
        if query.startswith("GRANT "):
            self.publisher.grants.add(query)
        return ""


class Publisher:
    def __init__(self, grants=()):
        self.grants = set(grants)
        self.closed = False

    def execute(self, query):
        if query == "SELECT currentUser()":
            return subject.PRINCIPAL
        if query == "SHOW GRANTS FINAL":
            return "\n".join(sorted(self.grants))
        raise AssertionError(query)

    def close(self):
        self.closed = True


def test_dry_run_never_connects(capsys, monkeypatch):
    monkeypatch.setattr(subject, "_admin_client", lambda _url:
                        pytest.fail("dry run connected"))
    assert subject.main([]) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_publisher_grants_exactly_two_typed_tables_and_four_catalogs(monkeypatch):
    monkeypatch.setattr(subject, "install_tables", lambda _admin: None)
    admin = Admin()
    publisher = Publisher()
    admin.publisher = publisher
    subject.provision(admin, credential=lambda **_kw: "p" * 40,
                      client_factory=lambda _u, _p: publisher)
    assert publisher.closed
    assert len([sql for sql in admin.sql if sql.startswith("GRANT ")]) == 8
    assert all(("INSERT ON system." not in sql) for sql in admin.sql)
    assert not any("bars_v1" in sql or "indicators_v1" in sql
                   or "liquidity_100ms_v1" in sql for sql in admin.sql)


def test_existing_foreign_grant_fails_closed(monkeypatch):
    monkeypatch.setattr(subject, "install_tables", lambda _admin: None)
    admin = Admin("1")
    publisher = Publisher((
        f"GRANT INSERT ON arte.bars_v1 TO {subject.PRINCIPAL}",))
    admin.publisher = publisher
    with pytest.raises(RuntimeError, match="foreign table grant"):
        subject.provision(admin, credential=lambda **_kw: "p" * 40,
                          client_factory=lambda _u, _p: publisher)
    assert publisher.closed
    assert not any(sql.startswith("GRANT ") for sql in admin.sql)
