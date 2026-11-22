import torch
from .temporal_basis import ActivityBasisLinear,dct64


def test_basis_preserves_forward_and_embedding_gradient():
    torch.manual_seed(17)
    native=torch.nn.Linear(16,1152).double()
    basis=ActivityBasisLinear(native)
    x=torch.randn(7,16,dtype=torch.float64,requires_grad=True)
    y=native(x);z=basis(x)
    torch.testing.assert_close(y,z,atol=1e-13,rtol=1e-12)
    g=torch.randn_like(y)
    a=torch.autograd.grad((y*g).sum(),x)[0]
    b=torch.autograd.grad((z*g).sum(),x)[0]
    torch.testing.assert_close(a,b,atol=1e-12,rtol=1e-12)
    assert sum(p.numel() for p in native.parameters())==sum(p.numel() for p in basis.parameters())
    assert 'weight' not in basis.state_dict()


def test_change_loss_has_no_dc_gradient_before_constraints():
    torch.manual_seed(19)
    basis=ActivityBasisLinear(torch.nn.Linear(16,1152).double())
    x=torch.randn(8,16,dtype=torch.float64)
    z=basis(x).reshape(-1,64,18)[:,:,basis.activity_indices]
    (z.diff(dim=1)-torch.randn_like(z[:,1:])).square().mean().backward()
    dc=basis.coefficient_weight.grad.reshape(64,18,16)[0,basis.activity_indices]
    torch.testing.assert_close(dc,torch.zeros_like(dc),atol=1e-15,rtol=0)
    q=dct64()
    torch.testing.assert_close(q@q.T,torch.eye(64,dtype=q.dtype),atol=1e-13,rtol=1e-12)

