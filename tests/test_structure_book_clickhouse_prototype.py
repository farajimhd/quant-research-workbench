"""Independent streaming fixture oracle; optional real ClickHouse SQL checks.

Set STRUCTURE_PROTOTYPE_SQL_TEST=1 to exercise SELECT-only fixtures on the
configured workstation. No real market rows leave the server.
"""
import importlib.util
import os
from pathlib import Path
import unittest
from collections import Counter
import datetime as dt

SPEC=importlib.util.spec_from_file_location('prototype',Path(__file__).parents[1]/'scripts/prototype_structure_book_clickhouse.py')
P=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def stream_swings(bars, horizon=1000):
    current=None
    completed=[]
    output=[]
    for bar in bars:
        if current is not None:
            if bar[0]-current[0]>3*horizon:
                completed=[]
            completed.append(current)
            if len(completed)>=3:
                left,center,right=completed
                if center[1]>=left[1] and center[1]>right[1]:
                    output.append((-1,center[1],center[3],bar[5],bar[6],center[0]))
                if center[2]<=left[2] and center[2]<right[2]:
                    output.append((1,center[2],center[4],bar[5],bar[6],center[0]))
                completed.pop(0)
        current=bar
    return sorted(output)


def bars(prices, starts=None):
    starts=starts or list(range(0,len(prices)*1000,1000))
    return [(start,float(price),float(price),start*1000+20,start*1000+20,start*1000+10,i+1)
            for i,(start,price) in enumerate(zip(starts,prices))]


class OracleTests(unittest.TestCase):
    def test_confirmation_requires_fourth_bucket(self):
        self.assertEqual(stream_swings(bars([1,3,2])),[])
        self.assertEqual(stream_swings(bars([1,3,2,4])),[(-1,3.,1000020,3000010,4,1000)])

    def test_plateau_owns_last_bucket(self):
        self.assertEqual(stream_swings(bars([1,3,3,2,1])),[(-1,3.,2000020,4000010,5,2000)])

    def test_completion_gap_resets(self):
        self.assertEqual(stream_swings(bars([1,3,2,4],[0,1000,2000,9000])),[])

    def test_v18_keeps_pre_gap_bucket_as_left_neighbor(self):
        self.assertEqual(stream_swings(bars([1,3,2,4],[0,9000,10000,11000])),
                         [(-1,3.,9000020,11000010,4,9000)])


@unittest.skipUnless(os.environ.get('STRUCTURE_PROTOTYPE_SQL_TEST')=='1','SELECT-only integration opt-in')
class SqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=P.Client(Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'),4)

    def test_detector_matches_stream_oracle(self):
        fixtures=[bars([1,3,2]),bars([1,3,2,4]),bars([1,3,3,2,1]),
                  bars([1,3,2,4],[0,1000,2000,9000]),bars([1,3,2,4],[0,9000,10000,11000]),
                  bars([4,2,3,1,5,4,4,2,3])]
        for fixture in fixtures:
            rows=','.join("('1s',1000,"+','.join(map(str,row))+',0.,1)' for row in fixture)
            schema='timeframe String,horizon_ms UInt32,bucket_ms Int64,high Float64,low Float64,high_us UInt64,low_us UInt64,first_us UInt64,first_ordinal UInt64,volume Float64,trades UInt64'
            sql=P.detector_sql('fixture').replace('INSERT INTO fixture.candidates','')
            sql=sql.replace('FROM fixture.buckets',f"FROM values('{schema}',{rows})")
            sql=sql.replace('SELECT groupArray(effective_ms) FROM fixture.splits',"SELECT CAST([],'Array(Int64)')")
            # Wrap to give stable names independent of expression rendering.
            sql=f'SELECT tuple(*) AS result FROM ({sql})'
            actual=self.client.query(sql,'fixture')
            observed=sorted(tuple(row['result'][1:7]) for row in actual)
            self.assertEqual(observed,stream_swings(fixture))

    def test_form_t_intersection(self):
        rules=[dict(token_id=60,modifier_int=0,update_high_low=1,update_last=1,update_volume=1),
               dict(token_id=72,modifier_int=12,update_high_low=0,update_last=0,update_volume=1),
               dict(token_id=81,modifier_int=21,update_high_low=0,update_last=0,update_volume=1)]
        expression=P.condition_sql(rules)
        cases=[(8,72,0,True),(10,72,0,False),(8,72,81,False),(10,0,0,True)]
        for hour,a,b,expected in cases:
            sql=f"SELECT price_eligible FROM (SELECT toDateTime('2026-08-21 {hour:02}:00:00','America/New_York') AS local_time,toUInt8({a}) AS condition_token_1,toUInt8({b}) AS condition_token_2,toUInt8(0) AS condition_token_3,toUInt8(0) AS condition_token_4,toUInt8(0) AS condition_token_5,{expression})"
            self.assertEqual(bool(self.client.query(sql,'form_t')[0]['price_eligible']),expected)

    def test_intervals_preserve_duplicate_ids_multiplicity_gaps_and_role_changes(self):
        data=[(1,10.,1),(1,10.,1),(2,10.,1),(2,10.,1),(3,10.,-1),(3,11.,1),(5,10.,1)]
        rows=','.join(f"({day},'2026-01-{day:02}',100,{price},{price-.1},{price+.1},{side},'active',0,123,456)" for day,price,side in data)
        schema='session_index UInt16,session_date Date,level_id UInt64,price Float64,lower Float64,upper Float64,side Int8,lifecycle String,pending_side Int8,created_ms Int64,confirmed_ms Int64'
        sql=P.interval_sql('fixture').replace('INSERT INTO fixture.intervals','').replace('FROM fixture.closes',f"FROM values('{schema}',{rows})")
        intervals=self.client.query('SELECT tuple(*) AS r FROM ('+sql+')','interval_fixture')
        reconstructed=Counter()
        for row in intervals:
            state=row['r']
            for day in range(state[10],state[11]):
                reconstructed[day,state[1],state[4]]+=state[9]
        self.assertEqual(reconstructed,Counter(data))

    def test_daily_anchor_dst_and_last_equal_extreme(self):
        def us(day,hour,minute=0,second=0):
            return int(dt.datetime(2026,3,day,hour,minute,second,tzinfo=dt.timezone.utc).timestamp()*1000000)
        times=[us(8,7,59),us(8,8),us(8,8,0,1)]
        rows=','.join(f'({i+1},{t},10.,1,1,1.)' for i,t in enumerate(times))
        schema='ordinal UInt64,sip_timestamp_us UInt64,price Float64,price_eligible UInt8,volume_eligible UInt8,size_primary Float64'
        source=f"SELECT *,fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time FROM values('{schema}',{rows})"
        sql=P.bucket_sql('1d',86400000,source,'fixture').replace('INSERT INTO fixture.buckets','')
        actual=self.client.query('SELECT tuple(*) r FROM ('+sql+') ORDER BY r.3','calendar_fixture')
        self.assertEqual([r['r'][2] for r in actual],[us(7,9)//1000,us(8,8)//1000])
        self.assertEqual(actual[-1]['r'][5],times[-1])
        self.assertEqual(actual[-1]['r'][6],times[-1])


if __name__=='__main__':
    unittest.main()
