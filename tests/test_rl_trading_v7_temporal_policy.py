import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from research.rl_trading.v7.experiments import temporal_policy as policy

spec=importlib.util.spec_from_file_location('entry_score_fixture',Path(__file__).with_name('test_rl_trading_v7_entry_score.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
authority=fixtures.authority


def test_analytic_loss_mask_and_gradient():
    scores=torch.zeros((1,5),requires_grad=True)
    known=torch.tensor([[True,False,False,False,False]])
    targets=torch.tensor([[10.,100000.,-9999.,0.,0.]])
    loss=policy.objective(scores,targets,known)
    assert loss.item()==pytest.approx(-.5-.001*np.log(2),abs=1e-7)
    loss.backward()
    assert scores.grad[0,0]<0 and scores.grad[0,1:].count_nonzero()==0
    empty=policy.objective(scores.detach(),targets,torch.zeros_like(known))
    assert empty==0
    metrics=policy.policy_metrics(np.zeros((1,5)),targets.numpy(),known.numpy(),np.array(['day']))
    assert metrics['greedy_net_per_decision']==0 and metrics['selected_count']==0
    assert metrics['stochastic_counterfactual_net_per_decision']==5


def test_reset_body_and_target_isolation(monkeypatch):
    torch.set_num_threads(2);torch.manual_seed(1917)
    initial=policy.TemporalNet(np.zeros(22),np.ones(22))
    with torch.no_grad():initial.head[-1].weight.fill_(.4);initial.head[-1].bias.fill_(2)
    monkeypatch.setattr(policy.temporal,'load_bundle',lambda *a,**k:SimpleNamespace(model=initial,binding={'initial':'bound'}))
    model,binding=policy.initial_model('unused',None,'cpu')
    torch.testing.assert_close(model.bar.weight,initial.bar.weight)
    assert model.head[-1].weight.count_nonzero()==0 and model.head[-1].bias.count_nonzero()==0
    obs=fixtures.snapshots(.1);obs['y']=np.full((1,5),999.)
    batch=policy.shared.observation_batch(obs)
    assert 'y' not in batch
    batch['seq']=np.zeros((1,5,7,60,14),np.float32)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001)
    for step in range(2):
        optimizer.zero_grad();scores=model(**policy.tensors(batch,[0],'cpu'))
        loss=policy.objective(scores,torch.ones((1,5))*10,torch.ones((1,5),dtype=torch.bool))
        loss.backward()
        assert torch.isfinite(loss) and model.head[-1].weight.grad.abs().sum()>0
        if step:assert model.cls.grad.abs().sum()>0
        optimizer.step()


def test_actual_main_resume_loader_and_receipts(authority,tmp_path,monkeypatch):
    data,labels=authority;data.source.root=tmp_path/'source'
    temporal_root=tmp_path/'temporal';temporal_root.mkdir()
    arrays=dict(rows=np.array([0],np.int64),seq=np.zeros((1,5,7,60,14),np.float32))
    for day in data.days:
        folder=temporal_root/day;folder.mkdir()
        policy.shared.write_json(folder/'complete.json',dict(parity_exact=True,parity_scalar_count=490,
            admitted_candidates=5,files={'seq.npy':'synthetic','rows.npy':'synthetic'}))
    temporal=SimpleNamespace(root=temporal_root,predictive=data,days=data.days,binding={'temporal':'synthetic'},load=lambda _:arrays)
    monkeypatch.setattr(policy,'TemporalDataset',lambda *_:temporal)
    x=np.concatenate([np.concatenate((np.full((4,14),i+1.),np.zeros((4,8))),1) for i in range(120)])
    mean,std=x.mean(0),x.std(0);std[std==0]=1
    torch.manual_seed(10)
    initial=policy.TemporalNet(mean,std)
    init_binding=dict(checkpoint='synthetic',contract=dict(temporal_receipts=policy.temporal_receipts(temporal),
        label_receipts=policy.shared.EntryLabels(data,labels).receipts,snapshots_binding=data.binding))
    monkeypatch.setattr(policy.temporal,'load_bundle',lambda *a,**k:SimpleNamespace(model=initial,binding=init_binding))
    common=['--labels',str(labels),'--device','cpu','--epochs','2','--batch-size','32']
    full=tmp_path/'full';assert policy.main(common+['--output',str(full)])==0
    interrupted=tmp_path/'interrupted';save=policy.save_epoch
    def stop(root,state):
        save(root,state)
        if state['epoch']==1:(root/'STOP').touch()
    monkeypatch.setattr(policy,'save_epoch',stop)
    assert policy.main(common+['--output',str(interrupted)])==2
    (interrupted/'STOP').unlink();monkeypatch.setattr(policy,'save_epoch',save)
    assert policy.main(common+['--output',str(interrupted),'--resume'])==0
    a=torch.load(full/'epoch-002.pt',weights_only=False);b=torch.load(interrupted/'epoch-002.pt',weights_only=False)
    assert a['metrics']==b['metrics']
    for key in a['model']:torch.testing.assert_close(a['model'][key],b['model'][key],atol=0,rtol=0)
    assert a['metrics'][0]['development']['greedy_net_per_decision']==0
    scorer=policy.load_bundle(full,data.source,'unused')
    assert scorer.row_step==10;json.dumps(scorer.binding)
    with pytest.raises(ValueError,match='TRAINING'):scorer.for_session(SimpleNamespace(day='2030-01-01'))
    path=temporal_root/data.days[0]/'complete.json';path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='receipt'):scorer.for_session(SimpleNamespace(day=data.days[0]))
    with pytest.raises(ValueError,match='sealed'):policy.main(common+['--output',str(interrupted),'--resume'])
    pointer=json.loads((full/'best.json').read_text())
    with (full/f"epoch-{pointer['epoch']:03d}.pt").open('ab') as f:f.write(b'tamper')
    with pytest.raises(ValueError,match='integrity'):policy.load_bundle(full,data.source)
