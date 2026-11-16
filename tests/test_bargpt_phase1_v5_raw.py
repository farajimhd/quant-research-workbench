from dataclasses import fields,replace
from types import SimpleNamespace
import json
import numpy as np
import pytest
import torch
from tests.test_bargpt_phase1_v5 import raw
from research.bar_gpt.phase1_v5_raw.data import prepare
from research.bar_gpt.phase1_v5_raw.model import Config,Autoencoder
from research.bar_gpt.phase1_v5_raw.objectives import Stats,fit_stats,loss


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def stats():return fit_stats([(*raw(),{'split':'train','sha256':'fixture'})],'fixture')


def test_compute_guard_excludes_viewers():
    from research.bar_gpt.phase1_v5_raw.run import v5_compute_command
    assert v5_compute_command(['python','-m','research.bar_gpt.phase1_v5.run'])
    assert v5_compute_command(['python',r'D:\codes\research\bar_gpt\phase1_v5_raw\run.py'])
    assert not v5_compute_command(['python',r'D:\codes\research\bar_gpt\phase1_v5\panel.py','--run','D:/phase1_v5_raw'])


@pytest.mark.parametrize('full',[False,True])
def test_raw_targets_inputs_time_and_chunk_parity(full):
    from research.bar_gpt.v5.transforms_v1 import BarInputTransformV1
    v,m,c,f=raw(259);m[:2,:,6:]=False;v[:2,:,6:]=float('nan')
    cfg=Config(feature_drop=0);s=stats();g=torch.Generator().manual_seed(12)
    b=prepare(v,m,c,f,s,g,cfg,full=full,native=True)
    other=prepare(v,m,c,f,s,torch.Generator().manual_seed(12),cfg,full=full,native=True,chunk_size=259)
    for field in fields(b):torch.testing.assert_close(getattr(b,field.name),getattr(other,field.name),rtol=0,atol=0,equal_nan=True)
    take=lambda x,ids:x.gather(1,ids[...,None].expand(-1,-1,x.shape[-1]))
    assert torch.equal(b.target,take(torch.where(m,v,0),b.target_positions))
    assert torch.equal(b.visible[...,:18],take(torch.where(m,v,0),b.positions))
    time=BarInputTransformV1()(v,m,c,f)
    assert torch.equal(b.visible[...,18:],take(time.values[...,18:],b.positions))
    assert b.visible.shape[1]==(64 if full else 16)
    assert not any('reference' in x.name for x in fields(b))


def test_feature_dropout_does_not_modify_target_or_time():
    args=raw(512);s=stats()
    a=prepare(*args,s,torch.Generator().manual_seed(9),Config(feature_drop=0))
    b=prepare(*args,s,torch.Generator().manual_seed(9),Config())
    assert torch.equal(a.target,b.target) and torch.equal(a.visible[...,18:],b.visible[...,18:])
    assert .06<float((~b.valid[...,:18]).float().mean())<.09
    assert torch.equal(b.visible[...,:18][b.valid[...,:18]],a.visible[...,:18][b.valid[...,:18]])


def test_loss_is_raw_huber_weighted_and_ignores_invalid_targets():
    s=Stats(torch.arange(1,19,dtype=torch.float64),'s',[{'split':'train'}])
    b=prepare(*raw(2),s,torch.Generator().manual_seed(1),Config())
    p=(b.target+2).detach().requires_grad_();value,terms=loss(p,b,s)
    weights=(1.5/s.market_scale).reshape(3,6)
    torch.testing.assert_close(terms,torch.stack((weights[:,:4].mean(),weights[:,4:].mean())).float())
    valid=b.target_valid.clone();valid[...,6:]=False
    modified=replace(b,target_valid=valid);value,_=loss(p,modified,s);value.backward()
    assert not p.grad[...,6:].any() and p.grad[...,:6].abs().sum()>0
    changed=replace(b,target=b.target*1000)
    # Fixed weights do not transform the actual inputs or targets.
    assert loss(p,changed,s)[0]>value


def test_training_only_calibration_and_no_reference_parameters():
    with pytest.raises(ValueError):fit_stats([(*raw(),{'split':'validation'})],'s')
    with pytest.raises(ValueError):Stats.load(dict(market_scale=[1]*18,split_hash='s',evidence=[{'split':'train'}],reference_scale=[1]*7))
    model=Autoencoder(Config(layers=1));b=prepare(*raw(2),stats(),torch.Generator().manual_seed(2),model.config)
    assert not any('reference' in n for n,_ in model.named_parameters())
    with torch.autocast('cpu',dtype=torch.bfloat16):z,p=model.from_batch(b)
    assert z.shape==(2,256) and p.shape==(2,48,18) and p.dtype==torch.float32
    # This CPU lacks BF16 backward support; test gradients through FP32 separately.
    _,p=model.from_batch(b)
    loss(p,b,stats())[0].backward();assert model.encoder.values.weight.grad.abs().sum()>0


def manifest(tmp_path):
    from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE
    from research.bar_gpt.v5.shards.contract import file_hash
    rows=np.zeros(8,dtype=SAMPLE_DTYPE);v,m,c,f=raw()
    for name,value in [('values',v),('valid',m),('end_us',c),('timeframe_us',f)]:rows[name]=value.numpy()
    path=tmp_path/'raw.npy';np.save(path,rows)
    entry=dict(path=str(path),start=0,stop=8,bytes=path.stat().st_size,sha256=file_hash(path))
    return {'splits':{'train':[entry],'validation':[entry]}}


def args(output):return SimpleNamespace(output=output,device='cpu',precision='fp32',batch_size=4,
    workers=0,prefetch=1,seed=17,shard_prefetch=0,shard_prefetch_mib=64,prepare_threads=1,
    learning_rate=3e-4,min_learning_rate=5e-5,warmup_updates=0,wandb_mode='disabled',resume=None,
    cuda_graph=False,log_every=1,checkpoint_every=1,train_steps=0,validation_steps=0,epochs=1)


def test_real_loader_training_validation_and_exact_resume(tmp_path):
    from research.bar_gpt.phase1_v5_raw.engine import train
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    cfg=Config(layers=1);contract=cfg.contract();m=manifest(tmp_path);s=stats()
    a=args(tmp_path/'resumed');a.output.mkdir();torch.manual_seed(7);model=Autoencoder(cfg)
    assert train(model,m,s,contract,a,lambda **kw:None)=='complete'
    saved=torch.load(a.output/'checkpoint_last.pt',weights_only=False)
    assert saved['step']==2 and saved['history'][0]['validation']['origins']==8
    validation=saved['history'][0]['validation']
    assert len(validation['masked_families'])==2 and 'reference_raw_mae' not in validation
    assert validation['full_raw_mae']['1s']['trade_close']>0
    a.resume=a.output/'checkpoint_last.pt';a.epochs=2
    assert train(Autoencoder(cfg),m,s,contract,a,lambda **kw:None)=='complete'
    final=torch.load(a.output/'checkpoint_last.pt',weights_only=False)
    b=args(tmp_path/'continuous');b.output.mkdir();b.epochs=2;torch.manual_seed(7)
    assert train(Autoencoder(cfg),m,s,contract,b,lambda **kw:None)=='complete'
    other=torch.load(b.output/'checkpoint_last.pt',weights_only=False)
    for key in ('model','optimizer','rng','cursor','step','scheduler'):assert state_hash(final[key])==state_hash(other[key]),key
    from research.bar_gpt.phase1_v5_raw.reporting import categorized
    metrics=categorized(validation,1,2)
    assert 'validation_loss/masked_activity' in metrics
    assert not any('reference' in key for key in metrics)


def test_profile_real_loader(tmp_path):
    from research.bar_gpt.phase1_v5_raw.profiling import run
    a=args(tmp_path);a.batches='4';a.steps=1;a.warmup=0
    result=run(manifest(tmp_path),stats(),Config(layers=1),a,lambda **kw:None,None)
    assert result['trials'][0]['windows']==4 and result['trials'][0]['windows_per_second']>0


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA unavailable')
def test_cuda_graph_matches_eager_gradients_and_survives_validation():
    from research.bar_gpt.phase1_v5_raw.replay import CudaStep
    from copy import deepcopy
    old=torch.are_deterministic_algorithms_enabled()
    torch.use_deterministic_algorithms(True)
    try:
        torch.manual_seed(8);model=Autoencoder(Config(layers=1)).cuda();eager=deepcopy(model)
        s=stats().to('cuda');b=prepare(*raw(2),stats(),torch.Generator().manual_seed(3),model.config).to('cuda')
        replay=CudaStep(model,2,s)
        for _ in range(2):
            eager.zero_grad(set_to_none=True)
            with torch.autocast('cuda',dtype=torch.bfloat16):_,p=eager.from_batch(b);value,terms=loss(p,b,s)
            assert p.dtype==torch.float32
            value.backward();norm=torch.nn.utils.clip_grad_norm_(eager.parameters(),1.)
            actual=replay(b)
            for a,e in zip(actual,(value,terms,norm)):torch.testing.assert_close(a,e,rtol=1e-5,atol=1e-6)
            for a,e in zip(model.parameters(),eager.parameters()):torch.testing.assert_close(a.grad,e.grad,rtol=1e-5,atol=1e-6)
            full=prepare(*raw(2),stats(),torch.Generator().manual_seed(3),model.config,full=True).to('cuda')
            model.eval()
            with torch.no_grad(),torch.autocast('cuda',dtype=torch.bfloat16):model.from_batch(full)
            model.train()
    finally:torch.use_deterministic_algorithms(old)
