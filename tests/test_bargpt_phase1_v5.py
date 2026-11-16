from dataclasses import replace
from types import SimpleNamespace
import json
import numpy as np
import pytest
import torch
from research.bar_gpt.phase1_v5.model import Config,Autoencoder
from research.bar_gpt.phase1_v5.data import prepare,normalize_raw
from research.bar_gpt.phase1_v5.objectives import fit_stats,loss,reconstruct
from research.bar_gpt.phase1_v5.panel import render


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def raw(n=8):
    from tests.test_bargpt_phase2_latent_v1 import bars
    from research.bar_gpt.v5.shards.sql import clock
    source=bars(np.arange(1,65)*1_000_000+clock('2023-01-03',4))
    v=torch.tensor(source['values'].copy()).repeat(n,1,1)
    v*=torch.linspace(.8,1.2,n)[:,None,None]
    m=torch.ones_like(v,dtype=torch.bool)
    return v,m,torch.tensor(source['end_us'].copy()).repeat(n,1),torch.full((n,),1_000_000,dtype=torch.int64)


def stats():return fit_stats([(*raw(),{'split':'train','sha256':'fixture'})],'fixture-split')


def batch(n=8,full=False,feature_drop=.075):
    return prepare(*raw(n),stats(),torch.Generator().manual_seed(2),Config(layers=1,feedforward=64,feature_drop=feature_drop),full=full,native=full)


def test_bar_removal_positions_and_feature_dropout():
    b=batch(512);assert b.visible.shape==(512,16,27) and b.target.shape==(512,48,18)
    assert torch.equal(torch.cat((b.positions,b.target_positions),1).sort(1).values,torch.arange(64).expand(512,-1))
    dropped=(~b.valid[...,:18]).float().mean();assert .06<float(dropped)<.09
    assert torch.equal(b.valid[...,:18],b.valid[:,:1,:18].expand(-1,16,-1))
    assert not torch.count_nonzero(b.visible[~b.valid])
    assert batch(full=True).visible.shape==(8,64,27)
    assert batch(full=True).valid[...,:18].all()


def test_references_normalization_and_inverse_match_source():
    b=batch(full=True);s=stats();actual=raw()[0]
    reconstructed,refs=reconstruct(b.target,b.reference_target,s)
    torch.testing.assert_close(reconstructed,actual.double(),rtol=2e-5,atol=2e-5)
    assert refs.shape==(8,7)
    assert torch.equal(b.reference_input,b.reference_target)
    from research.bar_gpt.phase2_v2.tokenizer import normalize
    expected,_=normalize(*raw()[:2]);assert torch.equal(expected,b.target)
    from research.bar_gpt.v5.pretraining_v3 import local_references
    price,quantity=local_references(*raw()[:2])
    assert torch.equal(b.native_reference,torch.cat((price.reshape(-1,1),quantity),1))


def test_two_prepare_threads_preserve_batches_and_rng():
    from dataclasses import fields
    v,m,c,f=raw(37)
    f=torch.tensor([100_000,1_000_000,10_000_000,60_000_000,3_600_000_000,86_400_000_000]*7)[:37]
    m[::2,:,6:12]=False
    old=torch.get_num_threads();s=stats();config=Config(width=128,embedding_width=256,feedforward=512)
    try:
        for full in (False,True):
            results=[]
            for count in (1,2):
                torch.set_num_threads(count);g=torch.Generator().manual_seed(71)
                results.append((prepare(v,m,c,f,s,g,config,full=full,native=full),g.get_state()))
            for field in fields(results[0][0]):
                assert torch.equal(getattr(results[0][0],field.name),getattr(results[1][0],field.name)),field.name
            assert torch.equal(results[0][1],results[1][1])
    finally:torch.set_num_threads(old)


def test_resumed_validation_keeps_optimizer_step_on_next_open_reporting_row():
    from research.bar_gpt.phase1_v5.engine import report
    calls=[]
    reporter=SimpleNamespace(step=101,log=lambda metrics,**kwargs:calls.append((metrics,kwargs)))
    report(reporter,{'validation/masked_objective':.2},100)
    assert calls==[({'validation/masked_objective':.2,'optimizer_step':100},{'step':101})]
    report(reporter,{'loss':.1},110)
    assert calls[-1]==({'loss':.1,'optimizer_step':110},{'step':110})


def test_training_only_calibration_and_sparse_evidence():
    with pytest.raises(ValueError,match='training-only'):
        fit_stats([(*raw(),{'split':'validation'})],'x')
    v,m,c,f=raw();m[:,:,6:]=False;v[:,:,6:]=0
    with pytest.raises(ValueError,match='lacks observed'):
        fit_stats([(v,m,c,f,{'split':'train'})],'x')


def test_only_masked_labels_and_reference_bottleneck():
    torch.manual_seed(1);model=Autoencoder(Config(layers=1,feedforward=64));b=batch();s=stats()
    z,p,r=model.from_batch(b);z.retain_grad();value,terms=loss(p,r,b,s);value.backward()
    assert z.shape==(8,512) and p.shape==(8,48,18) and r.shape==(8,7)
    assert z.grad is not None and z.grad.abs().sum()>0
    assert model.encoder.references.weight.grad.abs().sum()>0
    assert terms.shape==(3,) and torch.isfinite(value)
    # Decoder has no raw values/reference argument or skip path.
    fixed=torch.zeros_like(z);a=model.decoder(fixed,b.target_positions)
    altered=replace(b,reference_input=b.reference_input+1)
    assert not torch.equal(model.from_batch(altered)[0],z)
    torch.testing.assert_close(model.decoder(fixed,b.target_positions)[1],a[1])
    b2=replace(b,target=b.target+1)
    torch.testing.assert_close(model.from_batch(b2)[1],p)
    assert loss(p,r,b2,s)[0]!=value


@pytest.mark.parametrize('width',[128,256])
def test_approved_narrow_variants_export_512(width):
    model=Autoencoder(Config(width=width,layers=1,feedforward=4*width));b=batch(2)
    z,p,r=model.from_batch(b)
    assert z.shape==(2,512) and p.shape==(2,48,18) and r.shape==(2,7)
    assert model.encoder.blocks[0].self_attn.embed_dim==width
    assert model.config.contract()['embedding_width']==512
    loss(p,r,b,stats())[0].backward()
    assert model.encoder.export[0].weight.grad.abs().sum()>0


@pytest.mark.parametrize('width',[128,256])
def test_approved_256_output_variants(width):
    model=Autoencoder(Config(width=width,embedding_width=256,layers=1,feedforward=4*width));b=batch(2)
    z,p,r=model.from_batch(b)
    assert z.shape==(2,256) and model.decoder.summary.in_features==256
    assert model.config.contract()['embedding_width']==256
    loss(p,r,b,stats())[0].backward()
    assert model.encoder.values.weight.grad.abs().sum()>0


def test_reference_precision_survives_autocast():
    model=Autoencoder(Config(width=128,embedding_width=256,layers=1,feedforward=512));b=batch(2)
    with torch.autocast('cpu',dtype=torch.bfloat16):z,p,r=model.from_batch(b)
    assert z.dtype==torch.float32 and r.dtype==torch.float32
    assert p.dtype==torch.bfloat16


def test_sampling_weights_exposures_and_retains_rare_entry_coverage():
    from research.bar_gpt.phase1_v5.sampling import owners_for_sample
    sizes=np.ones(100,dtype=np.int64);sizes[1]=100000
    owners=owners_for_sample(sizes)
    assert 1 in owners and 0 in owners and 99 in owners
    assert len(owners)<=72 and np.array_equal(owners,np.unique(owners))
    with pytest.raises(ValueError):owners_for_sample([0,1])


def test_position_encoding_matters_but_joint_permutation_does_not():
    model=Autoencoder(Config(layers=1,feedforward=64)).eval();b=batch();perm=torch.arange(15,-1,-1)
    with torch.no_grad():
        z=model.from_batch(b)[0]
        permuted=replace(b,visible=b.visible[:,perm],valid=b.valid[:,perm],positions=b.positions[:,perm])
        torch.testing.assert_close(model.from_batch(permuted)[0],z,atol=2e-6,rtol=2e-5)
        shuffled_values=replace(permuted,positions=b.positions)
        assert not torch.allclose(model.from_batch(shuffled_values)[0],z)


def test_loader_hook_preserves_real_source_path(tmp_path):
    from tests.test_bargpt_phase2_latent_v1 import session
    from research.bar_gpt.phase1_v5.data import Stream
    from research.bar_gpt.v5.data_v3 import make_loader,close_loader
    # Existing sealed phase-one fixture authority exercises EligibleStream's real reader.
    from tests.test_bargpt_phase2_latent_v1 import bars
    from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE
    rows=np.zeros(8,dtype=SAMPLE_DTYPE)
    v,m,c,f=raw();rows['values']=v.numpy();rows['valid']=m.numpy();rows['end_us']=c.numpy();rows['timeframe_us']=f.numpy()
    path=tmp_path/'samples.npy';np.save(path,rows)
    from research.bar_gpt.v5.shards.contract import file_hash
    entry=dict(path=str(path),start=0,stop=8,bytes=path.stat().st_size,sha256=file_hash(path))
    stream=Stream({'splits':{'train':[entry]}},'train',stats(),4,config=Config(layers=1,feedforward=64))
    source=make_loader(stream,0,1)
    try:
        batches=list(source);assert len(batches)==2 and batches[0].visible.shape==(4,16,27)
    finally:close_loader(source)
    full_stream=Stream({'splits':{'train':[entry]}},'train',stats(),4,
        config=Config(layers=1,feedforward=64),full=True,native=True)
    source=make_loader(full_stream,0,1)
    try:
        full=next(iter(source));assert full.positions.is_contiguous() and full.positions.shape==(4,64)
        assert full.native_reference.shape==(4,7)
    finally:close_loader(source)


def test_training_resume_preserves_model_optimizer_rng(tmp_path,monkeypatch):
    from research.bar_gpt.phase1_v5 import engine
    from research.bar_gpt.phase2_v2.train import load_checkpoint
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    b=batch(2);units=[b,b]
    monkeypatch.setattr(engine,'loader',lambda *a,**k:units)
    monkeypatch.setattr(engine,'evaluate',lambda *a,**k:{'masked_objective':.2,'origins':4})
    config=Config(layers=1,feedforward=64)
    def args(output,resume=None):
        output.mkdir(exist_ok=True)
        return SimpleNamespace(output=output,resume=resume,device='cpu',precision='fp32',batch_size=2,
            learning_rate=1e-4,min_learning_rate=5e-5,warmup_updates=0,epochs=2,
            log_every=1,train_steps=0,validation_steps=0,cuda_graph=False,wandb_mode='disabled')
    torch.manual_seed(9);full=Autoencoder(config);a=args(tmp_path/'full')
    assert engine.train(full,{},stats(),{'fixture':True},a,lambda **k:None)=='complete'
    expected=load_checkpoint(a.output/'checkpoint_last.pt',{'fixture':True})
    torch.manual_seed(9);partial=Autoencoder(config);a=args(tmp_path/'resumed')
    def stop(**values):
        if values.get('step')==1:(a.output/'STOP').write_text('TEST OWNED STOP')
    assert engine.train(partial,{},stats(),{'fixture':True},a,stop)=='stopped'
    (a.output/'STOP').unlink();a.resume=a.output/'checkpoint_last.pt'
    assert engine.train(Autoencoder(config),{},stats(),{'fixture':True},a,lambda **k:None)=='complete'
    actual=load_checkpoint(a.resume,{'fixture':True})
    assert state_hash(actual)==state_hash(expected)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA lifecycle regression')
def test_cuda_graph_reused_after_validation_and_partial_tail(tmp_path,monkeypatch):
    from research.bar_gpt.phase1_v5 import engine,replay
    from research.bar_gpt.phase2_v2.train import load_checkpoint
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    old_deterministic=torch.are_deterministic_algorithms_enabled()
    torch.use_deterministic_algorithms(True)
    units=[batch(8),batch(3)]
    full=[batch(8,full=True)]
    monkeypatch.setattr(engine,'loader',lambda *a,**k:full if a[4]=='validation' else units)
    real_capture=replay.CudaStep;captures=[]
    def capture(*args):
        captures.append(True);return real_capture(*args)
    monkeypatch.setattr(replay,'CudaStep',capture)
    config=Config(width=128,embedding_width=256,feedforward=512)
    states=[]
    try:
        for captured in (False,True):
            output=tmp_path/str(captured);output.mkdir()
            args=SimpleNamespace(output=output,resume=None,device='cuda',precision='bf16',batch_size=8,
                learning_rate=1e-4,min_learning_rate=5e-5,warmup_updates=0,epochs=2,seed=17,
                log_every=1,train_steps=0,validation_steps=0,cuda_graph=captured,wandb_mode='disabled')
            torch.manual_seed(9);model=Autoencoder(config).cuda()
            assert engine.train(model,{},stats(),{'fixture':True},args,lambda **k:None)=='complete'
            states.append(load_checkpoint(output/'checkpoint_last.pt',{'fixture':True}))
            del model;torch.cuda.synchronize()
        assert len(captures)==1
        assert len(states[1]['history'])==2 and states[1]['step']==4
        assert state_hash(states[0])==state_hash(states[1])
    finally:torch.use_deterministic_algorithms(old_deterministic)


@pytest.mark.parametrize('width,height',[(120,38),(80,24),(60,16)])
def test_terminal_layout_and_units(width,height):
    from io import StringIO
    from rich.console import Console
    p=dict(state='profile_complete',stage='finished',mode='profile',profile_trial=3,profile_trials=3,
        profile_done=24,profile_total=24,train={'loss':.1},gpu_total_mib=96*1024,gpu_used_mib=8*1024,
        profile={'trials':[dict(version=v,kind='actual_loader',batch=1024,windows_per_second=12345.)
                           for v in ('v4','v5-eager','v5-captured')]})
    stream=StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
    console.print(render(p,width,height));text=stream.getvalue()
    assert 'V5' in text and 'References' in text and 'GiB' in text
    assert len(text.splitlines())<=height and all(len(line)<=width for line in text.splitlines())


def test_cli_requires_explicit_training_and_bounds(tmp_path):
    from research.bar_gpt.phase1_v5.run import arguments
    root='D:/TradingML/runtimes/bar_gpt/test-v5-not-started'
    assert arguments(['--output',root]).feature_drop==.075
    for extra in (['--mode','train'],['--feature-drop','.2'],['--workers','33']):
        with pytest.raises(SystemExit):arguments(['--output',root]+extra)


def test_three_loss_families_have_equal_weight():
    from research.bar_gpt.v5.pretraining_v1 import PRICES,ACTIVITY
    b=batch();s=stats()
    for ids in (PRICES,ACTIVITY):
        p=b.target.clone();p[...,ids]+=s.market_scale[list(ids)].float()
        total,terms=loss(p,b.reference_target,b,s)
        assert float(total)==pytest.approx(1/6,abs=1e-6)
    total,_=loss(b.target,b.reference_target+1,b,s)
    assert float(total)==pytest.approx(1/6,abs=1e-6)


def test_real_validation_masked_and_raw_paths(tmp_path):
    from research.bar_gpt.phase1_v5.engine import evaluate
    class Oracle:
        config=Config()
        def eval(self):return self
        def from_batch(self,b):return torch.zeros(len(b.target),512),b.target,b.reference_target
    args=SimpleNamespace(output=tmp_path,device='cpu',precision='fp32',seed=17)
    result=evaluate(Oracle(),[batch(full=True)],args,stats(),lambda **k:None,0)
    assert result['origins']==8 and result['masked_objective']==0 and result['raw_nonfinite']==0
    assert result['reference_nonfinite']==0 and result['full_population']
    assert result['full_raw_mae']['1s']['trade_close']<1e-5
    assert 'full_price_mape_pct/1s/trade_close' in result


def test_shared_loader_default_matches_original_preparation(tmp_path):
    from research.bar_gpt.v5.data_v3 import EligibleStream,make_loader,close_loader
    from research.bar_gpt.v5.pretraining_v3 import prepare_batch,LocalReferencePolicy
    from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE
    from research.bar_gpt.v5.shards.contract import file_hash
    from dataclasses import fields
    rows=np.zeros(8,dtype=SAMPLE_DTYPE);v,m,c,f=raw()
    rows['values']=v.numpy();rows['valid']=m.numpy();rows['end_us']=c.numpy();rows['timeframe_us']=f.numpy()
    path=tmp_path/'legacy.npy';np.save(path,rows)
    entry=dict(path=str(path),start=0,stop=8,bytes=path.stat().st_size,sha256=file_hash(path))
    source=make_loader(EligibleStream({'splits':{'train':[entry]}},'train',LocalReferencePolicy(),4,seed=17),0,1)
    try:actual=next(iter(source))
    finally:close_loader(source)
    rows=rows[:4][np.random.default_rng(17).permutation(4)]
    expected=prepare_batch(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
        torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()),
        LocalReferencePolicy(),torch.Generator().manual_seed(17))
    for field in fields(expected):assert torch.equal(getattr(actual,field.name),getattr(expected,field.name))


def test_actual_profile_pipeline_cpu_smoke(tmp_path):
    from research.bar_gpt.phase1_v5.profiling import run
    from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE
    from research.bar_gpt.v5.shards.contract import file_hash
    rows=np.zeros(8,dtype=SAMPLE_DTYPE);v,m,c,f=raw()
    rows['values']=v.numpy();rows['valid']=m.numpy();rows['end_us']=c.numpy();rows['timeframe_us']=f.numpy()
    path=tmp_path/'source.npy';np.save(path,rows)
    entry=dict(path=str(path),start=0,stop=8,bytes=path.stat().st_size,sha256=file_hash(path))
    args=SimpleNamespace(output=tmp_path,device='cpu',precision='fp32',batches='2',batch_size=2,
        steps=2,warmup=1,workers=0,prefetch=1,shard_prefetch=0,shard_prefetch_mib=64,seed=17)
    result=run({'splits':{'train':[entry]}},stats(),Config(layers=1,feedforward=64),args,lambda **k:None,rows)
    assert not result['recommended_capture']
    actual=[r for r in result['trials'] if r['kind']=='actual_loader']
    assert len(actual)==2 and all(t['windows']==4 and t['windows_per_second']>0 for t in actual)
