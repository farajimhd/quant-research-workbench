import copy
import random
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_unmasked_v1.config import Config
from research.bar_gpt.phase2_unmasked_v1.model import Forecaster,future_loss
from research.bar_gpt.phase2_unmasked_v1.run_train import arguments
from research.bar_gpt.phase2_v2.collapse_study import UnmaskedBaseline
from research.bar_gpt.phase2_v2.train import train,load_checkpoint
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_exact_qualified_baseline_and_no_mask_rng_or_metadata_path():
    torch.manual_seed(41);model=Forecaster().eval()
    torch.manual_seed(41);qualified=UnmaskedBaseline().eval()
    x=torch.randn(3,16,512);meta=torch.randn(3,16,9)
    a=model(x,meta);b=qualified(x,None)
    assert all(torch.equal(p,q) for p,q in zip(a,b))
    state=torch.get_rng_state().clone()
    pred,ids=model.training_prediction(x,meta)
    assert torch.equal(state,torch.get_rng_state())
    assert torch.equal(ids,torch.arange(512))
    assert torch.equal(pred,model(x,meta*100)[1])
    with pytest.raises(ValueError):model(x,mask=torch.zeros(512,dtype=torch.bool))


def test_all_future_coordinates_receive_loss_and_target_detached():
    pred=torch.ones(2,512,requires_grad=True);target=torch.zeros(2,512,requires_grad=True)
    loss=future_loss(pred,target,torch.arange(512));loss.backward()
    assert (pred.grad!=0).all() and target.grad is None


def test_strict_architecture_and_launcher():
    with pytest.raises(ValueError):Config(mask_rate=320/512)
    a=arguments(['--dataset-root','unused','--output','D:/TradingML/runtimes/bar_gpt/test-unmasked'])
    assert a.feature_drop_rate==0 and a.epochs==10 and a.validation_every==150000
    with pytest.raises(SystemExit):arguments(['--dataset-root','unused','--output','D:/TradingML/runtimes/bar_gpt/test-unmasked','--feature-drop-rate','.625'])


def test_full_state_resume_and_true_full_passes(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v2 import train as module
    units=[session(tmp_path/'train'),session(tmp_path/'val','2026-01-05')]
    def args(root,resume=None):
        a=options(root,resume);a.epochs=2;a.validation_every=3;a.warmup_updates=0
        a.lr_restart_decay=.9;a.batch_size=64;return a
    def fresh():
        random.seed(4);np.random.seed(4);torch.manual_seed(4)
        return Forecaster(),Teacher()
    contract={'test':'unmasked-full-pass','model':Config().manifest()}
    root=tmp_path/'full';root.mkdir();m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root),Reporter())=='complete'
    expected=load_checkpoint(root/'checkpoint_last.pt',contract)
    root=tmp_path/'resume';root.mkdir();save=module.save_checkpoint;once=[True]
    def crash(path,state):
        save(path,state)
        if state['step']==3 and state['phase']=='train' and once[0]:
            once[0]=False;raise KeyboardInterrupt
    monkeypatch.setattr(module,'save_checkpoint',crash);m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root),Reporter())=='interrupted_preserved_last_checkpoint'
    m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root,root/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(root/'checkpoint_last.pt',contract)
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    assert state_hash(actual)==state_hash(expected)
    assert actual['epoch']==2 and actual['step']==8
    assert [h['step'] for h in actual['history']]==[3,4,6,8]
