"""Fused stable-identity fill prefix and masked ledger writes; no host reads."""
import triton
import triton.language as tl


@triton.jit
def scatter_compact(Q,P,F,R,IDS,ORDER,L,COUNT,OVERFLOW,NOW,CLOCK,
                    N:tl.constexpr,M:tl.constexpr,MAX:tl.constexpr,
                    QS0:tl.constexpr,QS1:tl.constexpr,QS2:tl.constexpr,
                    PS0:tl.constexpr,PS1:tl.constexpr,PS2:tl.constexpr,
                    FS0:tl.constexpr,FS1:tl.constexpr,FS2:tl.constexpr,
                    RS0:tl.constexpr,RS1:tl.constexpr,RS2:tl.constexpr,
                    IS0:tl.constexpr,IS1:tl.constexpr,OS0:tl.constexpr,OS1:tl.constexpr,
                    LS0:tl.constexpr,LS1:tl.constexpr,LS2:tl.constexpr,
                    SIDE:tl.constexpr,BLOCK:tl.constexpr):
    candidate=tl.program_id(0);index=tl.arange(0,BLOCK);valid=index<N*M
    slot=index%M;rank=index//M
    ticker=tl.load(ORDER+candidate*OS0+rank*OS1,valid,other=0)
    quantity=tl.load(Q+candidate*QS0+ticker*QS1+slot*QS2,valid,other=0)
    active=valid&(quantity>0)
    initial=tl.load(COUNT+candidate)
    prefix=tl.cumsum(active.to(tl.int32),0)
    position=initial+prefix-1
    total=tl.sum(active.to(tl.int32),0)
    previous_overflow=tl.load(OVERFLOW+candidate)
    tl.store(OVERFLOW+candidate,previous_overflow|(initial+total>MAX))
    write=active&(position<MAX)
    identity=tl.load(IDS+candidate*IS0+ticker*IS1,write,other=0).to(tl.float64)
    price=tl.load(P+candidate*PS0+ticker*PS1+slot*PS2,write,other=0)
    fee=tl.load(F+candidate*FS0+ticker*FS1+slot*FS2,write,other=0)
    reason=tl.load(R+candidate*RS0+ticker*RS1+slot*RS2,write,other=0).to(tl.float64)
    address=L+candidate*LS0+position*LS1
    tl.store(address,tl.load(NOW).to(tl.float64),write)
    tl.store(address+LS2,identity,write)
    tl.store(address+2*LS2,slot.to(tl.float64),write)
    tl.store(address+3*LS2,tl.full((BLOCK,),SIDE,tl.float64),write)
    tl.store(address+4*LS2,quantity.to(tl.float64),write)
    tl.store(address+5*LS2,price,write)
    tl.store(address+6*LS2,fee,write)
    tl.store(address+7*LS2,reason,write)
    tl.store(address+8*LS2,tl.load(CLOCK).to(tl.float64),write)
    tl.store(COUNT+candidate,initial+total)


def append_masked(ledger,count,overflow,qty,price,fee,now,reason,clock,identities,ticker_order,side,maximum):
    price=price.expand(qty.shape);fee=fee.expand(qty.shape);reason=reason.expand(qty.shape)
    block=triton.next_power_of_2(qty.shape[1]*qty.shape[2])
    if block>4096:raise ValueError('Compact masked ledger exceeds qualified slot block')
    scatter_compact[(qty.shape[0],)](qty,price,fee,reason,identities,ticker_order,ledger,count,overflow,now,clock,
        qty.shape[1],qty.shape[2],maximum,*qty.stride(),*price.stride(),*fee.stride(),*reason.stride(),
        *identities.stride(),*ticker_order.stride(),*ledger.stride(),side,block,num_warps=8 if block>1024 else 4)
