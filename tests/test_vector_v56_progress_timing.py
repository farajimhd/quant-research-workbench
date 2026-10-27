import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from rich.console import Console
from research.vectorized_backtest.v56.campaign_progress import Model,render,duration

ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/progress-timing')
ROOT.mkdir(parents=True,exist_ok=True)

class TimingTests(unittest.TestCase):
    def test_sealed_work_same_source_eta_and_cached_history(self):
        with TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);job=dict(campaign=tmp,transport=tmp,workstation_share='fixture',source_sha256='new')
            with patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):model=Model(job)
            for g,seconds,source in ((1,600,'new'),(2,60,'old'),(3,90,'new'),(4,120,'new')):
                f=root/'global'/f'generation-{g:04d}';f.mkdir(parents=True);(f/'complete.json').write_text(json.dumps(dict(wall_seconds=seconds,source_sha256=source)))
            f=root/'global/generation-0005';f.mkdir();p=f/'population.json';p.write_text('{}');os.utime(p,(970,970))
            state=dict(completed_generations=4,owner=dict(creation_time=800))
            with patch('research.vectorized_backtest.v56.campaign_progress.time.time',return_value=1000):
                first=model.timing(root,state,running=True)
                self.assertEqual((first['work'],first['elapsed'],first['last'],first['rate']),(870,30,120,105))
                self.assertEqual(first['generation_eta'],75);self.assertEqual(first['eta'],4800)
                with patch.object(model,'read',side_effect=AssertionError('Sealed history must remain cached')):
                    self.assertEqual(model.timing(root,state,running=True),first)
                    self.assertIsNone(model.timing(root,state)['eta'])
            warm=model.timing(root,dict(completed_generations=1),running=True)
            self.assertIsNone(warm['eta'])

    def test_compact_timing_table_and_selected_background(self):
        with TemporaryDirectory(dir=ROOT) as tmp:
            job=dict(campaign=tmp,transport=tmp,workstation_share='fixture',source_sha256='new',training_days=['2026-08-07'],machines={'laptop':dict(session_days=['2026-08-07'])})
            with patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):
                model=Model(job)
                for width,height in ((80,24),(160,45)):
                    output=io.StringIO();console=Console(file=output,width=width,height=height,color_system=None)
                    console.print(render(model,width,height));value=output.getvalue()
                    self.assertIn('ETA',value);self.assertIn('Work' if width<120 else 'Session work',value);self.assertNotIn('\x1b',value)
                console=Console(file=io.StringIO(),width=160,height=45,force_terminal=True,color_system='truecolor',no_color=False)
                segments=list(console.render(render(model,160,45)))
                self.assertTrue(any(s.style and s.style.bgcolor and s.style.bgcolor.name=='blue' for s in segments))
                model.key('\r');model.key('d');self.assertEqual(model.view,'timings')
                self.assertEqual(model.rows(),[])
        self.assertEqual(duration(None),'-');self.assertEqual(duration(3661),'1h01m')

if __name__=='__main__':unittest.main()
