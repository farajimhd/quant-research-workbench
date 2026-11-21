import torch
from .model import Autoencoder,Config,positions,gather,objective


def fixture():
    torch.manual_seed(17)
    model=Autoencoder(Config(width=32,layers=1,heads=2,embedding=32,decoder_width=16,precision='fp32'),[0.]*16,[1.]*16)
    z=torch.randn(3,64,16);meta=torch.randn(3,64,36);valid=torch.ones(3,64,dtype=torch.bool)
    return model,z,meta,valid,positions(3,'cpu')


def test_mask_and_hidden_target_isolation():
    model,z,meta,valid,pos=fixture();assert pos.shape==(3,16)
    assert (pos.diff(dim=1)>0).all()
    other=positions(3,'cpu');assert not torch.equal(pos,other)
    known=torch.zeros(3,64,dtype=torch.bool).scatter(1,pos,True)
    out=model(z,meta,pos)
    torch.testing.assert_close(out,model(torch.where(known[...,None],z,z+100),torch.where(known[...,None],meta,meta-100),pos))
    embedding=model.encoder(gather(z,pos),gather(meta,pos),pos)
    assert embedding.shape==(3,32);torch.testing.assert_close(model.decode(embedding),out)
    assert out.shape==(3,64,16)


def test_valid_loss_and_finite_gradients():
    model,z,meta,valid,pos=fixture();out=model(z,meta,pos)
    valid[:,10]=False
    loss,parts=objective(model,out,z,valid,pos)
    changed=z.clone();changed[:,10]=1e6
    torch.testing.assert_close(loss,objective(model,out,changed,valid,pos)[0])
    loss.backward();assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    assert model.encoder.blocks[0].qkv.weight.grad.abs().sum()>0
    valid.zero_();assert objective(model,out,z,valid,pos)[0]==0


def test_fixed_calibration_inverse_and_state_roundtrip():
    model,z,meta,valid,pos=fixture()
    model.encoder.mean.copy_(torch.arange(16)*3.);model.encoder.scale.copy_(torch.arange(16)+.01)
    normalized=(z-model.encoder.mean)/model.encoder.scale
    torch.testing.assert_close(model.denormalize(normalized),z,atol=4e-6,rtol=1e-5)
    clone,_,_,_,_=fixture();clone.load_state_dict(model.state_dict())
    torch.testing.assert_close(clone(z,meta,pos),model(z,meta,pos),rtol=0,atol=0)


def test_attention_uses_actual_positions():
    model,z,meta,valid,pos=fixture()
    selected=gather(z,pos);metadata=gather(meta,pos)
    out=model.encoder(selected,metadata,pos)
    assert not torch.allclose(out,model.encoder(selected,metadata,(pos+1)%64))


def test_metric_masks_and_teacher_reference():
    from types import SimpleNamespace
    from .metrics import Metrics,scalars
    from ..phase0_v2.model import Autoencoder as BarEncoder,Config as BarConfig
    from ..phase1_v11.test_diagnostic_metrics import fixture as raw_fixture
    raw,logs,v,_,_,_=raw_fixture()
    values=torch.cat((logs.float(),torch.zeros(2,64,9)),-1)
    valid=torch.cat((v,torch.ones(2,64,9,dtype=torch.bool)),-1)
    teacher=BarEncoder(dict(mean=[0.]*27,scale=[1.]*27),BarConfig(width=4,layers=1,heads=1,pooling='flatten')).requires_grad_(False)
    z=teacher.encode(values,valid);model,_,_,_,_=fixture();pos=positions(2,'cpu')
    b=SimpleNamespace(grid=raw,frames=torch.tensor([1000000,60000000]))
    metric=Metrics('cpu');metric.add(model,teacher,b,(z,torch.zeros(2,64,36),v.any(-1),values,valid,z,z),pos)
    result=metric.result();s=scalars(result)
    assert sum(result['counts'][0])==96 and sum(result['counts'][1])==128
    assert s['masked/all/latent_loss']==0 and s['all_visible/all/latent_loss']==0
    assert s['masked/raw_mae/1s/trade_close']==s['teacher_masked/raw_mae/1s/trade_close']
    assert s['all_visible/raw_mae/1s/trade_close']==s['teacher_all_visible/raw_mae/1s/trade_close']
    assert result['nonfinite']==0
    reference={k:r for k,r in result['raw'].items() if k.startswith('teacher_')}
    cached=Metrics('cpu',reference);cached.add(model,teacher,b,(z,torch.zeros(2,64,36),v.any(-1),values,valid,z,z),pos)
    assert scalars(cached.result())==s


def test_optimizer_rng_resume_is_exact(tmp_path):
    from ..phase2_v2.train import save_checkpoint,load_checkpoint,rng_state,restore_rng
    model,z,meta,valid,_=fixture();opt=torch.optim.AdamW(model.parameters(),lr=1e-3)
    def step(m,o):
        pos=positions(len(z),'cpu');o.zero_grad();loss=objective(m,m(z,meta,pos),z,valid,pos)[0];loss.backward();o.step();return pos,loss.detach()
    step(model,opt)
    contract=dict(version='test-v12',config=vars(model.config))
    state=dict(model=model.state_dict(),optimizer=opt.state_dict(),rng=rng_state(),contract=contract,step=1,cursor=(0,1))
    path=tmp_path/'last.pt';save_checkpoint(path,state)
    expected_pos,expected_loss=step(model,opt)
    clone,_,_,_,_=fixture();other=torch.optim.AdamW(clone.parameters(),lr=1e-3)
    saved=load_checkpoint(path,contract);clone.load_state_dict(saved['model']);other.load_state_dict(saved['optimizer']);restore_rng(saved['rng'])
    actual_pos,actual_loss=step(clone,other)
    torch.testing.assert_close(actual_pos,expected_pos,rtol=0,atol=0);torch.testing.assert_close(actual_loss,expected_loss,rtol=0,atol=0)
    for key,value in model.state_dict().items():torch.testing.assert_close(clone.state_dict()[key],value,rtol=0,atol=0)


def test_teacher_epoch20_admission_and_frozen_parameters(tmp_path):
    import pytest
    from .data import load_teacher
    from ..phase0_v2.model import Autoencoder as BarEncoder,Config as BarConfig
    from ..phase2_v2.train import save_checkpoint
    c=BarConfig(width=4,layers=1,heads=1,pooling='flatten')
    teacher=BarEncoder(dict(mean=[0.]*27,scale=[1.]*27),c)
    contract=dict(version='phase0-v2',split_hash='test',counts=dict(train=2),config=vars(c))
    state=dict(contract=contract,model=teacher.state_dict(),optimizer=dict(state={}),step=10,cursor=(19,0),epoch=19,phase='train',windows=38)
    path=tmp_path/'teacher.pt';save_checkpoint(path,state)
    with pytest.raises(ValueError,match='epoch20'):load_teacher(path,'test')
    state.update(epoch=20,windows=40,cursor=(20,0));save_checkpoint(path,state)
    loaded,identity=load_teacher(path,'test');assert identity['epoch']==20
    assert all(not p.requires_grad for p in loaded.parameters()) and not loaded.training
    with pytest.raises(ValueError,match='split'):load_teacher(path,'changed')
