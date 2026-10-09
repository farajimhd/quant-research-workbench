"""Stable listing identities for moving compact financial slots."""
import torch
from functools import wraps


def _append(ledger:torch.Tensor,count:torch.Tensor,overflow:torch.Tensor,qty:torch.Tensor,
            price:torch.Tensor,fee:torch.Tensor,now:torch.Tensor,reason:torch.Tensor,
            clock:torch.Tensor,identities:torch.Tensor,ticker_order:torch.Tensor,side:int,maximum:int)->None:
    b,n,m=qty.shape;shape=(b,n*m)
    lots=torch.arange(m,device=qty.device)[None,None].expand(b,n,-1)
    # Canonical identity/lot order keeps simultaneous fills stable across slot changes.
    order=(ticker_order[:,:,None]*m+lots).reshape(shape)
    def flatten(value):return value.expand(qty.shape).reshape(shape).gather(1,order)
    amount=flatten(qty);active=amount>0;positions=count[:,None]+active.cumsum(-1)-1
    valid=positions<maximum;overflow.logical_or_((active&~valid).any(-1))
    rows=torch.stack((now.expand(shape),flatten(identities[:,:,None]),flatten(lots),
        torch.full_like(amount,side),amount,flatten(price),flatten(fee),flatten(reason),clock.expand(shape)),-1).to(torch.float64)
    scratch=torch.arange(n*m,device=qty.device)[None]
    destination=torch.where(active&valid,positions,maximum+scratch)
    ledger.scatter_(1,destination[...,None].expand(-1,-1,9),torch.where((active&valid)[...,None],rows,0))
    count.add_(active.sum(-1))


@wraps(_append)
def _dispatch(*args,**kwargs):
    if not args[0].is_cuda:return _append(*args,**kwargs)
    # Import after the pinned CUDA compiler has been configured.
    from .compact_ledger_scatter import append_masked
    return append_masked(*args,**kwargs)


try:compact_ledger_append=torch.ops.torch_backtest_v6.compact_ledger_append.default
except AttributeError:
    compact_ledger_append=torch.library.custom_op('torch_backtest_v6::compact_ledger_append',
        mutates_args=('ledger','count','overflow'))(_dispatch)
    compact_ledger_append.register_fake(lambda *args:None)
