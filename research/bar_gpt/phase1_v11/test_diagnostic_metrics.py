from types import SimpleNamespace
import numpy as np
import torch
from .diagnostic_metrics import Diagnostics
from .transforms import coordinates,center,references,decode,PRICES


def fixture():
    raw=torch.ones(2,64,18,dtype=torch.float64)
    for base,price in ((0,10.),(6,9.),(12,11.)):
        c=price+torch.arange(64,dtype=torch.float64)/100
        raw[:,:,base]=c-.01;raw[:,:,base+1]=c+.02;raw[:,:,base+2]=c-.03;raw[:,:,base+3]=c
    logs=raw.clone()
    for j in range(18):logs[...,j]=raw[...,j].log() if j in PRICES else raw[...,j].log1p()
    valid=torch.ones_like(raw,dtype=torch.bool);fallback=torch.zeros(9,dtype=torch.float64)
    ref,_=references(logs,valid,fallback);code,_=coordinates(logs,valid);code=center(code,ref)
    return raw,logs,valid,ref,code,SimpleNamespace(encoder=SimpleNamespace(reference_mean=fallback))


def test_identity_and_scale_shift():
    raw,logs,v,ref,code,model=fixture();original=raw.clone()
    h=torch.zeros(2,64,dtype=torch.bool);b=SimpleNamespace(raw=raw,frames=torch.tensor([1000000,60000000]))
    out=dict(logs=decode(code,ref),code=code,references=ref)
    d=Diagnostics('cpu');d.add(model,out,logs,v,h,b,64);r=d.finish()
    assert np.max(np.array(r['raw'])[:,:,2])<1e-10
    t=np.array(r['moves']['consecutive_close']);assert t[...,0].sum()==2*63*3
    assert t[...,3].sum()==t[...,0].sum()
    shifted=ref.clone();shifted[:,0::3]+=.01
    out=dict(logs=decode(code,shifted),code=code,references=shifted)
    d=Diagnostics('cpu');d.add(model,out,logs,v,h,b,64);r=d.finish()
    assert np.array(r['raw'])[:,:,2].sum()>1
    assert np.array(r['raw'])[:,:,3].sum()<1e-10
    # Common multiplicative offset cancels within-window returns, but not last-close error.
    assert abs(np.array(r['moves']['consecutive_close'])[...,2].sum())<1e-8
    assert np.array(r['moves']['last_close_offset'])[...,5].sum()>0
    torch.testing.assert_close(raw,original)


def test_masked_endpoint_and_validity_counts():
    raw,logs,v,ref,code,model=fixture();v[0,2,3]=False
    h=torch.ones(2,64,dtype=torch.bool);h[:,::4]=False
    b=SimpleNamespace(raw=raw,frames=torch.tensor([1000000,1000000]))
    d=Diagnostics('cpu');d.add(model,dict(logs=decode(code,ref),code=code,references=ref),logs,v,h,b,16)
    r=d.finish();assert np.array(r['moves']['candle_close_vs_open'])[...,0].sum()==2*48*3-1
    assert np.array(r['moves']['consecutive_close'])[...,0].sum()==2*32*3-2
    assert np.array(r['moves']['window_last_vs_first_close'])[...,0].sum()==0
    assert d.bucket(torch.tensor([0.,-.05,-.1,-.25,-.5,-1.,.05,.1,.25,.5,1.],dtype=torch.float64)).tolist()==list(range(11))


def test_confusion_matches_scalar_reference():
    actual=torch.tensor([[[0.,-.03,.3],[.02,-.4,1.]],[[.06,.2,-.7],[0.,-.1,.5]]],dtype=torch.float64)
    predicted=torch.tensor([[[.02,-.03,-.3],[-.02,-.2,.9]],[[.07,0.,-.3],[0.,.2,.5]]],dtype=torch.float64)
    mask=torch.ones_like(actual,dtype=torch.bool);mask[0,1,2]=False
    frames=torch.tensor([1,3]);d=Diagnostics('cpu');d.moves('test',actual,predicted,mask,frames)
    expected=np.zeros((6,3,11,11));sums=np.zeros((6,3,11,6))
    def label(x):
        if x==0:return 0
        bucket=next((i for i,edge in enumerate((.05,.1,.25,.5)) if abs(x)<=edge),4)
        return 1+bucket+(5 if x>0 else 0)
    for i in range(2):
        for t in range(2):
            for c in range(3):
                if not mask[i,t,c]:continue
                a=float(actual[i,t,c]);p=float(predicted[i,t,c]);f=int(frames[i]);b=label(a)
                expected[f,c,b,label(p)]+=1
                sums[f,c,b]+=[1,abs(p-a),p-a,float(np.sign(a)==np.sign(p)),a,p]
    r=d.finish();np.testing.assert_array_equal(r['confusion']['test'],expected)
    np.testing.assert_allclose(r['moves']['test'],sums,rtol=1e-14,atol=1e-14)
