"""Exact product principals and private credential boundaries, no real accounts."""
import re

import pytest

from scripts.clickhouse import provision_native_channel_principals as operator
from src.backend.native_channel_clients import native_channel_client, PRINCIPALS


class Admin:
    def __init__(self):
        self.users, self.grants, self.sql = set(), {role:set() for role in PRINCIPALS}, []

    def execute(self, sql):
        self.sql.append(sql)
        if sql.startswith('SELECT count()'):
            user = re.search(r"name='([^']+)'", sql).group(1)
            return '1' if user in self.users else '0'
        if sql.startswith('CREATE USER'):
            self.users.add(sql.split()[2]); return ''
        if sql.startswith('GRANT'):
            match = re.fullmatch(r'GRANT (\w+) ON (\S+) TO (\S+)', sql)
            role = next(r for r,u in PRINCIPALS.items() if u == match.group(3))
            self.grants[role].add((match.group(1), match.group(2))); return ''
        raise AssertionError(sql)


class Client:
    def __init__(self, admin, role):
        self.admin, self.role, self.closed = admin, role, False

    def execute(self, sql):
        if sql == 'SELECT currentUser()':
            return PRINCIPALS[self.role]
        assert sql == 'SHOW GRANTS FINAL'
        return '\n'.join(f'GRANT {p} ON {t} TO {PRINCIPALS[self.role]}'
                         for p,t in sorted(self.admin.grants[self.role]))

    def close(self):
        self.closed = True


def test_provision_exact_permissions_reuses_accounts_and_preserves_other_roles(monkeypatch):
    monkeypatch.setattr(operator, 'install', lambda admin: None)
    admin, clients, credentials = Admin(), [], []
    def factory(role, principal, password):
        assert principal == PRINCIPALS[role] and len(password) == 48
        client = Client(admin, role);clients.append(client);return client
    def credential(role, *, account_exists):
        credentials.append((role,account_exists));return 'x'*48
    first = operator.provision(admin,credential=credential,client_factory=factory)
    assert all(r['created'] for r in first)
    assert admin.grants == {r:set(g) for r,g in operator.GRANTS.items()}
    assert len(admin.grants['read']) == 9 and len(admin.grants['producer']) == 2
    assert all(p == 'INSERT' and t in {'arte.native_causal_channels_v1','arte.native_causal_channel_coverage_v1'}
               for p,t in admin.grants['producer'])
    assert all('backtest_v3_reader' not in q for q in admin.sql)
    assert all(c.closed for c in clients)
    second = operator.provision(admin,credential=credential,client_factory=factory)
    assert not any(r['created'] for r in second)
    assert sum(q.startswith('CREATE USER') for q in admin.sql) == 2
    assert credentials[-2:] == [('read',True),('producer',True)]


def test_broad_existing_grant_stops_without_revoking_or_adding(monkeypatch):
    monkeypatch.setattr(operator, 'install', lambda admin: None)
    admin=Admin();admin.users.update(PRINCIPALS.values())
    admin.grants['read'].add(('INSERT','arte.bars_v1'))
    with pytest.raises(operator.ProvisioningError,match='outside'):
        operator.provision(admin,credential=lambda *a,**k:'x'*48,
                           client_factory=lambda role,*a:Client(admin,role))
    assert not any(q.startswith(('GRANT','REVOKE','CREATE USER')) for q in admin.sql)


def test_native_client_uses_explicit_private_role_and_readonly_parameters(tmp_path):
    path=tmp_path/'credential.env'
    path.write_text('NATIVE_CHANNEL_READ_CLICKHOUSE_URL=http://localhost:18123\n'
        'NATIVE_CHANNEL_READ_CLICKHOUSE_USER=native_causal_channel_reader\n'
        'NATIVE_CHANNEL_READ_CLICKHOUSE_PASSWORD='+ 'x'*48+'\n')
    captured=[]
    def factory(*args,**kwargs):
        captured.append((args,kwargs));return object()
    env={'NATIVE_CHANNEL_READ_CREDENTIAL_FILE':str(path)}
    native_channel_client('read',environment=env,client_factory=factory)
    assert captured[0][0][1] == PRINCIPALS['read']
    assert captured[0][1]['default_query_params'] == {'readonly':1,'max_threads':2,'max_execution_time':60}
    with pytest.raises(ValueError,match='mixing'):
        native_channel_client('read',environment={**env,'NATIVE_CHANNEL_READ_CLICKHOUSE_PASSWORD':'x'*48},client_factory=factory)
    path.write_text(path.read_text().replace('native_causal_channel_reader','native_causal_channel_producer'))
    with pytest.raises(ValueError,match='identity'):
        native_channel_client('read',environment=env,client_factory=factory)


def test_dry_run_is_bounded_plain_output_without_connections(monkeypatch,capsys):
    monkeypatch.setattr(operator,'_admin_client',lambda *a:pytest.fail('Dry run connected'))
    assert operator.main([]) == 0
    out=capsys.readouterr()
    assert len(out.out.splitlines()) == 2 and not out.err
    assert max(map(len,out.out.splitlines())) <= 80


def test_failure_output_never_echoes_driver_credentials(monkeypatch,capsys):
    monkeypatch.setattr(operator.platform,'node',lambda:'DESKTOP-SAAI85T')
    monkeypatch.setattr(operator,'SECRET_ROOT',type('Root',(),{'is_dir':lambda self:True})())
    def fail(*args):
        raise RuntimeError('driver includes SUPER_PRIVATE_PASSWORD')
    monkeypatch.setattr(operator,'_admin_client',fail)
    assert operator.main(['--apply']) == 1
    out=capsys.readouterr()
    assert 'SUPER_PRIVATE_PASSWORD' not in out.err
    assert 'RuntimeError' in out.err and 'Partial setup' in out.err
