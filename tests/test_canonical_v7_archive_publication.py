from dataclasses import replace, asdict
from datetime import datetime, timezone
import copy
import json
from pathlib import Path

import pytest

from research.level_book.v7 import canonical_archive_publication as p
from research.level_book.v7.campaign import VERSION as PARENT_VERSION
from research.level_book.v7.campaign_source import source_hash
from src.backend.swing_book_source import HISTORICAL_POLICY, session_bounds
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.filtered_v7_history import successor
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.level_book_store import write, read
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION
from src.market_engine.canonical_v7_archive_contract import commit_row, validate_commit, validate_member, inventory_hash


@pytest.fixture
def archive(tmp_path):
    ticker='TEST';relative=p.CAMPAIGNS[0]
    days=[dict(ticker=ticker,source_date=day,event_count=1) for day in ('2026-08-03','2026-08-04')]
    parent=dict(version=PARENT_VERSION,start='2026-08-03',end='2026-08-04',source_policy=HISTORICAL_POLICY,
        extraction_version=EXTRACTION_VERSION,band_config=CONFIG,rules=[],
        rows=[dict(ticker=ticker,directory=ticker,status='queued',coverage=dict(days=2))])
    parent['plan_hash']=digest(parent);write(tmp_path/relative/'plan.json',parent)
    folder,plan=successor(tmp_path,parent,ticker);write(folder/'plan.json',plan)
    target=folder/'tickers'/ticker
    source=dict(days=days,splits=[],plan_hash=plan['plan_hash'],reporting_coverage_hash='b'*64)
    write(target/'source-plan.json',source)
    previous=None
    for metadata in days:
        day=metadata['source_date'];barhash=digest([day])
        book=dict(ticker=ticker,session=day,available_at=session_bounds(day)[1].timestamp(),
            prior_checkpoint_hash=previous or digest(dict(ticker=ticker,session='0001-01-01',available_at=session_bounds(day)[0].timestamp(),levels=[],source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG)),input_hash=barhash,input_policy=POLICY,levels=[],retrospective=True,version='historical-level-mle-book-1',source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG)
        book['checkpoint_hash']=digest(book)
        write(target/'books'/f'{day}.json.gz',book)
        write(target/'receipts'/f'{day}.json',dict(state='complete',source_hash=source_hash(metadata,[]),
             parent_hash=previous,bar_hash=barhash,checkpoint_hash=book['checkpoint_hash']))
        previous=book['checkpoint_hash']
    write(target/'ready.json',dict(plan_hash=plan['plan_hash'],source_plan_hash=digest(source)))
    request=p.ArchiveRequest('2026-08-05',ticker,relative,*(['a'*64]*5))
    return tmp_path,request,target,folder,parent


def mutate_file(path,edit,reseal=None):
    value=read(path);edit(value)
    if reseal:
        value[reseal]=digest({k:v for k,v in value.items() if k!=reseal})
    write(path,value,immutable=False)


def test_real_derived_archive_chronology_and_inventory(archive):
    root,request,target,folder,parent=archive
    plan=p.prepare_archive_plan(root,(request,));member=plan.members[0]
    assert member.chronology_sessions==2
    assert member.successor_plan_hash==read(folder/'plan.json')['plan_hash']
    assert member.parent_plan_hash==parent['plan_hash']
    assert member.checkpoint_hash==read(target/'books/2026-08-04.json.gz')['checkpoint_hash']
    rows=p.member_rows(plan)
    assert rows[0]['available_at']=='2026-08-05 00:00:00.000000000'
    assert validate_commit(commit_row(rows),rows)==commit_row(rows)
    assert commit_row(rows)['unit_count']==1
    assert commit_row(rows)['member_count']==1


@pytest.mark.parametrize('change',[
    'book_digest','book_policy','book_ticker','book_day','book_parent','book_future','book_barhash',
    'receipt_source','receipt_parent','receipt_checkpoint','receipt_state','source_plan','source_ticker',
    'source_duplicate','source_missing_prior','ready','successor_policy','successor_kernel','parent_digest'])
def test_actual_archive_corruption_rejects(archive,change):
    root,request,target,folder,parent=archive
    book=target/'books/2026-08-04.json.gz';receipt=target/'receipts/2026-08-04.json'
    if change=='book_digest':mutate_file(book,lambda v:v.update(input_hash='c'*64))
    elif change.startswith('book_'):
        key,value={'book_policy':('input_policy','legacy-unfiltered'),'book_ticker':('ticker','OTHER'),
            'book_day':('session','2026-08-03'),'book_parent':('prior_checkpoint_hash','c'*64),
            'book_future':('available_at',session_bounds('2026-08-04')[1].timestamp()+1),
            'book_barhash':('input_hash','c'*64)}[change]
        mutate_file(book,lambda v:v.update({key:value}),'checkpoint_hash')
        # Re-sealed receipt cannot make changed original source/book semantics valid.
        mutate_file(receipt,lambda v:v.update(checkpoint_hash=read(book)['checkpoint_hash']))
    elif change.startswith('receipt_'):
        key,value={'receipt_source':('source_hash','c'*64),'receipt_parent':('parent_hash','c'*64),
            'receipt_checkpoint':('checkpoint_hash','c'*64),'receipt_state':('state','active')}[change]
        mutate_file(receipt,lambda v:v.update({key:value}))
    elif change.startswith('source_'):
        edit={'source_plan':lambda v:v.update(plan_hash='c'*64),
              'source_ticker':lambda v:v['days'][0].update(ticker='OTHER'),
              'source_duplicate':lambda v:v['days'].append(v['days'][-1]),
              'source_missing_prior':lambda v:v['days'].pop()}[change]
        mutate_file(target/'source-plan.json',edit)
        mutate_file(target/'ready.json',lambda v:v.update(source_plan_hash=digest(read(target/'source-plan.json'))))
    elif change=='ready':mutate_file(target/'ready.json',lambda v:v.update(source_plan_hash='c'*64))
    elif change.startswith('successor_'):
        mutate_file(folder/'plan.json',lambda v:v.update(input_policy='legacy-unfiltered') if change=='successor_policy' else v['software'].update(python='foreign'),'plan_hash')
    else:mutate_file(root/request.parent_relative/'plan.json',lambda v:v.update(start='2026-01-01'))
    with pytest.raises((ValueError,FileNotFoundError)):
        p.prepare_archive_plan(root,(request,))


def test_empty_prior_retains_original_book_no_invented_seed(archive):
    root,request,target,folder,parent=archive
    earlier=read(target/'books/2026-08-03.json.gz')
    (target/'books/2026-08-04.json.gz').unlink()
    metadata=read(target/'source-plan.json')['days'][1]
    write(target/'receipts/2026-08-04.json',dict(state='empty',source_hash=source_hash(metadata,[]),parent_hash=earlier['checkpoint_hash']),immutable=False)
    member=p.verify_archive_member(root,request)
    assert member.seed_session=='2026-08-04'
    assert member.checkpoint_hash==earlier['checkpoint_hash']
    assert member.chronology_sessions==2


@pytest.mark.parametrize('field,value',[
    ('target_session',True),('target_session','2026-08-08'),('ticker',1),('ticker','test'),
    ('parent_relative','../foreign'),('original_market_token','0'*64),('scoped_price_token',True)])
def test_request_alias_or_missing_session_rejects(archive,field,value):
    root,request,*_=archive
    with pytest.raises((ValueError,TypeError)):
        p.prepare_archive_plan(root,(replace(request,**{field:value}),))


def test_duplicate_and_unsorted_members_reject(archive):
    root,request,*_=archive
    with pytest.raises(ValueError):p.prepare_archive_plan(root,(request,request))
    member=p.verify_archive_member(root,request)
    with pytest.raises(ValueError):p.CanonicalArchivePlan((member,member),p.projection_source_identity())
    with pytest.raises(ValueError):p.CanonicalArchivePlan([member],p.projection_source_identity())


class MemoryTransport:
    """Fixture seam: no installed table, source/cash or financial admission claim."""
    def __init__(self):self.members={};self.commit=None;self.calls=[];self.fail=None
    def read_member(self,row):return self.members.get(row['content_hash'])
    def publish_member(self,root,member,row):
        self.calls.append('member')
        if self.fail=='member':raise RuntimeError('fixture member transport failure')
        self.members[row['content_hash']]=copy.deepcopy(row);return self.members[row['content_hash']]
    def read_commit(self,token):return self.commit
    def publish_commit(self,row):
        self.calls.append('commit')
        if self.fail=='commit':raise RuntimeError('fixture commit transport failure')
        self.commit=copy.deepcopy(row);return self.commit


def test_retry_exact_members_final_commit_last_and_no_duplicate(archive):
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));transport=MemoryTransport()
    first=p.publish_archive_plan(root,plan,transport)
    assert transport.calls==['member','commit']
    assert p.publish_archive_plan(root,plan,transport)==first
    assert transport.calls==['member','commit']


@pytest.mark.parametrize('stage',['member','commit'])
def test_failed_publication_cannot_claim_complete_and_restart_reuses(archive,stage):
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));transport=MemoryTransport();transport.fail=stage
    with pytest.raises(RuntimeError):p.publish_archive_plan(root,plan,transport)
    assert transport.commit is None
    before=transport.calls.count('member');transport.fail=None
    p.publish_archive_plan(root,plan,transport)
    assert transport.calls[-1]=='commit'
    assert transport.calls.count('member')==before+(stage=='member')


def test_fresh_archive_mutation_rejects_before_any_transport(archive):
    root,request,target,*_=archive;plan=p.prepare_archive_plan(root,(request,));transport=MemoryTransport()
    mutate_file(target/'receipts/2026-08-04.json',lambda v:v.update(parent_hash='c'*64))
    with pytest.raises(ValueError):p.publish_archive_plan(root,plan,transport)
    assert transport.calls==[]


@pytest.mark.parametrize('mutation',['floatcount','boolcount','extra','hash','order','rowfloat'])
def test_shared_wire_contract_rejects_aliases_and_drift(archive,mutation):
    root,request,*_=archive;rows=p.member_rows(p.prepare_archive_plan(root,(request,)));commit=commit_row(rows)
    if mutation in ('floatcount','boolcount'):
        commit['unit_count']=1.0 if mutation=='floatcount' else True
        with pytest.raises(ValueError):validate_commit(commit,rows)
    elif mutation=='extra':
        commit['unknown']=1
        with pytest.raises(ValueError):validate_commit(commit,rows)
    elif mutation=='hash':
        commit['member_hash']='c'*64
        with pytest.raises(ValueError):validate_commit(commit,rows)
    elif mutation=='rowfloat':
        row=dict(rows[0],available_at=1.0)
        with pytest.raises(ValueError):validate_member(row)
    else:
        with pytest.raises(ValueError):inventory_hash(rows+rows)


def test_contracts_only_two_new_tables_ssd_and_v1_untouched():
    assert len(p.TABLES)==2
    assert all('live_market_ssd' in table.ddl() for table in p.TABLES)
    assert all('structural_levels_v7' not in table.name for table in p.TABLES)


def test_default_cli_real_archive_no_db_or_credentials(archive,monkeypatch,capsys):
    from scripts.publish_canonical_v7_archive import main
    from research.mlops.clickhouse import ClickHouseHttpClient
    root,request,*_=archive
    def forbidden(*a,**k):raise AssertionError('Planner must never open a DB client')
    monkeypatch.setattr(ClickHouseHttpClient,'__init__',forbidden)
    requests=root/'requests.json';requests.write_text(json.dumps({'schema':'canonical-v7-archive-requests@1','requests':[asdict(request)]}))
    output=root/'prepared.json'
    assert main(['--archive-root',str(root),'--requests',str(requests),'--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['consolidation_hash']==p.prepare_archive_plan(root,(request,)).token
    assert 'no publication' in capsys.readouterr().out
    assert main(['--archive-root',str(root),'--requests',str(requests),'--output',str(output)])==1


def test_cli_apply_rejects_before_requests_or_credentials(archive):
    from scripts.publish_canonical_v7_archive import main
    root,*_=archive
    with pytest.raises(SystemExit) as exc:
        main(['--archive-root',str(root),'--requests',str(root/'absent.json'),'--output',str(root/'out.json'),'--apply'])
    assert exc.value.code==2
    assert not (root/'out.json').exists()


def test_operation_local_chronology_cache_and_fresh_reverification(archive,monkeypatch):
    root,request,target,folder,parent=archive
    earlier=replace(request,target_session='2026-08-04')
    original=p._ArchiveSnapshot._load;loads=[]
    def observed(self,path):
        loads.append(str(path));return original(self,path)
    monkeypatch.setattr(p._ArchiveSnapshot,'_load',observed)
    plan=p.prepare_archive_plan(root,(earlier,request))
    assert plan.members[0].chronology_sessions==1
    assert plan.members[1].chronology_sessions==2
    assert sum(name.endswith('2026-08-03.json.gz') for name in loads)==1
    assert commit_row(p.member_rows(plan))['unit_count']==1
    assert commit_row(p.member_rows(plan))['member_count']==2
    mutate_file(target/'books/2026-08-03.json.gz',lambda v:v.update(input_policy='legacy-unfiltered'),'checkpoint_hash')
    with pytest.raises(ValueError):p.prepare_archive_plan(root,(earlier,request))


def test_missing_receipt_or_book_never_imputed(archive):
    root,request,target,*_=archive
    (target/'receipts/2026-08-04.json').unlink()
    with pytest.raises(FileNotFoundError):p.verify_archive_member(root,request)


def test_prefix_publication_is_checked_without_full_ready(archive):
    root,request,target,folder,parent=archive
    (target/'ready.json').unlink();source=read(target/'source-plan.json')
    book=read(target/'books/2026-08-04.json.gz')
    marker=dict(version=1,plan_hash=read(folder/'plan.json')['plan_hash'],ticker=request.ticker,
        before=request.target_session,sessions=2,through='2026-08-04',source_plan_hash=digest(source),checkpoint_hash=book['checkpoint_hash'])
    marker['prefix_hash']=digest(marker)
    write(target/'prefixes'/f'{request.target_session}.json',marker)
    assert p.verify_archive_member(root,request).chronology_sessions==2
    mutate_file(target/'prefixes'/f'{request.target_session}.json',lambda v:v.update(sessions=2.0),'prefix_hash')
    with pytest.raises(ValueError):p.verify_archive_member(root,request)



def nonempty_book(day,role,price,observations):
    stamp=session_bounds(day)[1].timestamp()
    level=dict(id='L',role=role,role_segments=[dict(start=stamp,role=role)],origin_session='2026-08-03',
        price=price,lower=price-.1,upper=price+.1,association_radius=.2,
        qualified=True,historical=True,fit=dict(status='estimated',count=len(observations),center=price,scale=.1,
            distribution='student_t',lower=price-.1,upper=price+.1,resolution=.01,coverage=.8,
            degrees_of_freedom=4.,scale_at_floor=False),observations=observations)
    result=dict(ticker='TEST',session=day,available_at=stamp,retrospective=True,
        input_policy=POLICY,levels=[level],input_hash=digest(day),prior_checkpoint_hash='a'*64)
    result['checkpoint_hash']=digest(result);return result


def test_snapshot_seed_equals_real_full_chronology_decoder_and_detects_same_count_sql_changes():
    from research.level_book.v7.clickhouse_persistence import compact_checkpoints, datetime64_ns, epoch_ns
    from src.backend.structural_v7_seed import _assemble_seed
    from datetime import date
    obs=dict(price=10.,resolution=.01,at=session_bounds('2026-08-03')[0].timestamp(),
        resolved_at=session_bounds('2026-08-03')[0].timestamp()+1,role='resistance',session='2026-08-03')
    first=nonempty_book('2026-08-03','resistance',10.,[obs])
    second=nonempty_book('2026-08-04','support',10.2,[obs,dict(obs,price=10.1)])
    second['prior_checkpoint_hash']=first['checkpoint_hash'];second['checkpoint_hash']=digest({k:v for k,v in second.items() if k!='checkpoint_hash'})
    levels,observations,coverage=compact_checkpoints((first,second),'a'*64)
    fence=datetime64_ns(epoch_ns(second['available_at']))
    active=lambda rows:[row for row in rows if row['valid_from']<=fence and (row['valid_to'] is None or row['valid_to']>fence)]
    active_levels=sorted(active(levels),key=lambda row:row['level_id'])
    active_obs=sorted(active(observations),key=lambda row:(row['level_id'],row['observation_id']))
    real=_assemble_seed('TEST',date(2026,8,5),coverage[-1],active_levels,active_obs)
    snapshot=p.projected_archive_seed(second,seed_session='2026-08-04',
        available_at=datetime.fromtimestamp(second['available_at'],timezone.utc).isoformat(),source_plan_hash='a'*64)
    assert real==snapshot
    assert real['checkpoint_hash']!=second['checkpoint_hash']
    changed=copy.deepcopy(active_levels);changed[0]['price']+=.01
    assert _assemble_seed('TEST',date(2026,8,5),coverage[-1],changed,active_obs)['checkpoint_hash']!=real['checkpoint_hash']
    changed_obs=copy.deepcopy(active_obs);changed_obs[0]['price']+=.01
    assert _assemble_seed('TEST',date(2026,8,5),coverage[-1],active_levels,changed_obs)['checkpoint_hash']!=real['checkpoint_hash']


def test_plan_projection_identity_and_mixed_lineage_fail_closed(archive):
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,))
    with pytest.raises(ValueError):replace(plan,projection_sources=tuple((name,'c'*64) for name,_ in plan.projection_sources))
    earlier=replace(plan.members[0],request=replace(request,target_session='2026-08-04'),seed_session='2026-08-03',
        available_at=session_bounds('2026-08-03')[1].astimezone(timezone.utc).isoformat(),successor_plan_hash='c'*64)
    with pytest.raises(ValueError):replace(plan,members=(earlier,plan.members[0]))


def test_parser_schema_no_unknown_overrides(archive):
    from scripts.publish_canonical_v7_archive import main
    root,request,*_=archive;inputfile=root/'requests.json'
    inputfile.write_text(json.dumps({'schema':'canonical-v7-archive-requests@1','requests':[dict(asdict(request),unknown=True)]}))
    assert main(['--archive-root',str(root),'--requests',str(inputfile),'--output',str(root/'out.json')])==1
    assert not (root/'out.json').exists()



@pytest.mark.parametrize('mutation',['unknown','missing','float','zero'])
def test_shared_population_scope_hash_strict_and_ticker_independent(archive,mutation):
    from src.market_engine.canonical_v7_archive_contract import SOURCE_SCOPE_KEYS,scope_hash
    _,request,*_=archive
    value={name:getattr(request,name) for name in SOURCE_SCOPE_KEYS}
    assert scope_hash(value)==request.scope_hash
    assert replace(request,ticker='OTHER').scope_hash==request.scope_hash
    changed=dict(value)
    if mutation=='unknown':changed['ticker']='OTHER'
    elif mutation=='missing':del changed['scoped_market_token']
    elif mutation=='float':changed['target_session']=20260805.0
    else:changed['scoped_market_token']='0'*64
    with pytest.raises(ValueError):scope_hash(changed)


@pytest.mark.parametrize('where',['member','commit'])
def test_corrupt_existing_transport_receipt_is_not_treated_as_success(archive,where):
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));transport=MemoryTransport()
    p.publish_archive_plan(root,plan,transport)
    if where=='member':next(iter(transport.members.values()))['book_hash']='c'*64
    else:transport.commit['member_count']=True
    calls=list(transport.calls)
    with pytest.raises(ValueError):p.publish_archive_plan(root,plan,transport)
    assert calls==transport.calls


class SQLTransportFixture:
    """Production execute/JSONEachRow contract; no database or authority seam."""
    def __init__(self):self.tables={};self.calls=[];self.fail_table=None
    def execute(self,sql):
        import re
        self.calls.append(sql)
        if sql.startswith('INSERT'):
            table=re.search(r'INSERT INTO (\S+)',sql)[1]
            if table==self.fail_table:raise RuntimeError('controlled insert failure')
            rows=[json.loads(line) for line in sql.split('FORMAT JSONEachRow\n',1)[1].splitlines()]
            self.tables.setdefault(table,[]).extend(rows);return ''
        assert sql.startswith('SELECT '),sql
        table=re.search(r' FROM (\S+)',sql)[1]
        rows=self.tables.get(table,[])
        # Fixtures select one ticker but multiple dated members independently.
        for column in ('ticker','consolidation_hash','session_date','target_session'):
            match=re.search(column+r"=(?:toDate\()?\s*'([^']+)'",sql)
            if match:rows=[row for row in rows if row.get(column)==match[1]]
        columns=sql[7:sql.index(' FROM ')].split(',')
        return '\n'.join(json.dumps(row if columns==['*'] else {k:row[k] for k in columns}) for row in rows)


def test_actual_sql_transport_content_coverage_member_commit_order_and_exact_restart(archive):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,*_=archive
    earlier=replace(request,target_session='2026-08-04')
    plan=p.prepare_archive_plan(root,(earlier,request));client=SQLTransportFixture()
    result=p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))
    assert result==commit_row(p.member_rows(plan))
    writes=[sql.split()[2] for sql in client.calls if sql.startswith('INSERT')]
    assert writes==[c.direct.COVERAGE,'arte.'+p.PROVENANCE.name,c.direct.COVERAGE,'arte.'+p.PROVENANCE.name,'arte.'+p.COMMIT.name]
    before=len(writes)
    p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))
    assert len([s for s in client.calls if s.startswith('INSERT')])==before
    assert all(' FINAL ' not in sql for sql in client.calls if p.PROVENANCE.name in sql or p.COMMIT.name in sql)


@pytest.mark.parametrize('table',['coverage','member','commit'])
def test_sql_failure_never_creates_final_commit_and_safe_restart(archive,table):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));client=SQLTransportFixture()
    client.fail_table={'coverage':c.direct.COVERAGE,'member':'arte.'+p.PROVENANCE.name,'commit':'arte.'+p.COMMIT.name}[table]
    with pytest.raises(RuntimeError):p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))
    assert not client.tables.get('arte.'+p.COMMIT.name)
    client.fail_table=None
    assert p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))==commit_row(p.member_rows(plan))


@pytest.mark.parametrize('change',['foreign','duplicate','readback'])
def test_actual_sql_reconciliation_rejects_foreign_duplicate_and_lost_write(archive,change):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));client=SQLTransportFixture()
    transport=c.ArchiveClickHouseTransport(client,root,plan)
    row={'ticker':'TEST','state_hash':'a'*64}
    if change=='foreign':client.tables[c.direct.LEVELS]=[dict(row,state_hash='b'*64)]
    elif change=='duplicate':client.tables[c.direct.LEVELS]=[row,row]
    else:
        original=client.execute
        client.execute=lambda sql:'' if sql.startswith('INSERT') else original(sql)
    with pytest.raises(ValueError):transport._reconcile(c.direct.LEVELS,[row],"ticker='TEST'")


def test_apply_review_mismatch_blocks_before_git_credentials_or_db(archive,monkeypatch):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,))
    path=root/'review.json';path.write_text('{}')
    monkeypatch.setattr(c.subprocess,'check_output',lambda *a,**k:pytest.fail('must fail before git'))
    with pytest.raises(ValueError,match='bytes differ'):
        c.verify_apply_review(plan,review_path=path,review_hash='a'*64,exclusion_path=root/'missing')
    with pytest.raises(ValueError,match='schema differs'):
        c.verify_apply_review(plan,review_path=path,review_hash=c.sha256(path.read_bytes()).hexdigest(),exclusion_path=root/'missing')


def test_operator_install_plan_exact_five_tables_no_runner_ddl_or_wildcards():
    from research.level_book.v7 import canonical_archive_clickhouse as c
    plan=c.installation_plan()
    assert len(plan['grants'])==10 and len({table for _,table in plan['grants']})==5
    assert {permission for permission,_ in plan['grants']}=={'SELECT','INSERT'}
    assert all('*' not in table for _,table in plan['grants'])
    assert all('live_market_ssd' in ddl for ddl in plan['ddl'] if 'CREATE TABLE' in ddl)
    assert not plan['creates_or_executes']


def test_real_nonempty_chronology_transport_preserves_intervals_and_projected_seed(archive):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    from src.backend.canonical_v7_seed import decode_member_seed
    root,request,target,*_=archive;prior=None
    obs=dict(price=10.,resolution=.01,at=session_bounds('2026-08-03')[0].timestamp(),
        resolved_at=session_bounds('2026-08-03')[0].timestamp()+1,role='resistance',session='2026-08-03')
    for day,role,price in [('2026-08-03','resistance',10.),('2026-08-04','support',10.2)]:
        path=target/'books'/f'{day}.json.gz';book=read(path)
        book['levels']=nonempty_book(day,role,price,[obs])['levels']
        if prior:book['prior_checkpoint_hash']=prior
        book['checkpoint_hash']=digest({k:v for k,v in book.items() if k!='checkpoint_hash'})
        write(path,book,immutable=False)
        mutate_file(target/'receipts'/f'{day}.json',lambda v:v.update(checkpoint_hash=book['checkpoint_hash'],parent_hash=prior))
        prior=book['checkpoint_hash']
    earlier=replace(request,target_session='2026-08-04')
    plan=p.prepare_archive_plan(root,(earlier,request));client=SQLTransportFixture()
    p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))
    assert len(client.tables[c.direct.LEVELS])==2
    writes=[sql.split()[2] for sql in client.calls if sql.startswith('INSERT')]
    assert writes[:2]==[c.direct.LEVELS,c.direct.OBSERVATIONS]
    for member in p.member_rows(plan):
        coverage=next(r for r in client.tables[c.direct.COVERAGE] if r['session_date']==member['seed_session'])
        fence=member['available_at']
        active=lambda table:[r for r in client.tables[table] if r['valid_from']<=fence and (r['valid_to'] is None or r['valid_to']>fence)]
        decoded=decode_member_seed(member,coverage=coverage,
            levels=sorted(active(c.direct.LEVELS),key=lambda r:r['level_id']),
            observations=sorted(active(c.direct.OBSERVATIONS),key=lambda r:(r['level_id'],r['observation_id'])))
        assert decoded['checkpoint_hash']==member['seed_content_hash']


@pytest.mark.parametrize('change',['none','missing','misplaced','principal','permission','broad_grants'])
def test_real_storage_and_permission_preflight_selected_five_table_contracts(change):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    from research.level_book.v7.clickhouse_persistence import EXPECTED_COLUMNS
    class MetadataClient:
        def __init__(self):self.calls=[]
        def execute(self,sql):
            self.calls.append(sql)
            if sql=='SHOW GRANTS FINAL':
                if change=='broad_grants':return 'GRANT ALL ON *.* TO '+c.PRINCIPAL
                return '\n'.join(f'GRANT {permission} ON {table} TO {c.PRINCIPAL}' for permission,table in c.installation_plan()['grants'])
            if sql=='SELECT currentUser()':return 'foreign' if change=='principal' else c.PRINCIPAL
            if sql.startswith('CHECK GRANT'):return '0' if change=='permission' else '1'
            if 'system.storage_policies' in sql:rows=[{'disks':['live_market_ssd']}]
            elif 'system.parts' in sql:rows=[{'table':'foreign','disk_name':'default'}] if change=='misplaced' else []
            elif 'system.data_skipping_indices' in sql:rows=[]
            elif 'system.tables' in sql:
                if p.PROVENANCE.name in sql:
                    rows=[dict(name=t.name,engine='MergeTree',storage_policy='live_market_ssd',partition_key=t.partition,sorting_key=t.order) for t in p.TABLES]
                else:
                    rows=[dict(name=t.split('.')[1],engine='ReplacingMergeTree',storage_policy='live_market_ssd',partition_key=partition,sorting_key=order) for t,partition,order in [
                        (c.direct.LEVELS,'cityHash64(ticker) % 64','ticker, level_id, valid_from'),
                        (c.direct.OBSERVATIONS,'cityHash64(ticker) % 64','ticker, observation_id, valid_from'),
                        (c.direct.COVERAGE,'toYYYYMM(session_date)','ticker, session_date')]]
                if change=='missing':rows.pop()
            elif 'system.columns' in sql:
                if p.PROVENANCE.name in sql:rows=[dict(table=t.name,name=name,type=kind) for t in p.TABLES for name,kind in t.columns]
                else:
                    kinds={'valid_from':"DateTime64(9, 'UTC')",'valid_to':"Nullable(DateTime64(9, 'UTC'))",'state_hash':'FixedString(64)',
                        'observation_id':'FixedString(64)','source_plan_hash':'FixedString(64)','reporting_revision':'LowCardinality(String)'}
                    rows=[]
                    for old,columns in EXPECTED_COLUMNS.items():
                        columns=tuple(item for name in columns for item in ((name,'reporting_revision') if name=='input_policy' and 'coverage' in old else (name,)))
                        rows.extend(dict(table=old+'_v2',name=name,type=kinds.get(name,'String')) for name in columns)
            else:raise AssertionError(sql)
            return '\n'.join(json.dumps(row) for row in rows)
    client=MetadataClient()
    if change=='none':c.storage_preflight(client)
    else:
        with pytest.raises((ValueError,RuntimeError)):c.storage_preflight(client)
    assert not any(sql.startswith(('INSERT','CREATE','GRANT','ALTER')) for sql in client.calls)


@pytest.mark.parametrize('change',['none','dirty','unpushed','market','price','population','configuration','bool_identity'])
def test_apply_binder_independently_reloads_configuration_products_and_dated_scope(archive,monkeypatch,change):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    from types import SimpleNamespace
    from src.backend import backtest_strategy_one_configuration as config
    from src.backend import backtest_market_data as market
    from src.backend import backtest_liquidity_price as price
    root,request,*_=archive;exclusions=root/'exclusions.json';exclusions.write_text('[]')
    exhash=c.sha256(exclusions.read_bytes()).hexdigest()
    request=replace(request,exclusion_policy_hash=exhash);plan=p.prepare_archive_plan(root,(request,))
    monkeypatch.setenv('BACKTEST_INPUT_EXCLUSIONS_FILE',str(exclusions));calls=[]
    commit='b'*40
    def git(args,**kwargs):
        cmd=args[3:]
        if cmd==['rev-parse','HEAD']:return commit+'\n'
        if cmd==['status','--porcelain']:return '?? unreviewed.py\n' if change=='dirty' else ''
        if cmd==['symbolic-ref','--short','HEAD']:return 'codex/test\n'
        if cmd[:2]==['ls-remote','origin']:return ('c'*40 if change=='unpushed' else commit)+'\trefs/heads/codex/test\n'
        raise AssertionError(cmd)
    monkeypatch.setattr(c.subprocess,'check_output',git)
    class Reader:
        def close(self):calls.append('closed')
    monkeypatch.setattr(market,'readonly_clickhouse_client',lambda **kw:Reader())
    configured=SimpleNamespace(payload_hash='d'*64,payload={'complete':'controlled typed release fixture'},revision=lambda:{'revision_id':'own-fixture-revision'})
    monkeypatch.setattr(config,'certify_numbered_configuration',lambda client,number:(calls.append(('configuration',number)) or configured))
    original=SimpleNamespace(token='c'*64 if change=='market' else 'a'*64,tickers=('OTHER',) if change=='population' else ('TEST',))
    monkeypatch.setattr(market,'certified_market_plan_from_arte',lambda **kw:(calls.append(('market',kw['sessions'])) or original))
    monkeypatch.setattr(market,'verify_market_day_plan',lambda selected,reader:calls.append('verified_original'))
    priced=SimpleNamespace(token='c'*64 if change=='price' else 'a'*64)
    priced.projected=lambda selected:priced
    monkeypatch.setattr(price,'certify_price_level_plan',lambda selected,reader:(calls.append('certified_original_price') or priced))
    review=dict(schema=c.REVIEW_VERSION,commit=commit,plan_hash=plan.token,sources=c.execution_sources(),
        configuration_number=True if change=='bool_identity' else 7001,
        configuration_revision_id='foreign' if change=='configuration' else 'own-fixture-revision',
        configuration_payload_hash='d'*64,exclusion_hash=exhash)
    path=root/'review.json';path.write_text(json.dumps(review));hash_=c.sha256(path.read_bytes()).hexdigest()
    if change=='none':
        assert c.verify_apply_review(plan,review_path=path,review_hash=hash_,exclusion_path=exclusions)==review
        assert 'verified_original' in calls and 'certified_original_price' in calls and calls[-1]=='closed'
    else:
        with pytest.raises(ValueError):c.verify_apply_review(plan,review_path=path,review_hash=hash_,exclusion_path=exclusions)


def test_compaction_rejects_archive_change_after_preverification(archive):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,target,*_=archive;plan=p.prepare_archive_plan(root,(request,))
    mutate_file(target/'receipts/2026-08-03.json',lambda row:row.update(extra='foreign'))
    with pytest.raises(ValueError,match='chronology bytes'):c.ticker_content(root,plan.members)


def test_final_inventory_commit_rejects_extra_stored_member_even_on_retry(archive):
    from research.level_book.v7 import canonical_archive_clickhouse as c
    root,request,*_=archive;plan=p.prepare_archive_plan(root,(request,));client=SQLTransportFixture()
    transport=c.ArchiveClickHouseTransport(client,root,plan)
    p.publish_archive_plan(root,plan,transport)
    client.tables['arte.'+p.PROVENANCE.name].append(dict(p.member_rows(plan)[0],ticker='OTHER'))
    with pytest.raises(ValueError,match='inventory membership'):
        p.publish_archive_plan(root,plan,c.ArchiveClickHouseTransport(client,root,plan))
