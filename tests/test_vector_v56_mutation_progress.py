import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import io,json,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from rich.console import Console
from research.vectorized_backtest.v56.campaign_progress import Model,render,replay_numbers
RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/qualification/mutation-progress')
RUNTIME.mkdir(parents=True,exist_ok=True)
class MutationProgressTests(unittest.TestCase):
    def test_diagnostic_scope_progress_receipts_and_navigation(self):
        with TemporaryDirectory(dir=RUNTIME) as tmp,patch('research.vectorized_backtest.v56.campaign_progress.remote',side_effect=lambda p,s:Path(p)):
            root=Path(tmp);folder=root/'phase-2/global/generation-0002';folder.mkdir(parents=True);(folder/'population.json').write_text('{}')
            job=dict(campaign=tmp,transport=tmp,workstation_share='fixture',training_days=['a','b'],validation_days=['c'],machines={},mutation_probe_size=32)
            state=dict(stage='Paired mutation diagnostic; selection and RNG isolated',status='running',completed_generations=1,replay_generation=2,replay_role='training',owner=dict(creation_time=1),active_sessions=['a'],updated_epoch=time.time(),session_paths={'a':dict(total_candidates=32,batch_candidates=32,completed_candidates=0,completed_seconds=50,total_seconds=100,session_started_epoch=time.time()-10,updated_epoch=time.time(),stage='Replay positions')})
            status=root/'phase-2/full-status.json';status.write_text(json.dumps(state))
            main=root/'phase-2/replays/training/generation-0002/a/receipt.json';main.parent.mkdir(parents=True);main.write_text('{}')
            model=Model(job)
            try:
                self.assertEqual(model.global_replays()['training:a']['status'],'complete')
                rows=model.diagnostic_replays();self.assertEqual(replay_numbers(rows[0])['fraction'],.5);self.assertEqual(rows[1]['status'],'queued')
                probe=root/'phase-2/mutation-probe-replay/training/generation-0002/b/receipt.json';probe.parent.mkdir(parents=True);probe.write_text('{}')
                self.assertEqual(model.diagnostic_replays()[1]['status'],'complete')
                model.view='global_sessions';model.key('x');self.assertEqual(model.view,'diagnostic_sessions')
                with patch.object(model,'timing',return_value=dict(last=20,elapsed=10,work=20,eta=None,generation_eta=None)):
                    for width,height in ((80,24),(160,45)):
                        out=io.StringIO();Console(file=out,width=width,height=height,color_system=None).print(render(model,width,height));text=out.getvalue()
                        self.assertIn('MUTATION DIAGNOSTIC',text);self.assertIn('50%',text);self.assertIn('1/2',text);self.assertNotIn('\x1b',text)
                model.key('back');self.assertEqual(model.view,'global_sessions')
                state['stage']='Phase 2 validation: display only';state['owner']['creation_time']=2;status.write_text(json.dumps(state));model.cache.clear()
                self.assertFalse(model.diagnostic_replays()[0]['progress'])
            finally:model.close()
