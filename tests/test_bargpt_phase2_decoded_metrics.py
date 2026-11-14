"""Raw-unit diagnostics, target isolation, decoder pinning and periodic recovery."""
import copy
import random
from dataclasses import asdict
import numpy as np
import pytest
import torch
from torch import nn
from research.bar_gpt.phase2_v1.metrics import DecodedMetrics
from research.bar_gpt.phase2_v1.tokenizer import load_teacher, SessionTokens
from research.bar_gpt.phase2_v1.data import checked_unit, plan_block
from research.bar_gpt.phase2_v1.train import evaluate, rng_state, train, load_checkpoint
from research.bar_gpt.phase2_v1.model import Forecaster
from research.bar_gpt.phase2_v1.config import Config
from research.bar_gpt.phase2_v1.run_train import main, arguments
from research.bar_gpt.v5.encoder_v4 import MaskedAutoencoderV4, EncoderConfig
from research.bar_gpt.v5.pretraining_v3 import TRANSFORM, REFERENCE_POLICY
from research.bar_gpt.v5.pretraining_v1 import PRICES, ACTIVITY
from research.bar_gpt.v5.shards.contract import file_hash, digest, write_json
from tests.test_bargpt_phase2_latent_v1 import session, Teacher, small, Reporter, options


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


class Decoder(nn.Module):
    def forward(self,embedding,ids,time,time_valid):
        return embedding[:,:18,None].expand(-1,-1,64)


def test_raw_inverse_relative_bands_masks_horizons_and_baseline():
    decoder=Decoder().requires_grad_(False).eval()
    reference=torch.tensor([.5,2.,10.,50.,200.],dtype=torch.float64)[:,None,None]
    raw=torch.ones(5,18,64,dtype=torch.float64)
    raw[:,PRICES]=reference
    raw[:,ACTIVITY]=3
    mask=torch.ones_like(raw,dtype=torch.bool);mask[:,0,0]=False
    evidence=dict(raw=raw,valid=mask,reference=reference,stats=torch.full((5,6),3.),
                  time=torch.zeros(5,64,9),time_valid=torch.ones(5,64,9,dtype=torch.bool))
    p=torch.zeros(5,512);p[:,PRICES]=float(100*np.log(1.1));p[:,ACTIVITY]=float(np.log(2))
    m=DecodedMetrics('cpu');m.add(decoder,p,torch.zeros_like(p),evidence);r=m.result()
    expected=float(reference.mean())*.1
    assert r['decoded_raw_mae/1s/trade_close']==pytest.approx(expected,rel=2e-6)
    assert r['decoded_raw_mae/1s/trade_volume']==pytest.approx(4.,rel=1e-6)
    assert r['decoded_raw_counts/1s/trade_open']==315
    assert r['decoded_raw_counts/1s/trade_open/horizon_01']==0
    assert r['decoded_raw_mae/1s/trade_open/horizon_01'] is None
    assert r['decoder_baseline_raw_mae/1s/trade_close']==0.
    assert r['decoded_price_mape_pct/1s/trade_close']==pytest.approx(10.,rel=2e-6)
    assert r['decoded_price_median_ape_pct/1s/trade_close']==pytest.approx(10.,rel=.006)
    assert r['decoded_price_p90_ape_pct/1s/trade_close']==pytest.approx(10.,rel=.006)
    for band in ('below_1','1_to_5','5_to_20','20_to_100','100_and_above'):
        assert r[f'decoded_price_relative_counts_by_price_band/{band}/1s/trade_close']==64
    assert not any('volume' in k or 'size' in k or 'count' in k.split('/')[-1]
                   for k in r if '_price_mape_pct/' in k)
    assert r['decoded_raw_rmse/1s/trade_close']==pytest.approx(
        float(reference.square().mean().sqrt())*.1,rel=2e-6)
    p[:,0]=1e6
    overflow=DecodedMetrics('cpu');overflow.add(decoder,p,torch.zeros_like(p),evidence)
    bad=overflow.result()
    assert bad['decoded_raw_overflows/1s/trade_open']==315
    assert bad['decoded_raw_mae/1s/trade_open'] is None
    assert bad['decoded_price_mape_pct/1s/trade_open'] is None


def test_target_evidence_exact_future_window_and_no_input_leak(tmp_path):
    u=session(tmp_path);reader=checked_unit(u);p=small(plan_block(reader,0),2)
    t=Teacher();s=SessionTokens(reader,t,'pinned',memory_mib=128)
    s.prepare(p);before=s.batch(p,0,2);e=s.reconstruction(p,0,2)
    raw=reader.view(0)
    for i,end in enumerate(p.targets):
        np.testing.assert_array_equal(e['raw'][i].T.numpy(),raw['values'][end-63:end+1])
        np.testing.assert_array_equal(e['valid'][i].T.numpy(),raw['valid'][end-63:end+1])
    assert e['reference'].dtype==torch.float64 and e['stats'].dtype==torch.float64
    e['reference'].mul_(1e6);e['time'].fill_(999)
    after=s.batch(p,0,2)
    for a,b in zip(before,after):assert torch.equal(a,b)


def test_evaluate_preserves_rng_teacher_state_and_training_mode(tmp_path):
    u=session(tmp_path/'validation','2026-01-05');teacher=Teacher()
    model=Forecaster(Config(layers=1,feedforward=64));model.eval()
    a=options(tmp_path);state=rng_state();weights=copy.deepcopy(teacher.state_dict())
    r=evaluate(model,[u],teacher,'pinned',a,None,Reporter(),23)
    assert r['decoded_origins']==r['origins']==193
    assert r['decoded_raw_counts/1s/trade_close']==193*64
    assert 'mse' in r and 'decoder_baseline_raw_mae/1s/trade_close' in r
    assert torch.equal(state['torch'],rng_state()['torch'])
    assert state['python']==rng_state()['python'] and state['numpy']==rng_state()['numpy']
    assert not model.training and not teacher.training
    assert all(torch.equal(v,teacher.state_dict()[k]) for k,v in weights.items())
    assert all(p.grad is None for p in teacher.parameters())


def test_same_checkpoint_decoder_strict_and_frozen(tmp_path):
    config=EncoderConfig(width=512,layers=1,heads=8,feedforward=64)
    m=MaskedAutoencoderV4(config);path=tmp_path/'v4.pt'
    value=dict(contract=dict(architecture='column-encoder-small-mlp-v4',preprocessing=TRANSFORM,
                            reference_policy=REFERENCE_POLICY,model=asdict(config)),
               model=m.state_dict(),epoch=50)
    torch.save(value,path);teacher,_=load_teacher(path,file_hash(path))
    assert not teacher.reconstruction_decoder.training
    for k,v in m.decoder.state_dict().items():assert torch.equal(v,teacher.reconstruction_decoder.state_dict()[k])
    assert not any(p.requires_grad for p in teacher.parameters())
    del value['model']['decoder.summary.weight'];torch.save(value,path)
    with pytest.raises(RuntimeError):load_teacher(path,file_hash(path))


def test_interval_validation_resume_exact_and_no_extra_guard_counts(tmp_path):
    units=[session(tmp_path/'train'),session(tmp_path/'val','2026-01-05')]
    contract=dict(test='periodic');saved=[]
    class StopValidation(Reporter):
        def update(self,**kw):
            if kw.get('stage')=='validation':(self.output/'STOP').write_text('fixture STOP')
    def fresh():
        random.seed(8);np.random.seed(8);torch.manual_seed(8)
        return Forecaster(Config(layers=1,feedforward=64)),Teacher()
    def args(root,resume=None):
        a=options(root,resume);a.epochs=1;a.validation_every=2;return a
    full=tmp_path/'full';full.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(full),Reporter())=='complete'
    expected=load_checkpoint(full/'checkpoint_last.pt',contract)
    assert len(expected['interval_history'])==2 and len(expected['history'])==1
    resumed=tmp_path/'resume';resumed.mkdir();m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(resumed),StopValidation(resumed))=='stopped'
    partial=load_checkpoint(resumed/'checkpoint_last.pt',contract)
    assert partial['phase']=='interval_validation' and partial['step']==2
    (resumed/'STOP').unlink()  # exact fixture-owned STOP only
    m,t=fresh()
    assert train(m,t,'pinned',units,contract,args(resumed,resumed/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(resumed/'checkpoint_last.pt',contract)
    for key in ('history','interval_history','step','last_validation_step','stale','scheduler'):
        assert actual[key]==expected[key],key
    assert torch.equal(actual['rng']['torch'],expected['rng']['torch'])
    for k in actual['model']:assert torch.equal(actual['model'][k],expected['model'][k])
    for k,v in actual['optimizer']['state'].items():
        for field,tensor in v.items():assert torch.equal(tensor,expected['optimizer']['state'][k][field])


def test_validation_interval_cli_defaults_and_rejection(tmp_path):
    base=['--dataset-root','unused','--output',str(tmp_path/'out')]
    assert arguments(base).validation_every==0
    assert arguments(base+['--validation-every','1000']).validation_every==1000
    with pytest.raises(SystemExit):arguments(base+['--validation-every','-1'])


def test_real_profile_launcher_includes_decoded_metrics_and_keeps_shards(tmp_path):
    import polars as pl
    root=tmp_path/'dataset';root.mkdir()
    plan=dict(version='profile-fixture');plan['hash']=digest(plan)
    write_json(root/'plan.json',plan);rows=[]
    for i,day in enumerate(('2023-01-03','2023-01-04','2023-01-05','2023-01-06')):
        u=session(root,day,liquid=int(i<3),binding=plan['hash'])
        rows.append((str(u.path.relative_to(root)),0,0,int(i<3),i))
    pl.DataFrame(rows,schema=['path','ordinal','split','liquid','selection'],orient='row').write_parquet(root/'selection.parquet')
    selection=dict(binding=plan['hash'],path='selection.parquet',sha256=file_hash(root/'selection.parquet'),
                   blocks=4,input_context_only=True,future_target_purge_required=True)
    selection['hash']=digest(selection);write_json(root/'selection.json',selection)
    write_json(root/'result.json',dict(state='complete',failure=None,binding=plan['hash'],
                                     selection=selection,selected_blocks=4))
    config=EncoderConfig(width=512,layers=1,heads=8,feedforward=64)
    teacher=MaskedAutoencoderV4(config);path=tmp_path/'teacher.pt'
    torch.save(dict(contract=dict(architecture='column-encoder-small-mlp-v4',preprocessing=TRANSFORM,
                                reference_policy=REFERENCE_POLICY,model=asdict(config)),
                    model=teacher.state_dict(),epoch=50),path)
    before={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    r=main(['--mode','profile','--dataset-root',str(root),'--output',str(tmp_path/'profile'),
            '--teacher-checkpoint',str(path),'--teacher-sha256',file_hash(path),
            '--max-blocks','1','--profile-origins','2','--profile-steps','1',
            '--cache-mib','128','--device','cpu'])
    assert r['passed'] and r['blocks'][0]['reconstruction']['decoded_origins']==2
    assert r['blocks'][0]['reconstruction']['decoded_raw_counts/1s/trade_close']==128
    assert r['contract']['validation']['every_updates']==0
    assert r['contract']['validation']['reconstruction']['version']=='phase2-v1-target-conditioned-decoded-v1'
    assert before=={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
