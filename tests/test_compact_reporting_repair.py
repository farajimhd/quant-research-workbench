from datetime import date,datetime,timezone
import os
from uuid import uuid4
import pytest
from pipelines.market_sip.flatfiles import compact_reporting_repair as repair


def test_partition_plan_includes_utc_spill_without_reordering_source_days():
    day=date(2026,3,31)
    first=int(datetime(2026,3,31,8,tzinfo=timezone.utc).timestamp()*1e6)
    last=int(datetime(2026,4,1,1,tzinfo=timezone.utc).timestamp()*1e6)
    inv={'rows':{day:dict(first_sip_timestamp_us=first,last_sip_timestamp_us=last)}}
    assert repair.partition_plan(inv,[day])=={(2026,202603):[day],(2026,202604):[day]}


def test_mutation_expression_has_two_exception_sets_and_no_clock_calculation():
    sql=repair.desired_sql([date(2026,3,i) for i in range(2,24)])
    assert sql.count(' IN (SELECT')==2
    assert 'participant' not in sql and 'splitByChar' not in sql
    assert sql.endswith(',0,64))')
    assert all('participant' not in sql for sql in repair.tables())


def test_journal_replaces_atomically_and_retains_resume_identity(tmp_path):
    path=tmp_path/'partition.json'
    repair.write_journal(path,{'marker':'one','submitted':False})
    repair.write_journal(path,{'marker':'one','submitted':True})
    import json
    assert json.loads(path.read_text())=={'marker':'one','submitted':True}
    assert not path.with_suffix('.tmp').exists()


def test_worker_failure_releases_waiters_and_stops_admission():
    from threading import Event
    from contextlib import nullcontext
    class Client:
        query=None
        def close(self):pass
    class Progress:
        from threading import RLock
        lock=RLock()
        def update(self,**kw):pass
    def fail(*args):raise ValueError('unresolved exception')
    stop=Event();day=date(2026,3,2)
    workers=repair.Workers([day],fail,Client,stop,Progress(),1)
    workers.thread.start();workers.thread.join(5)
    assert not workers.thread.is_alive() and stop.is_set()
    with pytest.raises(ValueError,match='unresolved'):workers.await_day(day)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded SSD integration opt-in')
def test_real_classification_duplicate_matching_and_flag_only_update():
    from scripts.build_market_day import Client,parse_args
    c=Client(parse_args(['--date','2026-03-02']),persistent=True)
    db='certified_research_test_compact_'+uuid4().hex[:12]
    day=date(2026,3,2);stamp=1772456400000000
    key_types='ticker String,sip_timestamp_us UInt64,price_primary_int UInt32,size_primary Float32,exchange_primary UInt8,'+','.join(f'condition_token_{i} UInt8' for i in range(1,6))
    def raw_row(ordinal,flags,price):
        return f"SELECT 'TEST' ticker,toUInt64({stamp}) sip_timestamp_us,toUInt32({price}) price_primary_int,toFloat32(1) size_primary,toUInt8(11) exchange_primary,"+','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))+f',toUInt8({flags|1}) event_meta'
    # Last pair deliberately shares identity but disagrees in classification.
    raw=' UNION ALL '.join(raw_row(i,f,p) for i,f,p in [(1,64,100),(2,192,200),(3,0,300),(4,64,400),(5,192,500),(6,192,500),(7,192,700),(8,64,700),(9,192,900),(10,0,900),(11,64,900)])
    try:
        c.query(f'CREATE DATABASE {db}',read=False)
        for ddl in repair.tables(db):c.query(ddl,read=False)
        c.query(f"CREATE TABLE {db}.events ({key_types},ordinal UInt64,event_meta UInt8,event_date Date) ENGINE=MergeTree PARTITION BY toYYYYMM(event_date) ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        values=[]
        for i,meta,price in [(1,1,100),(2,1,200),(3,65,300),(4,193,400),(5,1,500),(6,1,500),(7,1,700),(8,1,700),(9,1,900),(10,1,900),(11,1,900)]:
            values.append(f"('TEST',{stamp},{price},1,11,0,0,0,0,0,{i},{meta},'2026-03-02')")
        c.query(f'INSERT INTO {db}.events VALUES '+','.join(values),read=False)
        c.query(repair.classify_sql(raw,day,db),read=False)
        counts=c.query(f'SELECT sum(source_rows) n,sumIf(source_rows,expected_bits=64) timely FROM {db}.{repair.CANDIDATES}')[0]
        assert int(counts['n'])==11 and int(counts['timely'])==4
        c.query(repair.match_sql(db+'.events',day,[('TEST',1,12)],db),read=False)
        audit=c.query(f'SELECT countIf(matched_rows!=source_rows) ambiguous FROM {db}.{repair.EXCEPTIONS}')[0]
        assert int(audit['ambiguous'])==3
        selected=c.query(f'SELECT ordinal FROM {db}.{repair.EXCEPTIONS} WHERE ordinal>=7 ORDER BY ordinal')
        assert [int(r['ordinal']) for r in selected]==[7,9,10]
        desired=repair.desired_sql([day],db)
        before=c.query(f'SELECT ordinal,event_meta,price_primary_int,size_primary,sip_timestamp_us FROM {db}.events ORDER BY ordinal')
        sql=f'ALTER TABLE {db}.events UPDATE event_meta=toUInt8(bitOr(bitAnd(event_meta,63),{desired})) IN PARTITION 202603 WHERE bitAnd(event_meta,192)!={desired} SETTINGS mutations_sync=2'
        c.query(sql,read=False)
        after=c.query(f'SELECT ordinal,event_meta,price_primary_int,size_primary,sip_timestamp_us FROM {db}.events ORDER BY ordinal')
        assert [int(r['event_meta']) for r in after]==[65,193,1,65,193,193,193,65,193,1,65]
        for a,b in zip(before,after):
            assert int(a['event_meta'])&63==int(b['event_meta'])&63
            assert {k:v for k,v in a.items() if k!='event_meta'}=={k:v for k,v in b.items() if k!='event_meta'}
        assert int(c.query(f'SELECT countIf(bitAnd(event_meta,192)!={desired}) bad FROM {db}.events WHERE ordinal<7')[0]['bad'])==0
        assert c.query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='{db}'")==[{'disk_name':'live_market_ssd'}]
    finally:
        c.query(f'DROP DATABASE IF EXISTS {db} SYNC',read=False);c.close()
