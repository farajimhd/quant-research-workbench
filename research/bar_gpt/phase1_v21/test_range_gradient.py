import torch
from torch.nn import functional as F
from .range_gradient_audit import range_gradient
from .objectives import range_edges


def test_analytic_range_gradient_matches_autograd():
    p=torch.tensor([0.,.00001,.0005,.001,.003,.1],dtype=torch.float64,requires_grad=True)
    t=torch.tensor([0.,.001,.0005,0.,.002,.03],dtype=torch.float64)
    e=range_edges(p.device)
    loss=.1*F.smooth_l1_loss((p-t)*10000,torch.zeros_like(p),reduction='sum')+.05*F.binary_cross_entropy_with_logits((p[:,None]-e)*10000,(t[:,None]>e).double(),reduction='none').mean(-1).sum()
    actual,=torch.autograd.grad(loss,p);a,b=range_gradient(p,t)
    torch.testing.assert_close(actual,a+b,rtol=1e-12,atol=1e-12)
