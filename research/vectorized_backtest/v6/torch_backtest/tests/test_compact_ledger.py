"""CPU oracle for canonical slot order, masked CUDA writes and sticky overflow."""
import os
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.compact_ledger import _append,compact_ledger_append


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA qualification requires a GPU')
def test_fused_compact_ledger_exact_native_oracle():
    from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches,DEFAULT
    configure_caches(DEFAULT/'tests'/f'compact-ledger-cuda-{os.getpid()}')
    maximum=5
    qty=torch.tensor([[[1,2,0],[3,0,4],[0,0,0]],[[0,1,2],[3,0,0],[4,0,5]]]).transpose(1,2)
    identities=torch.tensor([[17,5,23],[8,9,3]])
    order=identities.argsort(dim=-1,stable=True)
    price=torch.tensor([[[10.25],[20.5],[30.75]]],dtype=torch.float64)
    fee=torch.arange(18,dtype=torch.float64).reshape(2,3,3)/100
    reason=torch.tensor([[[1],[3],[5]],[[2],[0],[4]]])
    ledger=torch.full((2,maximum+9,9),-7.,dtype=torch.float64)
    counts=torch.tensor([0,2]);overflow=torch.tensor([False,True])
    now=torch.tensor(12345);clock=torch.tensor(12)
    args=[ledger,counts,overflow,qty,price,fee,now,reason,clock,identities,order]
    gpu=[value.cuda() for value in args]
    for _ in range(2):
        _append(*args,-1,maximum);compact_ledger_append(*gpu,-1,maximum)
        torch.cuda.synchronize()
        torch.testing.assert_close(gpu[0][:,:maximum].cpu(),ledger[:,:maximum],rtol=0,atol=0)
        assert torch.equal(gpu[1].cpu(),counts) and torch.equal(gpu[2].cpu(),overflow)
