import torch
from .attention_kernels import attention


def test_padding_keeps_original_scale_and_gradients():
    torch.manual_seed(12)
    for d,t,mode in ((34,56,'pad'),(4,18,'tiny')):
        q,k,v=[torch.randn(2,4,t,d,requires_grad=True) for _ in range(3)]
        expected=torch.nn.functional.scaled_dot_product_attention(q,k,v)
        actual=attention(q,k,v,mode)
        torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-6)
        eg=torch.autograd.grad(expected.square().sum(),(q,k,v),retain_graph=True)
        ag=torch.autograd.grad(actual.square().sum(),(q,k,v))
        for a,b in zip(ag,eg):torch.testing.assert_close(a,b,rtol=1e-4,atol=3e-6)
