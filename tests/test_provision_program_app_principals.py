"""No network: native storage and exact-grant restart/failure qualification."""
import json
import re
import pytest

from scripts.clickhouse import provision_program_app_principals as operator


class Admin:
    def __init__(self):
        self.accounts = {}
        self.changes = []
        self.closed = False
        self.interrupt_once = False
        self.tables = tuple({(t.database, t.name): t for t in
            (*operator.HISTORY, *operator.OPENING, *operator.LIQUIDITY,
             *operator.RELEASE, *operator.SELECTION)}.values())

    def execute(self, query):
        if query == 'SELECT currentUser()':
            return 'operator'
        if 'FROM system.storage_policies' in query:
            return json.dumps({'disks': ['live_market_ssd']})
        if 'FROM system.tables' in query or 'FROM system.columns' in query:
            database = re.search("database='([^']+)'", query)[1]
            tables = [t for t in self.tables if t.database == database]
            if 'FROM system.tables' in query:
                rows = [dict(name=t.name, engine='MergeTree', storage_policy='live_market_ssd',
                    partition_key=t.partition, sorting_key=t.order) for t in tables]
            else:
                rows = [dict(table=t.name, name=n, type=k) for t in tables for n, k in t.columns]
            return '\n'.join(json.dumps(row) for row in rows)
        if 'FROM system.parts' in query or 'FROM system.data_skipping_indices' in query:
            return ''
        if 'FROM system.users' in query:
            return str(int(re.search("name='([^']+)'", query)[1] in self.accounts))
        if query.startswith('CREATE USER '):
            name = query.split()[2]
            assert name not in self.accounts
            self.accounts[name] = set()
            self.changes.append(('create', name))
            return ''
        if query.startswith('GRANT '):
            match = re.fullmatch(r'GRANT (SELECT|INSERT) ON ([a-z_]+)\.([a-z0-9_]+) TO ([a-z0-9_]+)', query)
            assert match
            if self.interrupt_once:
                self.interrupt_once = False
                raise KeyboardInterrupt
            self.accounts[match[4]].add((match[1], match[2], match[3]))
            self.changes.append(('grant', match[4]))
            return ''
        raise AssertionError(query)

    def close(self):
        self.closed = True


class Client:
    def __init__(self, admin, principal, readonly):
        self.admin, self.principal, self.readonly = admin, principal, readonly
        self.closed = False

    def execute(self, query):
        if query == 'SELECT currentUser()':
            return self.principal
        if query == "SELECT getSetting('readonly')":
            return str(int(self.readonly))
        if query == 'SHOW GRANTS FINAL':
            return '\n'.join(f'GRANT {privilege} ON {db}.{table} TO {self.principal}'
                for privilege, db, table in sorted(self.admin.accounts[self.principal]))
        raise AssertionError(query)

    def close(self):
        self.closed = True


def run(admin, *, credentials=None, clients=None):
    clients = [] if clients is None else clients
    def factory(user, password, *, readonly):
        client = Client(admin, user, readonly)
        clients.append(client)
        return client
    operator.apply_with_clients(admin=admin, plans=operator.desired_plans(),
        credential_factory=credentials or (lambda p, **kw: (p.principal + '-private').ljust(64, 'x')),
        client_factory=factory, emit=lambda value: None)
    return clients


def approval_payload():
    return {'principals': [{'principal': p.principal,
        'select_tables': [list(v) for v in sorted(p.reads)],
        'insert_tables': [list(v) for v in sorted(p.writes)], 'grants': list(p.grants())}
        for p in operator.desired_plans()]}


def test_exact_storage_grants_and_idempotent_resume():
    admin = Admin()
    clients = run(admin)
    assert len(admin.accounts) == len(clients) == 8
    assert all(c.closed for c in clients)
    assert len(admin.tables) == 28
    for p in operator.desired_plans():
        expected = {('SELECT', *s) for s in p.reads} | {('INSERT', *s) for s in p.writes}
        assert admin.accounts[p.principal] == expected
    before = list(admin.changes)
    assert all(c.closed for c in run(admin))
    assert admin.changes == before


def test_existing_extra_authority_blocks_all_new_credentials_and_changes():
    admin = Admin()
    p = operator.desired_plans()[-1]
    admin.accounts[p.principal] = {('INSERT', 'arte', 'bars_v1')}
    calls, clients = [], []
    def credentials(p, *, account_exists):
        calls.append(account_exists)
        return 'private'.ljust(64, 'x')
    with pytest.raises(ValueError, match='outside approved scope'):
        run(admin, credentials=credentials, clients=clients)
    assert calls == [True]
    assert not admin.changes
    assert all(c.closed for c in clients)


def test_storage_failure_precedes_credentials_or_accounts(monkeypatch):
    admin = Admin()
    monkeypatch.setattr(operator, 'storage_preflight',
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Wrong storage policy')))
    with pytest.raises(ValueError, match='storage policy'):
        run(admin, credentials=lambda *args, **kw: pytest.fail('Read private credential before storage audit'))
    assert not admin.changes


def test_interruption_keeps_existing_state_for_same_plan_resume():
    admin, clients = Admin(), []
    admin.interrupt_once = True
    with pytest.raises(KeyboardInterrupt):
        run(admin, clients=clients)
    assert len(admin.accounts) == 1
    assert all(c.closed for c in clients)
    run(admin)
    assert len(admin.accounts) == 8
    assert sum(kind == 'create' for kind, _ in admin.changes) == 8


def test_shared_password_is_rejected_before_account_creation():
    admin = Admin()
    with pytest.raises(ValueError, match='distinct'):
        run(admin, credentials=lambda *args, **kwargs: 'same-private'.ljust(64, 'x'))
    assert not admin.changes


def test_client_cleanup_settles_every_owned_connection(monkeypatch):
    admin, clients = Admin(), []
    original = Client.close
    def close(client):
        original(client)
        if client.principal == operator.desired_plans()[0].principal:
            raise ValueError('synthetic cleanup failure')
    monkeypatch.setattr(Client, 'close', close)
    with pytest.raises(ExceptionGroup, match='cleanup failed'):
        run(admin, clients=clients)
    assert len(clients) == 8 and all(client.closed for client in clients)


def test_private_credential_is_protected_before_write_and_reused(tmp_path, monkeypatch):
    p = operator.desired_plans()[0]
    observed, generated = [], []
    monkeypatch.setattr(operator, 'SECRET_ROOT', tmp_path)
    monkeypatch.setattr(operator, '_restrict_secret_file', lambda path: observed.append(path.read_text()))
    def token(size):
        generated.append(size)
        return 'synthetic-private'.ljust(64, 'x')
    monkeypatch.setattr(operator.secrets, 'token_urlsafe', token)
    first = operator.credential(p, account_exists=False)
    assert observed[0] == '' and first in observed[1]
    assert operator.credential(p, account_exists=True) == first
    assert generated == [48]
    path = tmp_path / (p.principal + '.env')
    path.unlink()
    with pytest.raises(ValueError, match='no private credential'):
        operator.credential(p, account_exists=True)
    assert not path.exists()


def test_approval_is_exact_and_bounded(tmp_path):
    path = tmp_path / 'approved.json'
    payload = approval_payload()
    path.write_text(json.dumps(payload))
    assert operator.approved_plan(path, operator.desired_plans()) == operator.desired_plans()
    payload['principals'][0]['grants'].pop()
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='differ'):
        operator.approved_plan(path, operator.desired_plans())
    with pytest.raises(ValueError, match='runtime artifact'):
        operator.approved_plan('C:/outside-approved-root.json', operator.desired_plans())


def test_plan_only_and_confirmation_never_connect(monkeypatch, capsys):
    monkeypatch.setattr(operator, '_admin_client', lambda *args: pytest.fail('Unexpected connection'))
    assert operator.main([]) == 0
    assert 'PLAN ONLY' in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        operator.main(['--apply'])
    assert error.value.code == 2


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_main_redacts_transport_errors_and_closes_operator(tmp_path, monkeypatch, capsys, cleanup_fails):
    path = tmp_path / 'approved.json'
    path.write_text(json.dumps(approval_payload()))
    admin = Admin()
    if cleanup_fails:
        def close():
            admin.closed = True
            raise ValueError('private-password-must-not-leak')
        admin.close = close
    monkeypatch.setattr(operator.platform, 'node', lambda: 'DESKTOP-SAAI85T')
    monkeypatch.setattr(operator, 'SECRET_ROOT', tmp_path)
    monkeypatch.setattr(operator.socket, 'getaddrinfo', lambda *args, **kw:
        [(None, None, None, None, (operator.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(operator, '_admin_client', lambda *args: admin)
    monkeypatch.setattr(operator, 'apply_with_clients', lambda **kw:
        (_ for _ in ()).throw(ValueError('private-password-must-not-leak')))
    assert operator.main(['--apply', '--confirm-program-principals', '--approved-plan', str(path)]) == 1
    assert 'private-password' not in capsys.readouterr().err
    assert admin.closed
