import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from research.vectorized_backtest.v56.session_pipeline import independent_sessions,SessionProgress
from research.vectorized_backtest.v56.distributed_preparation import PopulationPreparation


class PipelineTests(unittest.TestCase):
    def test_fast_lane_advances_without_waiting_and_results_keep_source_order(self):
        advanced=Event();seen=[]
        class Panel:
            def publish(self,**values):self.last=values
        panel=Panel();progress=SessionProgress(panel,list('abcd'),12,3,'training')
        def run(day):
            if day=='a':self.assertTrue(advanced.wait(3),'Fast lane was blocked by slow lane')
            if day=='d':advanced.set()
            seen.append(day);return dict(day=day,timings=[],clocks=99,wall_seconds=.01)
        with ThreadPoolExecutor(max_workers=1) as a,ThreadPoolExecutor(max_workers=1) as b:
            pools=[a,b]
            result=independent_sessions(list('abcd'),2,lambda lane,index,day,next_day:pools[lane].submit(run,day),progress)
        self.assertEqual([r['day'] for r in result],list('abcd'));self.assertLess(seen.index('d'),seen.index('a'))
        self.assertEqual(panel.last['completed_sessions'],4);self.assertEqual(panel.last['active_sessions'],[])
        self.assertEqual(panel.last['session_paths']['a']['completed_candidates'],12)

    def test_progress_keeps_both_sessions_and_failure_propagates(self):
        class Panel:
            def publish(self,**values):self.last=values
        panel=Panel();p=SessionProgress(panel,['a','b'],100,1,'training');p.begin('a');p.begin('b')
        p.event(dict(day='a',completed_seconds=40));p.event(dict(day='b',completed_seconds=60))
        self.assertEqual([panel.last['session_paths'][d]['completed_seconds'] for d in ['a','b']],[40,60])
        with ThreadPoolExecutor(max_workers=1) as pool:
            def fail():raise ValueError('replay failed')
            with self.assertRaisesRegex(ValueError,'replay failed'):independent_sessions(['a'],1,lambda *args:pool.submit(fail),p)

    def test_immutable_token_reuses_preparation_across_deserialized_requests(self):
        p=PopulationPreparation()
        try:
            with patch('research.vectorized_backtest.v56.distributed_preparation.prepare',return_value='prepared') as prepare:
                a=p.submit([{'x':1}],'same');self.assertEqual(a.result(),'prepared')
                b=p.submit([{'x':1}],'same');self.assertIs(a,b);self.assertEqual(prepare.call_count,1)
                p.submit([{'x':2}],'other').result();self.assertEqual(prepare.call_count,2)
        finally:p.close()

if __name__=='__main__':unittest.main()
