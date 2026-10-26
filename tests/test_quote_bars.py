import os
import pytest


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
     raw=c.query(f'SELECT count() n,uniqExact(ordinal) unique_ordinals,min(ordinal) first_ordinal,max(ordinal) last_ordinal,min(sip_timestamp_us) first_us,max(sip_timestamp_us) last_us,count() session_events,toUInt64(0) reporting_delayed_trades,sum(cityHash64(tuple(*))) hash FROM {db}.events')[0]
     assert c.query(q.stage_evidence(db,'stage'))==[raw]
     c.query(q.base_insert(db,'stage','b',day,'TEST',attempt),read=False)
     for p,t in zip(q.FRAMES,q.FRAMES[1:]):
      c.query('INSERT INTO '+db+'.quote_bars_v1 '+q.rollup_select(db,'b',day,'TEST',attempt,p,t),read=False)
      assert c.query(q.parity(db,'b',day,'TEST',attempt,p,t))==[dict(n=0)]
     first=c.query(f"SELECT side,open_int,high_int,low_int,close_int,event_count FROM {db}.quote_bars_v1 WHERE resolution_ms=1000 ORDER BY bucket_index,side");assert first[0]==dict(side='bid',open_int=1000000,high_int=1100000,low_int=900000,close_int=1010000,event_count=4)
     assert c.query(q.evidence(db,'b',day,'TEST',attempt))[0]['n']==14;assert c.query(q.evidence(db,'b',day,'TEST',attempt))[0]['bad']==0;q.verify_storage(c.query,db,['events','stage','quote_bars_v1','quote_bars_coverage_v1'])
    finally:c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()
