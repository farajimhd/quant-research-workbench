import copy
import torch
from .step import configure,backward
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import prepare
from ..phase1_v7.full_data import Batch
from ..phase1_v11.test_diagnostic_metrics import fixture
from ..phase0_v2.model import Autoencoder as Teacher,Config as TeacherConfig
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint


def test_native_step_frozen_teacher_and_exact_resume(tmp_path):
    torch.manual_seed(17)
    raw,_,valid,_,_,_=fixture()
    frames=torch.tensor([1000000,60000000])
    slots=1700000000000000+torch.arange(64)[None,:]*frames[:,None]
    batch=Batch(raw,slots,raw,valid,raw,frames)
    teacher=Teacher(dict(mean=[0.]*27,scale=[1.]*27),TeacherConfig(width=4,layers=1,heads=1,pooling='flatten')).requires_grad_(False).eval()
    model=Autoencoder(Config(width=32,layers=1,heads=2,embedding=256,precision='fp32'),[0.]*16,[1.]*16,'scaled_slots_price')
    contract=dict(loss_arm='joint',activity_calibration=dict(mean_square_log_change=[[1.]*6]*6))
    configure(model,contract);optimizer=torch.optim.AdamW(model.parameters(),lr=1e-5)
    def update(m,o):
        pos=positions(2,'cpu',16);o.zero_grad(set_to_none=True)
        loss,parts,norm=backward(m,teacher,batch,pos)
        assert torch.isfinite(loss) and torch.isfinite(parts).all() and torch.isfinite(norm)
        assert all(p.grad is None for p in teacher.parameters())
        o.step();return pos,loss
    update(model,optimizer)
    path=tmp_path/'checkpoint.pt'
    save_checkpoint(path,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),rng=rng_state(),step=1,cursor=(0,1)))
    saved=load_checkpoint(path,contract)
    expected=update(model,optimizer)
    clone=copy.deepcopy(model);clone.load_state_dict(saved['model']);other=torch.optim.AdamW(clone.parameters(),lr=1e-5);other.load_state_dict(saved['optimizer']);restore_rng(saved['rng'])
    actual=update(clone,other)
    for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0)
    for k,v in model.state_dict().items():torch.testing.assert_close(clone.state_dict()[k],v,rtol=0,atol=0)
    latent,meta,_,_,_=prepare(teacher,batch);pos=positions(2,'cpu',16)
    known=torch.zeros(2,64,dtype=torch.bool).scatter(1,pos,True)
    torch.testing.assert_close(model(latent,meta,pos),model(torch.where(known[...,None],latent,latent+100),torch.where(known[...,None],meta,meta-100),pos),rtol=0,atol=0)
