"""Canonical metadata/source fixtures, never native financial authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from research.level_book.v7 import canonical_metadata_parent as m
from research.level_book.v7 import canonical_scoped_campaign as scoped
from research.level_book.v7 import canonical_archive_publication as archive
from src.backend.swing_book_source import session_bounds
from src.market_engine.v7_catalog import CAMPAIGNS

H='a'*64


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    tmp_path=tmp_path.parent/sha256(tmp_path.name.encode()).hexdigest()[:8]
    tmp_path.mkdir()
    baseparent=dict(version=m.c.VERSION,start='2026-07-31',end='2026-08-04',
                    rows=[dict(ticker='ALPHA',status='queued')])
    baseparent['plan_hash']=m.c.digest(baseparent)
    m.c.write(tmp_path/CAMPAIGNS[0]/'plan.json',baseparent)
    base=dict(version=1,catalog_hash=H,rows=[dict(ticker='ALPHA',state='queued',parent=CAMPAIGNS[0],
        parent_hash=baseparent['plan_hash'],plan_hash=H,output='fixture',sessions=3,directory='ALPHA',before='2026-08-05')])
    base['manifest_hash']=m.c.digest(base)
    monkeypatch.setattr(m,'_inventory',lambda root:deepcopy(base))
    monkeypatch.setattr(scoped.filtered,'make_plan',lambda root,**kwargs:
        m.overlay_inventory(root,kwargs['metadata_parent'],inventory=base) if kwargs else deepcopy(base))
    declaration=dict(schema=scoped.VERSION,role='development',sessions=['2026-08-04','2026-08-05'],
        configuration_number=9001,configuration_revision_id='fixture',configuration_payload_hash=H,exclusion_hash=H)
    scopes=[dict(target_session=day,tickers=['ALPHA','BETA'],original_market_token=H,scoped_market_token=H,
        original_price_token=H,scoped_price_token=H,exclusion_policy_hash=H) for day in declaration['sessions']]
    days=[dict(ticker='BETA',source_date=day,event_count=1,next_ordinal=i+1,last_ordinal=i,
               first_sip_timestamp_us=1,last_sip_timestamp_us=2,build_step='fixture',updated_at='fixture')
          for i,day in enumerate(['2026-07-31','2026-08-03','2026-08-04'])]
    coverage=dict(ticker='BETA',days=3,events=3,first=days[0]['source_date'],last=days[-1]['source_date'],signature=H)
    reporting=[dict(source_date=d['source_date'],status='complete',source_digest=H,updated_at='fixture') for d in days]
    rules=[dict(token_id=1,modifier_int=0,update_high_low=1,update_last=1,update_volume=1)]
    class Reader:
        def __init__(self): self.calls=[];self.closed=False
        def execute(self,sql):
            self.calls.append(sql)
            if sql.startswith(m.c.RULE_SQL): value=rules
            elif 'historical_trade_reporting_coverage' in sql:value=reporting
            elif sql.startswith('SELECT * FROM market_sip_compact.events_ordinal_continuity'):value=days
            elif sql.startswith('SELECT ticker,count()'):value=[coverage]
            else:raise AssertionError(sql)
            return '\n'.join(json.dumps(v) for v in value)
        def close(self):self.closed=True
    reader=Reader()
    return tmp_path,declaration,scopes,base,reader,days,coverage,reporting,rules


def parent(f):
    root,scope,scopes,base,reader,*_=f
    return m.prepare_parent(root,scope,scopes,reader,inventory=base)


def test_genuine_metadata_parent_no_fundamental_or_parent_book(fixture):
    root,scope,scopes,base,reader,*_=fixture
    value=parent(fixture)
    assert value['version']==m.VERSION and value['input_policy']==m.POLICY
    assert [r['ticker'] for r in value['rows']]==['BETA']
    assert (value['start'],value['end'])==('2026-07-31','2026-08-04')
    assert not any('feature_tradable_universe' in sql or 'INSERT' in sql for sql in reader.calls)
    assert not list(root.rglob('*.gz'))
    assert m.verify_parent(root,value,inventory=base,scope=scope,scopes=scopes)==value


def test_child_is_real_numerical_producer_not_legacy_parent(fixture):
    value=parent(fixture);root=fixture[0]
    output,child=m.successor(root,value,'BETA')
    assert value['version']==m.VERSION and child['version']==m.c.VERSION
    assert child['derivation']==m.DERIVATION and child['metadata_parent_hash']==value['plan_hash']
    m.c.write(output/'plan.json',child)
    assert m.c.checked_plan(output)==child
    oldkeys=('rules','source_policy','band_config','extraction_version','software','source_files')
    assert all(child[k]==value[k] for k in oldkeys)


def test_overlay_is_exact_disjoint_inventory_and_preserves_old_rows(fixture):
    root,scope,scopes,base,*_=fixture;value=parent(fixture)
    combined=m.overlay_inventory(root,value,inventory=base,scope=scope,scopes=scopes)
    assert next(r for r in combined['rows'] if r['ticker']=='ALPHA')==base['rows'][0]
    assert sorted(r['ticker'] for r in combined['rows'])==['ALPHA','BETA']
    assert combined['metadata_parent_hash']==value['plan_hash']
    assert combined['base_inventory_hash']==m.c.digest(base)


@pytest.mark.parametrize('change',['extra','already_parented','duplicate','scopes','base','policy','version','sources','prefix','rules'])
def test_resealed_metadata_drift_rejects(fixture,change):
    root=fixture[0];value=parent(fixture)
    if change=='extra': value['rows'][0]['ticker']='OTHER'
    elif change=='already_parented':value['rows'][0]['ticker']='ALPHA'
    elif change=='duplicate':value['rows'].append(deepcopy(value['rows'][0]))
    elif change=='scopes':value['scopes'][0]['scoped_market_token']='b'*64
    elif change=='base':value['base_inventory']['catalog_hash']='b'*64
    elif change=='policy':value['input_policy']='legacy'
    elif change=='version':value['version']=m.c.VERSION
    elif change=='sources':value['metadata_parent_sources'][m.SOURCE_FILES[0]]='b'*64
    elif change=='prefix':value['start']='2026-08-03'
    else:value['rules'][0]['update_last']=True
    value['plan_hash']=m.c.digest({k:v for k,v in value.items() if k!='plan_hash'})
    with pytest.raises(ValueError):
        m.verify_parent(root,value,scope=fixture[1],scopes=fixture[2])


@pytest.mark.parametrize('change',['missing_prior','duplicate','foreign','alias','events','reporting','rules'])
def test_real_metadata_queries_fail_closed(fixture,change):
    root,scope,scopes,base,reader,days,coverage,reporting,rules=fixture
    if change=='missing_prior':days.pop();coverage.update(days=2,events=2,last='2026-08-03')
    elif change=='duplicate':days.append(deepcopy(days[-1]));coverage.update(days=4,events=4)
    elif change=='foreign':days[0]['ticker']='OTHER'
    elif change=='alias':days[0]['event_count']=True
    elif change=='events':coverage['events']=7
    elif change=='reporting':reporting[-1]['status']='incomplete'
    else:rules.append(deepcopy(rules[0]))
    with pytest.raises(ValueError):parent(fixture)


def test_uint64_text_preserved_not_normalized(fixture):
    for day in fixture[5]:
        for k in ('event_count','next_ordinal','last_ordinal'):day[k]=str(day[k])
    value=parent(fixture)
    assert type(value['rows'][0]['source_days'][0]['event_count']) is str


def test_no_arbitrary_source_interval(fixture):
    with pytest.raises(ValueError,match='frozen prefix'):
        m.prepare_parent(fixture[0],fixture[1],fixture[2],fixture[4],start='2026-08-03',inventory=fixture[3])


def test_content_addressed_immutable_restart(fixture):
    root=fixture[0];value=parent(fixture)
    path=m.publish_parent(root,value)
    assert m.publish_parent(root,value)==path
    assert m.load_parent(root,path,value['plan_hash'])==value
    with pytest.raises(ValueError,match='content-addressed'):
        m.load_parent(root,root/'arbitrary.json',value['plan_hash'])
    changed=deepcopy(value);changed['rules'][0]['update_last']=False
    with pytest.raises(ValueError):m.publish_parent(root,changed)


def test_scoped_manifest_real_successor_and_prior_proof(fixture):
    root,scope,scopes,base,reader,*_=fixture;value=parent(fixture)
    m.publish_parent(root,value)
    combined=m.overlay_inventory(root,value,inventory=base)
    result=scoped.prepare_manifest(root,scope,scopes,reader,canary_ticker='BETA',_inventory=combined)
    assert result['rows'][0]['parent']==m.relative(value['plan_hash'])
    assert len(result['scoped_authority']['retained_parent_inventory'])==2
    assert result['scoped_authority']['units'][0]['requested']==scope['sessions']


def test_archive_real_canonical_parent_complete_receipt_chain(fixture):
    root,scope,scopes,base,reader,days,*_=fixture;value=parent(fixture)
    m.publish_parent(root,value);folder,child=m.successor(root,value,'BETA');m.c.write(folder/'plan.json',child)
    target=folder/'tickers'/'BETA';source=dict(days=days,splits=[],plan_hash=child['plan_hash'],reporting_coverage_hash=H)
    m.c.write(target/'source-plan.json',source)
    previous=None
    for d in days:
        day=d['source_date'];barhash=m.c.digest([day])
        genesis=m.c.digest(dict(ticker='BETA',session='0001-01-01',available_at=session_bounds(day)[0].timestamp(),
                               levels=[],source_extraction_version=m.EXTRACTION_VERSION,band_config=m.CONFIG))
        book=dict(ticker='BETA',session=day,available_at=session_bounds(day)[1].timestamp(),
                  prior_checkpoint_hash=previous or genesis,input_hash=barhash,input_policy=m.POLICY,levels=[],
                  retrospective=True,version='historical-level-mle-book-1',source_extraction_version=m.EXTRACTION_VERSION,band_config=m.CONFIG)
        book['checkpoint_hash']=m.c.digest(book);m.c.write(target/'books'/f'{day}.json.gz',book)
        m.c.write(target/'receipts'/f'{day}.json',dict(state='complete',source_hash=m.source.source_hash(d,child['rules']),
            parent_hash=previous,bar_hash=barhash,checkpoint_hash=book['checkpoint_hash']))
        previous=book['checkpoint_hash']
    m.c.write(target/'ready.json',dict(plan_hash=child['plan_hash'],source_plan_hash=m.c.digest(source)))
    request=archive.ArchiveRequest('2026-08-05','BETA',m.relative(value['plan_hash']),*([H]*5),
                                   metadata_parent_hash=value['plan_hash'])
    member=archive.verify_archive_member(root,request)
    assert member.parent_plan_hash==value['plan_hash'] and member.chronology_sessions==3
    for field,foreign in [('metadata_parent_hash','b'*64),('scoped_market_token','b'*64),('ticker','OTHER')]:
        with pytest.raises(ValueError):archive.verify_archive_member(root,replace(request,**{field:foreign}))
    with pytest.raises(ValueError):replace(request,parent_relative=CAMPAIGNS[0])


def test_pair_guard_precedes_source_reader(monkeypatch):
    from src.backend import backtest_market_data as market
    monkeypatch.setattr(scoped,'read_scope',lambda *a: {})
    monkeypatch.setattr(scoped,'certified_scopes',lambda *a: [])
    class Reader:
        def close(self):pass
    monkeypatch.setattr(market,'readonly_clickhouse_client',lambda **k:Reader())
    monkeypatch.setattr(scoped.filtered,'make_plan',lambda *a: {})
    with pytest.raises(ValueError,match='Paired'):
        scoped.build_manifest(Path('.'),'scope',H,'exclusions',metadata_parent_hash=H)


def test_actual_worker_validates_source_plan_and_observed_receipts(fixture,monkeypatch):
    from research.level_book.v7.filtered_prefix import worker
    root,scope,scopes,base,reader,days,coverage,reporting,rules=fixture
    value=parent(fixture);folder,child=m.successor(root,value,'BETA');m.c.write(folder/'plan.json',child)
    def query(sql,threads=1):
        if sql.startswith(m.c.RULE_SQL):return deepcopy(rules)
        if sql.startswith('SELECT ticker,count()'):return [deepcopy(coverage)]
        if 'historical_trade_reporting_coverage' in sql:return deepcopy(reporting)
        if 'market_stock_split_v1' in sql:return []
        if sql.startswith('SELECT * FROM market_sip_compact.events_ordinal_continuity'):
            matching=[d for d in days if m.source.literal(d['source_date']) in sql]
            return deepcopy(matching if 'AND source_date=' in sql else days)
        if sql.startswith('WITH '):
            # Real worker calls canonical bars_sql. Fixture response is one
            # source second, not an alternative producer or financial claim.
            stamp=int(sql.split('sip_timestamp_us>=')[1].split(' ')[0])//1000000+1
            return [dict(t=stamp,open=10.,high=10.1,low=9.9,close=10.,volume=100.,
                         trades=1,last_count=1,extrema_count=1)]
        raise AssertionError(sql)
    monkeypatch.setattr(m.c,'query',query)
    worker(SimpleNamespace(runtime=folder,ticker='BETA',threads=1,before='2026-08-05'))
    target=folder/'tickers'/'BETA';stored=m.c.read(target/'source-plan.json')
    assert stored['days']==days and stored['plan_hash']==child['plan_hash']
    receipts=[m.c.read(target/'receipts'/f"{d['source_date']}.json") for d in days]
    assert all(r['state']=='complete' and r['bars']==1 for r in receipts)
    assert receipts[0]['parent_hash'] is None
    assert receipts[1]['parent_hash']==receipts[0]['checkpoint_hash']
    assert m.c.read(target/'progress.json')['completed']==3
    # Retry proves actual worker source/receipt chain without a second fit.
    worker(SimpleNamespace(runtime=folder,ticker='BETA',threads=1,before='2026-08-05'))
    assert m.c.read(target/'progress.json')['resumed']==3
    request=archive.ArchiveRequest('2026-08-05','BETA',m.relative(value['plan_hash']),*([H]*5),
                                   metadata_parent_hash=value['plan_hash'])
    m.publish_parent(root,value)
    assert archive.verify_archive_member(root,request).chronology_sessions==3


def test_reference_and_complete_overlay_cannot_be_resealed_foreign(fixture):
    root,scope,scopes,base,reader,*_=fixture;value=parent(fixture)
    m.publish_parent(root,value);combined=m.overlay_inventory(root,value,inventory=base)
    result=scoped.prepare_manifest(root,scope,scopes,reader,canary_ticker='BETA',_inventory=combined)
    proof=result['scoped_authority']
    proof['metadata_parent_reference']=dict(relative=m.relative(value['plan_hash']),plan_hash=value['plan_hash'],
        base_inventory_hash=value['base_inventory_hash'],retained_inventory_hash=m.c.digest(proof['retained_parent_inventory']))
    assert m.verify_manifest_reference(proof,root=root)==value
    mutated=deepcopy(proof);mutated['retained_parent_inventory'][0]['plan_hash']='b'*64
    mutated['metadata_parent_reference']['retained_inventory_hash']=m.c.digest(mutated['retained_parent_inventory'])
    with pytest.raises(ValueError,match='complete overlay'):
        m.verify_manifest_reference(mutated,root=root)
    for edit in (lambda r:r.update(extra=True),lambda r:r.update(plan_hash='b'*64),
                 lambda r:r.update(relative='../foreign')):
        mutated=deepcopy(proof);edit(mutated['metadata_parent_reference'])
        with pytest.raises(ValueError):m.verify_manifest_reference(mutated)


def test_source_read_plan_derived_before_private_queries(fixture):
    plan=m.source_read_plan(fixture[0],fixture[2],fixture[3],'canonical_sip_fixture')
    assert plan.years==(2026,)
    assert ('market_sip_compact','events_2026') in plan.tables
    assert all(g[0]=='SELECT' for g in plan.grants)


def test_cli_rejects_repo_output_before_scope_or_credentials(capsys):
    from scripts.prepare_canonical_v7_metadata_parent import main
    assert main(['--archive-root',str(m.c.REPO),'--development-scope','missing',
                 '--development-scope-sha256',H,'--exclusions','missing'])==1
    assert 'Blocked: ValueError' in capsys.readouterr().err


@pytest.mark.parametrize('drift',[False,True])
def test_actual_build_manifest_reloads_parent_with_private_plan(fixture,monkeypatch,drift):
    from src.backend import backtest_market_data as market
    from research.level_book.v7 import canonical_source_reader as private
    root,scope,scopes,base,reader,days,coverage,reporting,rules=fixture
    exclusions=root/'exclusions.json';exclusions.write_text('[]')
    scope['exclusion_hash']=sha256(exclusions.read_bytes()).hexdigest()
    for item in scopes:item['exclusion_policy_hash']=scope['exclusion_hash']
    scope_path=root/'development.json';scope_path.write_text(json.dumps(scope))
    monkeypatch.setenv('BACKTEST_INPUT_EXCLUSIONS_FILE',str(exclusions))
    monkeypatch.setenv(private.POLICY_KEY,'canonical_fixture_policy')
    monkeypatch.setenv(private.FILE_KEY,'fixture-private-file-not-opened')
    value=parent(fixture);path=m.publish_parent(root,value)
    calls=[]
    class ConfigReader:
        def close(self):calls.append('configuration-reader-closed')
    monkeypatch.setattr(market,'readonly_clickhouse_client',lambda **kwargs:ConfigReader())
    def scopes_read(config_reader,declaration):
        assert type(config_reader) is ConfigReader
        calls.append('fresh-certified-original-and-scoped-products')
        return deepcopy(scopes)
    monkeypatch.setattr(scoped,'certified_scopes',scopes_read)
    def source_factory(plan):
        assert type(plan) is private.SourceReadPlan and plan.years==(2026,)
        assert calls[-1]=='configuration-reader-closed'
        calls.append('private-source-reader')
        return reader
    monkeypatch.setattr(private,'source_client',source_factory)
    if drift:rules[0]['update_last']=0
    args=(root,scope_path,sha256(scope_path.read_bytes()).hexdigest(),exclusions)
    kwargs=dict(canary_ticker='BETA',metadata_parent_path=path,metadata_parent_hash=value['plan_hash'])
    if drift:
        with pytest.raises(ValueError,match='Fresh canonical metadata'):
            scoped.build_manifest(*args,**kwargs)
    else:
        result=scoped.build_manifest(*args,**kwargs)
        assert scoped.filtered.declared_source_plan(result).years==(2026,)
        assert m.verify_manifest_reference(result['scoped_authority'],root=root)==value
        assert result['rows'][0]['ticker']=='BETA'
    assert reader.closed


def test_real_execute_rejects_detached_work_row_before_worker(fixture,monkeypatch):
    from research.level_book.v7 import canonical_source_reader as private
    root,scope,scopes,base,reader,*_=fixture;value=parent(fixture)
    m.publish_parent(root,value);combined=m.overlay_inventory(root,value,inventory=base)
    result=scoped.prepare_manifest(root,scope,scopes,reader,canary_ticker='BETA',_inventory=combined)
    proof=result['scoped_authority'];proof['metadata_parent_reference']=dict(relative=m.relative(value['plan_hash']),
        plan_hash=value['plan_hash'],base_inventory_hash=value['base_inventory_hash'],
        retained_inventory_hash=m.c.digest(proof['retained_parent_inventory']))
    proof['exporter_sources'].update(m.sources())
    plan=private.SourceReadPlan((2026,),'canonical_fixture_policy');proof['source_read_contract']=plan.payload()
    proof['scope_proof_hash']=m.c.digest({k:v for k,v in proof.items() if k!='scope_proof_hash'})
    result['manifest_hash']=m.c.digest({k:v for k,v in result.items() if k!='manifest_hash'})
    folder=root/'prepared';m.c.write(folder/'plan.json',result)
    monkeypatch.setenv(private.POLICY_KEY,plan.canonical_policy);monkeypatch.setenv(private.FILE_KEY,'fixture-private-file')
    monkeypatch.setattr(scoped.filtered,'_WORKER_SOURCE_PLAN',plan)
    monkeypatch.setattr(m.c,'CLIENTS',{1:private.SourceReader(reader)})
    monkeypatch.setattr(scoped.filtered,'worker',lambda *a:pytest.fail('worker called'))
    row=deepcopy(result['rows'][0]);row['before']='2026-08-06'
    with pytest.raises(ValueError,match='Worker row differs'):
        scoped.filtered.execute(root,folder,row)


@pytest.mark.parametrize('ticker',['A'*20,'A'*19+'.'])
def test_operational_deepest_paths_fit_windows_archive_root(ticker):
    base=Path('D:/TradingML/runtimes/level-book-v7')
    directory=m.c.paths(Path('.'),ticker).name
    if ticker.endswith('.'):
        assert directory=='_ticker_'+ticker.encode('ascii').hex() and len(directory)==48
    deepest=base/m.SUCCESSOR_DIRECTORY/('a'*64)/'tickers'/directory/'books'/'.2026-08-03.json.gz.'
    assert len(str(deepest))+32+4 < 260


@pytest.mark.parametrize('change',['continuity','coverage','reporting'])
def test_changed_source_snapshot_rejected_before_parent_return(fixture,change):
    reader,days,coverage,reporting=fixture[4:8]
    original=reader.execute
    observed=False
    def execute(sql):
        nonlocal observed
        result=original(sql)
        if sql.startswith('SELECT * FROM market_sip_compact.events_ordinal_continuity') and not observed:
            observed=True
            # Source changes after the first captured chronology; counts can
            # stay unchanged. Returned JSON still contains the original rows.
            if change=='continuity':days[-1]['updated_at']='changed-source-identity'
            elif change=='coverage':coverage['signature']='b'*64
            else:reporting[-1]['source_digest']='b'*64
        return result
    reader.execute=execute
    with pytest.raises(ValueError,match='changed during metadata preparation'):
        parent(fixture)
