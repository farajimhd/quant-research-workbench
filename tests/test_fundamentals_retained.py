"""Retained-source runnable path. Live tests require explicit opt-in and SSD."""
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from pipelines.fundamentals import schema
from pipelines.fundamentals.adapters import ADAPTERS, projection, recipe_hash
from pipelines.fundamentals.producer import Batch, publish, plan
from pipelines.fundamentals.queries import observations_sql
from scripts.run_fundamentals import Client, render


def test_projection_is_bounded_and_preserves_null_clocks():
    sql=projection('short_interest',['S'],date(2026,1,1),date(2026,2,1),partition_id='202601')
    assert "_partition_id='202601'" in sql and 'published_at_utc' in sql
    assert 'Nullable(DateTime64' in sql and 'FINAL PREWHERE' in sql
    with pytest.raises(ValueError):projection('borrow',[],date.today(),date.today())
    with pytest.raises(ValueError):projection('borrow',['S']*257,date.today(),date.today())


def test_asof_refuses_naive_time_and_preserves_contexts():
    with pytest.raises(ValueError):observations_sql('arte','security',['S'],['M'],datetime.now())
    sql=observations_sql('arte','security',['S'],['M'],datetime.now(timezone.utc),clock='public')
    assert "quality='certified'" in sql and 'observed_at<=' not in sql
    assert 'context_id' in sql and 'rank()' in sql and 'conflict' in sql
    known=observations_sql('arte','security',['S'],['M'],datetime.now(timezone.utc),
        clock='public',known_at=datetime.now(timezone.utc))
    assert 'observed_at<=' in known


def test_progress_is_truthful_and_plain():
    s={'state':'stopped','mode':'forward-fill','counts':dict(published=2,unchanged=30,audited=0,failed=0),
       'active':{},'queued':4,'elapsed':5}
    text=render(s)
    assert '30 unchanged' in text and 'queued 4' in text and 'retries 0' in text
    assert '\x1b' not in text and max(map(len,text.splitlines()))<100
    assert max(map(len,render(s,40).splitlines()))<=40


def test_recipe_is_family_scoped_and_not_index_tuning(monkeypatch):
    original=recipe_hash('borrow')
    monkeypatch.setitem(ADAPTERS,'borrow',replace(ADAPTERS['borrow'],scan_key='security_id'))
    monkeypatch.setitem(ADAPTERS,'ipo',replace(ADAPTERS['ipo'],reason="'new_qualification'"))
    assert recipe_hash('borrow')==original
    monkeypatch.setitem(ADAPTERS,'borrow',replace(ADAPTERS['borrow'],reason="'changed_semantics'"))
    assert recipe_hash('borrow')!=original


@pytest.fixture
def live(monkeypatch):
    if os.environ.get('FUNDAMENTALS_LIVE_TEST')!='1':pytest.skip('Explicit live SSD integration opt-in required')
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    c=Client('http://192.168.1.218:18123');q=c.query
    db='fundamentals_validation_'+uuid4().hex[:12]
    q('CREATE DATABASE '+db,read=False)
    try:
        schema.install(q,db)
        q(f'''CREATE TABLE {db}.borrow (borrow_id String,security_id String,source_system String,broker String,
            observed_at_utc DateTime64(9,'UTC'),shortable_shares Nullable(UInt64),lender_count Nullable(UInt32),
            indicative_borrow_rate Nullable(Float64),fee_rate Nullable(Float64),borrow_status String,
            source_evidence_ref String,source_content_sha256 String,period_end Date)
            ENGINE=ReplacingMergeTree(observed_at_utc) ORDER BY (security_id,borrow_id)
            PARTITION BY toYYYYMM(observed_at_utc) SETTINGS storage_policy='live_market_ssd' ''',read=False)
        monkeypatch.setitem(ADAPTERS,'borrow',replace(ADAPTERS['borrow'],table=db+'.borrow',end='period_end',scan_key='security_id'))
        def insert(key,stamp,value=1000,period='2026-06-30'):
            q(f"INSERT INTO {db}.borrow VALUES ('{key}','SEC1','ibkr','ibkr','{stamp}',{value},2,1.5,NULL,'available','proof','hash','{period}')",read=False)
        batch=Batch('borrow','202610',('SEC1',),date(2026,1,1),date(2026,12,31),'a'*64)
        yield q,db,batch,insert
        schema.verify(q,db)
    finally:
        q('DROP DATABASE '+db+' SYNC',read=False);c.close()


def test_live_publish_reuse_and_audit(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    first=publish(q,b,db);assert first['rows']==5
    calls=[]
    def record(sql,**kw):calls.append(sql);return q(sql,**kw)
    assert publish(record,b,db)['state']=='unchanged'
    assert not any('INSERT' in sql or 'FROM '+db+'.borrow' in sql for sql in calls)
    assert publish(q,b,db,audit=True)['state']=='audited'
    assert q(f'SELECT count() n FROM {db}.reference_fact_v1 FINAL')[0]['n']==5


def test_live_interrupted_write_is_not_published_and_recovers(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    def interrupted(sql,**kw):
        result=q(sql,**kw)
        if sql.startswith(f'INSERT INTO {db}.reference_fact_v1'):
            raise ConnectionResetError('Simulated lost acknowledgement after committed write')
        return result
    with pytest.raises(ConnectionResetError):publish(interrupted,b,db)
    assert q(f'SELECT count() n FROM {db}.reference_source_v1')[0]['n']==0
    assert q(f'SELECT count() n FROM {db}.reference_batch_receipt_v1')[0]['n']==0
    assert publish(q,b,db)['rows']==5
    assert q(f'SELECT count() n FROM {db}.reference_fact_v1 FINAL')[0]['n']==5


def test_live_correction_and_context_seed(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    publish(q,b,db)
    insert('v1','2026-10-02 14:00:00',1200)
    assert publish(q,b,db)['rows']==5
    def lookup(day,**kwargs):
        return q(observations_sql(db,'security',['SEC1'],['borrow.shortable_shares'],
            datetime(2026,10,day,tzinfo=timezone.utc),**kwargs))
    assert lookup(2)[0]['value']==1000
    assert lookup(3)[0]['value']==1200
    assert lookup(3,clock='public')==[]
    history=lookup(3,since=datetime(2026,10,2,tzinfo=timezone.utc))
    assert sorted(r['value'] for r in history)==[1000,1200]
    insert('v2','2026-10-02 15:00:00',2000,'2026-09-30');publish(q,b,db)
    assert len(lookup(3))==2  # distinct periods, not last-amendment-wins


def test_live_same_time_conflict_is_exposed(live):
    q,db,b,insert=live
    insert('v1','2026-10-01 14:00:00',1000);insert('v2','2026-10-01 14:00:00',2000)
    publish(q,b,db)
    rows=q(observations_sql(db,'security',['SEC1'],['borrow.shortable_shares'],datetime(2026,10,2,tzinfo=timezone.utc)))
    assert len(rows)==2 and all(r['conflict'] for r in rows)


def test_live_audit_rejects_missing_fact(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00');publish(q,b,db)
    q(f"ALTER TABLE {db}.reference_fact_v1 DELETE WHERE metric='borrow.shortable_shares' SETTINGS mutations_sync=2",read=False)
    with pytest.raises(ValueError,match='readback differs'):publish(q,b,db,audit=True)


def test_live_schema_refuses_wrong_columns(live):
    q,db,_,_=live
    q(f'ALTER TABLE {db}.reference_fact_v1 ADD COLUMN unexpected String',read=False)
    with pytest.raises(ValueError,match='layout differs'):schema.verify(q,db)
    q(f'ALTER TABLE {db}.reference_fact_v1 DROP COLUMN unexpected',read=False)


def test_live_plan_caches_occupied_keys_and_invalidates_on_insert(live,tmp_path):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    calls=[]
    def record(sql,**kw):calls.append(sql);return q(sql,**kw)
    population={'borrow':['SEC1','ABSENT']}
    first=plan(record,population,b.start,b.end,cache_dir=tmp_path)
    assert len(first)==1 and first[0].entities==('SEC1',) and first[0].scan_keys==('SEC1',)
    calls.clear()
    assert plan(record,population,b.start,b.end,cache_dir=tmp_path)==first
    assert not any('groupUniqArray' in sql for sql in calls)
    insert('v2','2026-10-02 14:00:00');calls.clear()
    plan(record,population,b.start,b.end,cache_dir=tmp_path)
    assert any('groupUniqArray' in sql for sql in calls)


def test_live_preserves_source_nanoseconds(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00.123456789');publish(q,b,db)
    row=q(f'SELECT observed_at FROM {db}.reference_source_v1 FINAL')[0]
    assert row['observed_at'].endswith('.123456789')


def test_live_changed_source_blocks_before_headers(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    def changed(sql,**kw):
        result=q(sql,**kw)
        if sql.startswith(f'INSERT INTO {db}.reference_stage_v1'):
            insert('v2','2026-10-02 14:00:00')
        return result
    with pytest.raises(ValueError,match='Source changed'):publish(changed,b,db)
    assert q(f'SELECT count() n FROM {db}.reference_source_v1')[0]['n']==0
    assert publish(q,b,db)['source_rows']==2


def test_live_public_and_recorded_clocks_are_independent(live,monkeypatch):
    q,db,b,insert=live
    # An isolated qualified fixture exercises the consumer contract; production
    # adapters never grant certification without the existing evidence producer.
    monkeypatch.setitem(ADAPTERS,'borrow',replace(ADAPTERS['borrow'],
        public="toDateTime64('2026-10-01 12:00:00',9,'UTC')",quality="'certified'"))
    insert('v1','2026-10-02 14:00:00');publish(q,b,db)
    cutoff=datetime(2026,10,2,tzinfo=timezone.utc)
    base=(db,'security',['SEC1'],['borrow.shortable_shares'],cutoff)
    assert len(q(observations_sql(*base,clock='public')))==1
    assert q(observations_sql(*base,clock='recorded'))==[]
    assert q(observations_sql(*base,clock='public',known_at=cutoff))==[]


def test_live_receipt_acknowledged_before_cleanup_recovers(live):
    q,db,b,insert=live;insert('v1','2026-10-01 14:00:00')
    drops=[0]
    def interrupted(sql,**kw):
        if sql.startswith(f'ALTER TABLE {db}.reference_stage_v1 DROP PARTITION'):
            drops[0]+=1
            if drops[0]==2:raise ConnectionResetError('Interrupted after durable receipt')
        return q(sql,**kw)
    with pytest.raises(ConnectionResetError):publish(interrupted,b,db)
    assert publish(q,b,db)['state']=='unchanged'
    assert q(f'SELECT count() n FROM {db}.reference_stage_v1')[0]['n']==0

def test_population_multilisting_is_not_multiissuer():
    from pipelines.fundamentals.population import snapshot_population
    population=[dict(symbol_id=s,security_id=s,issuer_id='i',ticker=s) for s in ('A','B','C')]
    bridge=[dict(symbol_id=s,cik=c,mapping_status='active',ambiguity_status=a) for s,c,a in
        [('A','0000000001','ambiguous_multi_listing'),('B','0000000002','unique'),
         ('B','0000000003','ambiguous_multi_listing')]]
    def query(sql):
        if 'id_issuer_identifier_v1' in sql:return [dict(issuer_id='i',cik='0000000004')]
        return bridge if 'SELECT symbol_id,cik' in sql else population
    result=snapshot_population(query,date(2026,10,5))
    assert result['families']['sec_bulk']==['0000000001','0000000004']
    assert result['provenance']['sec_mapped_symbols']==2
    assert result['provenance']['sec_unresolved_symbols']==1
