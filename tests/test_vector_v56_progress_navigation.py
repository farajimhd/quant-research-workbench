import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,unittest
from unittest.mock import patch
from pathlib import Path
from rich.console import Console
from research.vectorized_backtest.v56.campaign_progress import Model,render,read_key,replay_progress,sorted_rows

class NavigationTests(unittest.TestCase):
    def model(self):
        with patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):
            return Model(dict(campaign='D:/TradingML/runtimes/fixture',transport='D:/TradingML/runtimes/fixture',workstation_share='fixture',training_days=['2026-08-13'],machines={'laptop':dict(session_days=['2026-08-13'])}))

    def test_extended_keys_are_consumed_not_letter_shortcuts(self):
        for scan,expected in [('H','k'),('P','j'),('M','\r'),('K','back'),('I','b'),('Q','n'),('O','end')]:
            source=iter(['\xe0',scan]);self.assertEqual(read_key(lambda:next(source)),expected)
            with self.assertRaises(StopIteration):next(source)
        self.assertEqual(read_key(lambda:'\x1b'),'back')

    def test_group_sort_members_details_and_back(self):
        model=self.model();model.day='2026-08-13';model.view='ranking'
        members=[dict(serial=1,score=10,net_pnl=20,gross_losses=2,drawdown=4,operators=['xor'],generation=3),dict(serial=2,score=3,net_pnl=30,gross_losses=3,drawdown=12,operators=['and'],generation=3)]
        groups=[dict(category='operator',group=r['operators'][0],best_score=r['score'],median_net=r['net_pnl']) for r in members]
        with patch.object(model,'report',return_value=dict(generation=3,candidates=members,groups=groups)),patch.object(model,'status',return_value=dict(training_ranking=members)):
            model.key('o');self.assertEqual(model.rows()[0]['group'],'xor')
            model.key('s');self.assertEqual(model.rows()[0]['group'],'and')
            model.key('r');self.assertEqual(model.rows()[0]['group'],'xor')
            model.key('\r');self.assertEqual([r['serial'] for r in model.rows()],[1])
            model.key('\r');self.assertEqual(model.selected['serial'],1);self.assertEqual(model.view,'detail')
            model.key('back');self.assertEqual(model.view,'candidates')
            model.key('back');self.assertEqual(model.view,'operators')
            model.key('back');self.assertEqual(model.view,'ranking')
        self.assertEqual([r['nodes'] for r in sorted_rows([dict(nodes=None),dict(nodes=2),dict(nodes=1)],'nodes',False)],[1,2,None])

    def test_progress_visible_normal_compact_and_redirected(self):
        model=self.model();model.day='2026-08-13';model.view='ranking'
        state=dict(status='running',completed_generations=25,stage='Session training',worker_progress=dict(stage='Replay positions',completed_seconds=50,total_seconds=100,completed_local_batches=1,total_local_batches=2))
        self.assertIn('75%',replay_progress(state['worker_progress']).plain)
        with patch.object(model,'status',return_value=state),patch.object(model,'report',return_value={}),patch.object(model,'timing',return_value=dict(last=10,elapsed=5,work=250,eta=250,generation_eta=5)):
            for width,height in ((80,24),(160,45)):
                out=io.StringIO();console=Console(file=out,width=width,height=height,color_system=None)
                console.print(render(model,width,height));text=out.getvalue()
                self.assertIn('Generations',text);self.assertIn('50%',text);self.assertIn('75%',text);self.assertIn('SESSION CANDIDATES',text);self.assertNotIn('\x1b',text)

if __name__=='__main__':unittest.main()
