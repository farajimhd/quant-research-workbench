import os
from pathlib import Path
import random
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from src.backend import experimental_structure_book as E
sys.path.insert(0, str(Path(__file__).parent))
from test_clickhouse_book_contract import reference


class ContinuationTests(unittest.TestCase):
    def test_vector_matches_scalar_every_prefix(self):
        rng = random.Random(320)
        events = [(i+1,rng.choice([8.,9.,9.95,10.,10.05,11.,12.]),rng.choice([0.,.1,1.])) for i in range(500)]
        expected = reference(events)
        state = np.zeros((1,10)); state[0,0] = 1
        for (ts,price,vol), want in zip(events, expected):
            E.transition(state,np.array([True]),price,vol,np.array([9.9]),np.array([10.1]),np.array([.01]),ts)
            flattened = [want[0],want[1],want[2],*want[3],want[4]]
            np.testing.assert_allclose(state[0],flattened,rtol=1e-13,atol=1e-10)

    def test_confirmation_cursor_and_no_prebirth_observation(self):
        at = datetime(2026,8,21,9,30,tzinfo=E.NY)
        born = E.micros(at)
        level = dict(id='18446744073709551610',price=10.,lower=9.9,upper=10.1,tick=.01,
                     side=1,born_us=born,timeframe='1s',state=[0,0,0,[0.,0.,0.,0,0,0],0],confirmed_ordinal=22)
        observations = [dict(known_us=born,price=8.,prior_range=1.),dict(known_us=born+1000000,price=8.,prior_range=1.)]
        with patch.object(E,'resolve',return_value={'id':'test','fingerprint':'hash'}), patch.object(E,'load_day',return_value=([level],observations,1.)):
            cursor=E.BookCursor('test','JUNS')
            self.assertEqual(cursor.snapshot(at,21)['unified_levels'],[])
            row=cursor.snapshot(at,22)['unified_levels'][0]
            self.assertEqual(row['lifecycle'],'active')
            self.assertEqual(row['unified_level_id'],level['id'])
            self.assertEqual(cursor.snapshot(at+timedelta(seconds=1))['unified_levels'][0]['lifecycle'],'crossed')
            self.assertEqual(cursor.snapshot(at-timedelta(microseconds=1))['unified_levels'],[])

    @unittest.skipUnless(os.environ.get('STRUCTURE_BOOK_BACKTEST_INTEGRATION')=='1','real ClickHouse opt-in')
    def test_real_closing_states_split_and_chart_deltas(self):
        build=E.builds()[0]
        for day in ['2026-08-06','2026-08-07','2026-08-21']:
            end=datetime.fromisoformat(day+'T20:00:00').replace(tzinfo=E.NY)
            cursor=E.BookCursor(build['id'],build['ticker']); cursor.advance(end)
            actual={r['id']:s for r,s in zip(cursor.levels,cursor.state)}
            expected=E.rows(f"SELECT toString(level_id) id,state FROM {build['id']}.history FINAL WHERE session_date='{day}'")
            self.assertEqual(len(actual),len(expected))
            for row in expected:
                s=row['state']; np.testing.assert_allclose(actual[row['id']],[s[0],s[1],s[2],*s[3],s[4]],rtol=1e-13,atol=1e-10)
            book=E.rows(f"SELECT toString(level_id) id,price,side,prominence FROM {build['id']}.book FINAL WHERE valid_from_us<={E.micros(end)} AND (valid_to_us IS NULL OR valid_to_us>{E.micros(end)})")
            snapshots={r['unified_level_id']:r for r in cursor.snapshot(end)['unified_levels']}
            for row in book:
                self.assertEqual(snapshots[row['id']]['side'],row['side'])
                self.assertAlmostEqual(snapshots[row['id']]['price'],row['price'],places=10)
                # ClickHouse's vector log and NumPy log1p differ by <1.6e-9
                # on these books; lifecycle counters/geometry are checked separately.
                self.assertAlmostEqual(snapshots[row['id']]['prominence'],row['prominence'],delta=1e-8)
            # The chart replays exactly the same provider independently.
            timeline=E.chart_rows(build['id'],build['ticker'],end.replace(hour=4),end)
            restored={}
            for row in timeline:
                if 'qmd_structure_unified_levels' in row:
                    restored={r['unified_level_id']:r for r in row['qmd_structure_unified_levels']}
                else:
                    delta=row['qmd_structure_unified_level_delta']
                    for r in delta['removed']: restored.pop(r['unified_level_id'],None)
                    restored.update({r['unified_level_id']:r for r in delta['upserts']})
            self.assertEqual(restored,snapshots)


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_and_frame_have_independent_causal_cursors(self):
        from src.backend.replay_run_service import ReplayRunController
        from unittest.mock import Mock
        run=object.__new__(ReplayRunController)
        run.definition=SimpleNamespace(experimental_structure_book='selected',experimental_structure_fingerprint='pinned')
        run._record_data_authority=Mock()
        instances=[]
        class Cursor:
            def __init__(self,*args): self.calls=[];instances.append(self)
            def snapshot(self,at,sequence=None): self.calls.append((at,sequence));return {'unified_levels':[]}
        at=datetime(2026,8,21,9,30,tzinfo=E.NY)
        with patch.object(E,'BookCursor',Cursor):
            await run._experimental_structure_snapshot('JUNS',at+timedelta(minutes=1),'frame')
            await run._event_structure_context(SimpleNamespace(ticker='JUNS',ts=at,raw={'arrival_sequence':22},sequence=9,price=10))
        self.assertEqual(len(instances),2)
        self.assertEqual(instances[1].calls,[(at,22)])
        self.assertEqual(instances[0].calls,[(at+timedelta(minutes=1),None)])

    def test_chart_endpoint_clamps_to_run_cursor_and_pins_source(self):
        from src.backend import app as A
        from fastapi.testclient import TestClient
        at=datetime(2026,8,21,9,30,tzinfo=E.NY)
        run=SimpleNamespace(current_time=at,definition=SimpleNamespace(experimental_structure_book='selected',experimental_structure_fingerprint='pinned'))
        with patch.object(A.backtest_run_service,'get',return_value=run),patch.object(E,'chart_rows',return_value=[]) as chart:
            response=TestClient(A.app).get('/api/trading/canvas-chart/history',params={
                'symbol':'JUNS','timeframe':'1s','mode':'backtest','run_id':'review',
                'session_date':'2026-08-21','as_of':(at+timedelta(minutes=5)).isoformat(),
                'indicator_columns':'bar_start,qmd_structure_unified_levels','stage':'full'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(chart.call_args.args[3],at)
        self.assertEqual(chart.call_args.args[4],'pinned')


if __name__=='__main__': unittest.main()
