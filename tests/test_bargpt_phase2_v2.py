"""Approved v2 shape, masked-gradient, leakage and full-pass recovery checks."""
import copy
import random
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_v2.config import Config,HISTORY
from research.bar_gpt.phase2_v2.model import Forecaster,future_loss
from research.bar_gpt.phase2_v2.train import train,load_checkpoint
from research.bar_gpt.phase2_v2.data import checked_unit,plan_block
from research.bar_gpt.phase2_v2.tokenizer import SessionTokens
from research.bar_gpt.phase2_v2.run_train import arguments
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options,small

@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)

def test_approved_shape_identity_and_no_unapproved_input_paths():
    m=Forecaster();x=torch.randn(2,16,512);meta=torch.randn(2,16,9)
    seen=[]
    hook=m.encoder.stem.register_forward_pre_hook(lambda _,args:seen.append(args[0].shape))
    y,ids=m.training_prediction(x,meta)
    assert y.shape==(2,512) and ids.shape==(192,) and len(ids.unique())==192
    assert seen==[torch.Size([2,192,16])]
    assert len(m.encoder.layers)==6 and m.encoder.layers[0].linear1.out_features==256
    assert not any(k.startswith(('frame.','metadata.','summary','position.')) for k in m.encoder.state_dict())
    m.eval();a,p=m(x,meta);b,q=m(x,meta*1000)
    assert torch.equal(a,b) and torch.equal(p,q) and seen[-1]==(2,512,16)
    assert torch.equal(a,m.encode_context(x))
    hook.remove()

def test_fresh_masks_coordinate_identity_and_gather_blocks_hidden_information():
    m=Forecaster().eval();x=torch.randn(2,16,512);ids=torch.arange(192)
    a=m.forward_visible(x,None,ids)[1]
    changed=x.clone();changed[:,:,192:]+=10000
    assert torch.equal(a,m.forward_visible(changed,None,ids)[1])
    reverse=ids.flip(0)
    torch.testing.assert_close(a,m.forward_visible(x,None,reverse)[1],atol=1e-6,rtol=1e-5)
    assert not torch.equal(a,m.encoder(x[:,:,:192],None,torch.arange(1,193)))
    draws=[m.draw_feature_ids('cpu') for _ in range(3)]
    assert all(len(d.unique())==192 for d in draws) and not torch.equal(draws[0],draws[1])

def test_loss_only_dropped_coordinates_and_target_has_no_gradient():
    pred=torch.randn(3,512,requires_grad=True);target=torch.randn(3,512,requires_grad=True)
    ids=torch.randperm(512)[:192];mask=torch.ones(512,dtype=torch.bool);mask[ids]=False
    loss=future_loss(pred,target,ids)
    torch.testing.assert_close(loss,(pred[:,mask]-target[:,mask]).square().mean())
    loss.backward();assert torch.count_nonzero(pred.grad[:,ids])==0 and target.grad is None
    assert torch.count_nonzero(pred.grad[:,mask])==3*320

def test_copied_data_and_tokenizer_match_v1_exactly(tmp_path):
    from research.bar_gpt.phase2_v1.data import plan_block as old_plan
    from research.bar_gpt.phase2_v1.tokenizer import SessionTokens as OldTokens
    reader=checked_unit(session(tmp_path));p=small(plan_block(reader,0));old=small(old_plan(reader,0))
    assert HISTORY==(512,256,128,64,64)
    for a,b in zip(p.endpoints,old.endpoints):np.testing.assert_array_equal(a,b)
    np.testing.assert_array_equal(p.targets,old.targets)
    t=Teacher();a=SessionTokens(reader,t,'same');b=OldTokens(reader,t,'same')
    a.prepare(p);b.prepare(old)
    for x,y in zip(a.batch(p,0,3),b.batch(old,0,3)):assert torch.equal(x,y)

def test_full_pass_recovery_exact_model_optimizer_rng_and_validation(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v2 import train as module
    units=[session(tmp_path/'train'),session(tmp_path/'val','2026-01-05')]
    def args(root,resume=None):
        a=options(root,resume);a.epochs=2;a.validation_every=3;a.warmup_updates=0
        a.lr_restart_decay=.9;a.batch_size=64;return a
    def fresh():
        random.seed(4);np.random.seed(4);torch.manual_seed(4)
        return Forecaster(),Teacher()
    contract={'test':'v2-full-pass'};full=tmp_path/'full';full.mkdir();m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(full),Reporter())=='complete'
    expected=load_checkpoint(full/'checkpoint_last.pt',contract)
    assert expected['epoch']==2 and expected['step']==8
    assert sum(h['train']['origins'] for h in expected['history'])==386
    assert [h['step'] for h in expected['history']]==[3,4,6,8]
    assert len(list(full.glob('checkpoint_pass_*.pt')))==2
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

def test_cli_fixed_design_and_no_v1_resume_compatibility(tmp_path):
    base=['--dataset-root','unused','--output',str(tmp_path/'run')]
    assert arguments(base).feature_drop_rate==320/512
    for extra in (['--feature-drop-rate','.2'],['--validation-every','0'],['--attention-layout','torch']):
        if extra[-1]=='torch':
            with pytest.raises(ValueError):Config(attention_layout='torch')
        else:
            with pytest.raises(SystemExit):arguments(base+extra)
    with pytest.raises(ValueError):Config(width=512)

def test_execution_bridge_preserves_all_state_and_rejects_scientific_changes():
    from research.bar_gpt.phase2_v2.engineering_resume import prepare_state,prepare_identity
    from research.bar_gpt.v5.wandb_identity_v1 import signature
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    old={'design':Config().manifest(),'implementation':'a'*64,'optimization':{'batch':1024},'dataset':{'hash':'frozen'},'schedule':{'epochs':10}}
    new=copy.deepcopy(old);new['implementation']='b'*64;new['optimization']['student_attention_backend']='cudnn'
    parent=dict(contract=old,model={'w':torch.randn(3)},optimizer={'moment':torch.ones(3)},rng={'torch':torch.get_rng_state()},cursor=(3,4,5),step=12,history=[{'value':.1}])
    before=state_hash(parent);prepared=prepare_state(parent,new)
    assert state_hash(parent)==before
    prepared['contract']=old;assert state_hash(prepared)==before
    a=dict(model=old,batch_size=1024,seed=1);b=dict(model=new,batch_size=1024,seed=1)
    identity=dict(id='fixed',name='same',signature=signature(a),root='root',project='bargpt-phase2')
    updated=prepare_identity(identity,a,b)
    assert updated['id']==identity['id'] and updated['name']==identity['name'] and updated['signature']==signature(b)
    for key,value in [('dataset',{'hash':'changed'}),('schedule',{'epochs':1})]:
        bad=copy.deepcopy(new);bad[key]=value
        with pytest.raises(ValueError):prepare_state(parent,bad)
    bad=copy.deepcopy(new);bad['optimization']['batch']=2048
    with pytest.raises(ValueError):prepare_state(parent,bad)
    with pytest.raises(ValueError):prepare_identity(identity,a,dict(b,seed=2))

def test_metric_reset_preserves_capture_addresses_and_all_values():
    from research.bar_gpt.phase2_v2.train import Metrics
    m=Metrics('cpu');sums=m.sums;moments=m.moments
    p=torch.randn(8,512);t=torch.randn(8,512);m.add(p,t,t)
    m.reset();assert m.count==0 and m.sums is sums and m.moments is moments
    assert torch.count_nonzero(m.sums)==0 and torch.count_nonzero(m.moments)==0
    m.add(p,t,t);other=Metrics('cpu');other.add(p,t,t)
    assert torch.equal(m.sums,other.sums) and torch.equal(m.moments,other.moments)

def test_deferred_checks_keep_exact_values_and_reject_before_publication(tmp_path):
    from research.bar_gpt.phase2_v2.tokenizer import normalize
    from tests.test_bargpt_phase2_latent_v1 import bars
    from research.bar_gpt.v5.shards.sql import clock
    raw=bars(np.arange(1,65)*1_000_000+clock('2023-01-03',4))
    values=torch.tensor(raw['values'].copy())[None];valid=torch.ones_like(values,dtype=torch.bool)
    checks=[];a=normalize(values,valid);b=normalize(values,valid,_checks=checks)
    for x,y in zip(a,b):assert torch.equal(x,y)
    assert all(bool(v[0]) for v in checks)
    bad=values.clone();bad[0,0,0]=0;checks=[]
    normalize(bad,valid,_checks=checks)
    assert not all(bool(v[0]) for v in checks)
    with pytest.raises(ValueError):normalize(bad,valid)
    reader=checked_unit(session(tmp_path));plan=small(plan_block(reader,0));teacher=Teacher()
    original=teacher.encode_full
    teacher.encode_full=lambda *args,**kwargs:original(*args,**kwargs)*torch.nan
    prepared=SessionTokens(reader,teacher,'same')
    with pytest.raises(FloatingPointError,match='Nonfinite'):prepared.prepare(plan)
    assert prepared.validation_checks is None
