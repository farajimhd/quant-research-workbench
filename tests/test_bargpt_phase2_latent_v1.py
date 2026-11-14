"""Real shard layouts, causal boundaries, frozen preprocessing and exact recovery."""
from dataclasses import replace
from types import SimpleNamespace
import copy
import json
from pathlib import Path
import random
import numpy as np
import polars as pl
import pytest
import torch
from torch import nn
from research.bar_gpt.phase2_v1.config import Config, HISTORY
from research.bar_gpt.phase2_v1.data import Unit, checked_unit, plan_block, load_units, beneath
from research.bar_gpt.phase2_v1.model import Forecaster, future_loss
from research.bar_gpt.phase2_v1.tokenizer import SessionTokens, normalize, load_teacher
from research.bar_gpt.phase2_v1.train import train, load_checkpoint, evaluate, save_checkpoint
from research.bar_gpt.phase2_v1.run_train import main
from research.bar_gpt.v5.phase2_ar_shards.contract import BAR, BLOCK, FRAMES, Compiler, Geometry
from research.bar_gpt.v5.phase2_ar_shards.storage import publish, ShardReader
from research.bar_gpt.v5.shards.contract import digest, file_hash, write_json
from research.bar_gpt.v5.shards.sql import clock
from research.bar_gpt.v5.pretraining_v3 import prepare_batch, LocalReferencePolicy, REFERENCE_POLICY, TRANSFORM
from research.bar_gpt.v5.encoder_v4 import MaskedAutoencoderV4, EncoderConfig


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def bars(ends):
    rows=np.zeros(len(ends),dtype=BAR);rows['end_us']=ends;rows['valid']=1
    for side,base in ((0,10.),(6,9.99),(12,10.01)):
        rows['values'][:,side:side+4]=base+np.arange(len(ends))[:,None]*.000001
        rows['values'][:,side+4]=100;rows['values'][:,side+5]=2
    return rows


def session(root,day='2023-01-03',coarse=True,step=1,liquid=1,binding='binding'):
    opened=clock(day,4)
    c=Compiler(Geometry())
    c.bootstrap([bars(opened-np.arange(63,-1,-1)*f) for f in FRAMES])
    new=[bars(opened+np.arange(1,BLOCK+1)*FRAMES[0]*step)]
    new += [bars(opened+np.arange(1,BLOCK*step*FRAMES[0]//f+1)*f)
            if coarse else np.empty(0,dtype=BAR) for f in FRAMES[1:]]
    compiled=c.compile(new,liquid)
    compiled['price_basis']=dict(session_date=day,basis_us=opened,actions_hash=digest([]))
    compiled['independent_unit']=True
    publish(root,'TEST',day,liquid,binding,'source',compiled,c.geometry)
    path=root/'shards'/digest('TEST')[:24]/day
    return Unit(root,path,binding,((0,int(day[:4]=='2026'),liquid,0),))


class Teacher(nn.Module):
    def __init__(self):
        super().__init__();self.map=nn.Linear(18,512,bias=False)
        with torch.no_grad():self.map.weight.fill_(.01)
        self.requires_grad_(False).eval()
    def encode_full(self,market,valid,time,time_valid):
        return self.map(market.mean(1)) + time.mean((1,2))[:,None]*.001


def small(plan,n=3):
    return replace(plan,origins=plan.origins[:n],endpoints=tuple(x[:n] for x in plan.endpoints),
                   targets=plan.targets[:n],origin_us=plan.origin_us[:n])


def test_exact_burnin_and_future_boundary(tmp_path):
    unit=session(tmp_path);reader=checked_unit(unit);p=plan_block(reader,0)
    assert p.report['first']==3839 and p.report['last']==4031
    assert len(p.origins)==193
    assert p.report['history_insufficient']==3839 and p.report['target_outside_block']==64
    assert p.report['rejected_union']+len(p.origins)==4096
    for f,n in enumerate((8,4,2,1,1)):
        assert p.endpoints[f].shape==(193,n)
        assert (np.diff(p.endpoints[f],axis=1)==64).all()
        pool=reader.view(f)
        assert (pool['end_us'][p.endpoints[f]]<=p.origin_us[:,None]).all()
        for row in (0,100,192):
            j=np.searchsorted(pool['end_us'],p.origin_us[row],side='right')-1
            assert p.endpoints[f][row,-1]==j
    origins=reader.array('origins.npy')[0]
    for k,i in enumerate(p.origins):
        f=reader.view(0)['end_us'][p.targets[k]-63:p.targets[k]+1]
        np.testing.assert_array_equal(f,origins['end_us'][i+1:i+65])


def test_no_history_is_padded_and_shards_unchanged(tmp_path):
    unit=session(tmp_path,coarse=False)
    before={x.name:file_hash(x) for x in unit.path.iterdir()}
    p=plan_block(checked_unit(unit),0)
    assert len(p.origins)==0 and p.report['reason']
    assert before=={x.name:file_hash(x) for x in unit.path.iterdir()}


@pytest.mark.parametrize('day',['2023-03-13','2023-11-06','2026-01-05'])
def test_sparse_observed_targets_dst_and_exact_coarse_closes(tmp_path,day):
    reader=checked_unit(session(tmp_path,day,step=2));p=plan_block(reader,0)
    assert p.report['first']==1919 and p.report['last']==4031
    origins=reader.array('origins.npy')[0]
    assert origins['end_us'][1919]==clock(day,4)+3840*1_000_000
    assert reader.view(2)['end_us'][p.endpoints[2][0,-1]]==p.origin_us[0]
    for k in (0,len(p.origins)-1):
        target=reader.view(0)['end_us'][p.targets[k]-63:p.targets[k]+1]
        assert target[0]>p.origin_us[k]
        assert target[-1]-p.origin_us[k]==128*1_000_000  # 64 observations, not 64 seconds


def test_selection_year_cannot_be_relabelled(tmp_path):
    unit=session(tmp_path)
    with pytest.raises(ValueError,match='Selection disagrees'):
        checked_unit(replace(unit,blocks=((0,1,1,0),)))


def test_stale_coarse_index_fails_even_when_it_is_in_the_past(tmp_path):
    unit=session(tmp_path);reader=checked_unit(unit)
    original=reader.block
    def corrupted(i):
        value=original(i);value.latest[100,1]-=1;return value
    reader.block=corrupted
    with pytest.raises(ValueError,match='last completed'):plan_block(reader,0)


def test_missing_or_nonconsecutive_origin_fails(tmp_path):
    reader=checked_unit(session(tmp_path))
    a=reader.array('origins.npy').copy();a['latest'][0,200,0]+=1
    reader.arrays['origins.npy']=a
    with pytest.raises(ValueError):plan_block(reader,0)


def test_cache_shapes_reuse_and_no_future_metadata(tmp_path):
    reader=checked_unit(session(tmp_path));p=small(plan_block(reader,0))
    teacher=Teacher();s=SessionTokens(reader,teacher,'teacher')
    s.prepare(p);encoded=s.encoded_windows
    x,meta,target=s.batch(p,0,3)
    assert x.shape==(3,16,512) and meta.shape==(3,16,9) and target.shape==(3,512)
    assert not x.requires_grad and not target.requires_grad
    s.prepare(p);assert s.encoded_windows==encoded
    for f in (1,2,3,4):
        np.testing.assert_array_equal(p.endpoints[f][0],p.endpoints[f][1])
    # All eight fine windows advance; higher frames reuse exact cached content.
    assert (p.endpoints[0][1]-p.endpoints[0][0]==1).all()
    assert torch.equal(x[0,8:],x[1,8:])
    prior=x.clone();prior_meta=meta.clone()
    s.bank[0][torch.tensor(p.targets)]+=1000
    xx,mm,yy=s.batch(p,0,3)
    assert torch.equal(xx,prior) and torch.equal(mm,prior_meta)
    assert not torch.equal(target,yy)
    assert all(t.grad is None for t in teacher.parameters())


def test_memory_budget_and_frozen_teacher_required(tmp_path):
    r=checked_unit(session(tmp_path));teacher=Teacher()
    with pytest.raises(MemoryError):SessionTokens(r,teacher,'x',memory_mib=1)
    teacher.train()
    with pytest.raises(ValueError,match='frozen'):SessionTokens(r,teacher,'x')


def test_real_v4_cache_tiles_are_exact_across_different_miss_orders(tmp_path):
    reader=checked_unit(session(tmp_path))
    model=MaskedAutoencoderV4(EncoderConfig(width=512,layers=1,heads=8,feedforward=64))
    teacher=model.encoder.requires_grad_(False).eval()
    a=SessionTokens(reader,teacher,'pinned',token_batch=64)
    b=SessionTokens(reader,teacher,'pinned',token_batch=64)
    ends=np.array([63,64,126,127,128])
    a.ensure(0,ends[:1]);a.ensure(0,ends[3:]);a.ensure(0,ends[1:3])
    b.ensure(0,ends[::-1])
    assert a.encoded_windows==b.encoded_windows==128
    assert torch.equal(a.bank[0][ends],b.bank[0][ends])
    assert torch.equal(a.refs[0][ends],b.refs[0][ends])


def test_normalization_matches_exact_v4_preparation():
    rows=bars(np.arange(1,65)*1_000_000+clock('2023-01-03',4))
    values=torch.tensor(rows['values'].copy())[None].repeat(2,1,1)
    valid=torch.tensor(rows['valid'].copy()).bool()[None].repeat(2,1,1)
    valid[0,:5,6:12]=False
    prepared=prepare_batch(values,valid,torch.tensor(rows['end_us'].copy())[None].repeat(2,1),
                           FRAMES[0],LocalReferencePolicy(),torch.Generator(),native_validation=True)
    scaled,refs=normalize(values,valid)
    assert torch.equal(scaled.transpose(1,2),prepared.visible)
    assert torch.equal(refs[:,0],prepared.native_reference[:,0,0].log().float())
    assert torch.equal(refs[:,1:],prepared.native_stats.log1p().float())


def test_mask_ratio_whole_vectors_and_eval_uses_all():
    m=Forecaster(Config(layers=1,feedforward=64))
    torch.manual_seed(3);mask=m.encoder.draw_mask(2000,'cpu')
    assert set(mask.sum(1).tolist())=={3,4}
    assert abs(float(mask.float().mean())-.2)<.005
    x=torch.randn(2,16,512);meta=torch.zeros(2,16,9)
    m.eval();a=m(x,meta)[1];b=m(x,meta,torch.zeros(2,16,dtype=torch.bool))[1]
    assert torch.equal(a,b)
    changed=x.clone();changed[:,0]+=900
    hide=torch.zeros(2,16,dtype=torch.bool);hide[:,0]=True
    assert torch.equal(m(x,meta,hide)[1],m(changed,meta,hide)[1])


def test_only_future_target_loss_and_encoder_gradient():
    m=Forecaster(Config(layers=1,feedforward=64));x=torch.randn(2,16,512)
    target=torch.randn(2,512,requires_grad=True)
    _,p=m(x,torch.zeros(2,16,9));loss=future_loss(p,target);loss.backward()
    assert target.grad is None
    assert m.encoder.layers[0].linear1.weight.grad.abs().sum()>0
    assert m.predictor[0].weight.grad.abs().sum()>0


def test_teacher_checkpoint_is_strictly_v4_and_sha_pinned(tmp_path):
    c=EncoderConfig(width=512,layers=1,heads=8,feedforward=64)
    model=MaskedAutoencoderV4(c)
    from dataclasses import asdict
    path=tmp_path/'checkpoint_epoch_040.pt'
    torch.save(dict(contract=dict(architecture='column-encoder-small-mlp-v4',preprocessing=TRANSFORM,
               reference_policy=REFERENCE_POLICY,model=asdict(c)),model=model.state_dict(),epoch=40),path)
    teacher,proof=load_teacher(path,file_hash(path))
    assert proof['epoch']==40 and not teacher.training
    assert not any(p.requires_grad for p in teacher.parameters())
    with pytest.raises(ValueError,match='SHA256'):load_teacher(path,'0'*64)
    value=torch.load(path,weights_only=False);value['contract']['architecture']='v3'
    torch.save(value,path)
    with pytest.raises(ValueError,match='exact V4'):load_teacher(path,file_hash(path))


def selection_root(root):
    root.mkdir();plan={'version':'test'};plan['hash']=digest(plan);write_json(root/'plan.json',plan)
    rows=[('shards/liquid/day',i,0,1,i) for i in range(3)]+[('shards/less/day',0,0,0,3)]
    pl.DataFrame(rows,schema=['path','ordinal','split','liquid','selection'],orient='row').write_parquet(root/'selected.parquet')
    s=dict(binding=plan['hash'],path='selected.parquet',sha256=file_hash(root/'selected.parquet'),blocks=4,
           input_context_only=True,future_target_purge_required=True)
    s['hash']=digest(s);write_json(root/'selection.json',s);write_json(root/'result.json',dict(state='complete'))
    return root


def test_index_validates_selected_parquet_seal_exposure_and_completion(tmp_path):
    root=selection_root(tmp_path/'data');units,identity=load_units([root]);assert len(units)==2
    assert sum(len(u.blocks) for u in units)==4 and identity['hash']
    with pytest.raises(ValueError,match='Duplicate'):load_units([root,root])
    with pytest.raises(ValueError,match='Unsafe'):beneath(root,'../foreign')
    with pytest.raises(ValueError,match='Unsafe'):beneath(root,'D:\\foreign')
    write_json(root/'result.json',dict(state='running'))
    with pytest.raises(ValueError,match='terminal'):load_units([root])


class Reporter:
    def __init__(self,output=None,stop_step=None):self.output=output;self.stop_step=stop_step
    def log(self,metrics,step):
        if self.stop_step==step and 'train_loss' in metrics:(self.output/'STOP').write_text('test owned STOP')
    def update(self,**kw):pass


def options(output,resume=None):
    return SimpleNamespace(device='cpu',precision='fp32',cache_mib=128,token_batch=256,batch_size=64,
        epochs=2,learning_rate=3e-4,min_learning_rate=5e-5,warmup_updates=2,patience=8,
        log_every=1,checkpoint_every=1,output=output,resume=resume)


def test_graceful_stop_resume_exact_optimizer_rng_cursor_and_weights(tmp_path):
    a=session(tmp_path/'data');b=session(tmp_path/'validation','2026-01-05');units=[a,b]
    contract={'version':'test','teacher':'pinned'}
    def fresh():
        random.seed(19);np.random.seed(19);torch.manual_seed(19)
        return Forecaster(Config(layers=1,feedforward=64)),Teacher()
    full=tmp_path/'full';full.mkdir();model,teacher=fresh()
    assert train(model,teacher,'pinned',units,contract,options(full),Reporter())=='complete'
    expected=load_checkpoint(full/'checkpoint_last.pt',contract)
    resumed=tmp_path/'resumed';resumed.mkdir();model,teacher=fresh()
    assert train(model,teacher,'pinned',units,contract,options(resumed),Reporter(resumed,1))=='stopped'
    stopped=load_checkpoint(resumed/'checkpoint_last.pt',contract)
    assert stopped['step']==1 and stopped['cursor'][2]==64
    (resumed/'STOP').unlink()  # Only this fixture's own marker; launchers never clear STOP.
    # Constructing a new process/model consumes RNG, which resume must replace.
    model,teacher=fresh()
    assert train(model,teacher,'pinned',units,contract,options(resumed,resumed/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(resumed/'checkpoint_last.pt',contract)
    assert actual['step']==expected['step'] and actual['scheduler']==expected['scheduler']
    assert actual['history']==expected['history']
    assert torch.equal(actual['rng']['torch'],expected['rng']['torch'])
    for key in actual['model']:assert torch.equal(actual['model'][key],expected['model'][key]),key
    for key,v in actual['optimizer']['state'].items():
        for field,tensor in v.items():assert torch.equal(tensor,expected['optimizer']['state'][key][field])
    with pytest.raises(ValueError,match='exact'):load_checkpoint(resumed/'checkpoint_last.pt',{'version':'changed'})
    epoch_file=resumed/'checkpoint_epoch_002.pt';before=file_hash(epoch_file)
    save_checkpoint(epoch_file,actual)
    assert file_hash(epoch_file)==before
    corrupt=copy.deepcopy(actual);corrupt['step']+=1
    with pytest.raises(FileExistsError,match='collision'):save_checkpoint(epoch_file,corrupt)
    corrupt=copy.deepcopy(actual);next(iter(corrupt['model'].values())).fill_(float('nan'))
    with pytest.raises(FloatingPointError,match='nonfinite'):save_checkpoint(resumed/'checkpoint_last.pt',corrupt)
    assert file_hash(epoch_file)==before


def test_actual_launcher_audits_selected_shards_without_modifying_them(tmp_path):
    root=tmp_path/'dataset';root.mkdir()
    plan=dict(version='fixture');plan['hash']=digest(plan);write_json(root/'plan.json',plan)
    rows=[]
    for i,day in enumerate(('2023-01-03','2023-01-04','2023-01-05','2023-01-06')):
        u=session(root,day,liquid=int(i<3),binding=plan['hash'])
        rows.append((str(u.path.relative_to(root)),0,0,int(i<3),i))
    pl.DataFrame(rows,schema=['path','ordinal','split','liquid','selection'],orient='row').write_parquet(root/'selection.parquet')
    selection=dict(binding=plan['hash'],path='selection.parquet',sha256=file_hash(root/'selection.parquet'),
                   blocks=4,input_context_only=True,future_target_purge_required=True)
    selection['hash']=digest(selection);write_json(root/'selection.json',selection)
    write_json(root/'result.json',dict(state='complete',failure=None))
    receipt=root/rows[0][0]/'receipt.json';before=file_hash(receipt)
    output=tmp_path/'audit'
    result=main(['--dataset-root',str(root),'--output',str(output),'--max-blocks','1'])
    assert result['passed'] and result['blocks'][0]['eligible_origins']==193
    assert file_hash(receipt)==before and not (root/'STOP').exists()
    assert (output/'result.json').exists()


def test_direct_launcher_and_training_are_opt_in(tmp_path):
    import subprocess,sys
    script=Path('research/bar_gpt/phase2_v1/run_train.py').resolve()
    r=subprocess.run([sys.executable,'-B',str(script),'--help'],capture_output=True,text=True)
    assert r.returncode==0 and '--teacher-sha256' in r.stdout
    with pytest.raises(SystemExit):main(['--mode','train','--dataset-root','missing','--output',str(tmp_path/'run')])
