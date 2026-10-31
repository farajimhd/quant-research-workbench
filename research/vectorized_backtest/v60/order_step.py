"""Single-pass fills, displayed-book consumption and position accounting.

Live state stays in place. The CPU financial_tick remains the independent oracle.
"""
import torch
import triton
from .order_transition import order_fill_kernel
from .protected_transition import aggregate_kernel


@torch.library.custom_op('vector_v60::order_step',mutates_args=('p','o','book','opened','ticker','diagnostic'))
def step(p:torch.Tensor,o:torch.Tensor,book:torch.Tensor,opened:torch.Tensor,ticker:torch.Tensor,
         aggregate:torch.Tensor,signals:torch.Tensor,ratios:torch.Tensor,policy:torch.Tensor,
         previous:torch.Tensor,mark:torch.Tensor,membership:torch.Tensor,swing:torch.Tensor,
         bid:torch.Tensor,ask:torch.Tensor,valid:torch.Tensor,snapshot:torch.Tensor,
         clock:torch.Tensor,terminal:torch.Tensor,active:torch.Tensor,
         entry:float,addition:float,fee:float,minimum_fee:float,cost:float,minimum:float,diagnostic:torch.Tensor
         )->tuple[torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor]:
    s,b,u,_=p.shape;tiles=triton.cdiv(u,128)
    if any(not x.is_contiguous() for x in (p,o,book,opened,ticker)):
        raise ValueError('Contiguous execution state required')
    vectors=[x.expand(s,b,u) for x in (signals,previous,membership,swing,bid,ask,valid)]+[snapshot.expand(s,b,u,3)]
    policy=policy.expand(s,b,u,11);mark=mark.expand(s,b,u)
    complete=torch.empty((s,b,u),device=p.device,dtype=torch.bool)
    closed=torch.empty_like(complete);profit=torch.empty_like(opened);entries=torch.empty_like(opened)
    record=torch.empty((s,b,u,17),device=p.device,dtype=p.dtype)
    partial=torch.empty((s*b,tiles,15),device=p.device,dtype=p.dtype)
    out=torch.empty_like(aggregate)
    order_fill_kernel[(s*b,tiles)](p,o,vectors[0],ratios,policy,*vectors[1:],clock,terminal,p,o,complete,record,
        u,b,tuple(x.stride() for x in vectors),policy.stride(),ratios.stride(),entry,addition,fee,minimum_fee,cost,minimum,128,
        True,book,mark,opened,ticker,active,closed,profit,entries,partial,mark.stride(),tiles,diagnostic if diagnostic is not None and diagnostic.numel() else None,
        num_warps=4,enable_fp_fusion=False)
    aggregate_kernel[(s*b,)](partial,aggregate,out,tiles,b,aggregate.stride(),triton.next_power_of_2(tiles),num_warps=4,enable_fp_fusion=False)
    return out,closed,profit,entries,complete,record


@step.register_fake
def fake(p,o,book,opened,ticker,aggregate,signals,ratios,policy,previous,mark,membership,swing,bid,ask,valid,snapshot,
         clock,terminal,active,entry,addition,fee,minimum_fee,cost,minimum,diagnostic):
    mask=torch.empty(p.shape[:-1],device=p.device,dtype=torch.bool)
    return (torch.empty_like(aggregate),mask,torch.empty_like(opened),torch.empty_like(opened),torch.empty_like(mask),
            torch.empty((*p.shape[:-1],17),device=p.device,dtype=p.dtype))
