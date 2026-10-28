import os
import pytest


def test_transport_retry_requires_confirmed_query_cancellation():
    from scripts.backfill_quote_bars import retryable_transport
    assert retryable_transport(ConnectionResetError('reset'))
    uncertain=ConnectionResetError('reset')
    uncertain.add_note('Cancellation could not be confirmed; reconcile the owned query before resuming writes.')
    assert not retryable_transport(uncertain)
    assert not retryable_transport(ValueError('source hash differs'))


def test_quote_progress_coalesces_display_without_losing_terminal_state(tmp_path,monkeypatch):
    import json
    from scripts.backfill_quote_bars import QuoteProgress
    from pipelines.market_sip.flatfiles import repair_progress
    clock=[100.]
    monkeypatch.setattr(repair_progress,'monotonic',lambda:clock[0])
    p=QuoteProgress(tmp_path);p.update(status='running',total=100)
    for i in range(20):
        p.worker('worker','stage-'+str(i));p.update(completed=i+1)
    assert json.loads((tmp_path/'status.json').read_text())['completed']==0
    clock[0]=101.1;p.flush(force=True)
    assert json.loads((tmp_path/'status.json').read_text())['completed']==20
    p.update(status='complete')
    assert json.loads((tmp_path/'status.json').read_text())['status']=='complete'


def test_quote_terminal_separates_new_reuse_and_waiting_work():
    import io
    from datetime import datetime,timezone
    from rich.console import Console
    from pipelines.market_sip.events.quote_terminal import snapshot
    output=io.StringIO()
    Console(file=output,width=80,height=24).print(snapshot(dict(status='running',total=100,
        completed=10,reused=5,failed=0,workers={'QUOTE_1':dict(stage='Estimated bands / halt transitions',
        day='2026-01-02',detail='AAPL')},elapsed=10,new_ticker_days_per_hour=3600,eta_seconds=85,
        updated_at=datetime.now(timezone.utc).isoformat()),80,24))
    text=output.getvalue()
    assert all(s in text for s in ('New 10','Reused 5','Active 1','Queued 84','New rate'))


def test_exact_source_fingerprint_hidden_extremes_and_all_funnel_frames():
    if os.environ.get("QUOTE_LIVE_TEST")!="1":pytest.skip("Explicit SSD integration opt-in")
    import json,uuid
    from datetime import date,datetime,timezone
    from pipelines.market_sip.events import quote_bars as q
    from scripts.run_fundamentals import Client
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False);c=Client('http://192.168.1.218:18123');db='quote_validation_'+uuid.uuid4().hex[:10];day=date(2026,1,2);attempt=str(uuid.uuid4())
    try:
     c.query('CREATE DATABASE '+db,read=False)
     c.query(f"CREATE TABLE {db}.events AS market_sip_compact.events_2026 ENGINE=MergeTree ORDER BY (ticker,sip_timestamp_us,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
     base=1767366000000000
     rows=[]
     for i,(offset,bid,ask) in enumerate([(0,100,102),(10_000,110,112),(90_000,90,92),(999_999,101,103),(1_000_000,102,104),(5_000_000,103,105)]):
      rows.append(dict(ticker='TEST',ordinal=i+1,event_meta=6,sip_timestamp_us=base+offset,price_primary_int=ask*10000,price_secondary_int=bid*10000,size_primary=10,size_secondary=20,exchange_primary=1,exchange_secondary=1,condition_token_1=1,condition_token_2=0,condition_token_3=0,condition_token_4=0,condition_token_5=0,event_date='2026-01-02'))
     c.query('INSERT INTO '+db+'.events FORMAT JSONEachRow\n'+'\n'.join(json.dumps(r) for r in rows),read=False)
     for ddl in q.ddl(db):c.query(ddl,read=False)
     c.query(q.stage_sql(db,'stage',day,'TEST',source='SELECT * FROM '+db+'.events'),read=False)
     from pipelines.market_sip.events import quote_execution as execution
     copies=[dict(r,ticker='TEST2',ordinal=r['ordinal']+1000) for r in rows]
     c.query('INSERT INTO '+db+'.events FORMAT JSONEachRow\n'+'\n'.join(json.dumps(r) for r in copies),read=False)
     c.query(execution.stage_sql(db,'batch_stage',day,['TEST','TEST2'],source='SELECT * FROM '+db+'.events'),read=False)
     for ticker in ['TEST','TEST2']:
      c.query(q.stage_sql(db,'single_'+ticker,day,ticker,source='SELECT * FROM '+db+'.events WHERE ticker='+q.L(ticker)),read=False)
      assert c.query(q.stage_evidence(db,'batch_stage')+' WHERE ticker='+q.L(ticker))==c.query(q.stage_evidence(db,'single_'+ticker))
     class BatchAdapter:
      def query(self,query,label='query',read=True):return c.query(query,read=read)
     attempts={'TEST':str(uuid.uuid4()),'TEST2':str(uuid.uuid4())}
     execution.insert_batch(BatchAdapter(),db,'batch_stage','batch',day,attempts)
     for t,a in attempts.items():
      for parent,target in zip(q.FRAMES,q.FRAMES[1:]):
       assert c.query(q.parity(db,'batch',day,t,a,parent,target))==[dict(n=0)]
      assert c.query(q.evidence(db,'batch',day,t,a))[0]['bad']==0
     cols=','.join(n for n in q.FIELDS if n not in ('build_id','ticker','attempt_id'))
     assert c.query(f"SELECT {cols} FROM {db}.quote_bars_v1 WHERE build_id='batch' AND ticker='TEST' ORDER BY resolution_ms,side,bucket_index")==c.query(f"SELECT {cols} FROM {db}.quote_bars_v1 WHERE build_id='batch' AND ticker='TEST2' ORDER BY resolution_ms,side,bucket_index")
     c.query('ALTER TABLE '+db+".events DELETE WHERE ticker='TEST2' SETTINGS mutations_sync=2",read=False)
     raw=c.query(f'SELECT count() n,uniqExact(ordinal) unique_ordinals,min(ordinal) first_ordinal,max(ordinal) last_ordinal,min(sip_timestamp_us) first_us,max(sip_timestamp_us) last_us,count() session_events,toUInt64(0) reporting_delayed_trades,sum(cityHash64(tuple(*))) hash FROM {db}.events')[0]
     assert c.query(q.stage_evidence(db,'stage'))==[raw]
     c.query(q.base_insert(db,'stage','b',day,'TEST',attempt),read=False)
     for p,t in zip(q.FRAMES,q.FRAMES[1:]):
      c.query('INSERT INTO '+db+'.quote_bars_v1 '+q.rollup_select(db,'b',day,'TEST',attempt,p,t),read=False)
      assert c.query(q.parity(db,'b',day,'TEST',attempt,p,t))==[dict(n=0)]
     first=c.query(f"SELECT side,open_int,high_int,low_int,close_int,event_count FROM {db}.quote_bars_v1 WHERE build_id='b' AND resolution_ms=1000 ORDER BY bucket_index,side");assert first[0]==dict(side='bid',open_int=1000000,high_int=1100000,low_int=900000,close_int=1010000,event_count=4)
     from pipelines.market_sip.events import market_day_sql as market
     for ddl in market.ddl(db,include_liquidity_1s=True):c.query(ddl,read=False)
     class Adapter:
      def query(self,sql,label='query',read=True):return c.query(sql,read=read)
     original=market.canonical_source
     market.canonical_source=lambda *_:'SELECT * FROM '+db+'.events'
     try:
      q.forward_broker(Adapter(),db,'forward',day,'TEST',str(uuid.uuid4()),[],'source')
      shared=c.query(f"SELECT side,open_int,high_int,low_int,close_int,event_count FROM {db}.quote_bars_current_v1 WHERE resolution_ms=1000 ORDER BY bucket_index,side")
      assert shared==first
     finally:market.canonical_source=original
     from pipelines.market_sip.events import market_signals as signals
     for ddl in signals.ddl(db):c.query(ddl,read=False)
     c.query(f"INSERT INTO {db}.bars_v1(build_id,session_date,ticker,attempt_id,resolution_ms,bucket_index,open_int,high_int,low_int,close_int,trade_count,price_valid,extremes_valid) VALUES "+','.join(f"('parent','{day}','TEST','{attempt}',1000,{34200+offset},{price},{price},{price},{price},1,1,1)" for offset,price in [(0,1000000),(1,1010000),(302,1200000),(23400,2000000)]),read=False)
     op=int(c.query('SELECT '+q.bounds(day,'09:30:00')+' n')[0]['n']);cl=int(c.query('SELECT '+q.bounds(day,'16:00:00')+' n')[0]['n'])
     c.query(signals.bands_sql(db,'signal','parent',day,'TEST',attempt,attempt,op,cl),read=False)
     band=c.query(f"SELECT reference_price,lower,upper FROM {db}.estimated_luld_1s_v1 ORDER BY bucket_index")
     assert band==[dict(reference_price=100.,lower=90.,upper=110.),dict(reference_price=100.5,lower=90.45,upper=110.55),dict(reference_price=120.,lower=108.,upper=132.)]
     tokens=[dict(source_family='cta_security_status',modifier_int=102,token_id=200,condition='Trading Halt'),dict(source_family='cta_security_status',modifier_int=103,token_id=201,condition='Resume')]
     extra=[]
     for i,token in enumerate([200,200,201]):
      r=dict(rows[0],ordinal=100+i,sip_timestamp_us=base+60_000_000+i*1_000_000,condition_token_1=token);extra.append(r)
     c.query('INSERT INTO '+db+'.events FORMAT JSONEachRow\n'+'\n'.join(json.dumps(r) for r in extra),read=False)
     c.query(signals.halt_sql(db,'signal',day,'TEST',attempt,tokens,source='SELECT * FROM '+db+'.events'),read=False)
     observed=c.query(f"SELECT halted,status_valid,halt_started_count,resume_count FROM {db}.market_halt_transition_v1 ORDER BY available_at_us,ordinal")
     assert observed==[dict(halted=0,status_valid=0,halt_started_count=0,resume_count=0),dict(halted=1,status_valid=1,halt_started_count=0,resume_count=0),dict(halted=0,status_valid=1,halt_started_count=0,resume_count=1)]
     original_source=signals.source_sql
     signals.source_sql=lambda *_:'SELECT * FROM '+db+'.events'
     try:
      result=signals.publish(Adapter(),db,'parent',day,'TEST',attempt,'source',tokens,op,cl)
      assert result==dict(state='published',band_rows=3,halt_rows=3)
      assert signals.publish(Adapter(),db,'parent',day,'TEST',attempt,'source',tokens,op,cl)==dict(state='reused',band_rows=3,halt_rows=3)
      sid=c.query(f"SELECT build_id FROM {db}.market_signals_coverage_v1 FINAL")[0]['build_id']
      projected=c.query(signals.halt_projection_sql(db,sid,day,'TEST',base+60_000_000,base+64_000_000))
      assert len(projected)==4 and all(r['status_valid']==1 for r in projected)
     finally:signals.source_sql=original_source
     assert c.query(q.evidence(db,'b',day,'TEST',attempt))[0]['n']==14;assert c.query(q.evidence(db,'b',day,'TEST',attempt))[0]['bad']==0;q.verify_storage(c.query,db,['events','stage','quote_bars_v1','quote_bars_coverage_v1'])
    finally:c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()


def test_quote_scan_batches_bound_units_and_events():
    from pipelines.market_sip.events.quote_execution import batches
    rows=[dict(ticker=str(i),event_count=n) for i,n in enumerate([10]*17+[3_000_000,20])]
    groups=list(batches(rows))
    assert [r for group in groups for r in group]==rows
    assert all(len(g)<=8 for g in groups)
    assert all(sum(r['event_count'] for r in g)<=2_000_000 or len(g)==1 for g in groups)


def test_batch_sql_preserves_raw_hash_before_grouping_ticker():
    from pipelines.market_sip.events.quote_execution import stage_sql
    q=stage_sql('arte','test','2026-05-13',['AAPL','MSFT'])
    assert "PREWHERE ticker IN ('AAPL','MSFT')" in q
    assert 'cityHash64(tuple(*)) raw_hash' in q
    assert 'GROUP BY ticker,bucket_index' in q
    assert 'ORDER BY (ticker,bucket_index)' in q


def test_cleanup_reset_reconnects_only_owned_idempotent_drop(monkeypatch):
    from scripts import backfill_quote_bars as runner
    calls=[];closed=[];retries=[]
    class Client:
        def __init__(self,fail=False):self.fail=fail
        def query(self,query,label,read):
            calls.append((query,label,read))
            if self.fail:raise ConnectionResetError('reset')
        def close(self):closed.append(True)
    monkeypatch.setattr(runner.bars,'Client',lambda *a,**kw:Client())
    monkeypatch.setattr(runner.time,'sleep',lambda _:None)
    runner.cleanup_stage(Client(True),object(),'quote_stage_owned',retries.append)
    assert retries==[1] and closed==[True]
    assert len(calls)==2 and calls[0][0]==calls[1][0]=='DROP TABLE IF EXISTS arte.quote_stage_owned SYNC'
    with pytest.raises(ValueError,match='Owned quote stage'):runner.cleanup_stage(Client(),object(),'bars_v1')


def test_cleanup_does_not_retry_uncertain_cancellation_or_server_error(monkeypatch):
    from scripts import backfill_quote_bars as runner
    class Client:
        def __init__(self,error):self.error=error
        def query(self,*a):raise self.error
    def forbidden(*a,**kw):raise AssertionError('Must not open retry connection')
    monkeypatch.setattr(runner.bars,'Client',forbidden)
    error=ConnectionResetError('reset');error.add_note('Cancellation could not be confirmed')
    with pytest.raises(ConnectionResetError):runner.cleanup_stage(Client(error),object(),'quote_stage_owned')
    with pytest.raises(RuntimeError):runner.cleanup_stage(Client(RuntimeError('server error')),object(),'quote_stage_owned')


def test_live_cleanup_reconciles_drop_applied_before_connection_reset():
    if os.environ.get('QUOTE_LIVE_TEST')!='1':pytest.skip('Explicit SSD integration opt-in')
    import uuid
    from pathlib import Path
    from types import SimpleNamespace
    from scripts import backfill_quote_bars as runner
    from scripts.run_fundamentals import Client
    from research.mlops.env import load_env_files
    from research.mlops.clickhouse import discover_clickhouse_env_files
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    os.environ['QMD_CLICKHOUSE_URL']='http://192.168.1.218:18123'
    c=Client(os.environ['QMD_CLICKHOUSE_URL']);stage='quote_stage_recovery_'+uuid.uuid4().hex
    options=SimpleNamespace(env_file=Path('D:/TradingML/secrets/.env'),query_timeout=30,max_threads=1,max_memory_gb=2)
    class LostDropResponse:
        def query(self,query,label,read):
            c.query(query,read=read)
            raise ConnectionResetError('Injected response loss after committed DROP')
    try:
        c.query(f"CREATE TABLE arte.{stage} (n UInt8) ENGINE=MergeTree ORDER BY n SETTINGS storage_policy='live_market_ssd'",read=False)
        runner.sql.verify_storage(c.query,'arte',[stage])
        c.query(f'INSERT INTO arte.{stage} VALUES (1)',read=False)
        runner.sql.verify_storage(c.query,'arte',[stage])
        runner.cleanup_stage(LostDropResponse(),options,stage)
        assert not c.query(f"SELECT name FROM system.tables WHERE database='arte' AND name='{stage}'")
    finally:
        c.query(f'DROP TABLE IF EXISTS arte.{stage} SYNC',read=False);c.close()
