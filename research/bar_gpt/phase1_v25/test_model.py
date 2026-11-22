import copy
import torch
from .model import Model
from .objectives import loss
from .step import rate
from ..phase1_v11.transforms import PRICES,ACTIVITY,coordinates,decode,inverse


def calibration():
    return dict(mean=[0.]*18,scale=[1.]*18,input_mean=[0.]*27,input_scale=[1.]*27,reference=[1.]*6,activity_mean=[0.]*6,activity_scale=[1.]*6)


def data():
    torch.manual_seed(71);raw=torch.ones(2,64,18)*10
    for b in (0,6,12):raw[...,b+1]=11;raw[...,b+2]=9;raw[...,b+3]=10.1
    x=torch.cat((torch.stack([raw[...,j].log() if j in PRICES else raw[...,j].log1p() for j in range(18)],-1),torch.zeros(2,64,9)),-1)
    return x,torch.ones_like(x,dtype=torch.bool),raw,torch.arange(56).expand(2,-1)


def test_family_isolation_hidden_independence_and_decoder():
    torch.set_num_threads(2);x,v,raw,pos=data()
    for kind,other in [('price',ACTIVITY),('activity',PRICES)]:
        m=Model(calibration(),kind);y,z=m(x,v,pos);changed=x.clone();changed[...,list(other)]+=3;changed[:,56:]+=7
        yy,zz=m(changed,v,pos);torch.testing.assert_close(z,zz,rtol=0,atol=0)
        torch.testing.assert_close(m.decode(z)['logs'],y['logs'],rtol=0,atol=0)
        val,_=loss(m,y,x,v,raw,pos);val.backward();assert all(p.grad is None or p.grad.isfinite().all() for p in m.parameters())


def test_joint_reproduces_independent_branches_and_gradients():
    torch.set_num_threads(2);x,v,raw,pos=data();joint=Model(calibration(),'separate');out,z=joint(x,v,pos);value,_=loss(joint,out,x,v,raw,pos);value.backward()
    for kind,channels in [('price',PRICES),('activity',ACTIVITY)]:
        solo=Model(calibration(),kind);so,sz=solo(x,v,pos);sv,_=loss(solo,so,x,v,raw,pos);sv.backward()
        torch.testing.assert_close(so['logs'][...,list(channels)],out['logs'][...,list(channels)],rtol=0,atol=0)
        for name,p in solo.named_parameters():torch.testing.assert_close(p.grad,dict(joint.named_parameters())[name].grad,rtol=1e-5,atol=1e-5)


def test_roundtrip_difficult_values_and_scheduler():
    raw=torch.tensor([1e-9,.01,.1,1.,100.,1e7],dtype=torch.float64)[:,None,None].expand(-1,64,18).clone()
    raw[...,list(ACTIVITY)]=torch.tensor([0.,1.,100.,1e5,1e10,1e15],dtype=torch.float64)[:,None,None]
    logs=torch.stack([raw[...,j].log() if j in PRICES else raw[...,j].log1p() for j in range(18)],-1)
    c,_=coordinates(logs,torch.ones_like(logs,dtype=torch.bool));actual=inverse(decode(c,torch.zeros(6,9,dtype=torch.float64),constrain=False))
    torch.testing.assert_close(actual,raw,rtol=1e-12,atol=1e-12)
    assert rate(0,1000,.001)<rate(49,1000,.001)
    assert abs(rate(999,1000,.001)-1e-5)<1e-12


def test_masked_invalid_targets_do_not_affect_loss():
    torch.set_num_threads(2);x,v,r,pos=data();v[...,4]=False;m=Model(calibration(),'activity');out,_=m(x,v,pos)
    value,_=loss(m,out,x,v,r,pos);r2=r.clone();r2[...,4]=1e20;x2=x.clone();x2[...,4]=46
    actual,_=loss(m,out,x2,v,r2,pos);torch.testing.assert_close(value,actual)
