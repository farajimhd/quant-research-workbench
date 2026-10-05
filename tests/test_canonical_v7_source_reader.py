from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.clickhouse import provision_canonical_v7_source_reader as provision
from scripts.clickhouse import provision_fixed_backtest_v3_principals as existing
from src.backend import canonical_v7_source_client as source


PASSWORD='fixture-private-only-'+('x'*48)
CATALOG=('events_2025','events_2026')


class Client:
    def __init__(self,state,user):
        self.state,self.user=state,user
        self.closed=False
        self.calls=[]
    def execute(self,sql):
        import json
        self.calls.append(sql)
        if sql=='SELECT currentUser()':return self.user
        if sql=="SELECT getSetting('readonly')":return self.state.get('readonly','1')
        if sql=="SELECT getSetting('max_threads')":return self.state.get('max_threads','2')
        if sql=="SELECT getSetting('max_execution_time')":return self.state.get('max_execution_time','60')
        if sql.startswith('SELECT name FROM system.tables'):
            return '\n'.join(json.dumps({'name':name}) for name in self.state.get('catalog',CATALOG))
        if sql.startswith('SELECT database,name FROM system.tables'):
            return '\n'.join(json.dumps({'database':database,'name':name}) for database,name in provision.REQUIRED_SOURCE_TABLES)
        if sql.startswith('SELECT count() FROM system.users'):return self.state.get('present','1')
        if sql=='SHOW GRANTS FINAL':return '\n'.join(self.state.get('grants',[]))
        if sql.startswith('CREATE USER '):self.state['present']='1';return ''
        if sql.startswith('ALTER USER '):
            for key,value in source.READ_SETTINGS:self.state[key]=str(value)
            return ''
        if sql.startswith('GRANT '):self.state.setdefault('grants',[]).append(sql);return ''
        raise AssertionError('Unexpected test query')
    def close(self):self.closed=True


def test_exact_source_catalog_grants_do_not_change_backtest_plans():
    before=existing.desired_plan()
    plan=provision.desired_plan(CATALOG)
    grants=existing._desired_grants(plan)
    assert all(privilege=='SELECT' and '*' not in database+table for privilege,database,table in grants)
    assert plan.select_arte==plan.insert_arte==frozenset()
    assert grants==frozenset({('SELECT','system','tables'),
        ('SELECT','market_sip_compact','events_2025'),('SELECT','market_sip_compact','events_2026'),
        ('SELECT','market_sip_compact','events_source_day_stats'),
        ('SELECT','market_sip_compact','events_ordinal_continuity'),
        ('SELECT','q_live','historical_trade_reporting_coverage_v1')})
    assert existing.desired_plan()==before
    for invalid in (('events_*',),('events_2027',),('events_2026','events_2026')):
        with pytest.raises(ValueError):provision.desired_plan(invalid)
    assert provision.discover_event_tables(Client({'catalog':(*CATALOG,'events_2027')},'admin'))==CATALOG


def test_default_plan_never_connects_or_reads_credentials(monkeypatch,capsys):
    monkeypatch.setattr(provision,'_admin_client',lambda *args:pytest.fail('default plan connected'))
    monkeypatch.setattr(provision,'credential',lambda **kwargs:pytest.fail('default plan touched secret'))
    assert provision.main([])==0
    output=capsys.readouterr().out
    assert 'No connection or change' in output and PASSWORD not in output


@pytest.mark.parametrize('extra',[
    f'GRANT SELECT ON market_sip_compact.* TO {source.PRINCIPAL}',
    f'GRANT INSERT ON market_sip_compact.events_2026 TO {source.PRINCIPAL}',
    f'GRANT SELECT ON arte.bars_v1 TO {source.PRINCIPAL}',
    f'GRANT broad_role TO {source.PRINCIPAL}'])
def test_unexpected_existing_authority_rejects_before_any_mutation(extra):
    state={'grants':[extra]}
    admin=Client(state,'administrator')
    reader=Client(state,source.PRINCIPAL)
    with pytest.raises(RuntimeError):
        provision.apply_with_clients(admin=admin,credential=lambda **kwargs:PASSWORD,
            client_factory=lambda *args:reader)
    assert not any(query.startswith(('CREATE','GRANT','ALTER','DROP','INSERT','REVOKE')) for query in admin.calls)
    assert reader.closed


def test_new_reader_gets_exact_select_only_readonly_and_readback():
    state={'present':'0'}
    admin=Client(state,'administrator')
    reader=Client(state,source.PRINCIPAL)
    plan=provision.apply_with_clients(admin=admin,credential=lambda **kwargs:PASSWORD,
        client_factory=lambda *args:reader)
    created=[query for query in admin.calls if query.startswith('CREATE')]
    assert len(created)==1 and 'SETTINGS readonly=1 READONLY' in created[0]
    assert 'max_threads=2 READONLY, max_execution_time=60 READONLY' in created[0]
    assert PASSWORD not in created[0]
    assert frozenset(state['grants'])==frozenset(plan.grants())
    assert all(query.startswith('GRANT SELECT ON ') for query in state['grants'])
    assert reader.closed


def private_file(monkeypatch,*,user=source.PRINCIPAL,password=PASSWORD):
    text=f'{source.STEM}URL={source.URL}\n{source.STEM}USER={user}\n{source.STEM}PASSWORD={password}\n'
    monkeypatch.setattr(Path,'read_text',lambda self,**kwargs:text)
    monkeypatch.setattr(source,'workstation_ipv4_transport',lambda url:'http://192.168.1.218:18123')


def test_reader_exact_identity_grants_and_mutation_guard(monkeypatch):
    private_file(monkeypatch)
    state={'grants':list(provision.desired_plan(CATALOG).grants())}
    transport=Client(state,source.PRINCIPAL)
    calls=[]
    def factory(*args,**kwargs):calls.append((args,kwargs));return transport
    reader=source.canonical_source_client(environment={},client_factory=factory)
    assert calls[0][0][1]==source.PRINCIPAL
    assert calls[0][1]['default_query_params']['readonly']==1
    assert calls[0][1]['default_query_params']=={'readonly':1}
    before=len(transport.calls)
    for sql in ('INSERT INTO x VALUES (1)','CREATE TABLE x(a UInt8)','SET readonly=0'):
        with pytest.raises(ValueError):reader.execute(sql)
    assert len(transport.calls)==before
    reader.close();assert transport.closed


def test_reader_wrong_identity_or_broad_grants_close_without_secret_error(monkeypatch):
    private_file(monkeypatch)
    for state,user in (({},'backtest_v3_reader'),({'grants':[f'GRANT ALL ON *.* TO {source.PRINCIPAL}']},source.PRINCIPAL)):
        transport=Client(state,user)
        with pytest.raises(ValueError) as error:
            source.canonical_source_client(environment={},client_factory=lambda *args,**kwargs:transport)
        assert PASSWORD not in str(error.value) and transport.closed
    with pytest.raises(ValueError):
        source.canonical_source_client(environment={'CANONICAL_V7_SOURCE_CREDENTIAL_FILE':'C:/public.env'},
            client_factory=lambda *args,**kwargs:pytest.fail('invalid privatepath connected'))
    def leaking_driver(*args,**kwargs):
        raise RuntimeError('driver echoed '+PASSWORD)
    with pytest.raises(ValueError) as error:
        source.canonical_source_client(environment={},client_factory=leaking_driver)
    assert PASSWORD not in str(error.value)


def test_secret_creation_reuses_private_acl_before_password_write(monkeypatch,tmp_path):
    path=tmp_path/'private.env'
    monkeypatch.setattr(provision,'SECRET_PATH',path)
    seen=[]
    def restrict(target):seen.append(target.read_text())
    monkeypatch.setattr(provision,'_restrict_secret_file',restrict)
    monkeypatch.setattr(provision.secrets,'token_urlsafe',lambda size:PASSWORD)
    assert provision.credential(account_exists=False)==PASSWORD
    assert seen[0]=='' and PASSWORD in seen[1]
    path.unlink()
    with pytest.raises(RuntimeError,match='lacks its private credential'):
        provision.credential(account_exists=True)
    assert not path.exists()


def test_select_transport_error_cannot_echo_private_credentials():
    class LeakingTransport:
        def execute(self, query):
            raise RuntimeError('driver echoed '+PASSWORD)
    reader=source.CanonicalSourceReadClient(LeakingTransport())
    with pytest.raises(ValueError,match='SELECT transport failed') as error:
        reader.execute('SELECT 1')
    assert PASSWORD not in str(error.value)


def test_existing_owned_reader_gets_server_limits_without_new_grants():
    state={'grants':list(provision.desired_plan(CATALOG).grants()),
        'max_threads':'16','max_execution_time':'0'}
    admin=Client(state,'administrator')
    reader=Client(state,source.PRINCIPAL)
    provision.apply_with_clients(admin=admin,credential=lambda **kwargs:PASSWORD,
        client_factory=lambda *args:reader)
    assert [sql for sql in admin.calls if sql.startswith(('ALTER','CREATE','GRANT'))]==[
        f'ALTER USER {source.PRINCIPAL} SETTINGS readonly=1 READONLY, max_threads=2 READONLY, max_execution_time=60 READONLY']
    assert reader.closed


def test_read_client_rejects_unbounded_server_profile(monkeypatch):
    private_file(monkeypatch)
    transport=Client({'max_execution_time':'0'},source.PRINCIPAL)
    with pytest.raises(ValueError,match='authentication failed'):
        source.canonical_source_client(environment={},client_factory=lambda *args,**kwargs:transport)
    assert transport.closed
