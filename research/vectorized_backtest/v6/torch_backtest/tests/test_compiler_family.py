import torch
from research.vectorized_backtest.v6.torch_backtest.compiler_family import isolated_tick


class Example:
    def __init__(self,n):self.n=n
    def tick(self,x):return x*self.n+1


def test_code_identity_preserves_instructions_closure_and_reuses_family():
    method=Example(3).tick
    clone=isolated_tick(method,('a',3))
    assert clone.__func__.__code__.co_code==method.__func__.__code__.co_code
    assert clone.__func__.__code__.co_consts==method.__func__.__code__.co_consts
    assert clone.__func__ is isolated_tick(Example(4).tick,('a',3)).__func__
    assert clone.__func__.__code__!=isolated_tick(method,('a',4)).__func__.__code__
    assert torch.equal(clone(torch.tensor([2.])),method(torch.tensor([2.])))


def test_separate_families_do_not_exhaust_shared_dynamo_recompile_limit():
    from torch._dynamo import config
    with config.patch(recompile_limit=2):
        compiled=[]
        for n in range(1,5):
            method=Example(n).tick
            fn=torch.compile(isolated_tick(method,('bounded-test',n)),backend='eager',fullgraph=True)
            x=torch.tensor([2.])
            assert torch.equal(fn(x),method(x))
            compiled.append((fn,n))
        for fn,n in compiled:
            assert torch.equal(fn(torch.tensor([3.])),torch.tensor([3.*n+1]))
