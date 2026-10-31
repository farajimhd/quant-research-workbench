"""Fused actual-fill reductions with division restricted to held-position tiles."""
import torch
import triton
import triton.language as tl
from .protected_transition import aggregate_kernel


@triton.jit
def order_account_kernel(BEFORE,AFTER,OLD,ORD,MARK,RECORD,PART,U:tl.constexpr,TILES:tl.constexpr,B:tl.constexpr,MS:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);tile=tl.program_id(1);j=tile*BLOCK+tl.arange(0,BLOCK);live=j<U;at=row*U+j
    p=AFTER+at*9;oldp=BEFORE+at*9;o=ORD+at*17;old=OLD+at*17
    q=tl.load(p,live,0.);avg=tl.load(p+1,live,0.);stop=tl.load(p+2,live,0.)
    realized=tl.load(p+7,live,0.);oldq=tl.load(oldp,live,0.);episode=tl.load(oldp+8,live,0.)
    mark=tl.load(MARK+(row//B)*MS[0]+(row%B)*MS[1]+j*MS[2],live,0.)
    price=tl.where(tl.abs(mark)<float('inf'),mark,0.)
    closed=(oldq>1.e-12)&(q<=1.e-12)&live;profit=realized-episode
    risk=q*tl.maximum(avg-stop,0.);distance=tl.full((BLOCK,),0.,tl.float64)
    if tl.sum((q>0).to(tl.int32),0)>0:distance=tl.where(q>0,tl.maximum(avg-stop,0.)/tl.maximum(avg,1.e-12),0.)
    filled=tl.load(o+14,live,0.)>tl.load(old+14,live,0.)
    new=tl.load(o+12,live,0.)>tl.load(old+12,live,0.)
    qty=tl.load(o+4,live,0.);notional=tl.load(o+5,live,0.)
    delta_qty=tl.where(new,qty,qty-tl.load(old+4,live,0.))
    delta_value=tl.where(new,notional,notional-tl.load(old+5,live,0.))
    first=filled&(qty==delta_qty);kind=tl.load(RECORD+at*17,live,0.)
    out=PART+(row*TILES+tile)*15
    tl.store(out,tl.sum(tl.where(live,realized+q*(price-avg),0.),0))
    tl.store(out+1,tl.sum(tl.where(live,q*price,0.),0));tl.store(out+2,tl.sum(risk,0))
    tl.store(out+3,tl.sum((first&(kind==1)).to(tl.float64),0));tl.store(out+4,tl.sum((first&(kind==2)).to(tl.float64),0))
    tl.store(out+5,tl.sum((first&(kind==3)).to(tl.float64),0));tl.store(out+6,tl.sum((q>1.e-12).to(tl.float64),0))
    tl.store(out+7,tl.min(tl.where(closed,profit,float('inf')),0));tl.store(out+8,tl.sum(closed.to(tl.float64),0))
    tl.store(out+9,tl.sum((closed&(profit>0)).to(tl.float64),0));tl.store(out+10,tl.sum(tl.where(filled,delta_value,0.),0))
    tl.store(out+11,tl.max(risk,0));tl.store(out+12,tl.max(distance,0));tl.store(out+13,0.);tl.store(out+14,0.)


@torch.library.custom_op('vector_v59::order_accounting',mutates_args=())
def account(before:torch.Tensor,after:torch.Tensor,old_orders:torch.Tensor,orders:torch.Tensor,
            aggregate:torch.Tensor,mark:torch.Tensor,record:torch.Tensor)->torch.Tensor:
    s,b,u,_=after.shape;tiles=triton.cdiv(u,128)
    mark=mark.expand(s,b,u);partial=torch.empty((s*b,tiles,15),device=after.device,dtype=after.dtype)
    out=torch.empty_like(aggregate)
    order_account_kernel[(s*b,tiles)](before,after,old_orders,orders,mark,record,partial,u,tiles,b,mark.stride(),128,num_warps=4,enable_fp_fusion=False)
    aggregate_kernel[(s*b,)](partial,aggregate,out,tiles,b,aggregate.stride(),triton.next_power_of_2(tiles),num_warps=4,enable_fp_fusion=False)
    return out


@account.register_fake
def fake(before,after,old_orders,orders,aggregate,mark,record):return torch.empty_like(aggregate)
