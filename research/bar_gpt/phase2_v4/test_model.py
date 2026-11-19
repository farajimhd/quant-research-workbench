import copy
import torch
import pytest
from ..phase1_v11.model import Autoencoder
from ..phase1_v11.config import Config as Phase1Config
from ..phase2_v3.test_model import example
from .model import Forecaster,Phase1Decoder


def test_identical_phase1_heads_and_embedding_only_forecast():
    from .metrics import Metrics
    from .profile import profile
    from .train import train
    parameters=dict(time_mean=[0.]*9,time_scale=[1.]*9,reference_mean=[0.]*9,reference_scale=[1.]*9)
    source=Autoencoder(Phase1Config(width=128,layers=4,embedding=256,latent_tokens=4,readout='split_scale',dropout=0.),parameters).eval()
    head=Phase1Decoder();head.initialize_phase1(source.state_dict());z=torch.randn(3,256)
    expected=source.decode(z);actual=head.details(z)
    for key in expected:torch.testing.assert_close(actual[key],expected[key],rtol=0,atol=0)
    m=Forecaster().eval();m.decoder=head;x,a,t=example()
    latent,p=m(x,a);assert p.shape==(4,64,18)
    torch.testing.assert_close(m.decoder(m.encode_context(x,a)),p,rtol=0,atol=0)
    loss=m.training_loss(p,t);loss.backward()
    assert all(q.grad is not None and torch.isfinite(q.grad).all() for q in m.parameters())
    changed=dict(t,logs=t['logs'].clone());changed['logs'][~t['valid']]=999.
    torch.testing.assert_close(m.training_loss(p,changed),loss)
    assert sum(p.numel() for p in head.parameters())==888489
    metrics=Metrics('cpu');metrics.add(p.detach(),t)
    assert metrics.result()['loss']==pytest.approx(float(loss.detach()))


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_phase1_heads_cuda_replay_resume():
    from ..phase2_v3.cuda_step import CudaStep
    torch.manual_seed(17);m=Forecaster().cuda();x,a,t=example(4,'cuda')
    # Real launch loads trained phase1 weights; small initial weights keep this synthetic gate finite.
    for q in m.decoder.parameters():torch.nn.init.normal_(q,std=.0005)
    opt=torch.optim.AdamW(m.parameters(),lr=1e-4,fused=True);g=CudaStep(m,4)
    p,l,n=g(x,a,t);assert torch.isfinite(l)&torch.isfinite(n);opt.step()
    saved=copy.deepcopy((m.state_dict(),opt.state_dict()))
    expected=g(x,a,t)[0].clone();opt.step()
    other=Forecaster().cuda();other.load_state_dict(saved[0]);o=torch.optim.AdamW(other.parameters(),lr=1e-4,fused=True);o.load_state_dict(saved[1]);h=CudaStep(other,4)
    actual=h(x,a,t)[0].clone();o.step()
    torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-6)
    for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-6)


def test_fixed_schedule_resume_and_validation_independence():
    from .schedule import FixedWarmup
    s=FixedWarmup(3e-5,3e-6,5000,1000)
    assert s.value()==pytest.approx(3e-6+2.7e-5/1000)
    s.updates=1000;assert s.value()==3e-5
    s.validation(.1);s.updates=80000;assert s.value()==3e-5
    other=FixedWarmup(3e-5,3e-6,5000,1000);other.load_state_dict(s.state_dict())
    assert other.value()==s.value() and other.updates==80000


def test_shuffled_sessions_preserve_population_validation_and_resume_order(monkeypatch,tmp_path):
    import polars as pl
    from ..phase2_v2 import data as old
    from ..phase2_v2.release import ReleasedUnits
    from .data import load_units
    n=40
    table=pl.DataFrame(dict(path=[f'shards/s{i}/2020-01-01' for i in range(n)],receipt_hash=[str(i) for i in range(n)],
        unit_split=[0]*35+[1]*5,ordinal=[[0]]*n,split=[[0]]*35+[[1]]*5,liquid=[[1]]*n,selection=[[i] for i in range(n)]))
    monkeypatch.setattr(old,'load_units',lambda *a,**kw:(ReleasedUnits([(tmp_path,'binding',table)]),dict(hash='base')))
    a,identity=load_units(release='fixture');b,other=load_units(release='fixture')
    first=a.groups[0][2];assert identity==other and first.equals(b.groups[0][2])
    assert set(first['path'])==set(table['path']) and not first.equals(table)
    assert first.filter(pl.col('unit_split')==1).equals(table.filter(pl.col('unit_split')==1))
    all_rows=list(a.iter_from(0));assert list(a.iter_from(0,12))==all_rows[12:]
