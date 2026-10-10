import torch
from research.vectorized_backtest.v6.torch_backtest.compiler_family import isolated_tick


class Example:
    def __init__(self,n):self.n=n
    def tick(self,x):return x*self.n+1


def test_code_identity_is_unchanged_and_resets_only_at_family_boundaries(monkeypatch):
    resets=[]
    monkeypatch.setattr(torch._dynamo,'reset_code',resets.append)
    method=Example(3).tick
    assert isolated_tick(method,('identity-test',3)) is method
    assert isolated_tick(Example(4).tick,('identity-test',3)).__func__ is method.__func__
    assert len(resets)==1
    assert isolated_tick(method,('identity-test',4)) is method
    assert resets==[method.__func__.__code__]*2


def test_separate_families_do_not_exhaust_shared_dynamo_recompile_limit():
    from torch._dynamo import config
    with config.patch(recompile_limit=2):
        for n in range(1,5):
            method=Example(n).tick
            fn=torch.compile(isolated_tick(method,('bounded-test',n)),backend='eager',fullgraph=True)
            x=torch.tensor([2.])
            assert torch.equal(fn(x),method(x))
            assert torch.equal(fn(torch.tensor([3.])),torch.tensor([3.*n+1]))
