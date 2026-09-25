from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.clickhouse import provision_fixed_backtest_v3_principals as command
from src.backend.backtest_fixed_v3_preflight import (
    running_v3_contracts, terminal_v3_contracts,
)


class FakeAdmin:
    def __init__(self, *, existing: str | None = None, current: str = "operator_admin"):
        self.existing = existing
        self.current = current
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        if sql == "SELECT currentUser()":
            return self.current
        if "FROM system.users" in sql:
            return "1" if self.existing and self.existing in sql else "0"
        return ""


class FakePrincipal:
    def __init__(self, user, grants=()):
        self.user = user
        self.grants = tuple(grants)

    def execute(self, sql):
        if sql == "SELECT currentUser()":
            return self.user
        if sql == "SHOW GRANTS FINAL":
            return "\n".join(self.grants)
        raise AssertionError(sql)


class StatefulAdmin:
    def __init__(self):
        self.users = set()
        self.grants = {}
        self.sql = []
        self.fail_on_grant = None
        self.fail_on_create = None
        self.grant_calls = 0

    def execute(self, sql):
        self.sql.append(sql)
        if sql == "SELECT currentUser()":
            return "operator_admin"
        if "FROM system.users" in sql:
            name = sql.split("name='", 1)[1].split("'", 1)[0]
            return "1" if name in self.users else "0"
        if sql.startswith("CREATE USER "):
            name = sql.split()[2]
            assert name not in self.users
            self.users.add(name)
            self.grants[name] = set()
            if name == self.fail_on_create:
                self.fail_on_create = None
                raise TimeoutError("fake lost CREATE USER response")
            return ""
        if sql.startswith("GRANT "):
            self.grant_calls += 1
            if self.grant_calls == self.fail_on_grant:
                self.fail_on_grant = None
                raise TimeoutError("fake partial grant failure")
            name = sql.rsplit(" TO ", 1)[1]
            self.grants[name].add(sql)
            return ""
        raise AssertionError(sql)

    def client(self, user, _password):
        assert user in self.users
        admin = self
        class Principal:
            def execute(self, sql):
                if sql == "SELECT currentUser()":
                    return user
                if sql == "SHOW GRANTS FINAL":
                    return "\n".join(sorted(admin.grants[user]))
                raise AssertionError(sql)
        return Principal()


def test_exact_three_role_plan_matches_v3_preflight_tables():
    read, running, terminal = command.desired_plan()
    assert len({read.principal, running.principal, terminal.principal}) == 3
    assert read.insert_arte == frozenset()
    assert running.select_arte >= {table.name for table in running_v3_contracts()}
    assert terminal.select_arte >= {table.name for table in terminal_v3_contracts()}
    assert running.insert_arte <= running.select_arte
    assert terminal.insert_arte <= terminal.select_arte
    assert running.insert_arte != terminal.insert_arte
    assert all("default" not in statement and " ON *.* " not in statement
               for plan in (read, running, terminal) for statement in plan.grants())


def test_default_cli_is_offline_dry_run_and_never_prints_secret(monkeypatch, capsys):
    monkeypatch.setattr(command, "_operator_apply", lambda *_args:
                        pytest.fail("dry run reached apply"))
    assert command.main([]) == 0
    output = capsys.readouterr().out
    assert "DRY RUN" in output and "No connection" in output
    assert all(name in output for name in command.PRINCIPALS.values())
    assert "PASSWORD" not in output and "CREATE USER" not in output


def test_fake_admin_apply_uses_exact_grants_and_principal_preflights(monkeypatch):
    plans = command.desired_plan()
    admin = FakeAdmin()
    checked = []
    monkeypatch.setattr(command, "storage_preflight", lambda client, *, tables:
                        checked.append(("storage", client, len(tables))))
    for role in ("read", "running", "terminal"):
        monkeypatch.setattr(command, f"{role}_v3_preflight",
                            lambda client, role=role: checked.append((role, client)))
    credentials = []
    def secret(plan, *, account_exists):
        assert account_exists is False
        credentials.append(plan.role)
        return f"private-secret-{plan.role}-" + "x" * 40
    command.apply_with_clients(
        plans, admin=admin, credential=secret,
        client_factory=lambda user, _password: FakePrincipal(user))
    assert credentials == ["read", "running", "terminal"]
    assert len([sql for sql in admin.sql if sql.startswith("CREATE USER ")]) == 3
    assert [sql for sql in admin.sql if sql.startswith("GRANT ")] == [
        f"GRANT {privilege} ON {database}.{table} TO {plan.principal}"
        for plan in plans for privilege, database, table in sorted(command._desired_grants(plan))]
    assert [entry[0] for entry in checked] == ["storage", "read", "running", "terminal"]
    assert all("private-secret" not in sql for sql in admin.sql)


def test_changed_plan_stops_before_any_credential_or_ddl(monkeypatch):
    plans = command.desired_plan()
    changed = (replace(plans[0], insert_arte=frozenset({"bars_v1"})), *plans[1:])
    with pytest.raises(ValueError, match="differs from exact"):
        command.apply_with_clients(changed, admin=FakeAdmin(),
            credential=lambda *_args, **_kwargs: pytest.fail("changed plan reached secret"),
            client_factory=lambda *_args: object())


def test_fake_admin_requires_distinct_principal_and_passwords(monkeypatch):
    plans = command.desired_plan()
    with pytest.raises(RuntimeError, match="Distinct ClickHouse administrator"):
        command.apply_with_clients(plans,
            admin=FakeAdmin(current=plans[0].principal),
            credential=lambda *_args, **_kwargs: pytest.fail("principal reached credential"),
            client_factory=lambda *_args: object())
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    admin = FakeAdmin()
    with pytest.raises(RuntimeError, match="three distinct private credentials"):
        command.apply_with_clients(plans, admin=admin,
            credential=lambda *_args, **_kwargs: "same-password-" + "x" * 40,
            client_factory=lambda *_args: object())
    assert not any(sql.startswith(("CREATE ", "GRANT ")) for sql in admin.sql)


def test_partial_create_and_grants_resume_only_missing_authority(monkeypatch):
    plans = command.desired_plan()
    admin = StatefulAdmin()
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    for plan in plans:
        def check(client, plan=plan):
            assert command._effective_grants(client, plan) == command._desired_grants(plan)
        monkeypatch.setattr(command, f"{plan.role}_v3_preflight", check)
    saved = {plan.role: f"private-{plan.role}-" + "x" * 40 for plan in plans}
    def credential(plan, *, account_exists):
        if account_exists:
            assert plan.principal in admin.users
        return saved[plan.role]
    admin.fail_on_grant = len(command._desired_grants(plans[0])) + 3
    with pytest.raises(TimeoutError, match="partial grant failure"):
        command.apply_with_clients(plans, admin=admin, credential=credential,
                                   client_factory=admin.client)
    assert plans[0].principal in admin.users
    assert plans[1].principal in admin.users
    assert plans[2].principal not in admin.users
    prior = {name: set(grants) for name, grants in admin.grants.items()}
    command.apply_with_clients(plans, admin=admin, credential=credential,
                               client_factory=admin.client)
    assert admin.users == {plan.principal for plan in plans}
    for name, grants in prior.items():
        assert grants <= admin.grants[name]
    assert len([sql for sql in admin.sql if sql.startswith("CREATE USER ")]) == 3
    assert all(command._effective_grants(admin.client(plan.principal, ""), plan)
               == command._desired_grants(plan) for plan in plans)


def test_lost_create_response_resumes_with_saved_credential(monkeypatch):
    plans = command.desired_plan()
    admin = StatefulAdmin()
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    for plan in plans:
        monkeypatch.setattr(command, f"{plan.role}_v3_preflight", lambda _client: None)
    saved = {plan.role: f"private-{plan.role}-" + "x" * 40 for plan in plans}
    def credential(plan, *, account_exists):
        if account_exists:
            assert plan.principal in admin.users
        return saved[plan.role]
    admin.fail_on_create = plans[0].principal
    with pytest.raises(TimeoutError, match="lost CREATE USER response"):
        command.apply_with_clients(plans, admin=admin, credential=credential,
                                   client_factory=admin.client)
    assert admin.users == {plans[0].principal}
    command.apply_with_clients(plans, admin=admin, credential=credential,
                               client_factory=admin.client)
    assert admin.users == {plan.principal for plan in plans}
    assert len([sql for sql in admin.sql if sql.startswith("CREATE USER ")]) == 3


@pytest.mark.parametrize("extra", [
    "GRANT INSERT ON arte.* TO backtest_v3_reader",
    "GRANT SELECT ON default.anything TO backtest_v3_reader",
    "GRANT SELECT ON *.* TO backtest_v3_reader",
])
def test_existing_extra_or_broad_grant_blocks_all_reconciliation(monkeypatch, extra):
    plans = command.desired_plan()
    admin = StatefulAdmin()
    admin.users.add(plans[0].principal)
    admin.grants[plans[0].principal] = {extra}
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    before = len(admin.sql)
    with pytest.raises(RuntimeError, match="extra or broad authority"):
        command.apply_with_clients(plans, admin=admin,
            credential=lambda plan, *, account_exists: f"private-{plan.role}-" + "x" * 40,
            client_factory=admin.client)
    assert not any(sql.startswith(("CREATE USER", "GRANT "))
                   for sql in admin.sql[before:])


def test_existing_principal_requires_saved_private_credential_before_grants(monkeypatch):
    plans = command.desired_plan()
    admin = StatefulAdmin()
    admin.users.add(plans[0].principal)
    admin.grants[plans[0].principal] = set()
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="missing private credential"):
        command.apply_with_clients(plans, admin=admin,
            credential=lambda plan, *, account_exists: (
                (_ for _ in ()).throw(RuntimeError("missing private credential"))
                if account_exists else f"private-{plan.role}-" + "x" * 40),
            client_factory=admin.client)
    assert not any(sql.startswith(("CREATE USER", "GRANT ")) for sql in admin.sql)


def test_private_file_is_required_for_existing_user_and_never_rotated(monkeypatch, tmp_path):
    plan = command.desired_plan()[0]
    monkeypatch.setattr(command, "SECRET_ROOT", tmp_path)
    acl_checks = []
    monkeypatch.setattr(command, "_restrict_secret_file",
                        lambda path: acl_checks.append(path))
    with pytest.raises(RuntimeError, match="lacks its private credential"):
        command._private_credential(plan, account_exists=True)
    first = command._private_credential(plan, account_exists=False)
    assert len(first) >= 40
    assert command._private_credential(plan, account_exists=True) == first
    assert len(acl_checks) == 3


def test_existing_credential_must_authenticate_exact_user_before_grant(monkeypatch):
    plans = command.desired_plan()
    admin = StatefulAdmin()
    admin.users.add(plans[0].principal)
    admin.grants[plans[0].principal] = set()
    monkeypatch.setattr(command, "storage_preflight", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="different principal"):
        command.apply_with_clients(plans, admin=admin,
            credential=lambda plan, *, account_exists: f"private-{plan.role}-" + "x" * 40,
            client_factory=lambda user, _password: FakePrincipal("wrong_user")
            if user == plans[0].principal else FakePrincipal(user))
    assert not any(sql.startswith(("CREATE USER", "GRANT ")) for sql in admin.sql)
