from datetime import date
import json
import os
from uuid import uuid4
import pytest
from pipelines.market_sip.events import market_day_sql as sql
from scripts import build_market_day as builder,run_market_data_pipeline as launcher


def test_launcher_freezes_scope_and_resumes_existing_worker_builder(tmp_path,monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        kwargs['progress_sink'](total=2,completed=2,reused=0,workers={})
        return 0
    monkeypatch.setattr(builder,'run',run)
    argv=['backfill','--step','bars','--start-date','2026-01-02','--end-date','2026-01-05',
          '--run-dir',str(tmp_path),'--bar-workers','12','--global-memory-gib','40']
    args=launcher.arguments(argv)
    assert launcher.run_bars(args)==0
    assert calls[0].workers==12 and calls[0].products=='bars'
    with pytest.raises(ValueError,match='requires --resume'):launcher.run_bars(args)
    assert launcher.run_bars(launcher.arguments(argv+['--resume']))==0
    assert json.loads((tmp_path/'status.json').read_text())['status']=='complete'


def test_retired_production_installation_cannot_recreate_tables():
    from pipelines.certified_research import schema,market,installation
    with pytest.raises(RuntimeError,match='retired'):list(market.staging_ddl('arte'))
    with pytest.raises(RuntimeError,match='retired'):installation.install(None,'arte',None)
    table=next(t for t in schema.TABLES if t.name=='research_bars_v1')
    with pytest.raises(RuntimeError,match='retired'):table.ddl('arte')


def test_compact_repair_receipt_admission_and_overlap_guard():
    source=dict(source_date='2026-05-01',trade_event_rows=3,updated_at='ignored')
    receipt=dict(source_date='2026-05-01',source_digest=builder.digest({k:v for k,v in source.items() if k!='updated_at'}),
        total_trades=3,exception_digest='a'*64)
    class Client:
        parts=[]
        def query(self,q,label):
            return {'reporting_source_certificates':[source],'reporting_coverage':[],
                'compact_reporting_table':[{'n':1}],'compact_reporting_receipts':[receipt],
                'reporting_active_mutations':[],'reporting_active_parts':self.parts}[label]
    client=Client()
    assert builder.reporting_coverage(client,date(2026,5,1),date(2026,5,1))[0]['counts']['n']==3
    client.parts=[{'result_part_name':'202605_1_2_3'}]
    with pytest.raises(ValueError,match='mutation'):builder.reporting_coverage(client,date(2026,5,1),date(2026,5,1))
    client.parts=[];receipt['source_digest']='changed'
    with pytest.raises(ValueError,match='coverage'):builder.reporting_coverage(client,date(2026,5,1),date(2026,5,1))


def test_retirement_is_exact_and_restart_safe(tmp_path):
    from scripts.retire_research_market_products import retire,TABLES
    class Claim:
        def check(self):pass
    calls=[]
    def query(q,read=True):
        calls.append(q)
        return [{'n':0}] if 'SELECT count()' in q else []
    retire(query,tmp_path,Claim());retire(query,tmp_path,Claim())
    result=json.loads((tmp_path/'retirement.json').read_text())
    assert result['dropped']==list(TABLES) and result['status']=='complete'
    drops=[q for q in calls if q.startswith('DROP')]
    assert set(drops)=={f'DROP TABLE IF EXISTS arte.{t} SYNC' for t in TABLES}
    assert not any('DROP DATABASE' in q for q in calls)


@pytest.mark.skipif(os.environ.get('MARKET_DAY_CLICKHOUSE_TEST')!='1',reason='Explicit SSD integration opt-in')
def test_real_funnel_liquidity_and_direct_trade_parity():
    c=builder.Client(builder.parse_args(['--date','2026-09-18']),readonly=False)
    db='market_day_funnel_test_'+uuid4().hex
    day=date(2026,9,18);attempt=str(uuid4());build='fixture'
    # Quote + two timely trades in one second, then sparse next-second/5s/1m buckets;
    # unknown and delayed extremes must not enter prices or volume.
    rows=[(0,6,11000,9000,10),(100000,71,10000,0,2),(900000,71,10500,0,3),
          (1100000,6,12000,9500,20),(1200000,71,11000,0,4),(1900000,7,99999,0,100),
          (5100000,199,88888,0,100),(61000000,71,12000,0,5)]
    raw=' UNION ALL '.join(f'''SELECT {sql.bounds(day,'04:00:00')}+toUInt64({offset}) sip_timestamp_us,
        toUInt64({i}) ordinal,toUInt8({meta}) event_meta,toUInt32({price}) price_primary_int,
        toUInt32({second}) price_secondary_int,toFloat32({size}) size_primary,toFloat32(8) size_secondary,
        toUInt8(0) condition_token_1,toUInt8(0) condition_token_2,toUInt8(0) condition_token_3,
        toUInt8(0) condition_token_4,toUInt8(0) condition_token_5'''
        for i,(offset,meta,price,second,size) in enumerate(rows))
    try:
        c.query('CREATE DATABASE '+db,read=False)
        for ddl in sql.ddl(db,include_liquidity_1s=True):c.query(ddl,read=False)
        c.query(sql.broker_sql(db,build,day,'TEST',attempt,[],raw,verified_timely=True),read=False)
        c.query(sql.base_sql(db,build,day,'TEST',attempt),read=False)
        c.query(sql.liquidity_rollup_sql(db,build,day,'TEST',attempt),read=False)
        builder.validate_liquidity_rollup(c,db,build,day,'TEST',attempt)
        values=c.query(f'SELECT open_int,high_int,low_int,close_int,execution_vwap,volume,trade_count,quote_timestamp_us,bid_int,ask_int,cumulative_volume FROM {db}.liquidity_1s_v1 ORDER BY bucket_index')
        assert [r['volume'] for r in values]==[5,4,0,5]
        assert [r['trade_count'] for r in values]==[2,1,0,1]
        assert [r['cumulative_volume'] for r in values]==[5,9,9,14]
        assert (values[1]['bid_int'],values[1]['ask_int'])==(9500,12000)
        assert tuple(values[0][k] for k in ('open_int','high_int','low_int','close_int'))==(10000,10500,10000,10500)
        assert values[0]['execution_vwap']==pytest.approx(1.03)
        queries=sql.funnel_queries(db,build,day,'TEST',attempt)
        for parent,query in zip(sql.FRAMES,queries):
            assert f'resolution_ms={parent}' in query and 'market_sip_compact' not in query
            c.query(query,read=False)
        builder.validate_bars(c,db,build,day,'TEST',attempt)
        result=c.query(f'SELECT resolution_ms,sum(volume) volume,sum(trade_count) trades,max(high_int) high FROM {db}.bars_v1 GROUP BY resolution_ms')
        assert len(result)==8 and all((r['volume'],r['trades'],r['high'])==(14,4,12000) for r in result)
        c.query(f'ALTER TABLE {db}.liquidity_1s_v1 UPDATE bid_int=1 WHERE 1 SETTINGS mutations_sync=2',read=False)
        with pytest.raises(ValueError,match='state'):builder.validate_liquidity_rollup(c,db,build,day,'TEST',attempt)
    finally:
        c.query('DROP DATABASE IF EXISTS '+db+' SYNC',read=False);c.close()
