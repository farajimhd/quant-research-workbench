import torch
from .window_probe import fit,predict,targets


def test_ridge_recovers_known_linear_information():
    g=torch.Generator().manual_seed(3);x=torch.randn(100,5,generator=g,dtype=torch.float64)
    y=x@torch.arange(10,dtype=torch.float64).reshape(5,2)+2
    solution=fit(x,y,ridge=1e-10)
    dx=torch.randn(30,5,generator=g,dtype=torch.float64)
    torch.testing.assert_close(predict(dx,solution),dx@torch.arange(10,dtype=torch.float64).reshape(5,2)+2,rtol=1e-7,atol=1e-7)


def test_missing_source_values_are_counted_not_used_as_labels():
    raw=torch.ones(3,64,18);valid=torch.ones_like(raw,dtype=torch.bool)
    valid[0,2,4]=False;raw[0,2,4]=-999
    mask,y=targets((None,valid,raw,None),4,'change')
    assert mask.tolist()==[False,True,True]
    assert y.shape==(2,63) and y.count_nonzero()==0
