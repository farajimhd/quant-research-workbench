"""Strict classification, exact persistence and causal restore invariants."""
from dataclasses import replace
from datetime import date, datetime
import os
from pathlib import Path
import sys
import unittest
import uuid
from threading import Event,Lock
import time

from pipelines.certified_research import contracts as c, schema as s
from pipelines.certified_research.checkpoints import CheckpointBinding, boundaries, encode, restore
from pipelines.market_sip.events import market_day_sql
from pipelines.certified_research.execution import Unit,execute
from pipelines.certified_research import market


class Contracts(unittest.TestCase):
    def test_parallel_fanout_reuses_one_parent_and_respects_global_memory(self):
        units=(Unit('bars','bars',memory_bytes=1),Unit('indicators','consume',('bars',),1),
               Unit('v7','consume',('bars',),1))
        starts=[];peak=[0];active=[0];lock=Lock();bar_result={'hash':'shared'}
        def build(u,parents,stop):starts.append(u.key);return bar_result
        def consume(u,parents,stop):
            self.assertIs(parents['bars'],bar_result)
            with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
            time.sleep(.02)
            with lock:active[0]-=1
            return {'unit':u.key}
        result=execute(units,{'bars':build,'consume':consume},workers=2,memory_bytes=2)
        self.assertEqual(result['status'],'complete');self.assertEqual(starts,['bars']);self.assertEqual(peak[0],2)
        peak[0]=0
        execute(units,{'bars':build,'consume':consume},workers=2,memory_bytes=1)
        self.assertEqual(peak[0],1)

    def test_failed_parent_blocks_consumers_without_hiding_other_lane(self):
        def fail(*args):raise ValueError('source failure')
        def good(u,*args):return {'unit':u.key}
        result=execute((Unit('source','bad'),Unit('child','good',('source',)),Unit('reference','good')),
                       {'bad':fail,'good':good})
        self.assertEqual(result['units'],dict(source='failed',child='blocked',reference='certified'))
        with self.assertRaisesRegex(ValueError,'cycle'):
            execute((Unit('a','good',('b',)),Unit('b','good',('a',))),{'good':good})
        stop=Event();stop.set()
        self.assertEqual(execute((Unit('a','good'),),{'good':good},stop=stop)['status'],'interrupted')

    def test_all_encoded_states_reject_unknown_delayed_and_invalid(self):
        for meta in range(256):
            expected=bool(meta & 1) and meta & 192 == 64
            self.assertEqual(c.timely_trade(meta),expected)
        for invalid in (-1,256,True,1.0):
            with self.assertRaises(ValueError):c.timely_trade(invalid)
        self.assertIn('bitAnd(event_meta,192)=64',c.timely_sql())

    def test_budget_and_ranges_are_frozen(self):
        config=c.CampaignConfig(date(2026,1,2),date(2026,1,5))
        self.assertEqual(config.content_hash,replace(config).content_hash)
        for fields in (dict(workers=32),dict(start=date(2025,12,31)),dict(database='default'),
                       dict(volume_windows_seconds=(60,10)),dict(resolutions_ms=(123,))):
            with self.assertRaises(ValueError):replace(config,**fields)

    def test_every_column_has_a_lossless_codec_and_normalized_keys(self):
        self.assertEqual(len(s.TABLES),len({t.name for t in s.TABLES}))
        for table,ddl in zip(s.TABLES,s.ddl()):
            self.assertEqual(ddl.count('CODEC('),len(table.columns))
            self.assertIn("storage_policy='live_market_ssd'",ddl)
            self.assertNotIn('Float32',ddl)
        bars=next(t for t in s.TABLES if t.name=='research_bars_v1')
        self.assertIn('listing_key',[x.name for x in bars.columns])
        self.assertNotIn('ticker',[x.name for x in bars.columns])

    def test_actual_misplaced_parts_fail_even_with_correct_table_policy(self):
        def query(sql):
            if 'system.storage_policies' in sql:return [dict(disk_name='live_market_ssd')]
            if 'system.tables' in sql:return [dict(name='research_bars_v1',storage_policy='live_market_ssd')]
            return [dict(table='research_bars_v1',disk_name='default')]
        with self.assertRaisesRegex(ValueError,'parts'):s.verify_storage(query)

    def test_strict_sql_keeps_quotes_and_changes_only_trade_gate(self):
        args=('arte','test',date(2026,1,2),'TEST','00000000-0000-0000-0000-000000000001',[])
        old=market_day_sql.broker_sql(*args)
        strict=market_day_sql.broker_sql(*args,verified_timely=True)
        self.assertIn('kind=1 AND bitAnd(event_meta,192)=64',strict)
        self.assertEqual(strict.replace('bitAnd(event_meta,192)=64','bitAnd(event_meta,128)=0'),old)

    def test_session_restore_matches_uninterrupted_pending_state(self):
        from tests.test_streaming_level_book import engine,bars
        a=engine(historical=True)
        # The fixture's nonempty seed intentionally has no strict provenance.
        a.seed_input_policy=a.input_policy
        sequence=bars(a)
        for row in sequence[:8]:a.update(row)
        binding=CheckpointBinding('a'*64,'b'*64,'c'*64,'TEST',date(2026,8,21))
        boundary=datetime.fromtimestamp(sequence[7]['t'],tz=boundaries(binding.session)['PM'].tzinfo)
        payload,hash=encode(a,binding,boundary)
        b=restore(payload,hash,binding)
        for row in sequence[8:]:a.update(row);b.update(row)
        self.assertEqual(a.checkpoint(),b.checkpoint())
        with self.assertRaisesRegex(ValueError,'hash'):restore(payload+b'x',hash,binding)
        with self.assertRaisesRegex(ValueError,'binding'):restore(payload,hash,replace(binding,input_prefix_hash='d'*64))
        with self.assertRaisesRegex(ValueError,'future'):encode(a,binding,boundary)

    def test_early_close_and_missing_timezone(self):
        day=date(2026,11,27)
        normal=boundaries(day)
        early=normal['REGULAR'].replace(hour=18) # 13:00 New York in November.
        self.assertEqual(boundaries(day,early)['REGULAR'],early)
        with self.assertRaises(ValueError):boundaries(day,datetime(2026,11,27,13))


@unittest.skipUnless(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')=='1',
                     'Bounded SSD integration requires explicit test opt-in')
class ClickHouseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import build_market_day as builder
        cls.builder=builder
        args=builder.parse_args(['--date','2026-01-02','--max-threads','2',
                                '--env-file',str(builder.ROOT/'.env')])
        cls.client=builder.Client(args,persistent=False)
        cls.database='certified_research_test_'+uuid.uuid4().hex
        cls.created=False
        try:
            s.verify_storage(cls.client.query,cls.database)
            cls.client.query('CREATE DATABASE '+cls.database,read=False)
            cls.created=True
            for ddl in (*s.ddl(cls.database),*market_day_sql.ddl(cls.database),*market.staging_ddl(cls.database)):
                cls.client.query(ddl,read=False)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.created:
                assert cls.database.startswith('certified_research_test_') and len(cls.database)==len('certified_research_test_')+32
                cls.client.query('DROP DATABASE '+cls.database+' SYNC',read=False)
        finally:cls.client.close()

    def test_real_ddl_roundtrip_and_actual_part_placement(self):
        dataset=str(uuid.uuid4());attempt=str(uuid.uuid4())
        self.client.query(f"INSERT INTO {self.database}.research_bars_v1 VALUES "
            f"('{dataset}','2026-01-02',1,'{attempt}',1000,14400,100000,101000,99000,100100,"
            "123.5,7,1235.1,120.5,1205.1,1,1)",read=False)
        rows=self.client.query(f"SELECT volume,trade_count,close_int FROM {self.database}.research_bars_v1")
        self.assertEqual(rows,[dict(volume=123.5,trade_count=7,close_int=100100)])
        s.verify_storage(self.client.query,self.database,require_tables=True)
        columns=self.client.query(f"SELECT name,compression_codec FROM system.columns "
            f"WHERE database='{self.database}' AND table='research_bars_v1'")
        self.assertTrue(columns)
        self.assertTrue(all('ZSTD' in r['compression_codec'] for r in columns))

    def test_strict_trade_gate_matches_real_sql_volume_price_and_count(self):
        day=date(2026,1,2);attempt=str(uuid.uuid4())
        rows=[(0,6,10200,9800,20.),(1,71,10000,0,5.),(2,7,990000,0,30.),
              (3,199,880000,0,40.),(4,135,770000,0,50.)]
        source=' UNION ALL '.join(
            f"SELECT toUInt64({market_day_sql.bounds(day,'04:05:00')}+{offset*100000}) sip_timestamp_us,"
            f"toUInt64({offset}) ordinal,toUInt8({meta}) event_meta,"
            f"toUInt32({price}) price_primary_int,toUInt32({bid}) price_secondary_int,"
            f"toFloat32({size}) size_primary,toFloat32(20) size_secondary,"
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))
            for offset,meta,price,bid,size in rows)
        self.client.query(market_day_sql.broker_sql(self.database,'fixture',day,'STRICT',attempt,[],source,
                          verified_timely=True),read=False)
        result=self.client.query(f"SELECT sum(volume) volume,sum(trade_count) trades,maxIf(high_int,extremes_valid) high,"
            f"sum(quote_event_count) quotes FROM {self.database}.liquidity_100ms_v1 WHERE ticker='STRICT'")
        self.assertEqual(result,[dict(volume=5,trades=1,high=10000,quotes=1)])
        # The actual new normalized path reuses the same aggregate for rollups
        # and indicators; source rows do not leave ClickHouse.
        unit=market.MarketUnit(str(uuid.uuid4()),day,'STRICT',7,str(uuid.uuid4()))
        self.client.query(market.aggregate_sql(unit,[],self.database,source=source),read=False)
        for query in market.bar_queries(unit,self.database):self.client.query(query,read=False)
        self.client.query(market.indicator_sql(unit,self.database),read=False)
        for product in ('bars','technical','broker_100ms'):
            self.client.query(market.normalized_insert(unit,product,self.database),read=False)
        result=self.client.query(f"SELECT sum(volume) volume,sum(trade_count) trades,"
            "sum(reporting_delayed_trades) delayed,sum(reporting_unknown_trades) unknown,"
            f"sum(reporting_invalid_trades) invalid FROM {self.database}.research_liquidity_v1 WHERE listing_key=7")
        self.assertEqual(result,[dict(volume=5,trades=1,delayed=1,unknown=1,invalid=1)])
        result=self.client.query(f"SELECT resolution_ms,volume,trade_count FROM {self.database}.research_bars_v1 "
                                 "WHERE listing_key=7 ORDER BY resolution_ms")
        self.assertEqual([r['resolution_ms'] for r in result],list(market_day_sql.FRAMES))
        self.assertTrue(all(r['volume']==5 and r['trade_count']==1 for r in result))
        indicators=self.client.query(f"SELECT ema_7 FROM {self.database}.research_indicators_v1 WHERE listing_key=7")
        self.assertEqual(indicators,[dict(ema_7=1)]*8)
        s.verify_storage(self.client.query,self.database,require_tables=True)


if __name__=='__main__':unittest.main()
