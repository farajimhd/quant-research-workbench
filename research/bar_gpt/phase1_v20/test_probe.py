import torch
from .bar_probe import solve


def test_probe_fit_recovers_linear_information_with_independent_masks():
    torch.manual_seed(9);x=torch.randn(1000,16,dtype=torch.float64);x=torch.cat((x,torch.ones(1000,1,dtype=torch.float64)),1)
    coefficients=torch.randn(18,17,dtype=torch.float64);y=x@coefficients.T;mask=torch.rand(1000,18)>.2
    gram=(mask.double().T@(x[:,:,None]*x[:,None,:]).flatten(1)).reshape(18,17,17)
    rhs=torch.where(mask,y,0).T@x
    torch.testing.assert_close(solve(gram,rhs,ridge=0),coefficients,rtol=1e-10,atol=1e-10)
