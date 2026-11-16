import random
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_bars_v1.config import Config
from research.bar_gpt.phase2_bars_v1.model import Forecaster
from research.bar_gpt.phase2_bars_v1.targets import from_raw,reconstruct,loss,BAR_WIDTH
from research.bar_gpt.phase2_bars_v1.data import BarSessionTokens,batches
from research.bar_gpt.phase2_bars_v1.train import train
from research.bar_gpt.phase2_bars_v1.metrics import Metrics,RawMetrics
from research.bar_gpt.phase2_v2.data import checked_unit,plan_block
from research.bar_gpt.phase2_v2.tokenizer import SessionTokens,normalize
from research.bar_gpt.phase2_v2.train import load_checkpoint
from research.bar_gpt.phase2_unmasked_v1.model import Forecaster as EmbeddingForecaster
from research.bar_gpt.v5.pretraining_v1 import PRICES,ACTIVITY
from tests.test_bargpt_phase2_latent_v1 import session,Teacher,Reporter,options


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def examples():
    history=torch.ones(2,64,18);raw=history.clone();valid=torch.ones_like(raw,dtype=torch.bool)
    history[...,PRICES]=20;raw[...,PRICES]=22+torch.arange(64).reshape(1,64,1)/100
    history[...,ACTIVITY]=10;raw[...,ACTIVITY]=torch.arange(64,dtype=torch.float32).reshape(1,64,1)
    valid[0,:,12:]=False;raw[0,:,12:]=0
    return raw,valid,history,torch.ones_like(valid)


def test_labels_are_exact_v4_transform_and_actual_future_references():
    raw,valid,past,pv=examples();t=from_raw(raw,valid,past,pv)
    scaled,refs=normalize(raw,valid)
    assert torch.equal(t['bars'],scaled.transpose(1,2).contiguous())
    assert not t['reference_valid'][0,-2:].any()
    closes=raw[0,:,3].numpy()
    expected=np.quantile(np.concatenate((closes,closes)).astype(np.float64),.5)
    assert float(t['actual_reference'][0,0])==float(expected)
    predicted=torch.cat((t['bars'].flatten(1),t['references']),1)
    decoded,recovered_refs=reconstruct(predicted,t['origin_log_reference'])
    torch.testing.assert_close(decoded[t['valid']],t['raw'].double()[t['valid']],rtol=1e-5,atol=2e-5)
    torch.testing.assert_close(recovered_refs[t['reference_valid']],t['actual_reference'][t['reference_valid']],rtol=1e-5,atol=2e-5)


def test_loss_masks_invalid_values_and_detaches_labels():
    t=from_raw(*examples());t['bars'].requires_grad_();t['references'].requires_grad_()
    pred=torch.cat((t['bars'].detach().flatten(1)+.1,t['references'].detach()+.1),1).requires_grad_()
    value=loss(pred,t);value.backward()
    mask=torch.cat((t['valid'].flatten(1),t['reference_valid']),1)
    assert not pred.grad[~mask].any() and (pred.grad[mask]!=0).all()
    assert t['bars'].grad is None and t['references'].grad is None


def test_encoder_identical_output_head_changed_and_causal_inference():
    torch.manual_seed(9);old=EmbeddingForecaster()
    torch.manual_seed(9);model=Forecaster().eval()
    assert all(torch.equal(v,old.encoder.state_dict()[k]) for k,v in model.encoder.state_dict().items())
    x=torch.randn(2,16,512);anchor=from_raw(*examples())['origin_log_reference']
    predicted=model.predict(x,anchor)
    assert predicted['embedding'].shape==(2,512) and predicted['bars'].shape==(2,64,18)
    assert predicted['references'].shape==(2,7)
    a=model(x,torch.randn(2,16,9))[1];b=model(x,torch.randn(2,64,27))[1]
    assert torch.equal(a,b)
    with pytest.raises(ValueError):model(x,mask=torch.zeros(512,dtype=torch.bool))


def test_no_future_reference_used_for_inverse():
    t=from_raw(*examples());pred=torch.zeros(2,1159)
    a=reconstruct(pred,t['origin_log_reference'])
    t['actual_reference'].mul_(1000);t['references'].add_(100)
    b=reconstruct(pred,t['origin_log_reference'])
    assert all(torch.equal(x,y) for x,y in zip(a,b))


def test_context_exact_parity_and_future_label_indices(tmp_path):
    reader=checked_unit(session(tmp_path));plan=plan_block(reader,0);teacher=Teacher()
    old=SessionTokens(reader,teacher,'pinned',token_batch=64)
    new=BarSessionTokens(reader,teacher,'pinned',token_batch=64)
    calls=[];ensure=new.ensure
    def spy(f,ends):calls.append((f,np.array(ends)));return ensure(f,ends)
    new.ensure=spy
    old.prepare(plan);new.prepare(plan)
    assert len(calls)==5  # No sixth explicit future embedding request.
    a=old.batch(plan,0,3);b=new.batch(plan,0,3)
    assert torch.equal(a[0],b[0]) and torch.equal(a[1],b[1])
    indices=plan.targets[:3,None]-np.arange(63,-1,-1)
    assert torch.equal(b[2]['raw'],torch.from_numpy(reader.view(0)['values'][indices].copy()).transpose(1,2))
    assert np.all(reader.view(0)['end_us'][indices[:,0]]>plan.origin_us[:3])
    # Change a future bar only in this test's independent in-memory pool.
    before=new.batch(plan,0,1)
    new.pools[0][0][int(plan.targets[0])]+=1
    after=new.batch(plan,0,1)
    assert torch.equal(before[0],after[0]) and torch.equal(before[1],after[1])
    assert not torch.equal(before[2]['raw'],after[2]['raw'])


def test_exact_reference_cache_and_binding_fail_closed(tmp_path):
    reader=checked_unit(session(tmp_path));plan=plan_block(reader,0)
    model=BarSessionTokens(reader,Teacher(),'pinned',token_batch=64)
    model.prepare(plan);_,_,cached=model.batch(plan,0,len(plan.origins))
    pool=reader.view(0);offset=np.arange(63,-1,-1)
    f=plan.targets[:,None]-offset;h=plan.endpoints[0][:,-1,None]-offset
    original=from_raw(torch.from_numpy(pool['values'][f].copy()),torch.from_numpy(pool['valid'][f].copy()).bool(),
        torch.from_numpy(pool['values'][h].copy()),torch.from_numpy(pool['valid'][h].copy()).bool())
    assert all(torch.equal(cached[k],original[k]) for k in original)
    assert model.reference_cache_misses>0  # Cold future endpoints computed without V4 target forwards.
    model._reference_binding='foreign'
    with pytest.raises(ValueError,match='Foreign'):model.batch(plan,0,1)


def test_raw_metrics_use_predicted_references_and_price_only_percentages():
    t=from_raw(*examples());p=torch.cat((t['bars'].flatten(1),t['references']),1)
    perfect=RawMetrics('cpu');perfect.add(p,t);a=perfect.result()
    assert a['full_raw_mae/1s/trade_close']<2e-5
    assert 'full_price_mape_pct/1s/trade_close' in a
    assert 'full_price_mape_pct/1s/trade_volume' not in a
    changed=p.clone();changed[:,BAR_WIDTH]+=100*torch.log(torch.tensor(2.))
    wrong=RawMetrics('cpu');wrong.add(changed,t);b=wrong.result()
    assert b['full_raw_mae/1s/trade_close']>20
    assert b['reference_raw_mae/price']>20


def test_population_metrics_state_and_component_balance():
    t=from_raw(*examples());p=torch.cat((t['bars'].flatten(1)+1,t['references']+2),1)
    m=Metrics('cpu');m.add(p,t);r=m.result()
    assert r['normalized_bar_mse']==pytest.approx(1) and r['reference_change_mse']==pytest.approx(4)
    assert r['mse']==pytest.approx(5)
    state=m.state();m.reset();m.restore(state)
    assert m.result()==r


def test_invalid_observed_sources_fail_closed():
    raw,valid,past,pv=examples();raw[0,0,0]=-1
    with pytest.raises(ValueError):from_raw(raw,valid,past,pv)


def test_packing_and_resume_cursor_cross_publications(tmp_path):
    units=[session(tmp_path/'one'),session(tmp_path/'two')]
    args=options(tmp_path/'output');args.batch_size=64
    teacher=Teacher();rows=list(batches(units,0,teacher,'same',args))
    assert len(rows)==7 and sum(len(r[2]['bars']) for r in rows)==386
    cursor=rows[3][3]
    tail=list(batches(units,0,teacher,'same',args,cursor=cursor))
    for a,b in zip(rows[4:],tail,strict=True):
        assert torch.equal(a[0],b[0]) and torch.equal(a[1],b[1]) and a[3]==b[3]
        assert all(torch.equal(a[2][k],b[2][k]) for k in a[2])


def test_real_launcher_audit_mode_is_read_only_and_teacher_free(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v2 import run_train as shared
    from research.bar_gpt.phase2_bars_v1.run_train import main
    unit=session(tmp_path/'source')
    monkeypatch.setattr(shared,'load_units',lambda *args:([unit],{'hash':'test-data'}))
    result=main(['--dataset-root','unused','--output',str(tmp_path/'audit'),
        '--max-blocks','1','--profile-origins','3'])
    assert result['passed'] and result['mode']=='audit'
    assert result['blocks'][0]['target_shape']==[3,64,18]
    assert result['contract']['teacher'] is None
    assert result['contract']['design']['version']=='bargpt-phase2-bars-v1'


def test_gpu_launcher_refuses_competing_trainer(monkeypatch):
    from research.bar_gpt.phase2_v2 import benchmark
    from research.bar_gpt.phase2_bars_v1.run_train import arguments
    monkeypatch.setattr(benchmark,'active_trainers',lambda:[{'pid':123}])
    with pytest.raises(RuntimeError,match='owns the GPU'):
        arguments(['--mode','train','--execute','--device','cuda','--dataset-root','unused',
            '--output','D:/TradingML/runtimes/bar_gpt/bars-not-launched'])


def test_capture_buffers_and_cpu_rejection():
    from research.bar_gpt.phase2_bars_v1.cuda_step import target_buffers,CudaStep
    buffers=target_buffers(3,'cpu')
    assert buffers['bars'].shape==(3,18,64) and buffers['references'].shape==(3,7)
    assert buffers['valid'].dtype==torch.bool
    with pytest.raises(ValueError,match='requires CUDA'):
        CudaStep(Forecaster(),3)


def test_full_pass_resume_with_direct_targets(tmp_path,monkeypatch):
    from research.bar_gpt.phase2_v2 import train as shared
    units=[session(tmp_path/'train'),session(tmp_path/'val','2026-01-05')]
    def args(root,resume=None):
        a=options(root,resume);a.epochs=2;a.validation_every=3;a.warmup_updates=0
        a.lr_restart_decay=.9;a.batch_size=64;return a
    def fresh():
        random.seed(4);np.random.seed(4);torch.manual_seed(4)
        return Forecaster(),Teacher()
    contract={'test':'bars-full-state','model':Config().manifest()}
    root=tmp_path/'full';root.mkdir();m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root),Reporter())=='complete'
    expected=load_checkpoint(root/'checkpoint_last.pt',contract)
    root=tmp_path/'resume';root.mkdir();save=shared.save_checkpoint;once=[True]
    def crash(path,state):
        save(path,state)
        if state['step']==3 and state['phase']=='train' and once[0]:once[0]=False;raise KeyboardInterrupt
    monkeypatch.setattr(shared,'save_checkpoint',crash);m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root),Reporter())=='interrupted_preserved_last_checkpoint'
    m,t=fresh()
    assert train(m,t,'teacher',units,contract,args(root,root/'checkpoint_last.pt'),Reporter())=='complete'
    actual=load_checkpoint(root/'checkpoint_last.pt',contract)
    from research.bar_gpt.v5.checkpoint_transition_v1 import state_hash
    assert state_hash(actual)==state_hash(expected)
    assert actual['epoch']==2 and actual['step']==8
    assert [h['step'] for h in actual['history']]==[3,4,6,8]
