"""Inlined CUDA counterpart of the policy-free quote-liquidity fill."""
import triton
import triton.language as tl


@triton.jit
def fill(kind,remaining,dollars,requested,filled,notional,fees,q,bid,ask,valid,
         buy_capacity,sell_capacity,FEE:tl.constexpr,MINFEE:tl.constexpr):
    buy=(kind==1)|(kind==2);sell=(kind==3)|(kind==4)
    fresh=valid&(bid>0)&(ask>=bid)&(tl.abs(bid)<float('inf'))&(tl.abs(ask)<float('inf'))
    capacity=tl.where(buy,buy_capacity,sell_capacity)
    capfinite=tl.abs(capacity)<float('inf');activate=buy&(requested==0)&fresh&capfinite&(capacity>1.e-12)
    if tl.sum(activate.to(tl.int32),0)>0:requested=tl.where(activate,dollars/tl.maximum(ask,1.e-12),requested)
    remaining=tl.where(activate,requested,remaining)
    available=tl.where(fresh&capfinite,tl.maximum(capacity,0.),0.)
    quantity=tl.minimum(tl.maximum(remaining,0.),available);quantity=tl.where(sell,tl.minimum(quantity,q),quantity)
    quantity=tl.where((kind!=0)&(quantity>1.e-12),quantity,0.);executed=quantity>0
    price=tl.where(fresh,tl.where(buy,ask,bid),0.);value=quantity*price
    new_filled=filled+quantity;new_notional=notional+value
    new_fees=tl.where(executed,tl.maximum(new_filled*FEE,MINFEE),fees)
    return requested,remaining,quantity,price,new_filled,new_notional,new_fees,executed
