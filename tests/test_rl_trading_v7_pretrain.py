import json
import numpy as np
import torch
from types import SimpleNamespace
from research.rl_trading.v7.experiments import pretrain


def test_actual_pretraining_launcher_and_completed_resume(tmp_path,monkeypatch):
    rng=np.random.default_rng(1)
    arrays=dict(features=rng.normal(size=(8,5,112)).astype(np.float32),
                candidate_valid=np.ones((8,5),bool),account=np.zeros((8,8),np.float32),
                y=rng.normal(size=(8,5,3)).astype(np.float32),target_valid=np.ones((8,5,3),bool))
    class Dataset:
        binding={'synthetic':True}
        fit_days=['2026-01-05'];development_days=['2026-01-06']
        days=fit_days+development_days
        def __init__(self,*args):pass
        def iter_days(self,split):yield ('2026-01-05' if split=='fit' else '2026-01-06'),arrays
        def load(self,day,cache_root=None):
            n=1000;selection=np.full((20,5),-1,np.int32);selection[:,0]=0
            e={k:np.full((n,1),v,dtype=d) for k,v,d in [('mark',10.,float),('bid',9.99,float),
                ('ask',10.01,float),('bid_size',1000.,float),('ask_size',1000.,float),
                ('quote_valid',True,bool),('quote_us',1000000,np.int64)]}
            return SimpleNamespace(day=day,tickers=['A'],clocks=np.arange(1,n+1),selection=selection,
                                   features=np.zeros((20,5,98),np.float32),execution=e)
    monkeypatch.setattr(pretrain,'PredictiveDataset',Dataset)
    monkeypatch.setattr(pretrain,'TrainingDataset',Dataset)
    arguments=['--output',str(tmp_path/'run'),'--device','cpu','--epochs','2','--batch','4']
    assert pretrain.main(arguments)==0
    root=tmp_path/'run'
    manifest_before=(root/'manifest.json').read_bytes()
    saved=torch.load(root/'checkpoint_best.pt',weights_only=False)
    assert len(saved['history'])>=1 and torch.isfinite(saved['policy']['forecast.weight']).all()
    assert saved['policy']['forecast.weight'].abs().sum()>0
    (root/'checkpoint_best.pt').unlink()
    (root/'metrics.json').unlink()
    assert pretrain.main([*arguments,'--resume'])==0
    assert (root/'manifest.json').read_bytes()==manifest_before
    assert (root/'checkpoint_best.pt').exists() and (root/'metrics.json').exists()
    status=json.loads((root/'status.json').read_text())
    assert status['status']=='complete' and status['completed_epochs']==2
    original_save=pretrain.save_checkpoint
    def stop_first_epoch(path,state):
        original_save(path,state)
        if path.name=='checkpoint_latest.pt' and state['epoch']==1:
            (path.parent/'STOP').write_text('controlled epoch stop')
    monkeypatch.setattr(pretrain,'save_checkpoint',stop_first_epoch)
    restart_args=['--output',str(tmp_path/'restarted'),'--device','cpu','--epochs','2','--batch','4']
    assert pretrain.main(restart_args)==2
    (tmp_path/'restarted'/'STOP').unlink()
    monkeypatch.setattr(pretrain,'save_checkpoint',original_save)
    assert pretrain.main([*restart_args,'--resume'])==0
    a=torch.load(root/'checkpoint_latest.pt',weights_only=False)
    b=torch.load(tmp_path/'restarted'/'checkpoint_latest.pt',weights_only=False)
    assert a['history']==b['history']
    for name,value in a['policy'].items():assert torch.equal(value,b['policy'][name]),name
    from research.rl_trading.v7.experiments import predictive_ppo
    monkeypatch.setattr(predictive_ppo,'TrainingDataset',Dataset)
    monkeypatch.setattr(predictive_ppo.train,'TrainingDataset',Dataset)
    replay=tmp_path/'forecast-replay'
    assert predictive_ppo.main(['--forecast',str(root/'checkpoint_best.pt'),'--frozen-forecast','--output',str(replay)])==0
    manifest=json.loads((replay/'manifest.json').read_text())
    assert 'supervised' in manifest['contract']['config']['labels']['initialization']
    assert manifest['contract']['mode']=='frozen-training-replay'
    ppo=tmp_path/'forecast-ppo'
    assert predictive_ppo.main(['--forecast',str(root/'checkpoint_best.pt'),'--output',str(ppo)])==0
    initialized=torch.load(ppo/'checkpoint_initial.pt',weights_only=False)
    refined=torch.load(ppo/'checkpoint_latest.pt',weights_only=False)
    for name,value in initialized['policy'].items():
        if not name.startswith(('buy.','control.','value.')):assert torch.equal(value,refined['policy'][name]),name
