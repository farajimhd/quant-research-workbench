import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import hashlib,io,json,threading,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from rich.console import Console
from research.vectorized_backtest.v56.progress_sessions import load_global
from research.vectorized_backtest.v56.campaign_progress import Model,render

ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/progress-sessions')
ROOT.mkdir(parents=True,exist_ok=True)

class SessionTests(unittest.TestCase):
    def test_sealed_train_validation_summary_navigation_and_integrity(self):
        with TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);folder=root/'phase-2/global/generation-0001';folder.mkdir(parents=True)
            job=dict(campaign=tmp,transport=tmp,workstation_share='fixture',training_days=['2026-08-13'],validation_days=['2026-09-01'],objective=dict(drawdown_weight=2.,loss_weight=1.),machines={'laptop':dict(session_days=['2026-08-13'])})
            def write(path,value):
                raw=json.dumps(value).encode();path.write_bytes(raw);return hashlib.sha256(raw).hexdigest()
            seal={}
            for role in ('training','validation'):
                day=job[role+'_days'][0];path=folder/(role+'-receipt.json')
                metrics=dict(net_pnl=[100,-20,999],drawdown=[10,30,0],closed_positions=[2,1,0],winning_positions=[1,0,0],terminal_valid=[True,True,False],episode_profits=[[110,-10],[-20],[]],episode_exit_clocks=[[1,2],[2],[]],maximum_position_risk=[0,0,0],maximum_stop_distance=[0,0,0])
                receipt=dict(day=day,split=role,population_sha256='p',full_session=True,validation_opened=role=='validation',metrics=metrics,evaluated_candidates=3)
                receipt_hash=write(path,receipt)
                evidence=dict(ids=[1,2,3],population_sha256='p',display_only=role=='validation',receipts={day:dict(receipt=str(path),receipt_sha256=receipt_hash)})
                seal[role+'_sha256']=write(folder/(role+'.json'),evidence)
            write(folder/'complete.json',seal)
            data=load_global(job,folder.parent,1,threading.Event())
            s=data['summaries']['training:2026-08-13']['strength']
            self.assertEqual((s['top_net'],s['median_net'],s['top_drawdown'],s['top_win_rate'],s['profitable_fraction'],s['best_score']),(100,40,10,50,50,70))
            with patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):
                model=Model(job);model.global_data=data;model.key('g')
                for width,height in ((80,24),(160,45)):
                    out=io.StringIO();Console(file=out,width=width,height=height,color_system=None).print(render(model,width,height))
                    text=out.getvalue();self.assertIn('VAL*',text);self.assertIn('2026-08-13',text);self.assertIn('100.00',text);self.assertNotIn('\x1b',text)
                model.key('j');model.key('\r');self.assertEqual(model.global_role,'validation');self.assertEqual(model.rows()[0]['metric_scope'],'validation:2026-09-01')
                model.key('s');self.assertEqual(model.rows()[0]['serial'],3)
                model.close();self.assertTrue(model.observer_stop.is_set())
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'evidence changed'):load_global(job,folder.parent,1,threading.Event())

if __name__=='__main__':unittest.main()
