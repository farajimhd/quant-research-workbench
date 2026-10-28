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
        data['panels']['training:b']['ids']=[2,1]
        with self.assertRaises(ValueError):combined(data,records,24)

    def test_global_entry_lineage_from_highlight_and_timings_keep_context(self):
        model=NavigationTests().model();model.view='global_sessions';model.key('a')
        self.assertTrue(model.aggregate_mode);self.assertEqual(model.view,'ranking')
        row=dict(serial=5,parents=[2,3],ancestors=[[1,2]],operation='crossover',score=1,net_pnl=2)
        with patch.object(model,'report',return_value=dict(candidates=[row])):
            model.key('l');self.assertEqual(model.view,'lineage');self.assertEqual(model.selected['serial'],5)
            lineage=model.lineage_rows();self.assertEqual(lineage[0]['parents'],'2, 3');self.assertEqual(lineage[1]['parents'],'1')
            model.key('back');self.assertEqual(model.view,'ranking')
        model.global_day='2026-08-13';model.global_role='training';model.key('d')
        self.assertEqual(model.global_day,'2026-08-13');model.key('back');self.assertEqual(model.view,'ranking');model.close()
