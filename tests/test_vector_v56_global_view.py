import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
from unittest.mock import patch
import numpy as np
from research.vectorized_backtest.v56.progress_global import combined
from tests.test_vector_v56_progress_navigation import NavigationTests

class GlobalViewTests(unittest.TestCase):
    def test_training_totals_are_aligned_partial_and_exclude_validation(self):
        panel=dict(score=np.array([1.,2.]),net_pnl=np.array([10.,20.]),gross_losses=np.array([1.,2.]),drawdown=np.array([4.,8.]),closed_positions=np.array([2.,2.]),winning_positions=np.array([1.,1.]),eligible=np.ones(2,bool))
        records=[dict(serial=i,node_count=12,operation='parent_mutation',parents=[8,-1],ancestors=[[8,1]],source_sessions=['day']) for i in (1,2)]
        data=dict(generation=1,panels={k:dict(ids=[1,2],panel=panel) for k in ('training:a','training:b','validation:c')})
        report=combined(data,records,24)
        self.assertEqual((report['coverage'],report['partial']),(2,True))
        self.assertEqual(report['candidates'][0]['net_pnl'],20)
        self.assertEqual(report['candidates'][0]['win_rate'],50)
        validation=combined(data,records,12,'validation')
        self.assertEqual(validation['candidates'][0]['net_pnl'],10)
        self.assertEqual(validation['scope'],'global-validation')
        self.assertEqual(validation['groups'][0]['median_net'],15)
        data['panels']['training:b']['ids']=[2,1]
        with self.assertRaises(ValueError):combined(data,records,24)

    def test_global_entry_lineage_from_highlight_and_timings_keep_context(self):
        model=NavigationTests().model();model.view='global_sessions';model.key('a')
        self.assertEqual(model.view,'global_splits');model.key('1')
        self.assertTrue(model.aggregate_mode);self.assertEqual(model.view,'ranking')
        row=dict(serial=5,parents=[2,3],ancestors=[[1,2]],operation='crossover',score=1,net_pnl=2)
        with patch.object(model,'report',return_value=dict(candidates=[row])):
            model.key('l');self.assertEqual(model.view,'lineage');self.assertEqual(model.selected['serial'],5)
            lineage=model.lineage_rows();self.assertEqual(lineage[0]['parents'],'2, 3');self.assertEqual(lineage[1]['parents'],'1')
            model.key('back');self.assertEqual(model.view,'ranking')
        model.global_day='2026-08-13';model.global_role='training';model.key('d')
        self.assertEqual(model.global_day,'2026-08-13');model.key('back');self.assertEqual(model.view,'ranking');model.close()

    def test_split_paths_preserve_groups_and_back_navigation(self):
        import io
        from rich.console import Console
        from research.vectorized_backtest.v56.campaign_progress import render
        m=NavigationTests().model();m.view='global_sessions'
        def report(role):
            value=10 if role=='training' else -7
            row=dict(serial=1,score=value,net_pnl=value,gross_losses=0,drawdown=1,closed_positions=2,nodes=12,operators=['xor'])
            return dict(coverage=1,total=24 if role=='training' else 12,partial=True,candidates=[row],groups=[dict(category='operator',group='xor',best_score=value,median_net=value)])
        with patch.object(m,'aggregate_report',side_effect=report),patch.object(m,'status',return_value={}),patch.object(m,'timing',return_value=dict(last=None,elapsed=None,work=None,eta=None,generation_eta=None)):
            m.key('a')
            for width,height in ((80,24),(160,45)):
                out=io.StringIO();Console(file=out,width=width,height=height,color_system=None).print(render(m,width,height));text=out.getvalue();self.assertIn('Training',text);self.assertIn('Validation',text);self.assertNotIn('\x1b',text)
            m.key('2');self.assertEqual(m.aggregate_role,'validation');self.assertEqual(m.rows()[0]['score'],-7)
            m.key('o');self.assertEqual(m.rows()[0]['best_score'],-7);m.key('\r');self.assertEqual(m.rows()[0]['score'],-7)
            m.key('back');m.key('back');m.key('back');self.assertEqual(m.view,'global_splits')
            m.key('1');m.key('o');self.assertEqual(m.rows()[0]['best_score'],10)
        m.close()
