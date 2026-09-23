import os
from pathlib import Path
import tempfile
import unittest
import uuid
from datetime import datetime, timezone

from pipelines.market_sip.events.trade_reporting_flags import reporting_flags, reporting_reason, reason_sql, flags_sql
from scripts import backfill_trade_reporting_flags as migration


def ns(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())*1_000_000_000


CASES = [
    ('',100,101,64), ('12',100,101,64), ('22',100,101,64),
    ('13',0,100,192), ('30',0,100,192), ('32',100,101,192),
    ('5,37',100,101,192), ('31',100,101,192), ('33',100,101,192),
    ('',0,100,0), ('',101,100,0), ('',100,0,0), ('','invalid',100,0),
    ('',100,10_000_000_100,64), ('',100,10_000_000_101,192),
    ('',ns('2026-09-18T03:59:59'),ns('2026-09-18T04:00:01'),192),
    ('',ns('2026-09-17T23:59:59'),ns('2026-09-18T00:00:01'),64),
]


class FlagsTests(unittest.TestCase):
    def test_classification(self):
        for conditions,p,s,expected in CASES:
            with self.subTest(conditions=conditions,p=p,s=s):
                self.assertEqual(reporting_flags(conditions,p,s),expected)

    def test_reason_preserves_overlapping_evidence(self):
        self.assertEqual(reporting_reason('13',0,100),9)
        self.assertEqual(reporting_reason('32',100,20_000_000_000),5)

    def test_cli_range(self):
        a=migration.parse_args(['--start-date','2026-08-01','--end-date','2026-09-30','--plan-only'])
        self.assertEqual(str(a.end_date),'2026-09-30')

    def test_certified_archive_hydration_is_atomic_and_repeatable(self):
        runtime=Path('D:/TradingML/runtimes')
        with tempfile.TemporaryDirectory(dir=runtime,prefix='reporting-flags-test-') as root:
            a=migration.parse_args(['--start-date','2026-08-03','--end-date','2026-08-03','--hydrate-missing-trades'])
            a.archive_flatfiles_root_win=Path(root)/'archive'
            a.cache_flatfiles_root_win=Path(root)/'cache'
            relative=Path('trades_v1/2026/08/2026-08-03.csv.gz')
            source=a.archive_flatfiles_root_win/relative
            source.parent.mkdir(parents=True)
            source.write_bytes(b'certified gzip bytes')
            stat=source.stat()
            certificate=dict(source_date='2026-08-03',trade_file_size=stat.st_size,trade_file_mtime_ns=stat.st_mtime_ns)
            migration.hydrate_trade(a,certificate)
            target=a.cache_flatfiles_root_win/relative
            self.assertEqual(target.read_bytes(),source.read_bytes())
            self.assertEqual(target.stat().st_mtime_ns,stat.st_mtime_ns)
            migration.hydrate_trade(a,certificate)
            target.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'differs from source certificate'):
                migration.hydrate_trade(a,certificate)

    def test_source_day_bounds_cover_utc_midnight(self):
        source=dict(source_date='2026-09-18',first_sip_timestamp_us=ns('2026-09-18T08:00:00')//1000,
                    last_sip_timestamp_us=ns('2026-09-19T00:00:08')//1000)
        where=migration.where_day(source)
        self.assertIn("event_date BETWEEN '2026-09-18' AND '2026-09-19'",where)


@unittest.skipUnless(os.environ.get('TRADE_FLAGS_CLICKHOUSE_TEST')=='1','real ClickHouse opt-in')
class ClickHouseTests(unittest.TestCase):
    def setUp(self):
        self.c=migration.Client(migration.parse_args(['--start-date','2026-09-18','--end-date','2026-09-18']))
        prefix='q_live.trade_reporting_test_'+uuid.uuid4().hex[:12]
        self.raw,self.target,self.mapping=prefix+'_raw',prefix+'_events',prefix+'_map'

    def tearDown(self):
        for table in (self.mapping+'c',self.mapping+'b',self.mapping,self.raw,self.target):
            self.c.query(f'DROP TABLE IF EXISTS {table}',read=False)

    def test_sql_python_parity(self):
        for conditions,p,s,expected in CASES:
            q=f"SELECT {reason_sql(migration.lit(conditions),migration.lit(str(p)),migration.lit(str(s)))} AS reason,{flags_sql('reason')} AS flags"
            self.assertEqual(self.c.query(q)[0],dict(reason=reporting_reason(conditions,p,s),flags=expected))

    def test_reconcile_mutate_verify_preserve_quotes_and_missing_rows(self):
        fields='ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64,price_primary_int UInt32,price_secondary_int UInt32,size_primary Float32,size_secondary Float32,exchange_primary UInt8,exchange_secondary UInt8,'+','.join(f'condition_token_{i} UInt8' for i in range(1,6))+',event_date Date'
        self.c.query(f"CREATE TABLE {self.target} ({fields}) ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        for ordinal,meta in [(1,1),(2,3),(3,17),(4,0)]:
            self.c.query(f"INSERT INTO {self.target} VALUES ('TEST',{ordinal},{meta},{ordinal*100},100,0,1,0,1,0,0,0,0,0,0,'2026-09-18')",read=False)
        self.c.query(f"CREATE TABLE {self.raw} ENGINE=MergeTree ORDER BY (ticker,sip_timestamp_us,sequence_number) SETTINGS storage_policy='live_market_ssd' AS SELECT * EXCEPT(event_meta,ordinal),toUInt8(multiIf(ordinal=1,65,ordinal=2,195,17)) event_meta,ordinal sequence_number,toUInt8(multiIf(ordinal=1,0,ordinal=2,1,8)) reporting_reason FROM {self.target} e WHERE bitAnd(e.event_meta,1)=1",read=False)
        self.c.query(f"CREATE TABLE {self.mapping} ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd' AS {migration.map_select(self.raw,self.target,'2026-09-18')}",read=False)
        self.assertEqual(self.c.query(f'SELECT count() n,sum(mismatch) bad FROM {self.mapping}')[0],dict(n=3,bad=0))
        before=migration.unchanged_digest(self.c,self.target,'2026-09-18')
        self.c.query(migration.mutation_sql(self.target,self.mapping,'2026-09-18').replace('mutations_sync=0','mutations_sync=2'),read=False)
        self.assertEqual(migration.verify(self.c,self.target,self.mapping,'2026-09-18'),3)
        self.assertEqual(migration.unchanged_digest(self.c,self.target,'2026-09-18'),before)
        self.assertEqual(self.c.query(f'SELECT event_meta FROM {self.target} ORDER BY ordinal'),[{'event_meta':m} for m in (65,195,17,0)])
        # Reapply is idempotent, and a missing source row cannot pass reconciliation.
        self.c.query(migration.mutation_sql(self.target,self.mapping,'2026-09-18').replace('mutations_sync=0','mutations_sync=2'),read=False)
        self.c.query(f'ALTER TABLE {self.raw} DELETE WHERE sequence_number=2 SETTINGS mutations_sync=2',read=False)
        self.assertGreater(self.c.query('SELECT sum(mismatch) bad FROM ('+migration.map_select(self.raw,self.target,'2026-09-18')+')')[0]['bad'],0)

    def test_monthly_mutation_combines_days_and_respects_bounds(self):
        fields='ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64,price_primary_int UInt32,price_secondary_int UInt32,size_primary Float32,size_secondary Float32,exchange_primary UInt8,exchange_secondary UInt8,'+','.join(f'condition_token_{i} UInt8' for i in range(1,6))+',event_date Date'
        self.c.query(f"CREATE TABLE {self.target} ({fields}) ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        times=[ns('2026-08-03T13:30:00')//1000,ns('2026-08-04T13:30:00')//1000,ns('2026-08-05T13:30:00')//1000]
        for ordinal,(stamp,day,meta) in enumerate(zip(times,['2026-08-03','2026-08-04','2026-08-05'],[1,1,1]),1):
            self.c.query(f"INSERT INTO {self.target} VALUES ('TEST',{ordinal},{meta},{stamp},100,0,1,0,1,0,0,0,0,0,0,'{day}')",read=False)
        self.c.query(f"INSERT INTO {self.target} VALUES ('TEST',4,0,{times[0]},100,0,1,0,1,0,0,0,0,0,0,'2026-08-03')",read=False)
        for name,ordinal,meta in [(self.mapping,1,65),(self.mapping+'b',2,193)]:
            self.c.query(f"CREATE TABLE {name} (ticker String,ordinal UInt64,expected_meta UInt8) ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
            self.c.query(f"INSERT INTO {name} VALUES ('TEST',{ordinal},{meta})",read=False)
        self.c.query(f"CREATE TABLE {self.mapping+'c'} (ticker String,ordinal UInt64,expected_meta UInt8) ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        for name in (self.mapping,self.mapping+'b'):
            self.c.query(f"INSERT INTO {self.mapping+'c'} SELECT * FROM {name}",read=False)
        sources=[dict(source_date=day,first_sip_timestamp_us=stamp,last_sip_timestamp_us=stamp) for day,stamp in zip(['2026-08-03','2026-08-04'],times)]
        query=migration.month_mutation_sql(self.target,sources,self.mapping+'c').replace('mutations_sync=0','mutations_sync=2')
        self.c.query(query,read=False)
        self.assertEqual(self.c.query(f'SELECT ordinal,event_meta FROM {self.target} ORDER BY ordinal'),
                         [dict(ordinal=i,event_meta=m) for i,m in [(1,65),(2,193),(3,1),(4,0)]])


if __name__=='__main__':
    unittest.main()
