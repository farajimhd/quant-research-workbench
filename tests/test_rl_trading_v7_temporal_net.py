import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch
from research.rl_trading.v7.experiments import temporal_net as net

spec=importlib.util.spec_from_file_location('entry_score_fixture',Path(__file__).with_name('test_rl_trading_v7_entry_score.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
authority=fixtures.authority


def sample():
    torch.set_num_threads(2);torch.manual_seed(1917)
    model=net.TemporalNet(np.zeros(22),np.ones(22)).eval()
    obs=net.shared.observation_batch(fixtures.snapshots(.1))
    obs['account'][:,0]=1
    obs['seq']=np.random.default_rng(7).normal(size=(1,5,7,60,14)).astype(np.float32)
    obs['seq'][...,7:]=1
    return model,obs


def test_initial_baseline_all_masked_finite_and_order_sensitive():
    model,obs=sample()
    batch=net.tensors(obs,[0],'cpu')
    with torch.no_grad():
        result=model(**batch).numpy()
        a,_=model.encode(batch['seq'],batch['features'],batch['account'],batch['candidate_valid'])
        b,_=model.encode(batch['seq'].flip(-2),batch['features'],batch['account'],batch['candidate_valid'])
    np.testing.assert_allclose(result,net.baseline(obs['features'],obs['account']),rtol=1e-6)
    assert not torch.allclose(a,b,rtol=0,atol=1e-6)
    batch['seq'].zero_();batch['candidate_valid'].zero_()
    assert torch.isfinite(model(**batch)).all()


def test_candidate_permutation_and_two_step_gradients():
    model,obs=sample();batch=net.tensors(obs,[0],'cpu')
    permutation=[3,1,4,0,2]
    with torch.no_grad():
        a,_=model.encode(batch['seq'],batch['features'],batch['account'],batch['candidate_valid'])
        b,_=model.encode(batch['seq'][:,permutation],batch['features'][:,permutation],batch['account'],batch['candidate_valid'][:,permutation])
    torch.testing.assert_close(a[:,permutation],b,atol=2e-6,rtol=1e-5)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001)
    for step in range(2):
        optimizer.zero_grad();loss=((model(**batch)-10)/10).square().mean();loss.backward()
        assert model.head[-1].weight.grad.abs().sum()>0
        if step:assert model.bar.weight.grad.abs().sum()>0
        optimizer.step()


def test_bound_clock_and_market_input_guard():
    model,obs=sample()
    snapshot=fixtures.snapshots(.1)
    snapshot['account'][:,3]=12/57600
    parent=SimpleNamespace(model=model,device='cpu',binding={})
    scorer=net.BoundScorer(parent,obs['seq'],snapshot,np.array([0]),np.array([12]))
    observation={k:v.copy() for k,v in snapshot.items() if k in ('features','candidate_valid','account')}
    assert scorer.score(observation).shape==(1,5)
    observation['account'][:,3]=13/57600
    with pytest.raises(ValueError,match='clock'):scorer.score(observation)
    observation['account'][:,3]=12/57600;observation['features'][...,0]+=1
    with pytest.raises(ValueError,match='mismatch'):scorer.score(observation)
    parent=net.Scorer(model,{'contract':{}},SimpleNamespace(days=['2026-01-05']),'cpu')
    with pytest.raises(ValueError,match='TRAINING'):parent.for_session(SimpleNamespace(day='2030-01-01'))


def test_replay_snapshot_loader_never_opens_target_arrays(tmp_path):
    day='2026-01-05';folder=tmp_path/day;folder.mkdir()
    arrays=fixtures.snapshots();arrays['y']=np.array([np.nan])
    files={}
    for key,value in arrays.items():
        path=folder/(key+'.npy');np.save(path,value);files[path.name]=net.shared.sha(path)
    identity={'day':day}
    net.shared.write_json(folder/'complete.json',dict(identity=identity,files=files))
    predictive=SimpleNamespace(root=tmp_path,_identity=lambda _:identity)
    result=net.causal_snapshots(predictive,day)
    assert set(result)=={'features','account','candidate_valid','selection','seconds'}
    (folder/'y.npy').unlink()
    assert set(net.causal_snapshots(predictive,day))==set(result)


def test_actual_main_resume_receipts_and_reload(authority,tmp_path,monkeypatch):
    data,labels=authority
    data.source.root=tmp_path/'source'
    temporal_root=tmp_path/'temporal';temporal_root.mkdir()
    arrays=dict(rows=np.array([0],np.int64),seq=np.zeros((1,5,7,60,14),np.float32))
    for day in data.days:
        folder=temporal_root/day;folder.mkdir()
        receipt=dict(parity_exact=True,parity_scalar_count=490,admitted_candidates=5,files={'seq.npy':'synthetic','rows.npy':'synthetic'})
        net.shared.write_json(folder/'complete.json',receipt)
    temporal=SimpleNamespace(root=temporal_root,predictive=data,days=data.days,binding={'temporal':'synthetic'},load=lambda day:arrays)
    monkeypatch.setattr(net,'TemporalDataset',lambda *_:temporal)
    monkeypatch.setattr(net.shared,'load_forecast',lambda *_:pytest.fail('Fresh model opened forecast'))
    common=['--labels',str(labels),'--device','cpu','--epochs','2','--batch-size','32']
    full=tmp_path/'full';assert net.main(common+['--output',str(full)])==0
    interrupted=tmp_path/'interrupted';save=net.save_epoch
    def stop(root,state):
        save(root,state)
        if state['epoch']==1:(root/'STOP').touch()
    monkeypatch.setattr(net,'save_epoch',stop)
    assert net.main(common+['--output',str(interrupted)])==2
    (interrupted/'STOP').unlink();monkeypatch.setattr(net,'save_epoch',save)
    assert net.main(common+['--output',str(interrupted),'--resume'])==0
    a=torch.load(full/'epoch-002.pt',weights_only=False);b=torch.load(interrupted/'epoch-002.pt',weights_only=False)
    assert a['metrics']==b['metrics']
    for key in a['model']:torch.testing.assert_close(a['model'][key],b['model'][key],atol=0,rtol=0)
    scorer=net.load_bundle(full,data.source,'ignored')
    day=data.days[0];snapshot=data.load(day)
    session=SimpleNamespace(day=day,tickers=['ABC'],selection=np.zeros((13,5),np.int32),
        features=np.repeat(snapshot['features'][...,:98],13,axis=0))
    snapshot['account'][:,3]=12/57600
    monkeypatch.setattr(net,'causal_snapshots',lambda *_:snapshot)
    bound=scorer.for_session(session)
    assert bound.score(snapshot).shape==(1,5)
    path=temporal_root/day/'complete.json';path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='receipt'):scorer.for_session(session)
    with pytest.raises(ValueError,match='resume'):net.main(common+['--output',str(interrupted),'--resume'])
    pointer=json.loads((full/'best.json').read_text())
    with (full/f"epoch-{pointer['epoch']:03d}.pt").open('ab') as stream:stream.write(b'tamper')
    with pytest.raises(ValueError,match='integrity'):net.load_bundle(full,data.source)
