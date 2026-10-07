"""Real storage/grant helpers over controlled catalog transport; no DB authority."""
import json
import pytest
from scripts.clickhouse import provision_backtest_v4_fixed_structural_lot_runner as install
from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as legacy_plan
from tests.test_original_risk_profile import Catalog

POLICY = install.FixedStructuralLotPolicy()
PASSWORD = 'synthetic-not-secret-' + 'x' * 48


class Client(Catalog):
    def __init__(self, *, operator=False, exists=True, missing=False, defect=None, grants=None):
        super().__init__()
        self.user = 'test-operator' if operator else install.PRINCIPAL
        self.exists, self.missing, self.failure = exists, missing, defect
        self.closed = False
        self.grants = set(install._desired_grants(install.desired_plan(POLICY)) if grants is None else grants)
        self.selected = install._tables(POLICY)
        self.contracts.update({t.name:t for t in self.selected})
        self.defect_table = self.selected[0].name
        self.defect = defect if defect in ('parts','policy') else None
        self.writer = None

    def execute(self, sql):
        if sql == "SELECT getSetting('readonly')":
            return '1' if self.failure == 'readonly' else '0'
        if 'FROM system.users' in sql:
            self.statements.append(sql)
            return '1' if self.exists else '0'
        if sql == 'SHOW GRANTS FINAL':
            return '\n'.join(f'GRANT {p} ON {d}.{t} TO {self.user}' for p,d,t in sorted(self.grants))
        if 'FROM system.storage_policies' in sql and self.failure == 'missing_policy':
            return ''
        if sql.startswith('SELECT name FROM system.tables'):
            return '' if self.missing else '\n'.join(json.dumps({'name':t.name}) for t in self.selected)
        if 'FROM system.columns' in sql and self.failure == 'schema':
            rows = [json.loads(line) for line in super().execute(sql).splitlines()]
            for row in rows:
                if row['table'] == self.defect_table:row['type'] = 'String'
            return '\n'.join(json.dumps(v) for v in rows)
        if sql.startswith('CREATE TABLE'):
            assert sql in tuple(t.ddl() for t in self.selected)
            self.statements.append(sql);self.missing=False;return ''
        if sql.startswith('CREATE USER'):
            assert install.PRINCIPAL in sql
            self.statements.append(sql);self.exists=True;return ''
        if sql.startswith('GRANT '):
            _,p,_,scope,_,user=sql.split();assert user==install.PRINCIPAL
            d,t=scope.split('.');self.writer.grants.add((p,d,t));self.statements.append(sql);return ''
        return super().execute(sql)

    def close(self):self.closed=True


def invoke(admin, writer, **kw):
    calls=[];admin.writer=writer
    def private(*,account_exists):calls.append(account_exists);return PASSWORD
    def factory(user,password):assert(user,password)==(install.PRINCIPAL,PASSWORD);return writer
    install.apply_with_clients(admin=admin,credential=private,client_factory=factory,policy=POLICY,**kw)
    return calls


def test_plan_unchanged_default_and_no_connections(monkeypatch,capsys):
    monkeypatch.setattr(install,'_admin_client',lambda *a:pytest.fail('connection'))
    assert install.main(['--json'])==0
    data=json.loads(capsys.readouterr().out)
    assert data['operations_executed']==0 and len(data['tables'])==7
    old=legacy_plan();new=install.desired_plan(POLICY)
    assert old.principal=='backtest_v4_runner' and new.principal==install.PRINCIPAL
    assert new.insert_arte-old.insert_arte==frozenset(t.name for t in install._tables(POLICY))


@pytest.mark.parametrize('args',[['--apply'],['--install-tables'],['--confirm-install-tables'],
    ['--apply','--confirm-fixed-lot-runner','--install-tables'],['--apply','--confirm-fixed-lot-runner','--json']])
def test_confirmations_before_connection(args,monkeypatch):
    monkeypatch.setattr(install,'_admin_client',lambda *a:pytest.fail('connection'))
    with pytest.raises(SystemExit):install.main(args)


@pytest.mark.parametrize('defect',['missing_policy','schema','policy','parts'])
def test_actual_storage_defects_before_credentials_and_writes(defect):
    admin=Client(operator=True,defect=defect)
    with pytest.raises((ValueError,RuntimeError)):
        install.apply_with_clients(admin=admin,policy=POLICY,install_tables=True,
            credential=lambda **kw:pytest.fail('private credential'),client_factory=lambda *a:pytest.fail('factory'))
    assert not any(q.startswith(('CREATE','GRANT')) for q in admin.statements)


def test_missing_tables_need_explicit_install_before_credentials():
    admin=Client(operator=True,missing=True)
    with pytest.raises(RuntimeError,match='explicit table'):
        install.apply_with_clients(admin=admin,policy=POLICY,
            credential=lambda **kw:pytest.fail('credential'),client_factory=object())


def test_real_install_preflight_exact_grants_and_idempotent_resume():
    writer=Client(grants=[]);admin=Client(operator=True,exists=False,missing=True)
    assert invoke(admin,writer,install_tables=True)==[False]
    assert sum(q.startswith('CREATE TABLE') for q in admin.statements)==7
    assert writer.closed and writer.grants==install._desired_grants(install.desired_plan(POLICY))
    admin.statements.clear();writer.closed=False
    assert invoke(admin,writer,install_tables=True)==[True]
    assert not any(q.startswith(('CREATE','GRANT')) for q in admin.statements)
    assert any('system.parts' in q for q in writer.statements)


@pytest.mark.parametrize('extra',[('ALTER','arte','x'),('SELECT','arte','*'),('INSERT','arte','strategy_one_entry_context_v1')])
def test_existing_extra_authority_before_table_creation(extra):
    writer=Client();writer.grants.add(extra);admin=Client(operator=True,missing=True)
    with pytest.raises(RuntimeError,match='authority'):invoke(admin,writer,install_tables=True)
    assert writer.closed and not any(q.startswith(('CREATE','GRANT')) for q in admin.statements)


def test_readonly_and_wrong_principal_fail_before_ddl():
    for readonly in (True,False):
        writer=Client(defect='readonly' if readonly else None)
        if not readonly:writer.user='backtest_v4_runner'
        admin=Client(operator=True,missing=True)
        with pytest.raises(RuntimeError):invoke(admin,writer,install_tables=True)
        assert writer.closed and not any(q.startswith(('CREATE','GRANT')) for q in admin.statements)


def test_private_namespace_duplicate_and_existing_missing_fail(tmp_path,monkeypatch):
    path=tmp_path/'private.env';monkeypatch.setattr(install,'SECRET_PATH',path)
    monkeypatch.setattr(install,'_restrict_secret_file',lambda p:None)
    with pytest.raises(RuntimeError,match='lacks'):install.credential(account_exists=True)
    lines=f'{install.STEM}URL={install.URL}\n{install.STEM}USER={install.PRINCIPAL}\n{install.STEM}PASSWORD={PASSWORD}\n'
    path.write_text(lines);assert install.credential(account_exists=True)==PASSWORD
    path.write_text(lines+lines.splitlines()[0]+'\n')
    with pytest.raises(RuntimeError,match='profile'):install.credential(account_exists=True)


def test_unmanaged_apply_failure_sanitized(monkeypatch,capsys):
    monkeypatch.setattr(install.platform,'node',lambda:'laptop')
    monkeypatch.setattr(install,'_admin_client',lambda *a:pytest.fail('connection'))
    assert install.main(['--apply','--confirm-fixed-lot-runner'])==1
    assert 'RuntimeError' in capsys.readouterr().err

def test_clickhouse_canonical_decimal_metadata_preserves_exact_types_and_resume():
    class CanonicalClient(Client):
        def execute(self, sql):
            result = super().execute(sql)
            if 'FROM system.columns' in sql:
                rows = [json.loads(line) for line in result.splitlines()]
                for row in rows:
                    row['type'] = row['type'].replace('Decimal(38,18)', 'Decimal(38, 18)')
                return '\n'.join(json.dumps(row) for row in rows)
            return result
    writer = CanonicalClient(grants=[])
    admin = CanonicalClient(operator=True, exists=False, missing=False)
    assert invoke(admin, writer, install_tables=True) == [False]
    assert not any(q.startswith('CREATE TABLE') for q in admin.statements)
    assert writer.grants == install._desired_grants(install.desired_plan(POLICY))
    assert writer.closed
