"""Private profile and actual provisioning-method fixtures; no live operations."""
import json
from types import SimpleNamespace as NS
import pytest
from research.level_book.v7 import canonical_source_reader as m
from scripts.clickhouse import provision_canonical_v7_source_reader as provision


@pytest.fixture
def plan():return m.SourceReadPlan((2025,2026),'assigned_canonical')


@pytest.mark.parametrize('years,policy', [((), 'assigned'),([2025],'assigned'),((True,),'assigned'),
    ((2025.,),'assigned'),((2026,2025),'assigned'),((2025,2025),'assigned'),((0,),'assigned'),
    ((10000,),'assigned'),((2025,),'default'),((2025,),'hdd'),((2025,),''),((2025,),"bad'policy")])
def test_exact_contract(years,policy):
    with pytest.raises(ValueError):m.SourceReadPlan(years,policy)


def test_exact_grant_closure(plan):
    assert len(plan.grants)==9
    assert all(g[0]=='SELECT' and '*' not in g for g in plan.grants)
    assert ('SELECT','market_sip_compact','events_2025') in plan.grants
    assert ('SELECT','arte','structural_levels_v7_v2') not in plan.grants


def test_foreign_proxy_cannot_supply_grants_or_trigger_admin(plan):
    class Proxy:
        def __post_init__(self):pass
        grants=frozenset({('INSERT','market_sip_compact','events_2025')})
    bad=Proxy();c=Client(plan)
    with pytest.raises(ValueError):provision.apply_with_clients(bad,admin=c,private_credential=lambda **k:pytest.fail('no credential'),client_factory=lambda *a:pytest.fail('no factory'))
    with pytest.raises(ValueError):m.verify_grants(c,bad)
    with pytest.raises(ValueError):m.storage_preflight(c,bad)
    assert not c.queries


def test_source_plan_subclass_rejected():
    class Child(m.SourceReadPlan):pass
    with pytest.raises(ValueError):Child((2025,),'assigned')


class Client:
    def __init__(self,plan):self.plan=plan;self.grants=set(plan.grants);self.queries=[];self.closed=False;self.bad=None
    def close(self):self.closed=True
    def execute(self,sql):
        self.queries.append(sql)
        if sql=='SELECT currentUser()':return m.PRINCIPAL
        if sql=='SHOW GRANTS FINAL':return '\n'.join(f'GRANT {p} ON {d}.{t} TO {m.PRINCIPAL}' for p,d,t in sorted(self.grants))
        if 'FROM system.storage_policies' in sql:rows=[dict(disks=['canonical_disk'])]
        elif 'FROM system.parts' in sql:rows=[]
        elif 'FROM system.tables' in sql and 'storage_policy' in sql:rows=[dict(name=f'events_{y}',storage_policy=self.plan.canonical_policy) for y in self.plan.years]
        elif 'FROM system.tables' in sql:rows=[dict(name=sql.split("name='")[1].split("'")[0])]
        else:return ''
        if self.bad=='default' and 'storage_policies' in sql:rows=[dict(disks=['default'])]
        if self.bad=='policy' and 'storage_policy FROM' in sql:rows[0]['storage_policy']='foreign'
        if self.bad=='parts' and 'system.parts' in sql:rows=[dict(table='events_2025',disk_name='hdd')]
        if self.bad=='missing' and 'system.tables' in sql:rows=[]
        return '\n'.join(json.dumps(r) for r in rows)


@pytest.mark.parametrize('sql',['INSERT INTO x VALUES (1)','CREATE TABLE x','SELECT 1; DROP TABLE x','ALTER USER x','WITH x AS (SELECT 1) INSERT INTO x.y SELECT 1'])
def test_transport_blocks_mutations(sql,plan):
    c=Client(plan)
    with pytest.raises(ValueError):m.SourceReader(c).execute(sql)
    assert not c.queries


def test_readonly_permission_inspection(plan):
    c=Client(plan);r=m.SourceReader(c)
    r.execute('CHECK GRANT INSERT ON x.y');r.execute('SELECT 1');r.execute('SHOW GRANTS FINAL')
    assert len(c.queries)==3


def test_private_transport_error_text_not_propagated():
    class Broken:
        def execute(self,sql):raise RuntimeError('private-password-or-authentication-body')
    with pytest.raises(ValueError) as error:m.SourceReader(Broken()).execute('SELECT 1')
    assert 'private-password' not in str(error.value)
    assert 'query_sha256=' in str(error.value)


def test_actual_canonical_bars_sql_with_select_is_readonly(plan):
    from research.level_book.v7 import campaign_source as source
    from src.backend.swing_book_source import session_bounds
    left,right=session_bounds('2026-08-03')
    metadata=dict(event_count=1,next_ordinal=1,last_ordinal=0,
        first_sip_timestamp_us=int(left.timestamp()*1000000),last_sip_timestamp_us=int(right.timestamp()*1000000)-1)
    sql=source.bars_sql('ALPHA','2026-08-03',[],metadata)
    c=Client(plan);m.SourceReader(c).execute(sql)
    assert c.queries==[sql]
    m.SourceReader(c).execute("WITH 'INSERT' AS label SELECT label")


@pytest.mark.parametrize('sql', ["WITH x AS (SELECT 'DROP' label), y AS (SELECT label FROM x) SELECT * FROM y",
    "WITH x AS (SELECT 1 /* UPDATE not an operation */) SELECT * FROM x -- INSERT label\n",
    "SELECT name FROM system.tables WHERE name='events_2025'", "SELECT 'SYSTEM' AS text"])
def test_nested_quoted_commented_readonly_queries(plan,sql):
    c=Client(plan);m.SourceReader(c).execute(sql)
    assert c.queries==[sql]


@pytest.mark.parametrize('sql', ["WITH x AS (SELECT 1) /* SELECT */ DELETE FROM x", "WITH x AS (SELECT 1) UPDATE x SET a=1",
    "WITH x AS (SELECT 1) RENAME TABLE x TO y", "WITH x AS (SELECT 1) /* harmless */ INSERT INTO x SELECT 1",
    "SELECT * FROM system.query_log", "/* harmless */ DROP TABLE x"])
def test_actual_writer_statements_not_hidden_by_comments(plan,sql):
    c=Client(plan)
    with pytest.raises(ValueError):m.SourceReader(c).execute(sql)
    assert not c.queries


def test_full_real_filtered_prefix_empty_receipt_on_declared_transport(declared_worker,plan,tmp_path,monkeypatch):
    from research.level_book.v7 import filtered_prefix
    from src.backend.swing_book_source import session_bounds
    f,client,cleanup=declared_worker
    left,right=session_bounds('2026-08-03')
    day=dict(ticker='ALPHA',source_date='2026-08-03',event_count=1,next_ordinal=1,last_ordinal=0,
        first_sip_timestamp_us=int(left.timestamp()*1000000),last_sip_timestamp_us=int(right.timestamp()*1000000)-1)
    frozen=dict(start='2026-08-03',end='2026-08-03',plan_hash='a'*64,rules=[],
        rows=[dict(ticker='ALPHA',status='queued',coverage={'days':1})])
    def execute(sql):
        client.queries.append(sql)
        if 'GROUP BY ticker' in sql:rows=[{'days':1}]
        elif 'event_condition_token_reference' in sql:rows=[]
        elif 'events_ordinal_continuity' in sql:rows=[day]
        elif 'historical_trade_reporting' in sql:rows=[dict(source_date=day['source_date'],status='complete')]
        elif 'market_stock_split' in sql:rows=[]
        elif sql.lstrip().startswith('WITH '):rows=[]
        else:pytest.fail('Unexpected actual worker SQL')
        return '\n'.join(json.dumps(r) for r in rows)
    client.execute=execute
    f.initialize_filtered_worker(plan.payload())
    monkeypatch.setattr(f.c,'checked_plan',lambda root:frozen)
    monkeypatch.setattr(filtered_prefix.shutil,'disk_usage',lambda path:NS(free=20*1024**3))
    filtered_prefix.worker(NS(runtime=tmp_path,ticker='ALPHA',threads=1,before='2026-08-04',stop_file=tmp_path/'STOP'))
    target=f.c.paths(tmp_path,'ALPHA')
    receipt=f.c.read(target/'receipts'/'2026-08-03.json')
    assert receipt['state']=='empty' and receipt['bars']==0 and receipt['parent_hash'] is None
    marker=f.c.read(filtered_prefix.marker(target,'2026-08-04'))
    assert marker['checkpoint_hash'] is None and marker['sessions']==1
    assert not (target/'books'/'2026-08-03.json.gz').exists()
    assert sum(sql.lstrip().startswith('WITH ') for sql in client.queries)==1
    cleanup[0]()


@pytest.mark.parametrize('bad',['default','policy','parts','missing'])
def test_storage_policy_and_actual_parts_fail_closed(plan,bad):
    c=Client(plan);c.bad=bad
    with pytest.raises(ValueError):m.storage_preflight(c,plan)


@pytest.mark.parametrize('bad',[('INSERT','market_sip_compact','events_2025'),('SELECT','market_sip_compact','*'),('SELECT','market_sip_compact','events_2024')])
def test_effective_grants_reject_broad_or_foreign(plan,bad):
    c=Client(plan);c.grants.add(bad)
    with pytest.raises(ValueError):m.verify_grants(c,plan)


def test_missing_grants(plan):
    c=Client(plan);c.grants.pop()
    with pytest.raises(ValueError,match='lacks'):m.verify_grants(c,plan)
    assert m.verify_grants(c,plan,complete=False)==c.grants


def test_provision_plan_no_credentials_or_connections(monkeypatch,capsys):
    monkeypatch.setattr(provision,'_admin_client',lambda *a:pytest.fail('no admin in plan'))
    monkeypatch.setattr(provision,'credential',lambda **k:pytest.fail('no credential in plan'))
    assert provision.main(['--years','2025','2026','--canonical-policy','assigned_canonical'])==0
    assert 'INSERT 0' in capsys.readouterr().out
    assert provision.main(['--years','2025','--canonical-policy','assigned_canonical','--apply'])==1


def test_actual_apply_is_exact_idempotent_and_closes(plan):
    reader=Client(plan);reader.grants.clear();writes=[]
    class Admin(Client):
        def execute(self,sql):
            writes.append(sql)
            if sql=='SELECT currentUser()':return 'operator'
            if 'FROM system.users' in sql:return '1'
            if sql.startswith('GRANT '):
                _,permission,_,table,_,user=sql.split();db,name=table.split('.');reader.grants.add((permission,db,name));return ''
            return super().execute(sql)
    admin=Admin(plan)
    provision.apply_with_clients(plan,admin=admin,private_credential=lambda **k:'x'*48,client_factory=lambda *a:reader)
    assert reader.closed
    assert len([s for s in writes if s.startswith('GRANT ')])==9
    assert not any(s.startswith(('INSERT','ALTER','CREATE')) for s in writes)
    writes.clear();reader.closed=False
    provision.apply_with_clients(plan,admin=admin,private_credential=lambda **k:'x'*48,client_factory=lambda *a:reader)
    assert not any(s.startswith('GRANT ') for s in writes)


@pytest.mark.parametrize('failure',['storage','extra-grants'])
def test_apply_stops_before_grants(plan,failure):
    reader=Client(plan);admin=Client(plan);admin.queries=[]
    if failure=='storage':admin.bad='parts'
    else:reader.grants.add(('INSERT','market_sip_compact','events_2025'))
    original=admin.execute
    def execute(sql):
        if sql=='SELECT currentUser()':return 'operator'
        if 'FROM system.users' in sql:return '1'
        return original(sql)
    admin.execute=execute
    with pytest.raises(ValueError):provision.apply_with_clients(plan,admin=admin,private_credential=lambda **k:'x'*48,client_factory=lambda *a:reader)
    assert not any(s.startswith('GRANT ') for s in admin.queries)


def test_loader_real_factory_private_profile_and_close_on_failure(tmp_path,monkeypatch,plan):
    import scripts.clickhouse.provision_trading_journal as private
    import src.trading_runtime.arte_journal_writer as writer
    import research.mlops.clickhouse as transport
    monkeypatch.setattr(private,'SECRET_ROOT',tmp_path)
    path=tmp_path/(m.PRINCIPAL+'.env');path.write_text('fixture; loader intercepted without secret read')
    monkeypatch.setenv(m.FILE_KEY,str(path))
    for suffix in ('URL','USER','PASSWORD'):monkeypatch.delenv(m.STEM+suffix,raising=False)
    acl=[];monkeypatch.setattr(private,'_restrict_secret_file',lambda p:acl.append(p))
    monkeypatch.setattr(writer,'_dedicated_clickhouse_credentials',lambda stem,key:(provision.URL,m.PRINCIPAL,'x'*48))
    c=Client(plan);factory=[]
    monkeypatch.setattr(transport,'ClickHouseHttpClient',lambda *a,**k:factory.append((a,k)) or c)
    reader=m.source_client(plan)
    assert acl==[path]
    assert factory[0][1]['default_query_params']['readonly']==1
    assert factory[0][0][1]==m.PRINCIPAL
    reader.close();c.closed=False;c.grants.add(('INSERT','market_sip_compact','events_2025'))
    with pytest.raises(ValueError):m.source_client(plan)
    assert c.closed
    monkeypatch.setenv(m.STEM+'USER','foreign')
    with pytest.raises(ValueError,match='private FILE'):m.source_client(plan)


def test_exporter_two_clients_closed_and_exact_years(tmp_path,monkeypatch):
    from research.level_book.v7 import canonical_scoped_campaign as scope
    import src.backend.backtest_market_data as market
    proof_client=NS(close=lambda:order.append('proof-close'))
    source=NS(close=lambda:order.append('source-close'))
    order=[]
    parent=dict(start='2025-01-01',end='2026-09-12');parent['plan_hash']=scope.c.digest(parent)
    scope.c.write(tmp_path/'parent'/'plan.json',parent)
    monkeypatch.setattr(scope,'read_scope',lambda *a:{})
    monkeypatch.setattr(market,'readonly_clickhouse_client',lambda **k:proof_client)
    def scopes(reader,s):
        assert reader is proof_client;order.append('original-proofs');return [dict(tickers=['ALPHA'])]
    monkeypatch.setattr(scope,'certified_scopes',scopes)
    monkeypatch.setattr(scope.filtered,'make_plan',lambda root:dict(rows=[dict(ticker='ALPHA',state='queued',parent='parent',parent_hash=parent['plan_hash'])]))
    monkeypatch.setenv(m.POLICY_KEY,'assigned_canonical')
    def client(selected):
        assert order==['original-proofs','proof-close'];assert selected.years==(2025,2026);return source
    monkeypatch.setattr(m,'source_client',client)
    def prepare(root,s,scopes,reader,**k):
        assert reader is source;order.append('source-metadata');return dict(scoped_authority={})
    monkeypatch.setattr(scope,'prepare_manifest',prepare)
    result=scope.build_manifest(tmp_path,'x','a'*64,'y')
    assert order==['original-proofs','proof-close','source-metadata','source-close']
    assert result['scoped_authority']['source_read_contract']['canonical_policy']=='assigned_canonical'


@pytest.fixture
def declared_worker(monkeypatch,plan):
    from research.level_book.v7 import filtered_campaign as f
    import multiprocessing.util
    monkeypatch.setattr(f.c,'CLIENTS',{})
    monkeypatch.setattr(f,'_WORKER_SOURCE_PLAN',None)
    monkeypatch.setenv(m.FILE_KEY,'private-fixture-file')
    monkeypatch.setenv(m.POLICY_KEY,plan.canonical_policy)
    monkeypatch.setattr(f.signal,'signal',lambda *a:None)
    cleanup=[]
    monkeypatch.setattr(multiprocessing.util,'Finalize',lambda obj,callback,**k:cleanup.append(callback))
    client=Client(plan)
    monkeypatch.setattr(m,'source_client',lambda selected:m.SourceReader(client))
    monkeypatch.setattr(f.c,'load_env_files',lambda *a,**k:pytest.fail('no generic credential discovery'))
    return f,client,cleanup


def test_actual_initializer_campaign_query_and_cleanup(declared_worker,plan):
    f,client,cleanup=declared_worker
    f.initialize_filtered_worker(plan.payload())
    assert type(f.c.CLIENTS[1]) is m.SourceReader
    assert f._WORKER_SOURCE_PLAN==plan
    # Real campaign.query uses exactly CLIENTS[threads], appends JSONEachRow.
    assert f.c.query("SELECT disks FROM system.storage_policies WHERE policy_name='assigned_canonical'",1)==[{'disks':['canonical_disk']}]
    assert client.queries[-1].endswith(' FORMAT JSONEachRow')
    cleanup[0]()
    assert client.closed and f.c.CLIENTS=={} and f._WORKER_SOURCE_PLAN is None


def test_actual_filtered_prefix_query_routes_declared_transport(declared_worker,plan,tmp_path,monkeypatch):
    from research.level_book.v7 import filtered_prefix
    f,client,cleanup=declared_worker
    f.initialize_filtered_worker(plan.payload())
    frozen=dict(start='2025-01-01',end='2026-08-03',rows=[dict(ticker='ALPHA',status='queued',coverage={'expected':'fixture'})])
    monkeypatch.setattr(f.c,'checked_plan',lambda root:frozen)
    # Deliberately rejects at first certified coverage query, before bars/fit.
    with pytest.raises(ValueError,match='Certified history differs'):
        filtered_prefix.worker(NS(runtime=tmp_path,ticker='ALPHA',threads=1,before='2026-08-04',stop_file=tmp_path/'STOP'))
    assert len(client.queries)==1
    assert 'events_ordinal_continuity FINAL' in client.queries[0]
    cleanup[0]()


def test_legacy_initializer_and_query_remain_original(monkeypatch):
    from research.level_book.v7 import filtered_campaign as f
    monkeypatch.setattr(f,'_WORKER_SOURCE_PLAN',None)
    for key in (m.FILE_KEY,m.POLICY_KEY):monkeypatch.delenv(key,raising=False)
    calls=[]
    monkeypatch.setattr(f,'initialize_worker',lambda:calls.append('legacy-initialize'))
    f.initialize_filtered_worker()
    assert calls==['legacy-initialize'] and f.declared_source_plan({}) is None


def test_real_legacy_initializer_preserves_original_discovery(monkeypatch):
    from research.level_book.v7 import filtered_campaign as f
    from research.level_book.v7.workstation import initialize_worker
    monkeypatch.setattr(f,'_WORKER_SOURCE_PLAN',None)
    monkeypatch.setattr(f,'initialize_worker',initialize_worker)
    calls=[]
    monkeypatch.setattr(f.signal,'signal',lambda *a:calls.append('ignore-interrupt'))
    monkeypatch.setattr(f.c,'discover_clickhouse_env_files',lambda:['legacy-fixture'])
    monkeypatch.setattr(f.c,'load_env_files',lambda files,**k:calls.append((files,k)))
    f.initialize_filtered_worker()
    assert calls==['ignore-interrupt',(['legacy-fixture'],{'verbose':False})]


@pytest.mark.parametrize('failure',['cached','policy','missing-file','foreign-schema'])
def test_declared_initializer_rejects_before_factory(declared_worker,plan,monkeypatch,failure):
    f,client,_=declared_worker
    payload=plan.payload()
    if failure=='cached':f.c.CLIENTS[1]=object()
    if failure=='policy':monkeypatch.setenv(m.POLICY_KEY,'foreign')
    if failure=='missing-file':monkeypatch.delenv(m.FILE_KEY)
    if failure=='foreign-schema':payload['policy_id']='foreign@9'
    with pytest.raises(ValueError):f.initialize_filtered_worker(payload)
    assert not client.queries


def test_manifest_policy_closure_and_foreign_source_reject(declared_worker,plan):
    from hashlib import sha256
    f,_,_=declared_worker
    paths=('research/level_book/v7/canonical_scoped_campaign.py','scripts/prepare_canonical_v7_scoped_campaign.py',
        'research/level_book/v7/canonical_source_reader.py','scripts/clickhouse/provision_canonical_v7_source_reader.py')
    proof=dict(schema='canonical-v7-scoped-development-campaign@1',source_read_contract=plan.payload(),
        exporter_sources={p:sha256((f.c.REPO/p).read_bytes()).hexdigest() for p in paths})
    proof['scope_proof_hash']=f.c.digest(proof)
    assert f.declared_source_plan({'scoped_authority':proof})==plan
    proof['exporter_sources'][paths[0]]='a'*64
    proof['scope_proof_hash']=f.c.digest({k:v for k,v in proof.items() if k!='scope_proof_hash'})
    with pytest.raises(ValueError,match='source bytes'):f.declared_source_plan({'scoped_authority':proof})
    with pytest.raises(ValueError,match='requires'):f.declared_source_plan({})
