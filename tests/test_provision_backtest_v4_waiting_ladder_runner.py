"""Source-only operator plans and actual validators over synthetic catalogs."""
from unittest.mock import patch

import pytest

from scripts.clickhouse import provision_backtest_v4_waiting_ladder_runner as waiting
from scripts.clickhouse.provision_backtest_v4_ladder_runner import desired_plan as legacy_plan
from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingPolicy
from tests.test_ladder_waiting_profile import Catalog

POLICY = LadderGeometryBindingPolicy()
PASSWORD = 'synthetic-private-value-' + 'x' * 48


class Writer(Catalog):
    def __init__(self, grants):
        super().__init__(waiting=True, binding_grant=True)
        self.grants = set(grants)
        self.closed = False

    def execute(self, sql):
        if "name='strategy_one_entry_context_v1'" in sql:
            # The inherited provision plan includes this installed producer
            # SELECT dependency; represent its catalog presence explicitly.
            return '{"name":"strategy_one_entry_context_v1"}'
        if sql == 'SHOW GRANTS FINAL':
            return '\n'.join(f'GRANT {privilege} ON {database}.{table} TO {waiting.PRINCIPAL}'
                             for privilege, database, table in sorted(self.grants))
        return super().execute(sql)

    def close(self):
        self.closed = True


class Operator(Catalog):
    def __init__(self, writer, *, exists=True, defect=None):
        super().__init__(waiting=True, binding_grant=True, defect=defect)
        self.user = 'synthetic-operator'
        self.writer = writer
        self.exists = exists
        self.closed = False

    def execute(self, sql):
        if 'FROM system.users' in sql:
            self.statements.append(sql)
            return '1' if self.exists else '0'
        if sql.startswith('GRANT '):
            self.statements.append(sql)
            _, privilege, _, table, _, user = sql.split()
            assert user == waiting.PRINCIPAL
            database, name = table.split('.')
            self.writer.grants.add((privilege, database, name))
            return ''
        if sql.startswith('CREATE TABLE'):
            self.statements.append(sql)
            assert sql == BINDING.ddl()
            if self.defect == 'missing':
                self.defect = None
            return ''
        if sql.startswith('CREATE USER'):
            self.statements.append(sql)
            assert waiting.PRINCIPAL in sql
            self.exists = True
            return ''
        return super().execute(sql)

    def close(self):
        self.closed = True


def invoke(admin, writer, **kwargs):
    called = []

    def private(*, account_exists):
        called.append(account_exists)
        return PASSWORD

    def factory(user, password):
        assert user == waiting.PRINCIPAL and password == PASSWORD
        return writer

    waiting.apply_with_clients(admin=admin, credential=private,
        client_factory=factory, policy=POLICY, **kwargs)
    return called


def test_plan_exactly_extends_legacy_without_mutation_or_broad_authority():
    base = legacy_plan()
    new = waiting.desired_plan(POLICY)
    assert new.principal == waiting.PRINCIPAL
    assert new.select_arte - base.select_arte == {BINDING.name}
    assert new.insert_arte - base.insert_arte == {BINDING.name}
    assert new.select_system == base.select_system
    assert new.select_reference == base.select_reference
    assert legacy_plan() == base
    grants = waiting._desired_grants(new)
    assert all(p in {'SELECT', 'INSERT'} and '*' not in (d, t) for p, d, t in grants)
    assert all(d == 'arte' for p, d, t in grants if p == 'INSERT')


@pytest.mark.parametrize('policy', [None, {}, True, 'ladder-wait-first-complete-geometry-v1'])
def test_foreign_policy_rejected_before_clients(policy):
    with pytest.raises(ValueError, match='exact typed'):
        waiting.apply_with_clients(admin=object(), credential=object(),
                                  client_factory=object(), policy=policy)


def test_plan_only_opens_no_client_or_secret(capsys):
    with patch.object(waiting, '_admin_client', side_effect=AssertionError), \
            patch.object(waiting, 'credential', side_effect=AssertionError), \
            patch.object(waiting, 'socket') as socket:
        socket.getaddrinfo.side_effect = AssertionError
        assert waiting.main([]) == 0
    output = capsys.readouterr().out
    assert 'Plan only' in output and BINDING.ddl() in output
    assert PASSWORD not in output
    assert "storage_policy = 'live_market_ssd'" in output


@pytest.mark.parametrize('args', [
    ['--apply'], ['--apply', '--confirm-policy', 'foreign'], ['--install-binding'],
    ['--confirm-install-binding'],
    ['--apply', '--confirm-policy', POLICY.version, '--install-binding'],
])
def test_cli_confirmation_fails_before_external_authority(args):
    with patch.object(waiting, '_admin_client', side_effect=AssertionError), \
            patch.object(waiting, 'credential', side_effect=AssertionError):
        with pytest.raises(SystemExit) as result:
            waiting.main(args)
    assert result.value.code == 2


@pytest.mark.parametrize('defect', ['missing', 'policy', 'parts'])
def test_real_storage_validation_before_any_secret_account_or_grant(defect):
    writer = Writer(())
    admin = Operator(writer, defect=defect)
    private = []
    with pytest.raises(ValueError, match='missing|layout differs|outside live_market_ssd'):
        waiting.apply_with_clients(admin=admin,
            credential=lambda **kw: private.append(kw), client_factory=object(), policy=POLICY)
    assert not private
    assert not any(sql.startswith(('CREATE', 'GRANT')) for sql in admin.statements)


def test_install_operator_ddl_and_real_full_preflight_then_idempotent_resume():
    plan = waiting.desired_plan(POLICY)
    writer = Writer(())
    admin = Operator(writer, exists=False, defect='missing')
    assert invoke(admin, writer, install_binding=True) == [False]
    assert writer.closed and writer.grants == set(waiting._desired_grants(plan))
    assert admin.statements.index(BINDING.ddl()) < next(i for i, sql in enumerate(admin.statements) if sql.startswith('CREATE USER'))
    assert any('FROM system.parts' in sql and BINDING.name in sql for sql in admin.statements)
    assert writer.ladder_geometry_policy == POLICY
    admin.statements.clear()
    assert invoke(admin, writer) == [True]
    assert not any(sql.startswith(('CREATE', 'GRANT')) for sql in admin.statements)


def test_install_cannot_repair_existing_wrong_storage():
    writer = Writer(())
    admin = Operator(writer, defect='policy')
    with pytest.raises(ValueError, match='layout differs'):
        invoke(admin, writer, install_binding=True)
    assert not any(sql.startswith(('ALTER', 'CREATE USER', 'GRANT')) for sql in admin.statements)


def test_install_rejects_absent_storage_policy_before_ddl():
    admin = Operator(Writer(()))
    original = admin.execute
    admin.execute = lambda sql: '' if 'FROM system.storage_policies' in sql else original(sql)
    with pytest.raises(RuntimeError, match='SSD-only'):
        invoke(admin, admin.writer, install_binding=True)
    assert not any(sql.startswith(('CREATE', 'GRANT')) for sql in admin.statements)


def test_extra_existing_grants_fail_before_grant_changes_and_close():
    writer = Writer({('CREATE', 'arte', '*')})
    admin = Operator(writer)
    with pytest.raises(RuntimeError, match='extra or broad'):
        invoke(admin, writer)
    assert writer.closed
    assert not any(sql.startswith('GRANT') for sql in admin.statements)


def test_existing_account_without_credential_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(waiting, 'SECRET_PATH', tmp_path / 'private.env')
    with pytest.raises(RuntimeError, match='lacks its private credential'):
        waiting.credential(account_exists=True)
    assert not waiting.SECRET_PATH.exists()


def test_private_fixture_credential_exact_namespace_and_duplicate_rejection(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic.env'
    monkeypatch.setattr(waiting, 'SECRET_PATH', path)
    restricted = []
    monkeypatch.setattr(waiting, '_restrict_secret_file', lambda p: restricted.append(p))
    path.write_text(f'{waiting.STEM}URL={waiting.URL}\n{waiting.STEM}USER={waiting.PRINCIPAL}\n'
                    f'{waiting.STEM}PASSWORD={PASSWORD}\n')
    assert waiting.credential(account_exists=True) == PASSWORD
    path.write_text(path.read_text() + f'{waiting.STEM}USER={waiting.PRINCIPAL}\n')
    with pytest.raises(RuntimeError, match='different exact profile'):
        waiting.credential(account_exists=True)
    assert restricted == [path, path]


def test_cli_failure_never_prints_underlying_exception_or_credential_hash(monkeypatch, capsys):
    monkeypatch.setattr(waiting.platform, 'node', lambda: 'DESKTOP-SAAI85T')
    monkeypatch.setattr(waiting, 'SECRET_ROOT', type('Root', (), {'is_dir': lambda self: True})())
    monkeypatch.setattr(waiting.socket, 'getaddrinfo', lambda *a, **kw: [(None, None, None, None, (waiting.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(waiting, '_admin_client', lambda url: (_ for _ in ()).throw(RuntimeError(PASSWORD)))
    assert waiting.main(['--apply', '--confirm-policy', POLICY.version]) == 1
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert 'Blocked: RuntimeError' in output.err


@pytest.mark.parametrize('failure', ['host', 'dns'])
def test_apply_rejects_unmanaged_host_or_transport_before_admin(monkeypatch, failure, capsys):
    monkeypatch.setattr(waiting.platform, 'node', lambda: 'FOREIGN' if failure == 'host' else 'DESKTOP-SAAI85T')
    monkeypatch.setattr(waiting, 'SECRET_ROOT', type('Root', (), {'is_dir': lambda self: True})())
    monkeypatch.setattr(waiting.socket, 'getaddrinfo', lambda *a, **kw: [(None, None, None, None, ('127.0.0.2', 18123))])
    monkeypatch.setattr(waiting, '_admin_client', lambda url: pytest.fail('foreign authority opened'))
    assert waiting.main(['--apply', '--confirm-policy', POLICY.version]) == 1
    assert 'Blocked: RuntimeError' in capsys.readouterr().err


def test_close_failure_is_sanitized_and_cannot_report_success(monkeypatch, capsys):
    monkeypatch.setattr(waiting.platform, 'node', lambda: 'DESKTOP-SAAI85T')
    monkeypatch.setattr(waiting, 'SECRET_ROOT', type('Root', (), {'is_dir': lambda self: True})())
    monkeypatch.setattr(waiting.socket, 'getaddrinfo', lambda *a, **kw: [(None, None, None, None, (waiting.WORKSTATION_IPV4, 18123))])
    admin = type('Admin', (), {'close': lambda self: (_ for _ in ()).throw(RuntimeError(PASSWORD))})()
    monkeypatch.setattr(waiting, '_admin_client', lambda url: admin)
    monkeypatch.setattr(waiting, 'apply_with_clients', lambda **kw: None)
    assert waiting.main(['--apply', '--confirm-policy', POLICY.version]) == 1
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert 'Verified:' not in output.out
    assert 'Blocked closing operator: RuntimeError' in output.err
