"""Independent scalar reference for the NEW completed-second book contract.

These fixtures do not execute Rust and are not a v18 reconstruction.
"""
import importlib.util
import os
from pathlib import Path
import random
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import build_structure_book_clickhouse as B


def reference(events,seed=(1,0,0,(0.,0.,0.,0,0,0),0),lower=9.9,upper=10.1,tick=.01):
    side,phase,count,reaction,confirmed=seed
    total,best,scale,encounter,score_side,n=reaction
    output=[]
    for ts,price,vol in events:
        old_phase=phase
        old_side=side
        past=price<lower-max(tick,vol*.25) if side>0 else price>upper+max(tick,vol*.25)
        contact=lower<=price<=upper
        if phase in (0,1):
            count=count+1 if past else 0
            phase=(2 if count>=2 else 1) if past else 0
        elif phase==2:
            phase=3 if contact else 2
            count=0
        else:
            flip=price<lower-tick if side>0 else price>upper+tick
            reject=price>upper+tick if side>0 else price<lower-tick
            if flip: side=-side
            if flip or reject: phase=0
            count=0
        if side!=old_side: confirmed=ts
        broken=old_phase==1 and phase==2
        if broken or (score_side!=0 and score_side!=side):
            if encounter: total+=best; n+=1
            best=scale=0.; encounter=0
        score_side=side
        if phase<=1 and not broken:
            if encounter==2 and contact:
                total+=best; n+=1; best=scale=0.; encounter=0
            if encounter==0 and contact and vol>0:
                scale=vol; encounter=1
            if encounter:
                distance=price-upper if side>0 else lower-price
                best=max(best,max(0.,distance)/scale)
                if best>=1: encounter=2
        output.append((side,phase,count,(total,best,scale,encounter,score_side,n),confirmed))
    return output


class SplitSourceTests(unittest.TestCase):
    def test_duplicate_delivery_is_one_action_and_retains_latest_provenance(self):
        rows=[dict(execution_date='2025-08-25',split_from=10,split_to=1,inserted_at=t)
              for t in ['2026-06-09','2026-07-08']]
        result=B.canonical_splits(rows)
        self.assertEqual(result,[rows[1]])
        self.assertEqual(result,B.canonical_splits(list(reversed(rows))))

    def test_conflicting_ratios_fail_closed(self):
        rows=[dict(execution_date='2025-08-25',split_from=n,split_to=1,inserted_at='2026-07-08') for n in [10,100]]
        with self.assertRaisesRegex(ValueError,'Conflicting split ratios'):
            B.canonical_splits(rows)


@unittest.skipUnless(os.environ.get('STRUCTURE_PROTOTYPE_SQL_TEST')=='1','SQL fixture integration opt-in')
class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=B.P.Client(Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))

    def test_every_prefix_and_day_resume(self):
        rng=random.Random(81)
        for k in range(10):
            events=[(i+1,rng.choice([8.,9.,9.95,10.,10.05,11.,12.]),rng.choice([0.,.1,1.])) for i in range(35)]
            expected=reference(events)
            array='['+','.join(f'tuple(toUInt64({t}),{p},{v})' for t,p,v in events)+']'
            seed='tuple(toInt8(1),toUInt8(0),toUInt32(0),tuple(0.,0.,0.,toUInt8(0),toInt8(0),toUInt64(0)),toUInt64(0))'
            fold=B.lifecycle_fold('arraySlice(events,1,n)',seed,'9.9','10.1','.01')
            actual=self.c.query(f'WITH {array} AS events SELECT arrayMap(n->{fold},range(1,length(events)+1)) states','new_contract')[0]['states']
            for got,want in zip(actual,expected):
                self.assertEqual(got[:3],list(want[:3]))
                self.assertEqual(got[4],want[4])
                for a,b in zip(got[3],want[3]): self.assertAlmostEqual(a,b,places=10)
            first=B.lifecycle_fold('arraySlice(events,1,17)',seed,'9.9','10.1','.01')
            second=B.lifecycle_fold('arraySlice(events,18)','checkpoint','9.9','10.1','.01')
            resumed=self.c.query(f'WITH {array} AS events,{first} AS checkpoint SELECT {second} state','new_contract_resume')[0]['state']
            self.assertEqual(resumed,actual[-1])

    def test_split_factor_has_no_effect_before_boundary(self):
        split=[dict(execution_date='2026-08-07',split_from=75,split_to=1)]
        expression=B.factor_sql(split,'ts')
        rows=self.c.query(f"SELECT {expression} factor FROM (SELECT arrayJoin([toUnixTimestamp64Micro(toDateTime64('2026-08-07 03:59:59',6,'America/New_York')),toUnixTimestamp64Micro(toDateTime64('2026-08-07 04:00:00',6,'America/New_York'))]) ts)",'split_boundary')
        self.assertEqual([r['factor'] for r in rows],[1,75])

    def test_range_elimination_equals_full_transition(self):
        rng=random.Random(98)
        seed='tuple(toInt8(1),toUInt8(0),toUInt32(0),tuple(0.,0.,0.,toUInt8(0),toInt8(0),toUInt64(0)),toUInt64(0))'
        for case in range(30):
            prices=[rng.choice([8.,9.,10.,10.3,12.]) for _ in range(12)]
            before='['+','.join(f'tuple(toUInt64({i+1}),{p},.5)' for i,p in enumerate(prices))+']'
            prices=[rng.choice(([11.,12.,13.] if case%3==0 else [7.,8.,9.] if case%3==1 else [8.,10.,12.])) for _ in range(15)]
            after='['+','.join(f'tuple(toUInt64({i+20}),{p},.5)' for i,p in enumerate(prices))+']'
            first=B.lifecycle_fold(before,seed,'9.9','10.1','.01')
            full=B.lifecycle_fold(after,'seed','9.9','10.1','.01')
            fast=B.lifecycle_fast(after,'seed','9.9','10.1','.01')
            row=self.c.query(f'WITH {first} AS seed SELECT {full} AS full,{fast} AS fast','range_elimination')[0]
            self.assertEqual(row['fast'],row['full'])

    def test_long_region_runs_preserve_all_prefixes(self):
        rng=random.Random(38)
        for case in range(6):
            events=[]
            for region in [1,0,1,-1,0,-1,1,0]:
                for i in range(30):
                    price=rng.uniform(11,14) if region==1 else rng.uniform(6,9) if region==-1 else rng.uniform(9.95,10.05)
                    events.append((len(events)+1,price,rng.uniform(.1,1.)))
            expected=reference(events)
            array='['+','.join(f'tuple(toUInt64({t}),{p},{v})' for t,p,v in events)+']'
            seed='tuple(toInt8(1),toUInt8(0),toUInt32(0),tuple(0.,0.,0.,toUInt8(0),toInt8(0),toUInt64(0)),toUInt64(0))'
            fold=B.lifecycle_fast('arraySlice(events,1,n)',seed,'9.9','10.1','.01')
            indices=[1,17,30,31,62,89,121,157,220,240]
            rows=self.c.query(f'WITH {array} AS events SELECT arrayMap(n->{fold},{indices}) states','long_runs')[0]['states']
            for got,n in zip(rows,indices):
                want=expected[n-1]
                self.assertEqual(got[:3],list(want[:3])); self.assertEqual(got[4],want[4])
                for a,b in zip(got[3],want[3]): self.assertAlmostEqual(a,b,places=10)


if __name__=='__main__': unittest.main()
