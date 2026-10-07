"""Masked FP64 actual-fill writes inside a vectorized Torch custom operator.

Imported only after CUDA compiler setup. No dynamic output shapes, host reads,
candidate loops or inactive scratch writes; prefix ranks remain native Torch.
"""
import triton
import triton.language as tl


@triton.jit
def scatter_fills(Q,P,F,R,POS,L,NOW,CLOCK,
                  N:tl.constexpr,M:tl.constexpr,TOTAL:tl.constexpr,MAX:tl.constexpr,
                  QS0:tl.constexpr,QS1:tl.constexpr,QS2:tl.constexpr,
                  PS0:tl.constexpr,PS1:tl.constexpr,PS2:tl.constexpr,
                  FS0:tl.constexpr,FS1:tl.constexpr,FS2:tl.constexpr,
                  RS0:tl.constexpr,RS1:tl.constexpr,RS2:tl.constexpr,
                  LS0:tl.constexpr,LS1:tl.constexpr,LS2:tl.constexpr,
                  SIDE:tl.constexpr,BLOCK:tl.constexpr):
    index=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK)
    valid=index<TOTAL;batch=index//(N*M);ticker=index//M%N;slot=index%M
    quantity=tl.load(Q+batch*QS0+ticker*QS1+slot*QS2,valid,other=0)
    position=tl.load(POS+index,valid,other=MAX)
    active=valid&(quantity>0)&(position<MAX)
    address=L+batch*LS0+position*LS1
    stamp=tl.load(NOW).to(tl.float64);clock=tl.load(CLOCK).to(tl.float64)
    price=tl.load(P+batch*PS0+ticker*PS1+slot*PS2,active,other=0)
    fee=tl.load(F+batch*FS0+ticker*FS1+slot*FS2,active,other=0)
    reason=tl.load(R+batch*RS0+ticker*RS1+slot*RS2,active,other=0).to(tl.float64)
    tl.store(address+0*LS2,stamp,active)
    tl.store(address+1*LS2,ticker.to(tl.float64),active)
    tl.store(address+2*LS2,slot.to(tl.float64),active)
    tl.store(address+3*LS2,tl.full((BLOCK,),SIDE,tl.float64),active)
    tl.store(address+4*LS2,quantity.to(tl.float64),active)
    tl.store(address+5*LS2,price,active)
    tl.store(address+6*LS2,fee,active)
    tl.store(address+7*LS2,reason,active)
    tl.store(address+8*LS2,clock,active)


def append_masked(ledger,fill_count,overflow,qty,price,fee,now,reason,clock,slot_axis,side,maximum_fills):
    active=qty.flatten(1)>0
    positions=(fill_count[:,None]+active.cumsum(-1)-1).contiguous()
    overflow.logical_or_((active&(positions>=maximum_fills)).any(-1))
    price=price.expand(qty.shape);reason=reason.expand(qty.shape);fee=fee.expand(qty.shape)
    scatter_fills[(triton.cdiv(qty.numel(),256),)](
        qty,price,fee,reason,positions,ledger,now,clock,
        qty.shape[1],qty.shape[2],qty.numel(),maximum_fills,
        *qty.stride(),*price.stride(),*fee.stride(),*reason.stride(),*ledger.stride(),side,256)
    fill_count.add_(active.sum(-1))
