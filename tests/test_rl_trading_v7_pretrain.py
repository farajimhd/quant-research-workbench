import json
import numpy as np
import torch
from research.rl_trading.v7.experiments import pretrain


def test_actual_pretraining_launcher_and_completed_resume(tmp_path,monkeypatch):
    rng=np.random.default_rng(1)
    arrays=dict(features=rng.normal(size=(8,5,112)).astype(np.float32),
                candidate_valid=np.ones((8,5),bool),account=np.zeros((8,8),np.float32),
                y=rng.normal(size=(8,5,3)).astype(np.float32),target_valid=np.ones((8,5,3),bool))
    class Dataset:
        binding={'synthetic':True}
        fit_days=['2026-01-05'];development_days=['2026-01-06']
        def __init__(self,*args):pass
        def iter_days(self,split):yield ('2026-01-05' if split=='fit' else '2026-01-06'),arrays
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
