"""Fused candidate price expressions; double precision and no implicit fallback."""
import torch
import triton as tr
import triton.language as tl


@tr.jit
def expressions(BANK,TIMES,A,B,OP,W,OFFSET,VOL,OUT,N:tl.constexpr,M:tl.constexpr,R:tl.constexpr,VI:tl.constexpr,BLOCK:tl.constexpr):
    x=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=x<N
    expr=x%5;slot=x//5%5;member=x//25%M;time=x//(25*M)
    e=member*5+expr;clock=tl.load(TIMES+time,live,0)
    base=(clock*5+slot)*R
    ai=tl.load(A+e,live,0);bi=tl.load(B+e,live,0)
    a=tl.load(BANK+base+ai,live,0);b=tl.load(BANK+base+bi,live,0)
    op=tl.load(OP+e,live,0);weight=tl.load(W+e,live,0)
    w=weight.to(tl.float64);other=(1.-weight).to(tl.float64)
    low=tl.where((a!=a)|(b!=b),float('nan'),tl.minimum(a,b))
    high=tl.where((a!=a)|(b!=b),float('nan'),tl.maximum(a,b))
    available=(a==a)&(tl.abs(a)!=float('inf'))&(a>0)
    value=tl.where(op==0,a,tl.where(op==1,low,tl.where(op==2,high,tl.where(op==3,w*a+other*b,tl.where(available,a,b)))))
    # Match Torch's float32 coefficient arithmetic before float64 promotion.
    scale=1.+tl.load(OFFSET+e,live,0)
    value=value*scale.to(tl.float64)
    if VI>=0:
        v=tl.load(BANK+base+VI,live,0)
        value=value+v*tl.load(VOL+e,live,0).to(tl.float64)
    tl.store(OUT+x,value,live)


def evaluate(plan,indices):
    t=len(indices);b=plan.a.shape[0]
    out=torch.empty((t,b,5,5),device=indices.device,dtype=plan.bank.dtype)
    expressions[(tr.cdiv(out.numel(),256),)](plan.bank,indices,plan.a,plan.b,plan.op,plan.weight,
        plan.offset,plan.volatility,out,out.numel(),b,len(plan.names),
        -1 if plan.vol_index is None else plan.vol_index,256,enable_fp_fusion=False)
    return out
